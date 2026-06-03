#!/usr/bin/env python3
"""
phase2_extract.py — run the real tree-sitter parser on Workshop.hxx
and see whether it produces the namespaces / classes / structs we
hand-wrote in phase1_smoke.

This is the "looking the parser in the eye" step. If the lists don't
match, the parser is wrong and we fix it before adding methods/fields.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tool.ts_parser import parse_file

FILE = "test_src/Workshop.hxx"

# What we said in Phase 1 the parser should find for this file.
# Only the "container" kinds — no methods/fields yet.
expected = [
    ('namespace', 'Garage'),
    ('interface', 'Garage::ILogger'),               # we'll detect "interface" later
    ('struct',    'Garage::ToolSet'),
    ('class',     'Garage::Workshop'),
    ('class',     'Garage::Workshop::Receipt'),
]

def main():
    entities = parse_file(FILE)

    print(f"── tree-sitter found {len(entities)} entities ──\n")
    print(f"{'kind':10s} {'qualified_name':35s} {'parent_qname':25s} lines")
    print("-" * 90)
    for e in entities:
        parent = e.parent_qname or '(top)'
        print(f"{e.kind:10s} {e.qualified_name:35s} {parent:25s} {e.start_line}-{e.end_line}")

    print("\n── compare against expected ──")
    got = {(e.kind, e.qualified_name) for e in entities}
    # We don't expect 'interface' yet — that's a Phase 2b refinement.
    # For now treat ILogger as a class.
    expected_phase2a = {
        ('namespace', 'Garage'),
        ('class',     'Garage::ILogger'),
        ('struct',    'Garage::ToolSet'),
        ('class',     'Garage::Workshop'),
        ('class',     'Garage::Workshop::Receipt'),
    }
    missing = expected_phase2a - got
    extra   = got - expected_phase2a
    if not missing and not extra:
        print("  ✓ exact match")
    else:
        if missing: print(f"  ✗ missing: {missing}")
        if extra:   print(f"  ⚠ extra:   {extra}")


if __name__ == "__main__":
    main()
