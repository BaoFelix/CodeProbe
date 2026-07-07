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


class TestCrtpScoring:
    """CRTP inverts the orchestrator signature — the core is the base
    class everyone builds on. The CRTP model must surface it; the OOP
    model must not."""

    def _base_heavy_graph(self):
        g = nx.DiGraph()
        for w in ("W1", "W2", "W3", "W4", "W5"):
            g.add_edge(w, "Base", weight=1.0)      # widgets inherit Base
        g.add_edge("Coord", "W1", weight=1.0)
        g.add_edge("Coord", "W2", weight=1.0)      # a coordinator (OOP-core)
        return g

    def test_crtp_model_surfaces_the_base(self):
        g = self._base_heavy_graph()
        crtp = score_nodes(g, style="crtp")
        top = max(crtp, key=lambda n: crtp[n]["score"])
        assert top == "Base"
        # reverse-reach = how many build on it (all 5 widgets + coord)
        assert crtp["Base"]["reach"] == 6

    def test_oop_model_does_not_pick_the_base(self):
        g = self._base_heavy_graph()
        oop = score_nodes(g, style="oop")
        top = max(oop, key=lambda n: oop[n]["score"])
        assert top != "Base"                       # OOP would sink the base

    def test_default_style_is_oop(self):
        g = self._base_heavy_graph()
        assert score_nodes(g) == score_nodes(g, style="oop")

    def test_crtp_end_to_end_through_scanner(self, tmp_path):
        # Synthetic template library big enough to trip detect_style
        # (>=50 classes, >=20 inherits, <2% abstract): the full
        # ScannerAgent path must detect 'crtp', switch scoring, and
        # persist the BASE as the orchestrator — not a leaf.
        from tool.agents import ScannerAgent
        from tool.db import DBManager
        from tool.llm import LLMClient
        from tool.source_io import SourceReader
        src = tmp_path / "lib"
        src.mkdir()
        body = "class Base { public: void Tick(); };\n" + "\n".join(
            f"class D{i} : public Base {{ public: void Run{i}(); }};"
            for i in range(59))
        (src / "all.hxx").write_text(body)
        db = DBManager(tmp_path / "t.db")
        db.ensure_tables()
        ScannerAgent(llm=LLMClient(cache=db), db=db,
                     reader=SourceReader(str(src), db=db)).run(str(src))
        mi = db.get_module_info()
        assert mi["style"] == "crtp", mi["style"]
        assert mi["orchestrator"] == "Base", mi["orchestrator"]


class TestCondenseLabels:
    def test_labels_are_unique_even_on_collision(self):
        # Two separate 2-cycles whose members have the SAME short names
        # across namespaces would both render 'cluster(A, B)'. The labels
        # are the resume key, so they MUST stay distinct.
        from tool.workflow import build_graph, condense
        from tool.model import Entity, Relationship

        def ent(q):
            return Entity(kind="class", name=q.split("::")[-1],
                          qualified_name=q, file_path="x.hxx",
                          start_line=1, end_line=2)

        def rel(s, t):
            return Relationship(source_qname=s, target_name=t.split("::")[-1],
                                kind="depends", evidence_file="x", evidence_line=1,
                                target_qname=t)

        ents = [ent(q) for q in ("N1::A", "N1::B", "N2::A", "N2::B")]
        rels = [rel("N1::A", "N1::B"), rel("N1::B", "N1::A"),
                rel("N2::A", "N2::B"), rel("N2::B", "N2::A")]
        _, label = condense(build_graph(ents, rels))
        labels = list(label.values())
        assert len(labels) == len(set(labels)), labels   # no collision


class TestClassifyUtility:
    def test_sink_with_fan_in_is_utility(self):
        g = nx.DiGraph([("a", "u"), ("b", "u")])
        assert classify_utility(g, "u")
        assert not classify_utility(g, "a")

    def test_node_with_outgoing_edge_is_not_utility(self):
        g = nx.DiGraph([("a", "u"), ("b", "u"), ("u", "x")])
        assert not classify_utility(g, "u")
