"""
v2_data.py — produce a single JSON blob describing the project for the
interactive HTML report.

What the front-end needs in one bundle:
  - nodes: every class/struct/interface (id = qualified_name)
  - edges: every relationship (collapsed multi-edges grouped per pair)
  - forest: dominator-tree responsibility hierarchy, one tree per root
  - utilities: classes the engine flagged as infrastructure
  - module: scan summary (orchestrator, file/class counts, style note)
  - pains: per-class responsibility findings keyed by sin

Pure function: takes a DBManager, returns a JSON-serializable dict.
"""
from collections import defaultdict

import networkx as nx

from ..ts_parser import parse_project as _parse  # for re-running cheaply if needed
from ..workflow import (
    build_graph, fold_abstractions, condense, find_roots,
    score_nodes, classify_utility, detect_style,
    dominator_children,
)
from ..model import Entity, Relationship


def build_payload(db):
    """Read everything we need from the DB, run the same graph/forest
    pipeline ScannerAgent ran, and assemble the JSON payload."""

    # ── Re-hydrate entities and relationships from the DB ────────
    entities = [_row_to_entity(r) for r in db.get_entities()]
    rel_rows = db.get_relationships()
    relationships = [_row_to_rel(r) for r in rel_rows]

    # ── Graph + forest (same pipeline as ScannerAgent) ────────────
    g = build_graph(entities, relationships)
    style, style_note = detect_style(entities, relationships, g)
    h, rep_map = fold_abstractions(g, mode='leaves')
    C, label = condense(h)

    # Multiple workflow roots; rank by reachable subtree size so the
    # biggest story comes first in the UI.
    roots = sorted(
        [r for r in find_roots(C) if len(nx.descendants(C, r)) > 0],
        key=lambda r: len(nx.descendants(C, r)),
        reverse=True)

    forest = [_build_dom_tree(C, label, r) for r in roots]

    # ── Orchestrator scoring + utility flagging on the unfolded graph
    scores = score_nodes(g)
    utilities = sorted(
        (n for n in g.nodes if classify_utility(g, n)),
        key=lambda n: g.in_degree(n, weight='weight'),
        reverse=True)

    # ── Nodes / edges for Cytoscape ───────────────────────────────
    qname_to_entity = {e.qualified_name: e for e in entities}
    cy_nodes = []
    for n in g.nodes:
        e = qname_to_entity.get(n)
        cy_nodes.append({
            'data': {
                'id': n,
                'label': n.split('::')[-1],
                'qname': n,
                'kind': e.kind if e else 'class',
                'file': e.file_path if e else None,
                'start_line': e.start_line if e else None,
                'is_orchestrator': 1 if (
                    forest and forest[0]['label'] == n) else 0,
                'is_utility': 1 if n in utilities else 0,
                'score': scores.get(n, {}).get('score', 0),
                'in_deg': g.in_degree(n),
                'out_deg': g.out_degree(n),
            }
        })

    # Edges keep the multi-edge structure — one Cytoscape edge per pair,
    # but with all kinds + levels recorded so the right panel can show
    # every piece of evidence.
    pair_edges = defaultdict(list)
    for r in rel_rows:
        if not r['target_qname']:
            continue                     # skip external/unresolved
        pair_edges[(r['source_qname'], r['target_qname'])].append({
            'kind': r['kind'],
            'level': r['level'],
            'evidence_file': r['evidence_file'],
            'evidence_line': r['evidence_line'],
            'evidence_text': r['evidence_text'],
        })

    cy_edges = []
    for (src, tgt), evs in pair_edges.items():
        max_lv = max(e['level'] for e in evs)
        kinds = sorted({e['kind'] for e in evs})
        cy_edges.append({
            'data': {
                'id': f'{src}__{tgt}',
                'source': src,
                'target': tgt,
                'level': max_lv,
                'kinds': kinds,
                'evidence': evs,
            }
        })

    # ── Module summary + style ────────────────────────────────────
    mi = db.get_module_info()
    def _row_get(row, col):
        try: return row[col]
        except (KeyError, IndexError, TypeError): return None
    summary = {
        'module': _row_get(mi, 'module_name'),
        'directory': _row_get(mi, 'directory'),
        'orchestrator': _row_get(mi, 'orchestrator'),
        'file_count': _row_get(mi, 'file_count'),
        'class_count': _row_get(mi, 'class_count'),
        'style': style,
        'style_note': style_note,
    }

    # ── Pain points (Seven Sins) from responsibility_analysis ─────
    pains = _gather_pains(db.get_all_responsibilities() or [])

    return {
        'summary': summary,
        'nodes': cy_nodes,
        'edges': cy_edges,
        'forest': forest,
        'utilities': [
            {'qname': u, 'short': u.split('::')[-1],
             'in_deg': g.in_degree(u)} for u in utilities],
        'pains': pains,
    }


# ── Helpers ──────────────────────────────────────────────────────

def _row_to_entity(row):
    """Build an Entity from a db row dict (sqlite Row supports indexing)."""
    import json as _json
    return Entity(
        kind=row['kind'],
        name=row['name'],
        qualified_name=row['qualified_name'],
        file_path=row['file_path'] or '',
        start_line=row['start_line'] or 0,
        end_line=row['end_line'] or 0,
        parent_qname=row['parent_qname'],
        signature=row['signature'],
        attrs=_json.loads(row['attrs'] or '{}'),
    )


def _row_to_rel(row):
    import json as _json
    return Relationship(
        source_qname=row['source_qname'],
        target_name=row['target_name'],
        target_qname=row['target_qname'],
        kind=row['kind'],
        evidence_file=row['evidence_file'] or '',
        evidence_line=row['evidence_line'] or 0,
        evidence_text=row['evidence_text'] or '',
        attrs=_json.loads(row['attrs'] or '{}'),
    )


def _build_dom_tree(C, label, root):
    """One responsibility tree, expressed as nested dicts. We attach the
    node's reach so the UI can sort siblings by weight at any layer."""
    children = dominator_children(C, root)

    def walk(n, depth):
        kids = sorted(children.get(n, []),
                      key=lambda k: len(nx.descendants(C, k)),
                      reverse=True)
        return {
            'label': label[n],
            'depth': depth,
            'reach': len(nx.descendants(C, n)),
            'children': [walk(k, depth + 1) for k in kids],
        }

    return walk(root, 0)


def _gather_pains(resp_rows):
    """Group responsibility findings by sin tag. Each row already has a
    `sin_diagnosis` field; we just parse it into a simple list."""
    grouped = defaultdict(list)
    for r in resp_rows:
        sins = (r['sin_diagnosis'] or '').strip()
        if not sins:
            continue
        for sin in [s.strip() for s in sins.split(',') if s.strip()]:
            grouped[sin].append({
                'class': r['class_name'],
                'actual': r['actual_responsibilities'],
                'ideal': r['ideal_responsibility'],
                'violations': r['srp_violations'],
                'candidates': r['extract_candidates'],
            })
    return [{'sin': k, 'classes': v} for k, v in sorted(grouped.items())]
