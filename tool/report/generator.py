"""Report generator — placeholder.

The previous UI was scrapped. A new design will be implemented here.
For now this writes a minimal file so the pipeline doesn't crash;
nothing visual is rendered.
"""
from pathlib import Path


def generate_html_report(db, output_path, reader=None):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        "<!DOCTYPE html><html><body>"
        "<p>Report UI is being rebuilt. No content yet.</p>"
        "</body></html>",
        encoding='utf-8')
    return output_path
