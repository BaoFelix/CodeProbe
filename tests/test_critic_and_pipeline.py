"""
Tests for the design-critic subtree assembly and the pipeline guard —
both found broken/untested by the debt sweep.
"""
from tool.workflow import build_graph, condense
from tool.design_critic import (_collect_subtree, _expand_to_concrete,
                                _safe_parse_json, _load_user_override)
from tool.model import Entity, Relationship
from tool.pipeline import Pipeline


def _ent(qname, kind="class"):
    return Entity(kind=kind, name=qname.split("::")[-1], qualified_name=qname,
                  file_path="x.hxx", start_line=1, end_line=2)


def _rel(s, t, kind="depends"):
    return Relationship(source_qname=s, target_name=t.split("::")[-1],
                        kind=kind, evidence_file="x.hxx", evidence_line=1,
                        target_qname=t)


class TestSubtreeCollection:
    def test_namespaced_cycle_members_survive_into_subtree(self):
        # Garage::A and Garage::B form a cycle → condensed to one cluster
        # node. The subtree walk must return their QUALIFIED names — the
        # old label-parsing approach degraded them to short names and the
        # review silently dropped every namespaced class in a cycle.
        ents = [_ent("Garage::A"), _ent("Garage::B"), _ent("Root")]
        rels = [_rel("Root", "Garage::A"),
                _rel("Garage::A", "Garage::B"),
                _rel("Garage::B", "Garage::A")]
        g = build_graph(ents, rels)
        C, label = condense(g)
        root = next(n for n in C.nodes if C.in_degree(n) == 0)
        names = _collect_subtree(C, root)
        assert "Garage::A" in names and "Garage::B" in names
        # and expansion keeps them resolvable as real qnames
        concrete = _expand_to_concrete(names, rep_map={})
        assert {"Garage::A", "Garage::B", "Root"} <= concrete


class TestReviewResilience:
    def test_aborts_cleanly_when_llm_unreachable(self, tmp_path):
        # Every subtree call returns None (persistent timeout / dead
        # endpoint). The concurrent review must abort with False — not
        # crash, not emit an empty review as if it succeeded. (Concurrency
        # itself replaces the old sequential circuit-breaker: all failures
        # now happen inside one timeout window, not N of them.)
        from tool.agents import ScannerAgent
        from tool.design_critic import DesignCriticAgent
        from tool.db import DBManager
        from tool.source_io import SourceReader

        db = DBManager(tmp_path / "t.db")
        db.ensure_tables()

        class DeadLLM:
            def generate(self, prompt, system_prompt="", tag=""):
                return None

        reader = SourceReader("test_src", db=db)
        ScannerAgent(llm=DeadLLM(), db=db, reader=reader).run("test_src")
        ok = DesignCriticAgent(llm=DeadLLM(), db=db, reader=reader).run()
        assert ok is False
        # nothing bogus persisted (all None responses)
        got = db.get_design_module()
        assert got is None or not (got["parsed_json"] or "")

    def test_completed_subtrees_not_reattempted_on_rerun(self, tmp_path):
        # The resume guarantee: subtrees already in the DB are skipped on a
        # re-run, so only the ones a prior (slow) run never finished are
        # attempted. Here run 1 completes everything, so run 2 must do zero
        # subtree calls.
        from tool.agents import ScannerAgent
        from tool.design_critic import DesignCriticAgent
        from tool.db import DBManager
        from tool.source_io import SourceReader

        db = DBManager(tmp_path / "t.db")
        db.ensure_tables()
        reader = SourceReader("test_src", db=db)

        class CountingLLM:
            def __init__(self):
                self.subtree_calls = 0

            def generate(self, prompt, system_prompt="", tag=""):
                if tag.startswith("critic_subtree"):
                    self.subtree_calls += 1
                return '{"essence": "x", "pains": []}'

        ScannerAgent(llm=CountingLLM(), db=db, reader=reader).run("test_src")

        run1 = CountingLLM()
        assert DesignCriticAgent(llm=run1, db=db, reader=reader).run() is True
        assert run1.subtree_calls >= 2          # did the work the first time

        run2 = CountingLLM()
        DesignCriticAgent(llm=run2, db=db, reader=reader).run()
        assert run2.subtree_calls == 0          # every subtree resumed, none redone

    def test_concurrent_fanout_is_order_independent(self, tmp_path):
        # With workers > 1 the subtree calls race; the persisted result set
        # must be exactly the roots regardless of completion order.
        from tool.agents import ScannerAgent
        from tool.design_critic import DesignCriticAgent
        from tool.db import DBManager
        from tool.source_io import SourceReader
        import random
        import time

        db = DBManager(tmp_path / "t.db")
        db.ensure_tables()
        reader = SourceReader("test_src", db=db)

        class JitterLLM:
            def generate(self, prompt, system_prompt="", tag=""):
                if tag.startswith("critic_subtree"):
                    time.sleep(random.uniform(0, 0.02))   # scramble order
                return '{"essence": "x", "pains": []}'

        ScannerAgent(llm=JitterLLM(), db=db, reader=reader).run("test_src")
        assert DesignCriticAgent(llm=JitterLLM(), db=db, reader=reader).run() is True
        rows = db.get_design_subtrees()
        labels = [r["subtree_root"] for r in rows]
        assert len(labels) == len(set(labels))            # no dupes from the race
        assert all(r["parsed_json"] for r in rows)        # all persisted intact


class TestSafeParseJson:
    def test_fenced_json(self):
        assert _safe_parse_json('```json\n{"a": 1}\n```') == {"a": 1}

    def test_prose_wrapped_json(self):
        assert _safe_parse_json('Sure! {"a": {"b": 2}} hope that helps') \
            == {"a": {"b": 2}}

    def test_garbage_returns_none(self):
        assert _safe_parse_json("no json here") is None
        assert _safe_parse_json("") is None


class TestSkillAnchoring:
    def test_skill_dir_is_project_anchored_not_cwd(self, tmp_path, monkeypatch):
        # Launching from an arbitrary CWD (as an MCP host does) must not
        # change where skills are looked up.
        from tool.config import SKILLS_DIR
        assert SKILLS_DIR.is_absolute()
        monkeypatch.chdir(tmp_path)          # hostile CWD
        assert _load_user_override() is None or True   # must not raise
        from tool.tools import _load_arch_skill
        assert _load_arch_skill() is None    # .example only → still None


class TestPipelineFromGuard:
    def test_invalid_from_step_aborts(self, tmp_path, monkeypatch):
        import tool.pipeline as P
        monkeypatch.setattr(P, "DB_PATH", tmp_path / "p.db", raising=False)
        p = Pipeline()
        p.db.db_path = tmp_path / "p.db"     # isolate state
        p.db.ensure_tables()
        assert p.run_full_analysis("test_src", from_step="design") is False
