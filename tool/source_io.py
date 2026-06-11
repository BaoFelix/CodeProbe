"""
source_io.py — file reading helpers (encoding-aware), nothing more.

Structure extraction lives in ts_parser.py / workflow.py; path lookup
by class name is answered by the entities table.
"""
from pathlib import Path


def read_file(filepath):
    """Read a file with encoding fallback. Returns (content, line_count)."""
    filepath = Path(filepath)
    if not filepath.exists():
        return None, 0
    for encoding in ('utf-8', 'latin-1', 'cp1252'):
        try:
            content = filepath.read_text(encoding=encoding)
            return content, content.count('\n') + 1
        except (UnicodeDecodeError, OSError):
            continue
    return None, 0


class SourceReader:
    """Thin wrapper carrying the source root; kept as the agents'
    `reader` injection point."""

    def __init__(self, source_root, db=None):
        self.source_root = Path(source_root)
        self.db = db

    def read_file(self, filepath):
        return read_file(filepath)
