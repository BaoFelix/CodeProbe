#!/usr/bin/env python3
"""
phase5_tree.py — responsibility forest via dominator tree (Phase 5a-2).

Shows:
  1. The dependency graph condensed (SCC) → DAG.
  2. Per-root dominator trees = independent workflow stories.
  3. Depth selection (depth=1 vs full).
  4. A synthetic cycle collapsing into a cluster node.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import networkx as nx
from tool.ts_parser import parse_project
from tool.workflow import (build_graph, score_nodes, classify_utility,
                           condense, find_roots, responsibility_tree,
                           render_tree)

ROOT = "test_src"


def show_forest(g, max_depth, title):
    C, label = condense(g)
    roots = find_roots(C)
    # rank roots by their own subtree weight so the biggest story prints first
    roots = sorted(roots, key=lambda r: len(nx.descendants(C, r)), reverse=True)
    utils = [n for n in g.nodes if classify_utility(g, n)]

    print(f"\n══ {title} ══")
    for r in roots:
        # skip roots that are themselves pure utilities or isolated leaves
        if len(nx.descendants(C, r)) == 0:
            continue
        tree = responsibility_tree(C, label, r, max_depth=max_depth)
        for line in render_tree(tree):
            print("  " + line)
        print()
    print(f"  [utilities / infrastructure — shown aside]: "
          f"{[u.split('::')[-1] for u in utils]}")


def main():
    entities, rels, _ = parse_project(ROOT)
    g = build_graph(entities, rels)

    show_forest(g, max_depth=1, title="depth = 1  (top responsibilities only)")
    show_forest(g, max_depth=None, title="depth = full  (everything)")

    # ── synthetic cycle test: prove SCC collapses a knot ──
    print("\n══ SCC cycle collapse test ══")
    cg = nx.DiGraph()
    cg.add_edge('Root', 'A', weight=1, kinds={'depends'}, max_level=0)
    cg.add_edge('A', 'P', weight=1, kinds={'depends'}, max_level=0)
    cg.add_edge('P', 'Q', weight=1, kinds={'depends'}, max_level=0)
    cg.add_edge('Q', 'P', weight=1, kinds={'depends'}, max_level=0)  # P⇄Q cycle
    cg.add_edge('A', 'Util', weight=1, kinds={'depends'}, max_level=0)
    C, label = condense(cg)
    print(f"  original nodes: {sorted(cg.nodes)}")
    print(f"  condensed labels: {sorted(label.values())}")
    roots = find_roots(C)
    for r in roots:
        tree = responsibility_tree(C, label, r)
        for line in render_tree(tree):
            print("  " + line)


if __name__ == "__main__":
    main()
