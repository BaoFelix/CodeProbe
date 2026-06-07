"""Report generator — three-section interactive HTML.

1. 架构分析 (dominator forest)  2. 详细关系 (relationship graph)
3. 设计审视 (design review)

All data comes from the DB; `reader` is accepted for backward
compatibility but unused.
"""
from pathlib import Path

from .data import build_payload
from .template import render


def generate_html_report(db, output_path, reader=None):
    payload = build_payload(db)
    html = render(payload)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding='utf-8')
    return output_path
