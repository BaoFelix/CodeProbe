#!/usr/bin/env python3
"""
phase5_orchestrator.py — does the dependency graph alone identify
orchestrators correctly? (Phase 5a-1: graph + scoring, no dominator
tree yet.)

Expectation for test_src/:
  - Vehicle holds Engine + FuelTank + Dashboard → should top the score
  - Workshop holds Engine + FuelTank + ToolSet + implements ILogger → high
  - Engine / FuelTank / Dashboard are leaf utilities → low score, util-flagged
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tool.ts_parser import parse_project
from tool.workflow import build_graph, score_nodes, classify_utility

ROOT = "test_src"


def main():
    entities, rels, stats = parse_project(ROOT)
    g = build_graph(entities, rels)

    print(f"── graph: {g.number_of_nodes()} nodes, {g.number_of_edges()} edges ──\n")
    print("── edges (A → B  [kinds, maxLv, weight]) ──")
    for u, v, d in sorted(g.edges(data=True)):
        kinds = ','.join(sorted(d['kinds']))
        print(f"  {u:28s} → {v:22s} [{kinds}; Lv{d['max_level']}; w={d['weight']}]")

    scores = score_nodes(g)
    print("\n── orchestrator scoring (high score = more orchestrator-like) ──")
    print(f"  {'class':28s} {'out':>6s} {'in':>6s} {'reach':>6s} {'score':>7s}  flag")
    for n, s in sorted(scores.items(), key=lambda kv: kv[1]['score'], reverse=True):
        flag = 'UTIL' if classify_utility(g, n) else ''
        print(f"  {n:28s} {s['out']:6.2f} {s['in']:6.2f} {s['reach']:6d} {s['score']:7.2f}  {flag}")

    print("\n── verdict ──")
    ranked = sorted(scores.items(), key=lambda kv: kv[1]['score'], reverse=True)
    top = ranked[0][0]
    print(f"  top orchestrator candidate: {top}")
    utils = [n for n in g.nodes if classify_utility(g, n)]
    print(f"  utilities routed aside: {utils}")


if __name__ == "__main__":
    main()
