"""
Graph-algorithm tests — the one-pass reach computation must be EXACTLY
equivalent to per-node nx.descendants (it replaced an O(V·(V+E)) hotspot;
an optimization that changes answers is a bug, not an optimization).
"""
import random

import networkx as nx

from tool.workflow import _reach_counts, score_nodes, classify_utility


def _random_digraph(n, p, seed):
    rng = random.Random(seed)
    g = nx.DiGraph()
    g.add_nodes_from(range(n))
    for u in range(n):
        for v in range(n):
            if u != v and rng.random() < p:
                g.add_edge(u, v, weight=1.0)
    return g


class TestReachCounts:
    def test_matches_nx_descendants_on_random_graphs(self):
        # DAGs, dense graphs, and cyclic graphs — including SCCs, where
        # every member must count its co-members as reachable.
        for seed, p in [(1, 0.05), (2, 0.15), (3, 0.4), (4, 0.02)]:
            g = _random_digraph(60, p, seed)
            fast = _reach_counts(g)
            for n in g.nodes:
                assert fast[n] == len(nx.descendants(g, n)), (seed, n)

    def test_two_node_cycle(self):
        g = nx.DiGraph([("a", "b"), ("b", "a")])
        assert _reach_counts(g) == {"a": 1, "b": 1}

    def test_empty_and_singleton(self):
        assert _reach_counts(nx.DiGraph()) == {}
        g = nx.DiGraph()
        g.add_node("solo")
        assert _reach_counts(g) == {"solo": 0}

    def test_score_nodes_uses_same_reach(self):
        g = _random_digraph(30, 0.1, seed=7)
        scores = score_nodes(g)
        for n in g.nodes:
            assert scores[n]["reach"] == len(nx.descendants(g, n))


class TestClassifyUtility:
    def test_sink_with_fan_in_is_utility(self):
        g = nx.DiGraph([("a", "u"), ("b", "u")])
        assert classify_utility(g, "u")
        assert not classify_utility(g, "a")

    def test_node_with_outgoing_edge_is_not_utility(self):
        g = nx.DiGraph([("a", "u"), ("b", "u"), ("u", "x")])
        assert not classify_utility(g, "u")
