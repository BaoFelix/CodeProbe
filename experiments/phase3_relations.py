#!/usr/bin/env python3
"""
phase3_relations.py — verify relationship extraction.

Phase 3a scope: inheritance only.
Workshop.hxx has one inheritance:
    class Workshop : public ILogger
ILogger is I-prefixed → should be detected as `implements` (Lv-2),
not `inherits` (Lv-5).
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tool.ts_parser import parse_file

FILE = "test_src/Workshop.hxx"


def main():
    entities, relationships = parse_file(FILE)

    print(f"── {len(relationships)} relationships extracted ──\n")
    print(f"{'kind':12s} {'source':32s} {'→ target':25s} {'(qname?)':25s} line  evidence")
    print("-" * 130)
    for r in relationships:
        resolved = r.target_qname if r.target_qname else '(unresolved)'
        print(f"{r.kind:12s} Lv-{r.level} {r.source_qname:28s} → {r.target_name:23s} {resolved:25s} {r.evidence_line:4d}  {r.evidence_text[:50]}")

    print("\n── coverage check ──")
    got = {(r.source_qname, r.kind, r.target_name) for r in relationships}
    expected = {
        ('Garage::Workshop', 'implements', 'ILogger'),
    }
    missing = expected - got
    extra = got - expected
    if not missing and not extra:
        print("  ✓ exact match")
    else:
        if missing: print(f"  ✗ missing: {missing}")
        if extra:   print(f"  ⚠ extra:   {extra}")

    # specific check: ILogger should resolve as same-file target
    impl = next((r for r in relationships if r.kind == 'implements'), None)
    if impl and impl.target_qname == 'Garage::ILogger':
        print("  ✓ same-file target resolved: ILogger → Garage::ILogger")
    else:
        print(f"  ✗ target_qname not resolved (got {impl.target_qname if impl else 'no impl rel'})")


if __name__ == "__main__":
    main()
