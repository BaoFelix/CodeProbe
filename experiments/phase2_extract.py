#!/usr/bin/env python3
"""
phase2_extract.py — full entity extraction test.

Phase 2 goal: tree-sitter produces every entity our hand-built
phase1_smoke listed (namespaces, classes, structs, methods, fields).
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tool.ts_parser import parse_file

FILE = "test_src/Workshop.hxx"


def main():
    entities, _ = parse_file(FILE)   # Phase 2 only cares about entities
    print(f"── tree-sitter found {len(entities)} entities ──\n")
    print(f"{'kind':10s} {'qualified_name':40s} parent / signature")
    print("-" * 100)
    for e in sorted(entities, key=lambda x: (x.start_line, x.qualified_name)):
        info = e.signature[:50] if e.signature else (e.parent_qname or '(top)')
        print(f"{e.kind:10s} {e.qualified_name:40s} {info}")

    # ── coverage check vs phase 1 hand-built set ──────────
    print("\n── coverage check ──")
    expected = {
        ('namespace', 'Garage'),
        ('interface', 'Garage::ILogger'),         # pure interface: all methods pure-virtual, no fields
        ('struct',    'Garage::ToolSet'),
        ('class',     'Garage::Workshop'),
        ('class',     'Garage::Workshop::Receipt'),
        ('method',    'Garage::ILogger::log'),
        ('method',    'Garage::ILogger::~ILogger'),
        ('method',    'Garage::Workshop::Workshop'),
        ('method',    'Garage::Workshop::Open'),
        ('method',    'Garage::Workshop::Close'),
        ('method',    'Garage::Workshop::Repair'),
        ('method',    'Garage::Workshop::log'),
        ('field',     'Garage::ToolSet::wrenchCount'),
        ('field',     'Garage::ToolSet::screwdriverCount'),
        ('field',     'Garage::Workshop::m_primaryEngine'),
        ('field',     'Garage::Workshop::m_spareTank'),
        ('field',     'Garage::Workshop::m_loaners'),
        ('field',     'Garage::Workshop::m_borrowedEngine'),
        ('field',     'Garage::Workshop::m_tools'),
        ('field',     'Garage::Workshop::Receipt::total'),
        ('field',     'Garage::Workshop::Receipt::customerName'),
    }
    got = {(e.kind, e.qualified_name) for e in entities}
    missing = expected - got
    extra = got - expected
    if not missing and not extra:
        print("  ✓ exact match")
    else:
        if missing: print(f"  ✗ missing ({len(missing)}): {sorted(missing)}")
        if extra:   print(f"  ⚠ extra   ({len(extra)}): {sorted(extra)}")


if __name__ == "__main__":
    main()
