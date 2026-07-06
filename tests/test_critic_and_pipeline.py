"""
Tests for the design-critic subtree assembly and the pipeline guard —
both found broken/untested by the debt sweep.
"""
import networkx as nx

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
