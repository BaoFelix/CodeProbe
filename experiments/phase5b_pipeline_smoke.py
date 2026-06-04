#!/usr/bin/env python3
"""
phase5b_pipeline_smoke.py — verify the new tree-sitter engine is wired
into CodeProbe's real pipeline.

Checks:
  1. ScannerAgent runs end-to-end without LLM.
  2. NEW tables (entities, relationships) are populated.
  3. LEGACY tables (classes, dependencies, module_info) are also
     populated — so ResponsibilityAgent and report keep working until
     Phase 7 drops them.
  4. The orchestrator picked by the new graph scoring matches what we
     observed in Phase 5 standalone tests.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tool.db import DBManager
from tool.source_io import SourceReader
from tool.agents import ScannerAgent


DB = "outputs/phase5b_smoke.db"


def main():
    os.makedirs("outputs", exist_ok=True)
    if os.path.exists(DB):
        os.remove(DB)
    db = DBManager(DB)
    db.ensure_tables()

    agent = ScannerAgent(llm=None, db=db, reader=SourceReader("test_src", db=db), prompts=None)
    n = agent.run("test_src")
    assert n > 0, "ScannerAgent returned 0 classes"

    # ── new tables filled? ───────────────────────────────
    entities = db.get_entities()
    rels = db.get_relationships()
    assert len(entities) >= 20, f"entities table only has {len(entities)}"
    assert len(rels) >= 5, f"relationships table only has {len(rels)}"
    print(f"  ✓ new tables: {len(entities)} entities, {len(rels)} relationships")

    # ── legacy tables filled? ────────────────────────────
    classes = db.get_all_tasks()
    deps = db.get_dependencies()
    assert len(classes) >= 5, f"classes table only has {len(classes)}"
    assert len(deps) >= 5, f"dependencies table only has {len(deps)}"
    print(f"  ✓ legacy tables: {len(classes)} classes, {len(deps)} dependencies")

    # ── orchestrator wired through to module_info? ───────
    mi = db.get_module_info()
    assert mi and mi['orchestrator'] == 'Garage::Workshop', \
        f"expected Workshop orchestrator, got {mi['orchestrator'] if mi else None}"
    print(f"  ✓ orchestrator picked: {mi['orchestrator']}")

    # ── qualified names preserved (Outer::Inner ≠ Outer) ─
    names = {c['class_name'] for c in classes}
    assert 'Outer::Inner' in names, "inner class qualified_name lost"
    print(f"  ✓ qualified names preserved (Outer::Inner present)")

    # ── ResponsibilityAgent could read its inputs? ───────
    wks_deps = db.get_dependencies('Garage::Workshop')
    targets = {d['target_class'] for d in wks_deps}
    expected = {'Engine', 'FuelTank', 'Garage::ToolSet', 'Garage::ILogger'}
    missing = expected - targets
    assert not missing, f"Workshop missing deps: {missing}"
    print(f"  ✓ Workshop's deps reachable via legacy API: {sorted(targets)}")

    print("\nPhase 5b smoke: PASS — engine successfully wired into pipeline.")


if __name__ == "__main__":
    main()
