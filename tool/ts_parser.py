"""
ts_parser.py — tree-sitter based C++ parser

Phase 2 scope: extract entities only. Relationships come in Phase 3.

Right now this handles namespaces, classes, structs. Methods and fields
will be added in a follow-up step.
"""
from pathlib import Path
import re
import tree_sitter_cpp
from tree_sitter import Language, Parser, Query, QueryCursor

from .model import Entity, Relationship


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


# ── Query 4: base classes (inheritance) ──────────────────────
# Matches `class X : public Y` and `class X : public N::Y`.
# We grab the child class def node so we can rebuild its qualified name,
# and the base's name node (either bare type_identifier or qualified_identifier).
_BASE_CLASS_QUERY = Query(CPP, """
    (class_specifier
        name: (type_identifier) @child
        (base_class_clause
            [(type_identifier) (qualified_identifier)] @base)) @def
    (struct_specifier
        name: (type_identifier) @child
        (base_class_clause
            [(type_identifier) (qualified_identifier)] @base)) @def
""")


def _looks_like_interface(short_name: str) -> bool:
    """Convention-based interface check: name starts with I + uppercase.
    Examples: ILogger ✓, IShape ✓, Iterator ✗, Image ✗.
    Phase 3+ can replace this with a real check (all methods pure virtual).
    """
    return (len(short_name) >= 2
            and short_name[0] == 'I'
            and short_name[1].isupper())


def _line_text(source: bytes, line_idx: int) -> str:
    """Return the full text of a line (0-indexed)."""
    try:
        return source.splitlines()[line_idx].decode(errors='replace').strip()
    except IndexError:
        return ''


# ── Field type classification ────────────────────────────────
# These regex live here, on a small sub-language (C++ type
# expressions) that tree-sitter has already isolated for us — not on
# raw source. Different scope, different tool tradeoff.

_PRIMITIVES = {'int', 'char', 'bool', 'short', 'long', 'float', 'double',
               'void', 'unsigned', 'signed', 'size_t', 'string',
               'auto', 'wchar_t', 'int8_t', 'int16_t', 'int32_t', 'int64_t',
               'uint8_t', 'uint16_t', 'uint32_t', 'uint64_t'}

_CONTAINER_NAMES = ('vector', 'list', 'deque', 'set', 'unordered_set',
                    'map', 'unordered_map', 'array')

# Container with a pointer-bearing template arg → aggregates.
# Match `vector<...X*...>` or `vector<...shared_ptr<X>...>`.
_AGGREGATE_RE = re.compile(
    r'(?:std::)?(?:' + '|'.join(_CONTAINER_NAMES) + r')\s*<\s*'
    r'(?:.*?)([A-Z]\w+)\s*(?:\*|\s*>\s*$|\s*,)'
)
# Single class-name inside unique_ptr<...> → composes.
_UNIQUE_RE = re.compile(r'(?:std::)?unique_ptr\s*<\s*([\w:]+)\s*\*?\s*>')
# Single class-name inside shared_ptr<...> → associates.
_SHARED_RE = re.compile(r'(?:std::)?shared_ptr\s*<\s*([\w:]+)\s*>')
# Raw pointer to a non-primitive class → associates.
_RAW_PTR_RE = re.compile(r'^\s*(?:const\s+)?([\w:]+)\s*\*\s*$')
# Bare value-type identifier → composes (if not a primitive).
_VALUE_RE = re.compile(r'^\s*(?:const\s+)?([\w:]+)\s*$')


def _last_segment(qname: str) -> str:
    return qname.split('::')[-1]


def classify_field_type(type_str: str):
    """Return (relation_kind, target_short_name) or None for primitives.

    Examples:
        'std::unique_ptr<Engine>' → ('composes',   'Engine')
        'FuelTank'                → ('composes',   'FuelTank')
        'std::vector<Engine*>'    → ('aggregates', 'Engine')
        'Engine*'                 → ('associates', 'Engine')
        'std::shared_ptr<X>'      → ('associates', 'X')
        'int'                     → None
        'char*'                   → None
    """
    s = type_str.strip()

    m = _UNIQUE_RE.search(s)
    if m:
        return ('composes', _last_segment(m.group(1)))

    m = _AGGREGATE_RE.search(s)
    if m and _last_segment(m.group(1)) not in _PRIMITIVES:
        return ('aggregates', _last_segment(m.group(1)))

    m = _SHARED_RE.search(s)
    if m:
        return ('associates', _last_segment(m.group(1)))

    m = _RAW_PTR_RE.match(s)
    if m:
        name = _last_segment(m.group(1))
        return None if name in _PRIMITIVES else ('associates', name)

    m = _VALUE_RE.match(s)
    if m:
        name = _last_segment(m.group(1))
        return None if name in _PRIMITIVES else ('composes', name)

    return None


def parse_file(file_path):
    """Parse a C++ file and return (entities, relationships).

    Entities cover namespaces, classes, structs, methods, fields —
    each with qualified_name and parent_qname filled in based on
    lexical nesting.

    Relationships so far cover inheritance (`inherits`) and interface
    implementation (`implements`). Same-file targets get their
    target_qname resolved; cross-file targets keep target_qname=None
    and rely on a later project-wide resolve pass.
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

    # ── relationships: inheritance ────────────────────────
    # Build a lookup of "short name → qualified_name" for same-file
    # target resolution (used to fill target_qname when possible).
    same_file_index = {e.name: e.qualified_name
                       for e in entities
                       if e.kind in ('class', 'struct', 'interface')}

    relationships = []
    for _idx, caps in QueryCursor(_BASE_CLASS_QUERY).matches(root):
        def_node = caps['def'][0]
        child_name_node = caps['child'][0]
        base_name_node = caps['base'][0]

        child_short = child_name_node.text.decode()
        base_text = base_name_node.text.decode()
        # For qualified_identifier like `Foo::Bar`, the "short name"
        # we use as the target_name is the last segment.
        base_short = base_text.split('::')[-1]

        # Source class qualified_name: rebuild via tree walk
        full_parent = _enclosing_container_qname(def_node, _CONTAINER_TYPES)
        source_qname = (f'{full_parent}::{child_short}'
                        if full_parent else child_short)

        kind = 'implements' if _looks_like_interface(base_short) else 'inherits'

        relationships.append(Relationship(
            source_qname=source_qname,
            target_name=base_short,
            target_qname=same_file_index.get(base_short),   # None if cross-file
            kind=kind,
            evidence_file=str(path),
            evidence_line=def_node.start_point[0] + 1,
            evidence_text=_line_text(source, def_node.start_point[0]),
        ))

    # ── relationships: field-based (composes / aggregates / associates) ──
    # Source class = the field's parent class (not the field itself).
    # Evidence  = the field declaration line.
    for e in entities:
        if e.kind != 'field' or e.parent_qname is None:
            continue
        # Skip fields of namespaces / structs-at-top-level — we only
        # surface relationships rooted in classes/structs.
        # (parent_qname being set already enforces enclosing container.)
        classified = classify_field_type(e.signature or '')
        if not classified:
            continue
        rel_kind, target_short = classified
        relationships.append(Relationship(
            source_qname=e.parent_qname,
            target_name=target_short,
            target_qname=same_file_index.get(target_short),
            kind=rel_kind,
            evidence_file=str(path),
            evidence_line=e.start_line,
            evidence_text=_line_text(source, e.start_line - 1),
            attrs={'via_field': e.name, 'type_text': e.signature},
        ))

    return entities, relationships
