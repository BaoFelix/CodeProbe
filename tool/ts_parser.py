"""
ts_parser.py — tree-sitter based C++ parser

Phase 2 scope: extract entities only. Relationships come in Phase 3.

Right now this handles namespaces, classes, structs. Methods and fields
will be added in a follow-up step.
"""
from pathlib import Path
import tree_sitter_cpp
from tree_sitter import Language, Parser, Query, QueryCursor

from .model import Entity


CPP = Language(tree_sitter_cpp.language())
_parser = Parser(CPP)

# Find every namespace, class, and struct definition in the file.
# We grab the whole node (@def) so we know its line range, and the
# name node (@name) so we know what it's called.
_ENTITY_QUERY = Query(CPP, """
    (namespace_definition
        name: (namespace_identifier) @name) @def
    (class_specifier
        name: (type_identifier) @name) @def
    (struct_specifier
        name: (type_identifier) @name) @def
""")


def _kind_of(node):
    """Map a tree-sitter node type to our entity kind string."""
    return {
        'namespace_definition': 'namespace',
        'class_specifier': 'class',
        'struct_specifier': 'struct',
    }[node.type]


def parse_file(file_path):
    """Parse a C++ file and return a list of Entity objects.

    Each entity gets its qualified_name and parent_qname filled in
    based on lexical nesting: a class inside a namespace gets the
    namespace prepended, a class inside a class gets the outer class
    prepended, and so on.
    """
    path = Path(file_path)
    source = path.read_bytes()
    tree = _parser.parse(source)
    root = tree.root_node

    # Use matches() not captures(): captures() groups by capture name
    # and returns each group sorted independently, so zipping @def
    # against @name gives wrong pairs when nodes nest. matches()
    # returns one entry per pattern hit with @def and @name bundled.
    cursor = QueryCursor(_ENTITY_QUERY)
    matches = cursor.matches(root)

    entities = []
    for _pattern_index, caps in matches:
        def_node = caps['def'][0]
        name_node = caps['name'][0]
        short_name = name_node.text.decode()
        kind = _kind_of(def_node)

        # Walk up the tree to find the enclosing namespace/class/struct.
        # Each ancestor we hit prepends its name → builds the qualified
        # name from outermost to innermost.
        parts = []
        ancestor = def_node.parent
        while ancestor is not None:
            if ancestor.type in ('namespace_definition',
                                 'class_specifier',
                                 'struct_specifier'):
                anc_name = ancestor.child_by_field_name('name')
                if anc_name is not None:
                    parts.append(anc_name.text.decode())
            ancestor = ancestor.parent

        # parts is innermost-first because we walked upward; flip it
        parent_qname = '::'.join(reversed(parts)) if parts else None
        qualified_name = (f'{parent_qname}::{short_name}'
                          if parent_qname else short_name)

        entities.append(Entity(
            kind=kind,
            name=short_name,
            qualified_name=qualified_name,
            parent_qname=parent_qname,
            file_path=str(path),
            start_line=def_node.start_point[0] + 1,   # tree-sitter is 0-based
            end_line=def_node.end_point[0] + 1,
        ))

    return entities
