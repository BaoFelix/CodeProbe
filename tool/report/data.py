"""data.py — assemble the report payload for the three-section UI.

Section 1 (架构分析):   dominator forest as a collapsible node/edge tree.
Section 2 (详细关系):   relationship graph (nodes + level-colored edges).
Section 3 (设计审视):   high-level problems + class/function-level problems.

Pure function: build_payload(db) -> dict (JSON-serializable).
"""
import json

import networkx as nx

from ..workflow import (
    build_graph, fold_abstractions, condense, find_roots,
    dominator_children, score_nodes, classify_utility,
)
from ..model import Entity, Relationship


def build_payload(db):
    entities = [_ent(r) for r in db.get_entities()]
    relationships = [_rel(r) for r in db.get_relationships()]

    g = build_graph(entities, relationships)
    h, rep = fold_abstractions(g, mode='leaves')
    C, label = condense(h)
    roots = sorted(find_roots(C),
                   key=lambda r: len(nx.descendants(C, r)), reverse=True)

    scores = score_nodes(g)
    orchestrator = (db.get_module_info() or {})
    orch_name = _get(orchestrator, 'orchestrator')
    utilities = {n for n in g.nodes if classify_utility(g, n)}

    arch = _build_arch(C, label, roots, orch_name, utilities)
    rel = _build_rel(g, db, roots, label, C, orch_name, utilities)
    review = _build_review(db)

    summary = {
        'directory': _get(orchestrator, 'directory'),
        'class_count': _get(orchestrator, 'class_count'),
        'file_count': _get(orchestrator, 'file_count'),
        'orchestrator': orch_name,
        'style': _detect_style_tag(db),
    }
    return {'summary': summary, 'arch': arch, 'rel': rel, 'review': review}


# ── Section 1: dominator forest ─────────────────────────────────

def _build_arch(C, label, roots, orch_name, utilities):
    """Flatten the dominator forest into nodes + parent->child edges.
    The front-end starts with only roots visible and expands level by
    level. `depth` lets the UI know the initial expand state.
    """
    nodes, edges = [], []
    seen = set()

    # Only roots that actually head a tree (have ≥1 descendant) are
    # "workflow". Isolated single-node roots are not architecture —
    # they still appear in the relationship graph (section 2).
    workflow_roots = [r for r in roots if len(_children(C, r)) > 0]

    def visit(cid, parent_label, depth):
        lbl = label[cid]
        if lbl in seen:
            return
        seen.add(lbl)
        children = _children(C, cid)
        nodes.append({
            'id': lbl,
            'label': lbl.split('::')[-1],
            'qname': lbl,
            'depth': depth,
            'is_root': parent_label is None,
            'has_children': len(children) > 0,
            'is_orch': 1 if lbl == orch_name else 0,
            'is_util': 1 if lbl in utilities else 0,
        })
        if parent_label is not None:
            edges.append({'source': parent_label, 'target': lbl})
        for ch in children:
            visit(ch, lbl, depth + 1)

    for root in workflow_roots:
        visit(root, None, 0)

    return {'nodes': nodes, 'edges': edges}


def _children(C, cid):
    """Direct dominator-tree children of a condensed node, within its
    own root's tree."""
    root = _root_of(C, cid)
    return dominator_children(C, root).get(cid, [])


def _root_of(C, cid):
    """Find the forest root that this condensed node belongs to (walk
    up predecessors until in-degree 0)."""
    cur = cid
    guard = 0
    while C.in_degree(cur) > 0 and guard < 10000:
        cur = next(iter(C.predecessors(cur)))
        guard += 1
    return cur


# ── Section 2: relationship graph ───────────────────────────────

_LEVEL = {0: 0, 1: 1, 2: 2, 3: 3, 4: 4, 5: 5}


def _build_rel(g, db, roots, label, C, orch_name, utilities):
    """All class nodes + collapsed multi-edges. The initial visible set
    is the forest roots; the UI reveals neighbors on expand.
    """
    nodes = []
    for n in g.nodes:
        nodes.append({
            'id': n,
            'label': n.split('::')[-1],
            'qname': n,
            'kind': g.nodes[n].get('kind', 'class'),
            'is_orch': 1 if n == orch_name else 0,
            'is_util': 1 if n in utilities else 0,
            'out_deg': g.out_degree(n),
        })

    # Collapse multi-edges to one per (src,tgt), recording all evidence.
    from collections import defaultdict
    pair = defaultdict(list)
    for r in db.get_relationships():
        if not r['target_qname']:
            continue
        pair[(r['source_qname'], r['target_qname'])].append({
            'kind': r['kind'], 'level': r['level'],
            'evidence_file': r['evidence_file'],
            'evidence_line': r['evidence_line'],
            'evidence_text': r['evidence_text'],
        })
    edges = []
    for (s, t), evs in pair.items():
        edges.append({
            'id': f'{s}__{t}', 'source': s, 'target': t,
            'level': max(e['level'] for e in evs),
            'kinds': sorted({e['kind'] for e in evs}),
            'evidence': evs,
        })

    # Initial visible = forest roots (top-level boxes).
    root_qnames = []
    for cid in roots:
        members = C.nodes[cid].get('members', {label[cid]})
        # pick a representative concrete qname that is an actual node
        for m in members:
            if m in g.nodes:
                root_qnames.append(m)
                break
        else:
            root_qnames.append(label[cid])

    return {'nodes': nodes, 'edges': edges, 'roots': root_qnames}


# ── Section 3: design review ────────────────────────────────────

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

    return {'high_level': high_level, 'class_level': class_level}


# ── helpers ─────────────────────────────────────────────────────

def _kv(pairs):
    return [{'label': k, 'text': v} for k, v in pairs if v]


def _sort_by_priority(recs):
    order = {'high': 0, 'medium': 1, 'low': 2}
    return sorted(recs, key=lambda r: order.get(r.get('priority'), 3))


def _detect_style_tag(db):
    # The style was computed at scan time but not persisted; recompute
    # cheaply would need the graph. Leave blank — not essential here.
    return ''


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
