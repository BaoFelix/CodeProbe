"""Interactive HTML report generator. Replaces the old static three-layer
report with a single-page Cytoscape.js view fed by the entity-relationship
graph.
"""
from pathlib import Path

from .v2_data import build_payload
from .v2_template import render


def generate_html_report(db, output_path, reader=None):
    """Generate the interactive architecture report and write it to
    `output_path`. `reader` is accepted for backward compatibility but
    not used — all data comes from the DB.
    """
    payload = build_payload(db)
    html = render(payload)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding='utf-8')
    return output_path
