#!/usr/bin/env python3
"""
phase4_project.py — parse the whole test_src/ tree and verify that
cross-file targets get resolved.

Before: Workshop's edges to Engine/FuelTank all show
        target_qname = None (because we only saw Workshop.hxx).
After:  the project-wide pass should fill them in.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tool.ts_parser import parse_project

ROOT = "test_src"


def main():
    entities, rels, stats = parse_project(ROOT)

    print(f"── project scan stats ──")
    for k, v in stats.items():
        print(f"  {k:24s}: {v}")

    print(f"\n── all entities (class/struct/interface only) ──")
    for e in sorted(entities, key=lambda x: x.qualified_name):
        if e.kind in ('class', 'struct', 'interface'):
            print(f"  {e.kind:10s} {e.qualified_name:35s} {e.file_path}")

    print(f"\n── Workshop's relationships (after resolve) ──")
    for r in rels:
        if r.source_qname != 'Garage::Workshop':
            continue
        resolved = r.target_qname or '(still external)'
        mark = '✓' if r.target_qname else '·'
        print(f"  {mark} Lv-{r.level} {r.kind:11s} → {r.target_name:15s} resolved: {resolved}")

    # ── checks ──
    print(f"\n── verify Workshop→Engine edges all resolved ──")
    engine_edges = [r for r in rels
                    if r.source_qname == 'Garage::Workshop' and r.target_name == 'Engine']
    unresolved = [r for r in engine_edges if r.target_qname is None]
    if not unresolved:
        print(f"  ✓ all {len(engine_edges)} Workshop→Engine edges resolved to {engine_edges[0].target_qname}")
    else:
        print(f"  ✗ {len(unresolved)}/{len(engine_edges)} still unresolved")

    # Vehicle from Vehicle.hxx should also have edges to Engine etc.
    print(f"\n── Vehicle's relationships ──")
    vehicle_rels = [r for r in rels if r.source_qname == 'Vehicle']
    if not vehicle_rels:
        print(f"  (Vehicle has no extracted relationships)")
    for r in vehicle_rels:
        resolved = r.target_qname or '(external)'
        print(f"  Lv-{r.level} {r.kind:11s} → {r.target_name:15s} {resolved}")


if __name__ == "__main__":
    main()
