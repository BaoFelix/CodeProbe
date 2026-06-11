#!/usr/bin/env python3
"""
phase1_smoke.py — smoke test the new entity/relationship schema + API.

NOTE: parsing isn't wired up yet (that's Phase 2). Here we hand-build
the Entity/Relationship objects we *expect* tree-sitter to produce for
test_src/Workshop.hxx, persist them, and read them back. The point is
to prove the data model + DB layer hold together end-to-end before we
plug in the parser.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tool.db import DBManager
from tool.model import Entity, Relationship


DB_PATH = "outputs/phase1_smoke.db"
FILE = "test_src/Workshop.hxx"

# ── what we expect the parser to extract (hand-built for now) ────
expected_entities = [
    Entity('namespace', 'Garage', 'Garage', FILE, 13, 56),

    # interface (ILogger has a pure-virtual method → marked as interface)
    Entity('interface', 'ILogger', 'Garage::ILogger', FILE, 15, 19,
           parent_qname='Garage', attrs={'is_pure_virtual': True}),
    Entity('method', 'log', 'Garage::ILogger::log', FILE, 18, 18,
           parent_qname='Garage::ILogger',
           signature='virtual void log(const char* msg) = 0',
           attrs={'is_virtual': True, 'is_pure': True}),

    Entity('struct', 'ToolSet', 'Garage::ToolSet', FILE, 21, 24,
           parent_qname='Garage'),
    Entity('field', 'wrenchCount', 'Garage::ToolSet::wrenchCount', FILE, 22, 22,
           parent_qname='Garage::ToolSet', signature='int'),
    Entity('field', 'screwdriverCount', 'Garage::ToolSet::screwdriverCount', FILE, 23, 23,
           parent_qname='Garage::ToolSet', signature='int'),

    Entity('class', 'Workshop', 'Garage::Workshop', FILE, 26, 47,
           parent_qname='Garage'),
    Entity('method', 'Open', 'Garage::Workshop::Open', FILE, 29, 29,
           parent_qname='Garage::Workshop', signature='void Open()'),
    Entity('method', 'Repair', 'Garage::Workshop::Repair', FILE, 31, 31,
           parent_qname='Garage::Workshop', signature='void Repair(Engine& e)'),
    Entity('class', 'Receipt', 'Garage::Workshop::Receipt', FILE, 34, 38,
           parent_qname='Garage::Workshop'),  # inner class

    Entity('field', 'm_primaryEngine', 'Garage::Workshop::m_primaryEngine', FILE, 41, 41,
           parent_qname='Garage::Workshop', signature='std::unique_ptr<Engine>'),
    Entity('field', 'm_loaners', 'Garage::Workshop::m_loaners', FILE, 43, 43,
           parent_qname='Garage::Workshop', signature='std::vector<Engine*>'),
    Entity('field', 'm_borrowedEngine', 'Garage::Workshop::m_borrowedEngine', FILE, 44, 44,
           parent_qname='Garage::Workshop', signature='Engine*'),
]

expected_rels = [
    # Workshop : public ILogger → implements (I-prefixed pure-virtual interface)
    Relationship('Garage::Workshop', 'ILogger', 'implements',
                 FILE, 26, 'class Workshop : public ILogger {',
                 target_qname='Garage::ILogger'),
    # composes / aggregates / associates — from field types
    Relationship('Garage::Workshop', 'Engine', 'composes',
                 FILE, 41, 'std::unique_ptr<Engine>     m_primaryEngine;',
                 attrs={'via': 'unique_ptr'}),
    Relationship('Garage::Workshop', 'FuelTank', 'composes',
                 FILE, 42, 'FuelTank                    m_spareTank;',
                 attrs={'via': 'value'}),
    Relationship('Garage::Workshop', 'Engine', 'aggregates',
                 FILE, 43, 'std::vector<Engine*>        m_loaners;',
                 attrs={'via': 'vector_of_ptr'}),
    Relationship('Garage::Workshop', 'Engine', 'associates',
                 FILE, 44, 'Engine*                     m_borrowedEngine;',
                 attrs={'via': 'raw_ptr'}),
    # depends — from #include
    Relationship('Garage::Workshop', 'Engine',  'depends',
                 FILE, 2, '#include "Engine.hxx"'),
    Relationship('Garage::Workshop', 'FuelTank','depends',
                 FILE, 3, '#include "FuelTank.hxx"'),
]


def main():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)

    db = DBManager(DB_PATH)
    db.ensure_tables()
    db.clear_graph()

    n_e = db.save_entities(expected_entities)
    n_r = db.save_relationships(expected_rels)
    print(f"wrote {n_e} entities, {n_r} relationships\n")

    # ── read back & verify ────────────────────────────────
    print("─── entities by kind ───")
    for kind in ['namespace', 'class', 'struct', 'interface', 'method', 'field']:
        rows = db.get_entities(kind=kind)
        names = [r['qualified_name'] for r in rows]
        print(f"  {kind:10s} ({len(rows)}): {names}")

    print("\n─── Workshop's relationships ───")
    for r in db.get_relationships(source_qname='Garage::Workshop'):
        target = r['target_qname'] or f"(external: {r['target_name']})"
        print(f"  Lv-{r['level']} {r['kind']:11s} → {target:30s}  @{r['evidence_file']}:{r['evidence_line']}")

    print("\n─── what `Workshop` composes/aggregates/associates with Engine? ───")
    rels = db.get_relationships(source_qname='Garage::Workshop')
    engine_rels = [r for r in rels if r['target_name'] == 'Engine']
    for r in engine_rels:
        print(f"  {r['kind']:11s} (Lv-{r['level']})  evidence: {r['evidence_text'].strip()}")
    print(f"  → same pair (Workshop, Engine) has {len(engine_rels)} edges of different kinds ✓")

    print("\n─── inner class linkage ───")
    inners = db.get_entities(parent_qname='Garage::Workshop')
    print(f"  children of Garage::Workshop: {[r['qualified_name'] + ' (' + r['kind'] + ')' for r in inners]}")


if __name__ == "__main__":
    main()
