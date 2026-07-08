"""data.py — turns the database into the report's JSON payload.

build_payload(db) → one JSON-serializable dict with four keys:

  summary  header line + architecture-style warning (oop/mixed/crtp)
  arch     Section 1: the dominator forest as parent→child rows.
           Each edge carries the real relationship kind when a direct
           dependency backs it, or 'dominates' when the hierarchy is
           pure attribution (e.g. Logger under OrderService although
           OrderService never touches Logger directly).
  graph    Section 2: the UML class diagram. Multi-edges between the
           same pair are collapsed into ONE edge that remembers every
           kind (label) and the strongest kind (notation).
  review   Section 3: DesignCritic output reshaped into two ladders —
           high-level issues (recommendations / cross-observations /
           missing abstractions) and per-class pain points.

DESIGN RULES BAKED IN HERE
  - Phantom classes (declaration never seen; inferred from .cxx
    out-of-line methods) are EXCLUDED from both diagrams: only code
    whose full definition is in scope is what the user is analyzing.
  - External domain types (IIteration, ResultParameters...) ARE shown
    as dashed boxes, but config-enum noise (JA_*, *_t, ALL_CAPS),
    template fragments and std wrappers are filtered out.
  - Initial visible set is adaptive: small projects (≤25 classes)
    show everything; large ones start from the workflow roots.

This file is a pure function of the DB — no LLM calls, no file I/O.
"""
import json

import networkx as nx

from ..workflow import (
    build_graph, fold_abstractions, condense, find_roots,
    dominator_children, classify_utility,
)
from ..model import Entity, Relationship


def build_payload(db):
    entities = [_ent(r) for r in db.get_entities()]
    relationships = [_rel(r) for r in db.get_relationships()]

    g = build_graph(entities, relationships)

    orchestrator = (db.get_module_info() or {})
    orch_name = _get(orchestrator, 'orchestrator')

    # The orchestrator is the workflow's headline — never fold it into a
    # base class it merely subclasses (it would vanish behind a '+N impls'
    # tag on that base). Same rule as utilities.discard(orch_name) below.
    protect = {orch_name} if orch_name else frozenset()
    h, rep = fold_abstractions(g, mode='leaves', protect=protect)
    C, label = condense(h)
    roots = sorted(find_roots(C),
                   key=lambda r: len(nx.descendants(C, r)), reverse=True)
    utilities = {n for n in g.nodes if classify_utility(g, n)}
    # A CRTP core (everyone inherits it, it depends on nothing) matches
    # the utility shape too — the orchestrator must never render as a
    # utility, whichever scoring model picked it.
    utilities.discard(orch_name)

    phantoms = {e.qualified_name for e in entities
                if e.kind in ('class', 'struct', 'interface')
                and e.attrs.get('phantom')}

    graph = _build_graph_payload(g, db, roots, label, C, orch_name,
                                 utilities, phantoms)
    arch = _build_arch(g, rep, C, label, roots, orch_name, utilities,
                       phantoms)
    arch_graph = _build_arch_graph(db)
    review = _build_review(db)

    summary = {
        'directory': _get(orchestrator, 'directory'),
        'class_count': _get(orchestrator, 'class_count'),
        'file_count': _get(orchestrator, 'file_count'),
        'orchestrator': orch_name,
        'style': _get(orchestrator, 'style'),
        'style_note': _get(orchestrator, 'style_note'),
    }
    return {'summary': summary, 'arch_graph': arch_graph,
            'graph': graph, 'arch': arch, 'review': review}


def _build_arch_graph(db):
    """Module-level graph for the report's headline Architecture section.
    Reads the persisted deterministic audit; None if none has run (so the
    section hides itself). Nodes/edges already carry in_cycle / god flags."""
    audit = db.get_arch_audit()
    if not audit:
        return None
    p = audit['payload']
    return {
        'nodes': p['nodes'],
        'edges': p['edges'],
        'findings': p['findings'],
        'strategy': p.get('strategy'),
        'unresolved_pct': p.get('unresolved_pct'),
        'module_count': p.get('module_count'),
        'edge_count': p.get('edge_count'),
    }


def _build_arch(g, rep_map, C, label, roots, orch_name, utilities, phantoms):
    """架构分析 = the dominator forest. Each edge is parent→child in
    the WORKFLOW hierarchy (ownership of responsibility), which is not
    always a direct dependency: Logger may hang under OrderService
    because all paths to it pass through OrderService, even though
    OrderService itself never touches Logger.

    For each tree edge we attach the real relationship kind when a
    direct edge exists (so the UI can draw proper UML notation), else
    kind='dominates' (drawn as a dotted grey line).

    Folded families annotate their representative: NotificationChannel
    that absorbed EmailChannel/SmsChannel gets impls=['EmailChannel',
    'SmsChannel'] so the node can read 'NotificationChannel (+2 impls)'.
    """
    # representative → folded-away members
    family = {}
    for child, parent in rep_map.items():
        family.setdefault(parent, []).append(child)

    nodes, edges = [], []
    seen = set()

    def kind_between(src_lbl, tgt_lbl):
        """Strongest direct relationship between two condensed labels,
        considering folded members on both sides."""
        srcs = [src_lbl] + family.get(src_lbl, [])
        tgts = {tgt_lbl, *family.get(tgt_lbl, [])}
        best, best_lv = None, -1
        for s in srcs:
            if s not in g.adj:
                continue
            for t, d in g.adj[s].items():
                if t in tgts:
                    for k in d.get('kinds', set()):
                        if _LEVEL_OF.get(k, 0) > best_lv:
                            best, best_lv = k, _LEVEL_OF.get(k, 0)
        return best

    def visit(cid, parent_lbl, depth):
        lbl = label[cid]
        if lbl in seen or lbl in phantoms:
            return
        seen.add(lbl)
        kids = _children(C, cid)
        impls = sorted(m.split('::')[-1] for m in family.get(lbl, []))
        nodes.append({
            'id': lbl, 'label': lbl.split('::')[-1], 'qname': lbl,
            'depth': depth, 'is_root': parent_lbl is None,
            'has_children': len(kids) > 0,
            'impls': impls,
            'is_orch': 1 if lbl == orch_name else 0,
            'is_util': 1 if lbl in utilities else 0,
        })
        if parent_lbl is not None:
            k = kind_between(parent_lbl, lbl)
            edges.append({'source': parent_lbl, 'target': lbl,
                          'kind': k or 'dominates'})
        for ch in kids:
            visit(ch, lbl, depth + 1)

    for root in roots:
        if len(_children(C, root)) > 0:
            visit(root, None, 0)

    return {'nodes': nodes, 'edges': edges}


# ── Graph payload (shared by 架构分析 + 详细关系, UML rendering) ──

# Container / smart-pointer wrappers and config-enum families that are
# noise as graph nodes — they're not collaborator classes.
_WRAPPERS = {'vector', 'list', 'map', 'set', 'unordered_map', 'unordered_set',
             'array', 'deque', 'SharedPtr', 'shared_ptr', 'unique_ptr',
             'weak_ptr', 'ScopedSMArray', 'ScopedSMString', 'pair', 'tuple'}

# Strongest-first, so a multi-edge picks its UML notation from the
# strongest structural relationship present.
_KIND_RANK = ['inherits', 'implements', 'composes', 'aggregates',
              'associates', 'depends']
_LEVEL_OF = {'depends': 0, 'associates': 1, 'implements': 2,
             'aggregates': 3, 'composes': 4, 'inherits': 5}
# Which kinds count as "structural" (shown in 架构分析).
_STRUCTURAL = {'inherits', 'implements', 'composes', 'aggregates'}


def _is_noise_external(name):
    """An unresolved target_name not worth showing as a node."""
    if not name:
        return True
    if any(ch in name for ch in '<>*, '):
        return True
    if name.startswith(('JA_', 'SFRES_', 'SPP', 'SM_')):
        return True
    if name.endswith(('_t', '_type')):
        return True
    if name.isupper():
        return True
    if name in _WRAPPERS:
        return True
    if name in ('tag_t', 'logical', 'Vint', 'Vfloat', 'Vdouble'):
        return True
    return False


def _children(C, cid):
    root = _root_of(C, cid)
    return dominator_children(C, root).get(cid, [])


def _root_of(C, cid):
    cur, guard = cid, 0
    while C.in_degree(cur) > 0 and guard < 10000:
        cur = next(iter(C.predecessors(cur)))
        guard += 1
    return cur


def _build_graph_payload(g, db, roots, label, C, orch_name, utilities, phantoms):
    """One UML class-diagram dataset shared by both graph sections.

    nodes  — the project's own classes only. External / third-party types
             (unresolved targets: SDK types, std, forward-declared externals)
             are NOT shown — they add hundreds of dashed leaf nodes and
             drown the actual architecture. Only edges between two internal
             classes are kept.
    edges  — one per (src,tgt) pair, carrying:
               primary   : the strongest relationship kind (UML notation)
               kinds     : every kind between the pair (edge label)
               level     : max coupling level (edge color)
               structural: bool — shown in 架构分析
               evidence  : per-kind source lines
    roots  — initial-visible set (adaptive to project size).
    """
    # Phantom classes (declaration never seen — inferred from .cxx
    # out-of-line methods) are NOT shown: only classes whose full
    # definition is in scope are what the user is analyzing.
    internal = set(g.nodes) - phantoms
    nodes = []
    for n in internal:
        kind, _ = _entity_kind_phantom(db, n)
        nodes.append({
            'id': n, 'label': n.split('::')[-1], 'qname': n,
            'kind': kind,
            'is_orch': 1 if n == orch_name else 0,
            'is_util': 1 if n in utilities else 0,
            'is_external': 0,
        })

    from collections import defaultdict
    pair = defaultdict(list)
    for r in db.get_relationships():
        src = r['source_qname']
        tgt = r['target_qname']
        # internal → internal only: skip unresolved (external) targets and
        # anything that isn't one of our own in-scope classes.
        if src not in internal or tgt not in internal or src == tgt:
            continue
        pair[(src, tgt)].append({
            'kind': r['kind'], 'level': r['level'],
            'evidence_file': r['evidence_file'],
            'evidence_line': r['evidence_line'],
            'evidence_text': r['evidence_text'],
        })

    edges = []
    for (s, t), evs in pair.items():
        kinds = {e['kind'] for e in evs}
        primary = next((k for k in _KIND_RANK if k in kinds), 'depends')
        edges.append({
            'id': f'{s}__{t}', 'source': s, 'target': t,
            'primary': primary,
            'kinds': sorted(kinds, key=lambda k: -_LEVEL_OF.get(k, 0)),
            'level': max(_LEVEL_OF.get(k, 0) for k in kinds),
            'structural': 1 if (kinds & _STRUCTURAL) else 0,
            'evidence': evs,
        })

    # Initial-visible roots. The graph only ever reveals a node by
    # expanding one of its in-edge sources, so the root set MUST make
    # every node reachable — otherwise a class with no incoming edge that
    # isn't a root (e.g. a stand-alone Builder) can never be shown, no
    # matter how much the user clicks. Roots = every in-degree-0 source;
    # then add a representative for any pure-cycle / isolated component
    # that the sources don't reach.
    if len(internal) <= 25:
        root_qnames = list(internal)
    else:
        succ = defaultdict(list)
        indeg = {n: 0 for n in internal}
        for (s, t) in pair:
            succ[s].append(t)
            indeg[t] += 1
        root_qnames = [n for n in internal if indeg[n] == 0]
        reached = set(root_qnames)
        stack = list(root_qnames)
        while stack:
            x = stack.pop()
            for t in succ[x]:
                if t not in reached:
                    reached.add(t)
                    stack.append(t)
        # anything still unreached lives in a cycle with no source — pick
        # deterministically the member with the most out-edges as its entry.
        for n in sorted(internal - reached,
                        key=lambda n: (-len(succ[n]), n)):
            if n in reached:
                continue
            root_qnames.append(n)
            reached.add(n)
            stack = [n]
            while stack:
                x = stack.pop()
                for t in succ[x]:
                    if t not in reached:
                        reached.add(t)
                        stack.append(t)

    return {'nodes': nodes, 'edges': edges, 'roots': root_qnames}


def _entity_kind_phantom(db, qname):
    """(kind, is_phantom) for a class node. Phantom = declaration never
    seen; inferred from out-of-line method definitions in a .cxx."""
    row = db.get_entity(qname)
    if not row:
        return 'class', False
    attrs = json.loads(row['attrs'] or '{}')
    return row['kind'], bool(attrs.get('phantom'))



# ── Section 3: design review ────────────────────────────────────

def _build_arch_review(db):
    """The architecture-level LLM review: the Tier-2 global conclusion +
    the Tier-1 per-module assessments. None if neither has run."""
    out = {'summary': '', 'priorities': [], 'modules': []}
    concl = db.get_design_module('architecture')
    if concl and concl['parsed_json']:
        c = json.loads(concl['parsed_json'])
        out['summary'] = c.get('summary', '')
        out['priorities'] = c.get('priorities', [])
    for r in db.get_arch_module_reviews() or []:
        if not r['parsed_json']:
            continue
        m = json.loads(r['parsed_json'])
        out['modules'].append({
            'module': r['module_name'],
            'role': m.get('role', ''),
            'assessment': m.get('assessment', ''),
            'risks': m.get('risks', []) or [],
            'recommendation': m.get('recommendation', ''),
        })
    return out if (out['summary'] or out['modules']) else None


def _build_review(db):
    high_level = []
    class_level = []

    module_row = db.get_design_module('default')
    if module_row and module_row['parsed_json']:
        mod = json.loads(module_row['parsed_json'])

        for r in _sort_by_priority(mod.get('recommendations', [])):
            high_level.append({
                'kind': 'recommendation',
                'priority': r.get('priority', 'medium'),
                'title': r.get('title') or r.get('target') or 'recommendation',
                'details': _kv([
                    ('target', r.get('target')),
                    ('action', r.get('action')),
                    ('impact', r.get('expected_impact')),
                    ('evidence', r.get('evidence')),
                ]),
            })
        for o in mod.get('cross_observations', []):
            high_level.append({
                'kind': 'observation',
                'priority': 'info',
                'title': o.get('pattern', 'cross-cutting observation'),
                'details': _kv([
                    ('suggestion', o.get('suggestion')),
                    ('affected', ', '.join(o.get('affected_subtrees', []) or [])),
                ]),
            })
        for m in mod.get('missing_abstractions', []):
            high_level.append({
                'kind': 'missing',
                'priority': 'info',
                'title': f"Missing abstraction: {m.get('role', '?')}",
                'details': _kv([
                    ('suggested interface', m.get('suggested_interface')),
                    ('current implementations',
                     ', '.join(m.get('current_implementations', []) or [])),
                ]),
            })

    for sub in db.get_design_subtrees() or []:
        if not sub['parsed_json']:
            continue
        a = json.loads(sub['parsed_json'])
        pains = a.get('pains', [])
        if not pains:
            continue
        class_level.append({
            'class': sub['subtree_root'],
            'short': sub['subtree_root'].split('::')[-1],
            'essence': a.get('essence', ''),
            'pains': [{
                'title': p.get('title') or p.get('category') or 'issue',
                'category': p.get('category', ''),
                'details': _kv([
                    ('where', p.get('where')),
                    ('detail', p.get('what')),
                ]),
            } for p in pains],
        })

    return {'architecture': _build_arch_review(db),
            'high_level': high_level, 'class_level': class_level}


# ── helpers ─────────────────────────────────────────────────────

def _kv(pairs):
    return [{'label': k, 'text': v} for k, v in pairs if v]


def _sort_by_priority(recs):
    order = {'high': 0, 'medium': 1, 'low': 2}
    return sorted(recs, key=lambda r: order.get(r.get('priority'), 3))


def _get(row, col):
    if row is None:
        return None
    try:
        return row[col]
    except (KeyError, IndexError, TypeError):
        return None


def _ent(row):
    return Entity(
        kind=row['kind'], name=row['name'],
        qualified_name=row['qualified_name'],
        file_path=row['file_path'] or '',
        start_line=row['start_line'] or 0, end_line=row['end_line'] or 0,
        parent_qname=row['parent_qname'], signature=row['signature'],
        attrs=json.loads(row['attrs'] or '{}'),
    )


def _rel(row):
    return Relationship(
        source_qname=row['source_qname'], target_name=row['target_name'],
        target_qname=row['target_qname'], kind=row['kind'],
        evidence_file=row['evidence_file'] or '',
        evidence_line=row['evidence_line'] or 0,
        evidence_text=row['evidence_text'] or '',
        attrs=json.loads(row['attrs'] or '{}'),
    )
