"""
workflow.py — orchestrator detection & responsibility hierarchy

Built on the class-level dependency graph (entities + relationships from
ts_parser). This is the ATTRIBUTION view: "which classes own which
subsystems", computed with a dominator tree. It is NOT an execution-flow
view — we don't track call order.

Pipeline:
    build_graph → condense SCCs → score nodes → pick root(s)
    → dominator tree → route utilities → truncate by depth

Everything rides on networkx primitives; we supply the judgement
(weights, signatures, thresholds), not the algorithms.
"""
import networkx as nx

from .model import LEVEL_OF


# Edge weights by relationship kind. Stronger structural ties count
# more toward "this class coordinates that one". Tunable — this is the
# judgement layer, the part that isn't handed to a library.
_KIND_WEIGHT = {
    'inherits':   1.0,
    'composes':   1.0,
    'aggregates': 0.8,
    'implements': 0.7,
    'associates': 0.6,
    'depends':    0.3,
}

# Entity kinds that can be graph nodes (relationships point at these).
_NODE_KINDS = ('class', 'struct', 'interface')


def build_graph(entities, relationships):
    """Build a directed graph of internal classes.

    Node  = a class/struct/interface, keyed by qualified_name.
    Edge  = A → B means "A uses/coordinates B". Multiple relationships
            between the same pair collapse into one edge whose weight is
            the max kind weight, and which remembers every kind+level
            seen (so we don't lose the multi-edge evidence).

    Only edges whose target was resolved to an internal entity are kept;
    external targets (std::, unresolved) are dropped — they're not part
    of this module's structure.
    """
    g = nx.DiGraph()

    # Nodes first.
    qname_to_entity = {}
    for e in entities:
        if e.kind in _NODE_KINDS:
            g.add_node(e.qualified_name, kind=e.kind, name=e.name,
                       file=e.file_path)
            qname_to_entity[e.qualified_name] = e

    # Edges, collapsed.
    for r in relationships:
        src = r.source_qname
        tgt = r.target_qname
        if tgt is None:              # external / unresolved → skip
            continue
        if src not in g or tgt not in g:
            continue                 # endpoint isn't an internal class
        if src == tgt:
            continue                 # ignore self-loops
        w = _KIND_WEIGHT.get(r.kind, 0.3)
        if g.has_edge(src, tgt):
            data = g[src][tgt]
            data['weight'] = max(data['weight'], w)
            data['kinds'].add(r.kind)
            data['max_level'] = max(data['max_level'], LEVEL_OF[r.kind])
        else:
            g.add_edge(src, tgt, weight=w, kinds={r.kind},
                       max_level=LEVEL_OF[r.kind])

    return g


def score_nodes(g):
    """Score every node for its 'orchestrator-ness'.

    Orchestrator signature: high weighted out-degree (coordinates many),
    low in-degree (rarely depended upon), large reachable set (owns a lot
    of downstream work).

    Returns {qname: {'out', 'in', 'reach', 'score'}}.
    """
    scores = {}
    for n in g.nodes:
        out_w = g.out_degree(n, weight='weight')
        in_w = g.in_degree(n, weight='weight')
        # reachable set: how much downstream work this node can pull in
        reach = len(nx.descendants(g, n))
        # The judgement formula: reward fan-out and reach, penalize being
        # depended upon. Coefficients are deliberately simple & tunable.
        score = out_w + 0.5 * reach - 0.8 * in_w
        scores[n] = {
            'out': round(out_w, 2),
            'in': round(in_w, 2),
            'reach': reach,
            'score': round(score, 2),
        }
    return scores


def classify_utility(g, n):
    """Tool/infrastructure signature: depended upon by many, depends on
    little, shallow reach. These get routed to a side list, not the
    main responsibility tree.
    """
    in_deg = g.in_degree(n)
    out_deg = g.out_degree(n)
    reach = len(nx.descendants(g, n))
    return in_deg >= 2 and out_deg == 0 and reach == 0
