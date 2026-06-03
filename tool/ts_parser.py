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

# ── Query 1: containers ──────────────────────────────────────
# namespace / class / struct. We grab the whole def node (for line
# range) and the name node (for the short name).
_CONTAINER_QUERY = Query(CPP, """
    (namespace_definition
        name: (namespace_identifier) @name) @def
    (class_specifier
        name: (type_identifier) @name) @def
    (struct_specifier
        name: (type_identifier) @name) @def
""")

# ── Query 2: methods inside a class body ─────────────────────
# Three shapes (in tree-sitter-cpp):
#   (a) regular member decl:  void Open();         → field_declaration
#   (b) ctor/dtor decl:       Workshop(); ~W();    → declaration (no return type)
#   (c) inline definition:    void Open() {…}      → function_definition
_METHOD_IN_CLASS_QUERY = Query(CPP, """
    (field_declaration
        (function_declarator
            declarator: (_) @name)) @decl
    (declaration
        (function_declarator
            declarator: (_) @name)) @decl
    (function_definition
        (function_declarator
            declarator: (_) @name)) @decl
""")

# ── Query 3: fields (non-method members) ─────────────────────
# A field_declaration with a non-function declarator. Field name lives
# under field_identifier, possibly wrapped in pointer_declarator,
# array_declarator, etc.
_FIELD_QUERY = Query(CPP, """
    (field_declaration
        type: (_) @type
        declarator: (field_identifier) @name) @decl
    (field_declaration
        type: (_) @type
        declarator: (pointer_declarator
            declarator: (field_identifier) @name)) @decl
""")


def _kind_of(node):
    """Map a tree-sitter node type to our entity kind string."""
    return {
        'namespace_definition': 'namespace',
        'class_specifier': 'class',
        'struct_specifier': 'struct',
    }[node.type]


def _enclosing_container_qname(node, container_types):
    """Walk up from `node` and build the qualified name of the nearest
    enclosing namespace/class/struct chain. Returns None if at top level.
    """
    parts = []
    cur = node.parent
    while cur is not None:
        if cur.type in container_types:
            name_node = cur.child_by_field_name('name')
            if name_node is not None:
                parts.append(name_node.text.decode())
        cur = cur.parent
    return '::'.join(reversed(parts)) if parts else None


_CONTAINER_TYPES = ('namespace_definition', 'class_specifier', 'struct_specifier')


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

    entities = []
    # Use matches() not captures(): captures() groups by capture name
    # and returns each group sorted independently, so zipping @def
    # against @name gives wrong pairs when nodes nest. matches()
    # bundles captures per pattern hit, keeping pairs intact.

    # ── containers: namespace / class / struct ────────────
    for _idx, caps in QueryCursor(_CONTAINER_QUERY).matches(root):
        def_node = caps['def'][0]
        name_node = caps['name'][0]
        short_name = name_node.text.decode()
        parent_qname = _enclosing_container_qname(def_node, _CONTAINER_TYPES)
        qualified_name = (f'{parent_qname}::{short_name}'
                          if parent_qname else short_name)
        entities.append(Entity(
            kind=_kind_of(def_node),
            name=short_name,
            qualified_name=qualified_name,
            parent_qname=parent_qname,
            file_path=str(path),
            start_line=def_node.start_point[0] + 1,
            end_line=def_node.end_point[0] + 1,
        ))

    # ── methods ───────────────────────────────────────────
    # Parent for a method = the nearest enclosing class/struct.
    # If there's no enclosing class (free function), we skip it for now —
    # CodeProbe is class-centric.
    for _idx, caps in QueryCursor(_METHOD_IN_CLASS_QUERY).matches(root):
        decl = caps['decl'][0]
        name_node = caps['name'][0]
        short_name = name_node.text.decode()
        parent_qname = _enclosing_container_qname(
            decl, ('class_specifier', 'struct_specifier'))
        if parent_qname is None:
            continue  # free function — not a class member, skip for now
        # The method's qualified_name needs the namespace chain too.
        # Re-walk including namespaces:
        full_parent = _enclosing_container_qname(decl, _CONTAINER_TYPES)
        qualified_name = f'{full_parent}::{short_name}'
        # Signature: the whole declarator text (`Repair(Engine& e)`),
        # plus return type if we can find it. Keep it pragmatic — the
        # raw source slice is the most honest signature we can give.
        signature = source[decl.start_byte:decl.end_byte].decode(errors='replace').strip()
        entities.append(Entity(
            kind='method',
            name=short_name,
            qualified_name=qualified_name,
            parent_qname=full_parent,
            file_path=str(path),
            start_line=decl.start_point[0] + 1,
            end_line=decl.end_point[0] + 1,
            signature=signature,
        ))

    # ── fields ────────────────────────────────────────────
    for _idx, caps in QueryCursor(_FIELD_QUERY).matches(root):
        decl = caps['decl'][0]
        name_node = caps['name'][0]
        type_node = caps['type'][0]
        short_name = name_node.text.decode()
        parent_qname = _enclosing_container_qname(decl, _CONTAINER_TYPES)
        if parent_qname is None:
            continue  # field declared at namespace top level — rare, skip
        qualified_name = f'{parent_qname}::{short_name}'
        # signature for a field = its type string (including pointer if any)
        type_text = type_node.text.decode()
        # Detect pointer / reference by inspecting the declarator wrapper
        decl_decl = decl.child_by_field_name('declarator')
        if decl_decl is not None and decl_decl.type == 'pointer_declarator':
            type_text += '*'
        entities.append(Entity(
            kind='field',
            name=short_name,
            qualified_name=qualified_name,
            parent_qname=parent_qname,
            file_path=str(path),
            start_line=decl.start_point[0] + 1,
            end_line=decl.end_point[0] + 1,
            signature=type_text,
        ))

    return entities
