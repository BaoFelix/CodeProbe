#!/usr/bin/env python3
"""
ts_demo.py — Side-by-side: regex parser vs tree-sitter, on the same file.

Run: python experiments/ts_demo.py test_src/Tricky.hxx

This is a LEARNING experiment, not production code. It demonstrates *why*
tree-sitter beats the regex heuristics currently in tool/reader.py.
"""
import sys
import re
from pathlib import Path

import tree_sitter_cpp
from tree_sitter import Language, Parser, Query, QueryCursor


# ─────────────────────────────────────────────────────────────
# Approach A: the regex heuristic (a trimmed copy of reader.py logic)
# ─────────────────────────────────────────────────────────────
def regex_extract(content):
    # strip comment lines first (reader.py does the same — a patch for the
    # "comment false-positive" problem)
    stripped = '\n'.join(
        line for line in content.split('\n')
        if not re.match(r'\s*//', line) and not re.match(r'\s*\*', line)
    )
    classes = re.findall(r'(?<!\w)class\s+(\w+)', stripped)
    noise = {'class', 'struct', 'public', 'private', 'protected'}
    classes = [c for c in classes if c not in noise and c[0].isupper() and len(c) > 2]

    # members: relies on the m_ naming convention
    members = re.findall(r'\b(m_\w+)\b', content)
    return classes, members


# ─────────────────────────────────────────────────────────────
# Approach B: tree-sitter — parse real grammar, query the tree
# ─────────────────────────────────────────────────────────────
CPP = Language(tree_sitter_cpp.language())

# S-expression query: capture every class definition's name node.
# (class_specifier name: (type_identifier) @name) means:
#   "find a class_specifier node, grab the child in its `name` field,
#    which must be a type_identifier, and label it @name"
CLASS_Q = Query(CPP, "(class_specifier name: (type_identifier) @name)")

# Capture every field (member variable) declaration's identifier.
FIELD_Q = Query(CPP, """
(field_declaration
  declarator: (field_identifier) @member)
(field_declaration
  declarator: (pointer_declarator
    declarator: (field_identifier) @member))
""")

# Capture lambdas — something regex can't even attempt.
LAMBDA_Q = Query(CPP, "(lambda_expression) @lambda")


def ts_extract(content):
    parser = Parser(CPP)
    tree = parser.parse(content.encode())
    root = tree.root_node

    def names(query, cap):
        cur = QueryCursor(query)
        caps = cur.captures(root)
        out = []
        for node in caps.get(cap, []):
            out.append((node.text.decode(), node.start_point[0] + 1))  # +1 → 1-based line
        return out

    classes = names(CLASS_Q, "name")
    members = names(FIELD_Q, "member")
    lambdas = names(LAMBDA_Q, "lambda")
    has_error = root.has_error
    return classes, members, lambdas, has_error


def main():
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "test_src/Tricky.hxx")
    content = path.read_text()

    print(f"═══ File: {path} ═══\n")

    r_classes, r_members = regex_extract(content)
    print("── REGEX approach ──")
    print(f"  classes : {r_classes}")
    print(f"  members : {r_members}")
    print(f"  lambdas : (can't detect)\n")

    t_classes, t_members, t_lambdas, err = ts_extract(content)
    print("── TREE-SITTER approach ──")
    print(f"  classes : {[c for c, _ in t_classes]}   (with lines: {t_classes})")
    print(f"  members : {[m for m, _ in t_members]}")
    print(f"  lambdas : {len(t_lambdas)} found at lines {[l for _, l in t_lambdas]}")
    print(f"  parse had syntax errors? {err}")


if __name__ == "__main__":
    main()
