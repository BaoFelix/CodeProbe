"""
Architecture-level LLM review tests (both tiers), offline with fakes.

Tier 1: grounded, concurrent, per-module, resumable.
Tier 2: accumulative synthesis over the Tier-1 results, reusing the DB.
"""
from pathlib import Path

from tool.db import DBManager, graph_fingerprint
from tool.source_io import SourceReader
from tool.agents import ScannerAgent
from tool.architect import (ArchitectReviewer, synthesize_conclusion,
                            run_architecture_audit, plan_decoupling,
                            audit_payload)


class FakeLLM:
    api_key = "k"
    api_url = "u"

    def __init__(self):
        self.module_calls = 0

    def generate(self, prompt, system_prompt="", tag=""):
        if tag.startswith("arch_module"):
            self.module_calls += 1
            # the prompt must be GROUNDED — the module's findings appear
            assert "Deterministic findings" in prompt
            return ('{"role":"r","assessment":"a","risks":["x"],'
                    '"recommendation":"fix"}')
        if tag == "arch_synth_final":
            return ('{"summary":"cycle between foo and bar",'
                    '"priorities":[{"title":"break foo-bar","why":"w",'
                    '"modules":["foo","bar"]}]}')
        if tag.startswith("arch_synth"):
            return '{"running":"running conclusion","note":"n"}'
        return "{}"


def _audited_db(tmp_path):
    root = tmp_path / "src"
    for m in ("foo", "bar", "baz"):
        (root / m).mkdir(parents=True)
    (root / "foo" / "A.hxx").write_text("class B;\nclass A { B* b; };\n")
    (root / "bar" / "B.hxx").write_text("class A;\nclass B { A* a; };\n")
    (root / "baz" / "C.hxx").write_text("class A;\nclass C { A* a; };\n")
    db = DBManager(tmp_path / "t.db"); db.ensure_tables()
    ScannerAgent(llm=FakeLLM(), db=db,
                 reader=SourceReader(str(root), db=db)).run(str(root))
    cl = [dict(r) for r in db.get_classes()]
    rl = [dict(r) for r in db.get_relationships()]
    f, mg = run_architecture_audit(cl, rl)
    db.save_arch_audit(audit_payload(f, mg, plan_decoupling(mg)),
                       graph_hash=graph_fingerprint(rl))
    return db


class TestTier1:
    def test_reviews_every_module_grounded(self, tmp_path):
        db = _audited_db(tmp_path)
        llm = FakeLLM()
        assert ArchitectReviewer(llm=llm, db=db).run() is True
        reviews = {r["module_name"] for r in db.get_arch_module_reviews()}
        assert reviews == {"foo", "bar", "baz"}
        assert llm.module_calls == 3

    def test_resume_skips_completed_modules(self, tmp_path):
        db = _audited_db(tmp_path)
        ArchitectReviewer(llm=FakeLLM(), db=db).run()
        llm2 = FakeLLM()
        ArchitectReviewer(llm=llm2, db=db).run()
        assert llm2.module_calls == 0            # all resumed

    def test_no_audit_returns_false(self, tmp_path):
        db = DBManager(tmp_path / "e.db"); db.ensure_tables()
        assert ArchitectReviewer(llm=FakeLLM(), db=db).run() is False


class TestTier2:
    def test_synthesis_reuses_tier1_and_persists(self, tmp_path):
        db = _audited_db(tmp_path)
        ArchitectReviewer(llm=FakeLLM(), db=db).run()
        result = synthesize_conclusion(FakeLLM(), db)
        assert "cycle" in result["summary"]
        assert result["priorities"][0]["title"] == "break foo-bar"
        # persisted under the 'architecture' key, distinct from class review
        assert db.get_design_module("architecture") is not None

    def test_synthesis_needs_tier1_first(self, tmp_path):
        db = _audited_db(tmp_path)          # audit but no Tier-1 reviews
        assert synthesize_conclusion(FakeLLM(), db) is None
