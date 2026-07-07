"""
Report data-layer tests — build_payload was the largest untested surface
(everything the HTML shows is built here). Covers the three behaviors the
debt sweep flagged as silently breakable: phantom exclusion, multi-edge
collapse to the strongest kind, and payload shape.
"""
from tool.report.data import build_payload
from tool.agents import ScannerAgent
from tool.db import DBManager
from tool.llm import LLMClient
from tool.source_io import SourceReader


def _scan_fixture(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    # Alpha holds Beta by value AND by pointer → two edges Alpha→Beta of
    # different strengths (composes level 4 vs associates level 1).
    (src / "AB.hxx").write_text("""
class Beta { public: void Poke(); };
class Alpha { Beta m_b; Beta* m_pb; };
""")
    # Lone .cxx with an unseen class → phantom Ghost is materialized for
    # analysis but must NEVER surface in the report.
    (src / "Impl.cxx").write_text("void Ghost::Run() { }\n")
    db = DBManager(tmp_path / "t.db")
    db.ensure_tables()
    ScannerAgent(llm=LLMClient(cache=db), db=db,
                 reader=SourceReader(str(src), db=db)).run(str(src))
    return db


class TestBuildPayload:
    def test_payload_shape(self, tmp_path):
        payload = build_payload(_scan_fixture(tmp_path))
        assert set(payload) == {"summary", "graph", "arch", "review"}
        assert payload["summary"]["class_count"] == 3   # Alpha, Beta, Ghost

    def test_phantom_excluded_from_graph_and_arch(self, tmp_path):
        payload = build_payload(_scan_fixture(tmp_path))
        node_ids = {n["id"] for n in payload["graph"]["nodes"]}
        assert "Ghost" not in node_ids           # phantom never rendered
        assert "Alpha" in node_ids and "Beta" in node_ids

        def walk(nodes):
            for n in nodes:
                yield n
                yield from walk(n.get("children", []))
        arch_names = {n.get("id") or n.get("label")
                      for root in payload["arch"] for n in walk([root])} \
            if isinstance(payload["arch"], list) else set()
        assert "Ghost" not in arch_names

    def test_multi_edge_collapses_to_strongest_primary(self, tmp_path):
        payload = build_payload(_scan_fixture(tmp_path))
        edges = [e for e in payload["graph"]["edges"]
                 if e["source"] == "Alpha" and e["target"] == "Beta"]
        assert len(edges) == 1                   # one drawn edge per pair
        e = edges[0]
        assert e["primary"] == "composes"        # strongest kind wins
        assert "associates" in e["kinds"]        # weaker kind still listed

    def test_review_section_safe_when_no_review_ran(self, tmp_path):
        payload = build_payload(_scan_fixture(tmp_path))
        assert payload["review"] is not None     # empty-but-valid, no crash

    def test_arch_graph_absent_until_audit_runs(self, tmp_path):
        payload = build_payload(_scan_fixture(tmp_path))
        assert payload["arch_graph"] is None     # section hides itself

    def test_arch_graph_present_with_cycle_flags_after_audit(self, tmp_path):
        # a real 2-folder module cycle → persisted audit → arch_graph payload
        from tool import tools as T
        from tool.llm import LLMClient
        src = tmp_path / "src"
        (src / "foo").mkdir(parents=True)
        (src / "bar").mkdir(parents=True)
        (src / "foo" / "A.hxx").write_text("class B;\nclass A { B* b; };\n")
        (src / "bar" / "B.hxx").write_text("class A;\nclass B { A* a; };\n")
        db = DBManager(tmp_path / "t.db"); db.ensure_tables()
        ctx = T.ToolContext(db=db, llm=LLMClient(cache=db),
                            reader=SourceReader(str(src), db=db),
                            source_root=src)
        reg = T.build_registry(ctx)
        T.run_tool(reg, "scan_source", {"directory": str(src)}, ctx)
        T.run_tool(reg, "architecture_audit", {"strategy": "folder"}, ctx)

        ag = build_payload(db)["arch_graph"]
        assert ag is not None
        assert {n["id"] for n in ag["nodes"]} == {"foo", "bar"}
        assert all(n["in_cycle"] for n in ag["nodes"])       # both in the cycle
        assert all(e["in_cycle"] for e in ag["edges"])
        assert any(f["kind"] == "no_module_cycle" for f in ag["findings"])

    def test_orchestrator_never_renders_as_utility(self, tmp_path):
        # A CRTP core matches the utility shape (in>=2, out==0). If the
        # scorer crowned it orchestrator, the report must not tag the same
        # node is_util — contradictory rendering.
        src = tmp_path / "src"
        src.mkdir()
        src.joinpath("W.hxx").write_text("""
class Base { public: void Tick(); };
class W1 : public Base {};
class W2 : public Base {};
""")
        db = DBManager(tmp_path / "t.db")
        db.ensure_tables()
        ScannerAgent(llm=LLMClient(cache=db), db=db,
                     reader=SourceReader(str(src), db=db)).run(str(src))
        # force the sink to be the recorded orchestrator (what the CRTP
        # scorer would do on a big template codebase)
        db._execute("UPDATE module_info SET orchestrator='Base'")
        payload = build_payload(db)
        base = next(n for n in payload["graph"]["nodes"] if n["id"] == "Base")
        assert base["is_orch"] == 1
        assert base["is_util"] == 0
