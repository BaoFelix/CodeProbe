"""
source_io.py — file reading helpers (encoding-aware), nothing more.

Replaces the file-read piece of the old reader.py. The structure
extraction it used to do is now in ts_parser.py / workflow.py; the
filesystem search by class name is now answered by the entities table.
"""
from pathlib import Path
from .config import IMPL_EXTS


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


def get_file_preview(content, max_lines=200):
    """First N lines, plus the total line count."""
    lines = content.split('\n')
    return '\n'.join(lines[:max_lines]), len(lines)


class SourceReader:
    """Drop-in replacement for the old FileReader for the bits we still
    use. Keeps a source_root for path resolution; backs read_class_source
    with the entities table so we no longer guess paths from filename
    stems.
    """

    def __init__(self, source_root, db=None):
        self.source_root = Path(source_root)
        self.db = db

    def read_file(self, filepath):
        return read_file(filepath)

    def get_file_preview(self, content, max_lines=200):
        return get_file_preview(content, max_lines)

    def find_class_files(self, class_name):
        """Look up header + impl paths from the entities table. Accepts
        either a short name or a qualified name. Returns (header, impl)
        as Path or None.
        """
        if self.db is None:
            return None, None
        # Exact qualified_name first; fall back to short name.
        rows = self.db.get_entities()
        match = next(
            (r for r in rows
             if r['kind'] in ('class', 'struct', 'interface')
             and (r['qualified_name'] == class_name or r['name'] == class_name)),
            None)
        if not match:
            return None, None
        header = Path(match['file_path']) if match['file_path'] else None
        impl = None
        if header is not None:
            stem = header.with_suffix('')
            for ext in IMPL_EXTS:
                cand = stem.with_suffix(ext)
                if cand.exists():
                    impl = cand
                    break
        return header, impl

    def read_class_source(self, class_name, max_impl_lines=None):
        """Header + impl content for one class, concatenated. Same shape
        the old reader returned: (source_text, header_path, impl_path).
        """
        header, impl = self.find_class_files(class_name)
        parts = []
        if header:
            content, lines = read_file(header)
            if content:
                parts.append(f"// === Header: {header.name} ({lines} lines) ===\n{content}")
        if impl:
            content, lines = read_file(impl)
            if content:
                if max_impl_lines and lines > max_impl_lines:
                    preview = '\n'.join(content.split('\n')[:max_impl_lines])
                    parts.append(
                        f"// === Impl: {impl.name} ({lines} lines, first {max_impl_lines}) ===\n{preview}")
                else:
                    parts.append(f"// === Impl: {impl.name} ({lines} lines) ===\n{content}")
        return '\n\n'.join(parts), header, impl
