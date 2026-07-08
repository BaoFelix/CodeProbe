"""
Registry + Host tests — the agentic wiring.

The registry is the system's stable contract; the Host is a dumb loop by
design. What we verify: dispatch safety (unknown tools / bad args / write
SQL degrade into messages, never exceptions), scan idempotency (the
scan-once-then-read-DB behavior), and the loop's mechanics (threading tool
results back, step ceiling).
"""
from tool.tools import build_registry, tool_schemas, run_tool
from tool.host import Host, MAX_STEPS
from conftest import fresh_ctx, ScriptedLLM, tool_step, final_step


class TestRegistry:
    def test_every_tool_has_a_valid_schema(self, ctx):
        registry = build_registry(ctx)
        schemas = tool_schemas(registry)
        assert len(schemas) == len(registry) >= 10
        for s in schemas:
            assert s["name"] and s["description"]
            assert s["parameters"]["type"] == "object"

    def test_unknown_tool_degrades(self, ctx):
        registry = build_registry(ctx)
        assert "Unknown tool" in run_tool(registry, "nope", {}, ctx)

    def test_bad_args_degrade(self, ctx):
        registry = build_registry(ctx)
        out = run_tool(registry, "get_overview", {"bogus_arg": 1}, ctx)
        assert "Bad arguments" in out

    def test_query_db_rejects_writes(self, ctx):
        registry = build_registry(ctx)
        out = run_tool(registry, "query_db",
                       {"sql": "DROP TABLE entities"}, ctx)
        assert "Only SELECT" in out

    def test_read_tools_safe_on_empty_db(self, ctx):
        registry = build_registry(ctx)
        for name in ("get_overview", "list_classes", "get_relationships",
                     "architecture_audit", "decoupling_plan",
                     "get_findings"):
            out = run_tool(registry, name, {}, ctx)
            assert "scan_source first" in out or "No design review" in out


class TestScanIdempotency:
    """The 'scan once, then read from the DB' contract."""

    def test_second_scan_is_skipped(self, tmp_path):
        ctx = fresh_ctx(tmp_path)
        registry = build_registry(ctx)
        first = run_tool(registry, "scan_source",
                         {"directory": "test_src"}, ctx)
        assert "Scan complete" in first
        second = run_tool(registry, "scan_source",
                          {"directory": "test_src"}, ctx)
        assert "Already scanned" in second

    def test_scanned_db_answers_grounded_queries(self, scanned_ctx):
        registry = build_registry(scanned_ctx)
        rels = run_tool(registry, "get_relationships",
                        {"class_qname": "Boat"}, scanned_ctx)
        # grounding: kind + file:line evidence in the answer text
        assert "--inherits-->" in rels and ".sch:" in rels

    def test_incoming_direction_finds_reverse_dependents(self, scanned_ctx):
        registry = build_registry(scanned_ctx)
        # Engine is used by many classes but depends on nothing → the
        # reverse view must list dependents, the forward view must not.
        inc = run_tool(registry, "get_relationships",
                       {"class_qname": "Engine", "direction": "incoming"},
                       scanned_ctx)
        assert "--> Engine" in inc            # others point AT Engine
        out = run_tool(registry, "get_relationships",
                       {"class_qname": "Engine", "direction": "outgoing"},
                       scanned_ctx)
        assert "No relationships" in out or "Engine --" not in out


class TestModuleDependencies:
    """'Module A depends on B N times — what are the N?' is a module-level
    question SQL cannot answer (modules aren't a DB column). The dedicated
    tool must reproduce the count AND enumerate the backing class edges."""

    def test_matrix_lists_module_edges_with_weight(self, scanned_ctx):
        registry = build_registry(scanned_ctx)
        out = run_tool(registry, "module_dependencies", {}, scanned_ctx)
        assert "Garage" in out and "→" in out and "×" in out

    def test_specific_edge_enumerates_class_evidence(self, scanned_ctx):
        registry = build_registry(scanned_ctx)
        out = run_tool(registry, "module_dependencies",
                       {"from_module": "Garage", "to_module": "(root)"},
                       scanned_ctx)
        # exactly the grounded class-level references behind the edge
        assert "class-level reference" in out
        assert "-->" in out                       # per-edge evidence lines
        assert out.count("·") >= 1

    def test_unknown_module_lists_choices(self, scanned_ctx):
        registry = build_registry(scanned_ctx)
        out = run_tool(registry, "module_dependencies",
                       {"from_module": "NoSuchModule",
                        "to_module": "Garage"}, scanned_ctx)
        assert "No module matches" in out


class TestDesignReviewStaleness:
    """A stored review counts as cached only while the graph is unchanged."""

    def _fake_critic(self, calls):
        class FakeCritic:
            def __init__(self, llm=None, db=None, reader=None):
                self.db = db

            def run(self):
                calls.append(1)
                from tool.db import graph_fingerprint
                self.db.save_design_module(
                    "default", "p", "r", {"recommendations": []},
                    graph_hash=graph_fingerprint(self.db.get_relationships()))
                return True
        return FakeCritic

    def test_rerun_skipped_while_graph_unchanged(self, tmp_path, monkeypatch):
        import tool.tools as T
        ctx = fresh_ctx(tmp_path)
        registry = build_registry(ctx)
        run_tool(registry, "scan_source", {"directory": "test_src"}, ctx)
        calls = []
        monkeypatch.setattr(T, "DesignCriticAgent", self._fake_critic(calls))
        assert "complete" in run_tool(registry, "design_review", {}, ctx)
        out = run_tool(registry, "design_review", {}, ctx)
        assert "unchanged" in out           # second call is a cache hit
        assert len(calls) == 1

    def test_rerun_happens_when_graph_changed(self, tmp_path, monkeypatch):
        import tool.tools as T
        ctx = fresh_ctx(tmp_path)
        registry = build_registry(ctx)
        run_tool(registry, "scan_source", {"directory": "test_src"}, ctx)
        calls = []
        monkeypatch.setattr(T, "DesignCriticAgent", self._fake_critic(calls))
        run_tool(registry, "design_review", {}, ctx)
        # simulate a rescan that changed the graph
        ctx.db._execute("DELETE FROM relationships WHERE rowid IN "
                        "(SELECT rowid FROM relationships LIMIT 1)")
        run_tool(registry, "design_review", {}, ctx)
        assert len(calls) == 2              # stale → re-ran, no force needed


class TestHostLoop:
    def test_loop_runs_tools_then_answers(self, tmp_path):
        script = [tool_step("scan_source", {"directory": "test_src"}),
                  tool_step("get_overview", call_id="c2"),
                  final_step("11 classes, orchestrated by Workshop.")]
        ctx = fresh_ctx(tmp_path, llm=ScriptedLLM(script))
        host = Host(ctx=ctx, verbose=False)
        answer = host.ask("what does this module look like?")
        assert answer == "11 classes, orchestrated by Workshop."
        # history: user + 2×(assistant + tool result) + final assistant
        assert len(host.history) == 6

    def test_step_ceiling_stops_runaway_plans(self, tmp_path):
        # a model that never stops calling tools must be cut off
        script = [tool_step("get_overview")]      # replays forever
        ctx = fresh_ctx(tmp_path, llm=ScriptedLLM(script))
        host = Host(ctx=ctx, verbose=False)
        answer = host.ask("loop forever")
        assert "step limit" in answer
        # exactly MAX_STEPS rounds ran, not one more
        tool_msgs = [m for m in host.history if m.get("role") == "tool"]
        assert len(tool_msgs) == MAX_STEPS
