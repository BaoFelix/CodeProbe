"""row_shapes.py — small adapters that reshape db.py's natural rows
(entities / relationships) into the dict shape the report sections
already know how to render. Lives in the report package because the
field names here (source_class / target_class / level_name / …) are
the report's vocabulary, not the data layer's.
"""


def dep_dict(row):
    """relationships row → legacy dependency dict the section renderers
    consume."""
    target = row['target_qname'] or row['target_name']
    return {
        'source_class': row['source_qname'],
        'target_class': target,
        'level': row['level'],
        'level_name': f"Lv-{row['level']}",
        'kind': row['kind'],                                 # new info, optional
        'source_evidence': (row['evidence_text'] or '').strip(),
        'target_is_external': 0 if row['target_qname'] else 1,
    }


def class_dict(row, orchestrator=None, kid_rows=None):
    """entities row → legacy class dict. `kid_rows` is the project-wide
    parent_qname / kind / count grouping; computing it once per report
    keeps each class lookup O(1).
    """
    qname = row['qualified_name']
    method_counts = {r['parent_qname']: r['n']
                     for r in (kid_rows or []) if r['kind'] == 'method'}
    field_counts = {r['parent_qname']: r['n']
                    for r in (kid_rows or []) if r['kind'] == 'field'}
    return {
        'class_name': qname,
        'header_path': row['file_path'],
        'impl_path': None,
        'method_count': method_counts.get(qname, 0),
        'member_count': field_counts.get(qname, 0),
        'line_count': max(0, (row['end_line'] or 0) - (row['start_line'] or 0) + 1),
        'is_orchestrator': 1 if orchestrator and qname == orchestrator else 0,
    }
