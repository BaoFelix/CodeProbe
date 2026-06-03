#!/usr/bin/env python3
"""
phase3_relations.py — relationship extraction tests.

Phase 3a: inheritance / interface implementation.
Phase 3b: field-based composes / aggregates / associates.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tool.ts_parser import parse_file, classify_field_type

FILE = "test_src/Workshop.hxx"


def show_unit_tests():
    """Quick sanity check on the type classifier."""
    print("── classify_field_type unit checks ──")
    cases = [
        ('std::unique_ptr<Engine>',  ('composes',   'Engine')),
        ('FuelTank',                 ('composes',   'FuelTank')),
        ('std::vector<Engine*>',     ('aggregates', 'Engine')),
        ('Engine*',                  ('associates', 'Engine')),
        ('std::shared_ptr<Logger>',  ('associates', 'Logger')),
        ('ToolSet',                  ('composes',   'ToolSet')),
        ('int',                      None),
        ('char*',                    None),
        ('double',                   None),
    ]
    bad = 0
    for type_str, expected in cases:
        got = classify_field_type(type_str)
        mark = '✓' if got == expected else '✗'
        if got != expected: bad += 1
        print(f"  {mark} {type_str:30s} → {got}")
    print(f"  ({len(cases) - bad}/{len(cases)} passed)\n")


def main():
    show_unit_tests()
    entities, relationships = parse_file(FILE)

    print(f"── {len(relationships)} relationships extracted from {FILE} ──\n")
    print(f"{'kind':12s} Lv  {'source':28s} → {'target':18s} {'qname?':25s} line")
    print("-" * 110)
    for r in sorted(relationships, key=lambda x: (x.evidence_line, x.kind)):
        resolved = r.target_qname or '(unresolved → external)'
        print(f"{r.kind:12s} {r.level}   {r.source_qname:28s} → {r.target_name:18s} {resolved:25s} {r.evidence_line}")

    # ── Coverage check ─────────────────────────────────────
    print("\n── coverage check ──")
    got = {(r.source_qname, r.kind, r.target_name) for r in relationships}
    expected = {
        # Phase 3a
        ('Garage::Workshop', 'implements', 'ILogger'),
        # Phase 3b: Workshop fields
        ('Garage::Workshop', 'composes',   'Engine'),     # unique_ptr<Engine>
        ('Garage::Workshop', 'composes',   'FuelTank'),   # value type
        ('Garage::Workshop', 'aggregates', 'Engine'),     # vector<Engine*>
        ('Garage::Workshop', 'associates', 'Engine'),     # Engine*
        ('Garage::Workshop', 'composes',   'ToolSet'),    # value type, same-file
    }
    missing = expected - got
    extra = got - expected
    if not missing and not extra:
        print("  ✓ exact match")
    else:
        if missing: print(f"  ✗ missing ({len(missing)}): {sorted(missing)}")
        if extra:   print(f"  ⚠ extra   ({len(extra)}): {sorted(extra)}")

    # Show the key insight: (Workshop, Engine) has THREE different edges
    engine_edges = [r for r in relationships
                    if r.source_qname == 'Garage::Workshop' and r.target_name == 'Engine']
    print(f"\n  (Workshop → Engine) has {len(engine_edges)} edges of different kinds:")
    for r in engine_edges:
        print(f"    Lv-{r.level} {r.kind:11s} via {r.attrs.get('via_field', '-')}  ({r.attrs.get('type_text', '')})")


if __name__ == "__main__":
    main()
