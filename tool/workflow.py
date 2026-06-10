"""
workflow.py — graph analysis: orchestrator detection & the workflow tree.

WHAT THIS FILE ANSWERS
  Given the class-level dependency graph from ts_parser:
    "Which class is the coordinator (orchestrator)?"
    "Which classes are plumbing (utilities)?"
    "How do responsibilities nest?"  → the workflow tree in the report.

THE BIG IDEA: dominator tree = responsibility hierarchy
  A dominator tree answers: "to reach class B from the root, must you
  pass through class A?" If yes, B's responsibility belongs under A.
  This gives the ATTRIBUTION view ("who owns what") — NOT an
  execution-flow view (we never track call order; we don't need to).

  A nice free property: a class used by several siblings (e.g. Logger
  used by every service) automatically floats UP to their common
  dominator instead of being duplicated under each user. Nobody wrote
  a rule for that — it falls out of the math.

PIPELINE (each step is a small function below)
    build_graph         entities+relationships → weighted DiGraph
    detect_style        warn when CRTP/template-heavy code would
                        break our scoring assumptions
    fold_abstractions   collapse impl families (tcp_sink → sink)
    condense            Tarjan SCC: cycles become one "cluster" node,
                        making the graph a DAG (dominators need that)
    score_nodes         orchestrator score = out-degree + reach − in
    classify_utility    inverse signature: used-by-many, uses-nothing
    dominator_children  the tree itself (networkx immediate_dominators)

WHY NETWORKX
  Tarjan SCC, dominator trees, reachability — all textbook algorithms
  with decades of optimization. We supply only the JUDGEMENT layer:
  edge weights, score formulas, thresholds. Never re-implement
  algorithms a library already does right.
"""
from collections import Counter

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


# ── Architecture-style detection ──────────────────────────────
# Our orchestrator scoring assumes traditional OOP: orchestrators have
# high out-degree (they coordinate others) and low in-degree (they're
# not heavily depended upon). This assumption inverts on CRTP-heavy /
# template-metaprogramming codebases like Eigen: the architectural cores
# (MatrixBase, EigenBase) are BASE classes — high in-degree, low
# out-degree — so our formula scores them as utilities instead of cores.
#
# We don't try to fix scoring for both worlds at once. Instead, we
# detect the style and warn the caller when our default scoring is
# unlikely to be meaningful.

def detect_style(entities, relationships, g):
    """Return (style, note) describing how appropriate the default
    orchestrator scoring is for this codebase.

    style: 'oop' | 'crtp' | 'mixed'
    note:  a short, human-readable explanation
    """
    classes = sum(1 for e in entities
                  if e.kind in ('class', 'struct', 'interface'))
    abstractions = sum(1 for e in entities
                       if e.kind == 'interface' or e.attrs.get('abstract'))
    inherits_edges = sum(1 for _, _, d in g.edges(data=True)
                         if 'inherits' in d.get('kinds', set())
                         or 'implements' in d.get('kinds', set()))

    # Signal: lots of inheritance, but almost no virtual abstraction
    # (no interfaces, no abstract base classes). Classic CRTP fingerprint.
    if classes >= 50 and inherits_edges >= 20:
        abs_ratio = abstractions / max(classes, 1)
        if abs_ratio < 0.02:                            # <2% abstract
            return ('crtp',
                    f"CRTP / template-metaprogramming style detected "
                    f"({abstractions}/{classes} abstract, {inherits_edges} inherits). "
                    "Default orchestrator scoring assumes traditional OOP "
                    "(high out-degree = coordinator). In CRTP code the "
                    "architectural cores are base classes — high in-degree, "
                    "low out-degree — so they appear as utilities under the "
                    "default scoring. Inspect the most-inherited base "
                    "classes directly instead of the top-scored orchestrators.")
        if abs_ratio < 0.05:
            return ('mixed',
                    f"Mostly concrete inheritance ({abstractions}/{classes} "
                    "abstract). Orchestrator ranking may include template "
                    "bases that aren't coordinators in the traditional sense.")
    return ('oop', '')


# ── Abstraction folding: a base represents its implementers ───

def _parent_map(g):
    """Direct is-a parent for each class: the first inherits/implements
    target. (Multiple inheritance keeps the first base.)"""
    parent = {}
    for u, v, d in g.edges(data=True):
        if d.get('kinds', set()) & {'implements', 'inherits'}:
            parent.setdefault(u, v)
    return parent


def _representative_map(g, mode='leaves'):
    """Map each class to the node that represents it after folding.

    mode='none'   → {} : no folding, every class stays itself.
    mode='leaves' → fold only leaf classes (nothing inherits from them)
                    into their *immediate* parent, preserving the
                    intermediate taxonomy (Geom_Curve, Geom_Conic stay).
    mode='all'    → fold every class onto its top-most base (Geom_Circle
                    → Geom_Geometry). Good for shallow polymorphic code,
                    too aggressive for deep type hierarchies.
    """
    if mode == 'none':
        return {}
    parent = _parent_map(g)
    if mode == 'leaves':
        has_children = set(parent.values())
        # Only fold into a base that is a real polymorphic family
        # (≥2 subclasses). A base with a single implementer isn't a
        # family — folding would bury a substantial class (e.g. an
        # orchestrator that merely implements one interface) into a
        # marginal base.
        child_count = Counter(parent.values())
        return {child: par for child, par in parent.items()
                if child not in has_children and child_count[par] >= 2}
    # mode == 'all'
    rep = {}
    for n in list(parent):
        cur, seen = n, {n}
        while cur in parent and parent[cur] not in seen:
            cur = parent[cur]
            seen.add(cur)
        rep[n] = cur
    return rep


def fold_abstractions(g, mode='leaves'):
    """Collapse subtypes onto a representative base so a family of
    implementations shows as one node that absorbs their outgoing
    structure. Removes the swarm of in-degree-0 polymorphic leaves that
    would otherwise look like independent workflow roots.

    mode (see _representative_map): 'leaves' (default) folds only leaf
    classes one level up — the sweet spot that cleans up polymorphic
    code while keeping deep type taxonomies intact.

    Returns (folded_graph, rep_map). Each folded node carries a 'members'
    set listing the originals it represents.
    """
    rep = _representative_map(g, mode)
    R = lambda n: rep.get(n, n)

    h = nx.DiGraph()
    for n in g.nodes:
        r = R(n)
        if not h.has_node(r):
            h.add_node(r, **g.nodes.get(r, {}))
        h.nodes[r].setdefault('members', set()).add(n)

    for u, v, d in g.edges(data=True):
        ru, rv = R(u), R(v)
        if ru == rv:
            continue             # the is-a edge we just folded away
        if h.has_edge(ru, rv):
            data = h[ru][rv]
            data['weight'] = max(data['weight'], d['weight'])
            data['kinds'] |= set(d['kinds'])
            data['max_level'] = max(data['max_level'], d['max_level'])
        else:
            h.add_edge(ru, rv, weight=d['weight'], kinds=set(d['kinds']),
                       max_level=d['max_level'])
    return h, rep


# ── SCC condensation: collapse cycles into cluster nodes ──────

def condense(g):
    """Tarjan SCC condensation → a DAG.

    Returns (C, label) where C is the condensed DiGraph with integer
    node ids, and label maps each id to a readable name: the lone
    member's qualified_name, or a 'cluster(A, B, …)' tag for a real
    strongly-connected component (size > 1). A cluster IS the circular-
    dependency signal — a tight knot the user should see as one node.
    """
    C = nx.condensation(g)               # nodes carry a 'members' set
    label = {}
    for cid in C.nodes:
        members = C.nodes[cid]['members']
        if len(members) == 1:
            label[cid] = next(iter(members))
        else:
            shorts = sorted(m.split('::')[-1] for m in members)
            label[cid] = 'cluster(' + ', '.join(shorts) + ')'
    return C, label


# ── dominator tree: the responsibility hierarchy ─────────────

def find_roots(C):
    """Roots = condensed nodes nothing points to (in-degree 0). Each is
    its own independent workflow story (multi-entry case)."""
    return [n for n in C.nodes if C.in_degree(n) == 0]


def dominator_children(C, root):
    """Build the immediate-dominator tree rooted at `root`.

    Returns {parent_id: [child_id, …]} covering only nodes reachable
    from root. A→B in this tree means 'every dependency path to B goes
    through A' → B's responsibility belongs under A.
    """
    idom = nx.immediate_dominators(C, root)
    children = {}
    for node, parent in idom.items():
        if node == parent:               # root dominates itself
            continue
        children.setdefault(parent, []).append(node)
    return children


def _subtree_weight(C, node):
    """How much downstream work a condensed node owns — used for
    within-layer ranking so shallow views surface the heaviest
    responsibilities first."""
    return len(nx.descendants(C, node))


def responsibility_tree(C, label, root, max_depth=None):
    """Walk the dominator tree from `root` to `max_depth`, ranking each
    layer by subtree weight (heaviest first). Returns a nested dict:
        {'label': str, 'children': [ ... ], 'truncated': int}
    `truncated` = how many children were hidden by the depth cut.
    """
    children = dominator_children(C, root)

    def build(node, depth):
        kids = sorted(children.get(node, []),
                      key=lambda c: _subtree_weight(C, c), reverse=True)
        node_dict = {'label': label[node], 'children': [], 'truncated': 0}
        if max_depth is not None and depth >= max_depth:
            node_dict['truncated'] = len(kids)
            return node_dict
        for c in kids:
            node_dict['children'].append(build(c, depth + 1))
        return node_dict

    return build(root, 0)


def render_tree(node, indent=0):
    """Pretty-print a responsibility_tree dict as indented text."""
    lines = []
    prefix = '  ' * indent + ('└─ ' if indent else '')
    lines.append(f"{prefix}{node['label']}")
    for child in node['children']:
        lines.extend(render_tree(child, indent + 1))
    if node['truncated']:
        lines.append('  ' * (indent + 1) + f"▸ {node['truncated']} more…")
    return lines
