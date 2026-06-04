"""
ts_parser.py — tree-sitter based C++ parser

Phase 2 scope: extract entities only. Relationships come in Phase 3.

Right now this handles namespaces, classes, structs. Methods and fields
will be added in a follow-up step.
"""
from pathlib import Path
from dataclasses import asdict
from concurrent.futures import ProcessPoolExecutor
import json
import os
import re
import tree_sitter_cpp
from tree_sitter import Language, Parser, Query, QueryCursor

from .model import Entity, Relationship


def _entity_to_dict(e):
    return asdict(e)


def _rel_to_dict(r):
    return asdict(r)


def _parse_one_file(path_str):
    """Top-level worker — picklable, runs in either main or subprocess.
    Returns (entities, relationships, aliases). Re-importing this module
    in a subprocess re-initializes the module-level Parser, which is
    what we want (each process gets its own tree-sitter state).
    """
    path = Path(path_str)
    if path.suffix in _SCH_EXTS:
        entities, rels = parse_sch_file(path)
        aliases = {}
    else:
        entities, rels = parse_file(path)
        aliases = extract_aliases(path)
    return entities, rels, aliases


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
# Matches `class X : public Y`, `class X : public N::Y`, and templated
# bases `class X : public Base<Mutex>` (very common — policy/CRTP). The
# base capture may be a type_identifier, a qualified_identifier, or a
# template_type; template params are stripped when taking the short name.
_BASE_CLASS_QUERY = Query(CPP, """
    (class_specifier
        name: (type_identifier) @child
        (base_class_clause
            [(type_identifier) (qualified_identifier) (template_type)] @base)) @def
    (struct_specifier
        name: (type_identifier) @child
        (base_class_clause
            [(type_identifier) (qualified_identifier) (template_type)] @base)) @def
""")


def _line_text(source: bytes, line_idx: int) -> str:
    """Return the full text of a line (0-indexed)."""
    try:
        return source.splitlines()[line_idx].decode(errors='replace').strip()
    except IndexError:
        return ''


# ── Entity-kind refinement: interface vs abstract vs concrete ─
# Decided by the actual shape of the class, not a naming convention:
#   interface = has pure-virtual method(s), no data fields, and every
#               method is pure-virtual or a destructor (e.g. ILogger).
#   abstract  = has pure-virtual method(s) but also fields/concrete
#               methods (e.g. spdlog's sink — an abstract base class).
#   class/struct = no pure-virtual methods (concrete).

_PURE_VIRTUAL_RE = re.compile(
    r'\)\s*(?:const|noexcept|override|final|\s)*=\s*0\s*;?\s*$')


def _is_pure_virtual(signature) -> bool:
    """True only for a pure-virtual declaration: '= 0' appears after the
    parameter list ')' and any cv/ref qualifiers, with no method body.

    Guards against two false positives we hit on real code:
      - default arguments:  void f(int x = 0);   ('=0' is inside the parens)
      - inline body content: void f() { x = 0; }  ('=0' is in the body)
    """
    if not signature or '{' in signature:    # has a body → not pure
        return False
    return bool(_PURE_VIRTUAL_RE.search(signature))


def _refine_class_kinds(entities):
    """Re-tag class/struct entities as 'interface', and mark abstract
    ones in attrs, based on their members. Mutates entities in place.
    """
    children = {}
    for e in entities:
        if e.parent_qname:
            children.setdefault(e.parent_qname, []).append(e)

    for e in entities:
        if e.kind not in ('class', 'struct'):
            continue
        kids = children.get(e.qualified_name, [])
        methods = [c for c in kids if c.kind == 'method']
        fields = [c for c in kids if c.kind == 'field']
        pure = [m for m in methods if _is_pure_virtual(m.signature)]
        if not pure:
            continue                      # concrete — leave as class/struct
        e.attrs['abstract'] = True
        impure = [m for m in methods
                  if not _is_pure_virtual(m.signature)
                  and not m.name.startswith('~')]
        if not fields and not impure:
            e.kind = 'interface'          # pure interface


# Export/visibility macros (SPDLOG_API, Standard_EXPORT, MYLIB_EXPORT,
# __declspec(...)). tree-sitter doesn't expand macros, so they corrupt
# parsing in two spots:
#   `class SPDLOG_API logger {…}`  → macro stolen as the class name
#   `Standard_EXPORT void Foo();`  → macro stolen as the return type
# We blank these tokens out before parsing, replacing with equal-length
# spaces so line/column offsets stay intact.

# (1) macro sitting between class/struct and the real class name + body.
_CLASS_MACRO_RE = re.compile(
    rb'\b(class|struct)\s+'
    rb'(__declspec\s*\([^)]*\)\s+|[A-Z][A-Z0-9_]{2,}\s+)'   # the macro
    rb'(?=[A-Za-z_]\w*\s*[:{])'                              # real name + body/base
)
# (2) export/api macro tokens anywhere: identifiers ending in _EXPORT,
# _API, _IMPORT, _DECL — these are almost always visibility macros.
_TOKEN_MACRO_RE = re.compile(rb'\b[A-Za-z]\w*_(?:EXPORT|API|IMPORT|DECL|DLLAPI)\b')


def _strip_export_macros(source: bytes) -> bytes:
    """Blank export macros so tree-sitter sees clean declarations.
    Equal-length space replacement preserves offsets. The class-position
    rule only fires when a macro sits between the keyword and ANOTHER
    name+body, so `class Foo {` / `class FOO {` (single name) are safe.
    """
    def blank_class(m):
        return m.group(1) + b' ' + (b' ' * len(m.group(2)))
    source = _CLASS_MACRO_RE.sub(blank_class, source)
    source = _TOKEN_MACRO_RE.sub(lambda m: b' ' * len(m.group(0)), source)
    return source


# ── Field type classification ────────────────────────────────
# These regex live here, on a small sub-language (C++ type
# expressions) that tree-sitter has already isolated for us — not on
# raw source. Different scope, different tool tradeoff.

_PRIMITIVES = {'int', 'char', 'bool', 'short', 'long', 'float', 'double',
               'void', 'unsigned', 'signed', 'size_t', 'string',
               'auto', 'wchar_t', 'int8_t', 'int16_t', 'int32_t', 'int64_t',
               'uint8_t', 'uint16_t', 'uint32_t', 'uint64_t'}

# std utility wrappers that aren't a class dependency in themselves.
# `function` is a callable (target is a lambda, not a class); the rest
# are value/utility types we don't want to surface as relationships.
_STD_NOISE = {'function', 'string', 'string_view', 'pair', 'tuple',
              'initializer_list'}

_CONTAINER_NAMES = ('vector', 'list', 'deque', 'set', 'unordered_set',
                    'multiset', 'map', 'unordered_map', 'multimap', 'array')

_SMART_PTRS = ('unique_ptr', 'shared_ptr', 'weak_ptr')

# Builtin keywords we should NOT treat as class references.
_KEYWORDS = {'class', 'struct', 'const', 'static', 'virtual', 'override',
             'final', 'noexcept', 'inline', 'explicit', 'mutable',
             'volatile', 'extern', 'register', 'typename', 'template',
             'public', 'private', 'protected', 'friend', 'return',
             'if', 'else', 'for', 'while', 'switch', 'case'}


def _last_segment(qname: str) -> str:
    return qname.split('::')[-1]


def _last_top_level_arg(inner: str) -> str:
    """Given the inside of a <...>, return the last comma-separated arg
    at bracket depth 0. For map<K, V> this yields V (the value type),
    which is the more interesting dependency."""
    depth = 0
    start = 0
    args = []
    for i, ch in enumerate(inner):
        if ch in '<([':
            depth += 1
        elif ch in '>)]':
            depth -= 1
        elif ch == ',' and depth == 0:
            args.append(inner[start:i])
            start = i + 1
    args.append(inner[start:])
    return args[-1].strip() if args else inner.strip()


def _innermost_type_name(s: str):
    """Peel template args, pointers, refs, const, and namespaces down to
    the core type identifier. Returns None if it bottoms out at a
    primitive or std noise type. Does NOT require uppercase — a class is
    'any identifier that isn't a known primitive/keyword'.
    """
    s = s.strip()
    s = re.sub(r'^const\s+', '', s)
    s = s.rstrip('*& ').strip()

    # Templated: descend into the last top-level template argument.
    m = re.search(r'<(.+)>', s)
    if m:
        return _innermost_type_name(_last_top_level_arg(m.group(1)))

    name = s.split('::')[-1].strip().rstrip('*& ').strip()
    if (not name
            or name in _PRIMITIVES
            or name in _KEYWORDS
            or name in _STD_NOISE
            or not re.match(r'^[A-Za-z_]\w*$', name)):
        return None
    return name


def classify_field_type(type_str: str):
    """Return (relation_kind, target_short_name) or None.

    The KIND comes from the outermost wrapper; the TARGET comes from the
    innermost class name (uppercase is NOT required).

        'std::unique_ptr<Engine>'      → ('composes',   'Engine')
        'FuelTank'                     → ('composes',   'FuelTank')
        'std::vector<Engine*>'         → ('aggregates', 'Engine')
        'std::vector<sink_ptr>'        → ('aggregates', 'sink_ptr')  # alias resolved later
        'std::shared_ptr<sinks::sink>' → ('associates', 'sink')
        'Engine*'                      → ('associates', 'Engine')
        'int' / 'char*' / 'std::function<...>' → None
    """
    s = type_str.strip()

    # std::function etc. — callable / utility, not a class dependency.
    head = s.split('<')[0].split('::')[-1].strip()
    if head in _STD_NOISE:
        return None

    core = _innermost_type_name(s)
    if core is None:
        return None

    if re.search(r'\bunique_ptr\s*<', s):
        return ('composes', _last_segment(core))
    # shared_ptr / weak_ptr / OCCT occ::handle / Qt QSharedPointer etc.
    # are reference-counted shared ownership → associates, like a pointer.
    if re.search(r'\b(?:shared_ptr|weak_ptr|handle|intrusive_ptr)\s*<', s, re.IGNORECASE):
        return ('associates', _last_segment(core))
    if re.search(r'\b(?:' + '|'.join(_CONTAINER_NAMES) + r')\s*<', s):
        return ('aggregates', _last_segment(core))
    if s.rstrip().endswith('*'):
        return ('associates', _last_segment(core))
    return ('composes', _last_segment(core))


def _type_names_in(node):
    """Walk a subtree and yield identifier strings that look like
    user-defined type names. Used to mine parameter and return types
    for depends edges. Stops descending once a named type is found so
    nested template args still get yielded one level at a time.
    """
    if node is None:
        return
    if node.type in ('type_identifier', 'qualified_identifier'):
        text = node.text.decode()
        name = text.split('::')[-1]
        # A class is any identifier that isn't a known primitive / keyword
        # / std noise type — no uppercase requirement.
        if (name
                and name not in _PRIMITIVES
                and name not in _KEYWORDS
                and name not in _STD_NOISE):
            yield name
        # Don't descend — we have the name of this type
        return
    for child in node.children:
        yield from _type_names_in(child)


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
    source = _strip_export_macros(path.read_bytes())
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
        # Skip forward declarations (`class gp_Trsf;`): a real definition
        # has a body. Without this they become phantom entities with no
        # members that pollute the graph and resolution.
        if (def_node.type in ('class_specifier', 'struct_specifier')
                and def_node.child_by_field_name('body') is None):
            continue
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

    # Refine kinds now that all members are known: interface vs abstract
    # vs concrete. Must happen before inheritance edges so we can tell
    # implements from inherits by the base's abstractness.
    _refine_class_kinds(entities)

    # ── relationships: inheritance ────────────────────────
    # Build a lookup of "short name → qualified_name" for same-file
    # target resolution (used to fill target_qname when possible).
    same_file_index = {e.name: e.qualified_name
                       for e in entities
                       if e.kind in ('class', 'struct', 'interface')}
    # Short names of same-file abstract types (interface or ABC). An edge
    # into one of these is `implements`; into a concrete class, `inherits`.
    same_file_abstract = {e.name for e in entities
                          if e.kind == 'interface'
                          or e.attrs.get('abstract')}

    relationships = []
    for _idx, caps in QueryCursor(_BASE_CLASS_QUERY).matches(root):
        def_node = caps['def'][0]
        child_name_node = caps['child'][0]
        base_name_node = caps['base'][0]

        child_short = child_name_node.text.decode()
        base_text = base_name_node.text.decode()
        # Strip template params, then take the last namespace segment:
        #   spdlog::sinks::base_sink<Mutex> → base_sink
        base_short = base_text.split('<')[0].split('::')[-1].strip()

        # Source class qualified_name: rebuild via tree walk
        full_parent = _enclosing_container_qname(def_node, _CONTAINER_TYPES)
        source_qname = (f'{full_parent}::{child_short}'
                        if full_parent else child_short)

        # Decide implements vs inherits from the base's actual shape.
        # If the base is in another file we can't tell yet → provisional
        # 'inherits', re-tagged in parse_project once kinds are global.
        kind = 'implements' if base_short in same_file_abstract else 'inherits'

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

    # ── relationships: depends (method signature uses a type) ───
    # Re-run the method query — we don't keep decl nodes on Entity.
    # For each method, mine parameter types and the return type for
    # user-defined names. Source of the edge = method's parent class.
    seen_depends = set()  # dedupe (source_qname, target_name) per file
    for _idx, caps in QueryCursor(_METHOD_IN_CLASS_QUERY).matches(root):
        decl = caps['decl'][0]
        parent = _enclosing_container_qname(decl, _CONTAINER_TYPES)
        # Skip free functions (no enclosing class/struct)
        if parent is None or not any(
                a == 'class_specifier' or a == 'struct_specifier'
                for a in _ancestor_types(decl)):
            continue

        # Mine names from the whole method declaration node — covers
        # both return type (sibling of function_declarator) and
        # parameter list (inside function_declarator → parameter_list).
        for name in set(_type_names_in(decl)):
            key = (parent, name)
            if key in seen_depends:
                continue
            seen_depends.add(key)
            relationships.append(Relationship(
                source_qname=parent,
                target_name=name,
                target_qname=same_file_index.get(name),
                kind='depends',
                evidence_file=str(path),
                evidence_line=decl.start_point[0] + 1,
                evidence_text=_line_text(source, decl.start_point[0]),
                attrs={'via': 'method_signature'},
            ))

    return entities, relationships


def _ancestor_types(node):
    """Yield ancestor node types from `node` upward (excluding itself)."""
    cur = node.parent
    while cur is not None:
        yield cur.type
        cur = cur.parent


# ── Project-wide parsing & resolution ────────────────────────

# Bump when parser logic changes so cached entries get invalidated.
# Any edit to parse_file / parse_sch_file / extract_aliases /
# classify_field_type / _refine_class_kinds should bump this.
PARSER_VERSION = 1


_CPP_EXTS = {'.h', '.hxx', '.hpp', '.cxx', '.cpp', '.c'}
# .sch is a private DSL with two custom keywords (`superclass Parent`
# and `forward_declare class X;`) that tree-sitter-cpp won't parse.
# It gets a small regex-based path below (parse_sch_file), wired into
# parse_project the same way as .cpp files. The accuracy on .sch is
# necessarily lower than on real C++ — we document the limit honestly
# rather than pretend the regex matches tree-sitter's reach.
_SCH_EXTS = {'.sch'}
_ALL_EXTS = _CPP_EXTS | _SCH_EXTS


# ── .sch regex extractor ─────────────────────────────────────
# Three patterns are enough to cover the conventions the old reader
# already handled. Anything more exotic stays unparsed.
_SCH_CLASS_RE = re.compile(
    r'^\s*class\s+(\w+)\s*\{', re.MULTILINE)
_SCH_SUPERCLASS_RE = re.compile(
    r'^\s*superclass\s+(\w+)', re.MULTILINE)
_SCH_FIELD_RE = re.compile(
    # Type then a member name (member-prefix convention common in .sch),
    # ending with semicolon. Skips method declarations (have '(').
    r'^\s*([\w:]+(?:<[\w:,\s*&]+>)?[\s*&]*)\s+(m_\w+|\w+)\s*;',
    re.MULTILINE)


def parse_sch_file(file_path):
    """Minimal .sch parser. Returns (entities, relationships) with the
    same shape parse_file produces, so the rest of the pipeline can
    treat .sch and .hxx identically.

    Limits (documented, not hidden):
      - One class per file is the only well-tested case. Multi-class
        .sch files extract each class but the first is the inheritance
        anchor for `superclass`.
      - Methods aren't extracted (signatures are too varied in this
        DSL); only fields and inheritance show up.
      - No nesting, no namespaces, no aliases.
    """
    path = Path(file_path)
    source = path.read_text(errors='replace')
    lines = source.splitlines()

    entities = []
    relationships = []

    class_names = _SCH_CLASS_RE.findall(source)
    if not class_names:
        return entities, relationships

    # Walk each class definition; record where it starts so we can give
    # the field declarations a parent.
    for m in _SCH_CLASS_RE.finditer(source):
        cn = m.group(1)
        start_line = source.count('\n', 0, m.start()) + 1
        entities.append(Entity(
            kind='class',
            name=cn,
            qualified_name=cn,
            parent_qname=None,
            file_path=str(path),
            start_line=start_line,
            end_line=start_line,    # we don't track block ends in .sch
        ))

    primary = class_names[0]

    # Inheritance: superclass Parent → inherits/implements edge on the
    # first class (the .sch convention is one class per file).
    for m in _SCH_SUPERCLASS_RE.finditer(source):
        parent = m.group(1)
        line_no = source.count('\n', 0, m.start()) + 1
        relationships.append(Relationship(
            source_qname=primary,
            target_name=parent,
            kind='inherits',     # global retag may upgrade to implements
            evidence_file=str(path),
            evidence_line=line_no,
            evidence_text=lines[line_no - 1].strip() if line_no <= len(lines) else '',
        ))

    # Fields: reuse classify_field_type, the same logic .hxx uses.
    for m in _SCH_FIELD_RE.finditer(source):
        type_text = m.group(1).strip()
        field_name = m.group(2)
        line_no = source.count('\n', 0, m.start()) + 1
        # Skip lines that look like method declarations slipped in.
        line_text = lines[line_no - 1] if line_no <= len(lines) else ''
        if '(' in line_text:
            continue
        entities.append(Entity(
            kind='field',
            name=field_name,
            qualified_name=f'{primary}::{field_name}',
            parent_qname=primary,
            file_path=str(path),
            start_line=line_no,
            end_line=line_no,
            signature=type_text,
        ))
        classified = classify_field_type(type_text)
        if not classified:
            continue
        rel_kind, target_short = classified
        relationships.append(Relationship(
            source_qname=primary,
            target_name=target_short,
            kind=rel_kind,
            evidence_file=str(path),
            evidence_line=line_no,
            evidence_text=line_text.strip(),
            attrs={'via_field': field_name, 'type_text': type_text,
                   'source': '.sch'},
        ))

    return entities, relationships


# Type aliases: `using X = Y;` (alias_declaration) and `typedef Y X;`
# (type_definition). Real projects route most relationships through
# aliases (spdlog's sink_ptr = shared_ptr<sinks::sink>), so we must
# resolve them or the dependency graph collapses.
_ALIAS_QUERY = Query(CPP, """
    (alias_declaration
        name: (type_identifier) @alias
        type: (type_descriptor) @target)
    (type_definition
        type: (_) @target
        declarator: (type_identifier) @alias)
""")


def extract_aliases(file_path):
    """Return {alias_short_name: target_type_text} for one file."""
    source = Path(file_path).read_bytes()
    tree = _parser.parse(source)
    aliases = {}
    for _idx, caps in QueryCursor(_ALIAS_QUERY).matches(tree.root_node):
        if 'alias' not in caps or 'target' not in caps:
            continue
        alias = caps['alias'][0].text.decode()
        target = caps['target'][0].text.decode()
        aliases[alias] = target
    return aliases


def _resolve_alias_chain(alias_map, name, _depth=0):
    """Follow an alias to its innermost class name, chasing alias→alias
    chains. Returns a class short-name, or None if it bottoms out at a
    primitive / std type. Bounded depth guards against cyclic typedefs.
    """
    if _depth > 8 or name not in alias_map:
        return None
    inner = _innermost_type_name(alias_map[name])
    if inner is None:
        return None
    if inner in alias_map:                       # alias points at another alias
        chained = _resolve_alias_chain(alias_map, inner, _depth + 1)
        return chained if chained else None
    return inner


# Directory name fragments that mark vendored / third-party code we
# don't want polluting the graph (spdlog bundles all of fmt here).
_VENDOR_DIRS = ('bundled', 'third_party', 'thirdparty', 'external',
                'vendor', 'deps', '_deps')


def _is_vendored(path, root):
    rel_parts = set(path.relative_to(root).parts)
    return any(v in rel_parts for v in _VENDOR_DIRS)


# Below this many cache-miss files, the subprocess startup overhead
# eats the parallelism win. Tuned by hand on a 4-core laptop; bigger
# scans benefit linearly, tiny ones stay serial.
_PARALLEL_THRESHOLD = 20


def parse_project(root_dir, exclude_vendored=True, cache=None, workers=None):
    """Parse every C++ file under root_dir and return
    (all_entities, all_relationships, stats) with cross-file
    target_qname resolved wherever possible.

    Resolution rule: if a relationship's target_name appears exactly
    once as the short name of some entity in the project, fill the
    edge's target_qname. Skip resolution when the name is missing
    (truly external, e.g. std types) or ambiguous (collides across
    namespaces) — the edge stays unresolved.

    exclude_vendored: skip third-party/bundled directories so their
    code doesn't pollute the dependency graph.

    cache: optional object exposing cache_get/cache_put — hits skip
    tree-sitter entirely. Cache reads/writes stay in the main process;
    workers do pure parsing.

    workers: None = auto (max(1, cpu_count - 1)); 0 or 1 = serial.
    Workers below `_PARALLEL_THRESHOLD` cache misses also stay serial
    because subprocess startup beats the parsing time on small jobs.
    """
    root = Path(root_dir)
    all_entities = []
    all_relationships = []
    alias_map = {}          # short_name → target type text (project-wide)
    skipped_vendored = 0
    cache_hits = 0
    cache_misses = 0

    # Phase A: walk the tree once, partition into (cache hits) and
    # (paths needing parse). Cache I/O stays single-threaded in main.
    misses = []        # list of (path, mtime, size)
    for path in sorted(root.rglob('*')):
        if not path.is_file() or path.suffix not in _ALL_EXTS:
            continue
        if exclude_vendored and _is_vendored(path, root):
            skipped_vendored += 1
            continue

        # cache lookup
        if cache is not None:
            try:
                st = path.stat()
                mtime, size = st.st_mtime, st.st_size
                row = cache.cache_get(path, mtime, size, PARSER_VERSION)
                if row is not None:
                    all_entities.extend(
                        Entity(**d) for d in json.loads(row['entities_json']))
                    all_relationships.extend(
                        Relationship(**d) for d in json.loads(row['relationships_json']))
                    alias_map.update(json.loads(row['aliases_json']))
                    cache_hits += 1
                    continue
                misses.append((path, mtime, size))
            except Exception:
                misses.append((path, None, None))
        else:
            misses.append((path, None, None))

    # Phase B: parse the misses, possibly in parallel.
    if workers is None:
        workers = max(1, (os.cpu_count() or 2) - 1)
    use_pool = bool(workers and workers >= 2 and len(misses) >= _PARALLEL_THRESHOLD)
    parsed = []        # list of (path, mtime, size, entities, rels, aliases)

    if use_pool:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_parse_one_file, str(p)): (p, m, s)
                       for (p, m, s) in misses}
            for fut, (p, m, s) in futures.items():
                try:
                    e, r, a = fut.result()
                    parsed.append((p, m, s, e, r, a))
                except Exception as exc:
                    print(f"  ⚠ parse failed for {p}: {exc}")
    else:
        for (p, m, s) in misses:
            try:
                e, r, a = _parse_one_file(str(p))
                parsed.append((p, m, s, e, r, a))
            except Exception as exc:
                print(f"  ⚠ parse failed for {p}: {exc}")

    # Phase C: write through to cache (main thread) and accumulate.
    for (p, m, s, e, r, a) in parsed:
        cache_misses += 1
        if cache is not None and m is not None:
            try:
                cache.cache_put(
                    p, m, s, PARSER_VERSION,
                    json.dumps([_entity_to_dict(x) for x in e]),
                    json.dumps([_rel_to_dict(x) for x in r]),
                    json.dumps(a))
            except Exception:
                pass
        all_entities.extend(e)
        all_relationships.extend(r)
        alias_map.update(a)

    # ── alias expansion: rewrite relationship targets through aliases ─
    # e.g. target_name 'sink_ptr' → alias chain → 'sink'. If an alias
    # bottoms out at a primitive (level_t = atomic<int>), the edge is
    # dropped (its target isn't a class).
    alias_expanded = 0
    alias_dropped = 0
    surviving = []
    for rel in all_relationships:
        if rel.target_qname is None and rel.target_name in alias_map:
            resolved = _resolve_alias_chain(alias_map, rel.target_name)
            if resolved is None:
                alias_dropped += 1
                continue        # alias → primitive/std: not a class edge
            rel.attrs['alias_from'] = rel.target_name
            rel.target_name = resolved
            alias_expanded += 1
        surviving.append(rel)
    all_relationships = surviving

    # ── build global short-name → qualified_name index ───
    # Only "container" entities are valid relationship targets
    # (relationships point at classes/structs/interfaces, not at
    # methods or fields). A short name that maps to multiple distinct
    # qnames is ambiguous → we don't resolve it.
    index = {}              # short_name → set of qnames
    for e in all_entities:
        if e.kind not in ('class', 'struct', 'interface'):
            continue
        index.setdefault(e.name, set()).add(e.qualified_name)

    # ── second pass: fill target_qname where unambiguous ─
    resolved_count = 0
    ambiguous_count = 0
    for rel in all_relationships:
        if rel.target_qname is not None:
            continue   # already resolved (same-file)
        candidates = index.get(rel.target_name)
        if not candidates:
            continue   # truly external (e.g. std types)
        if len(candidates) == 1:
            rel.target_qname = next(iter(candidates))
            resolved_count += 1
        else:
            ambiguous_count += 1   # leave unresolved — too risky to guess

    # ── re-tag inheritance edges using global abstractness ─
    # parse_file could only judge same-file bases. Now that entity kinds
    # are global, fix cross-file inheritance: a base that is an interface
    # or abstract base class → implements; otherwise → inherits.
    abstract_qnames = {e.qualified_name for e in all_entities
                       if e.kind == 'interface' or e.attrs.get('abstract')}
    retagged = 0
    for rel in all_relationships:
        if rel.kind not in ('inherits', 'implements'):
            continue
        if rel.target_qname is None:
            continue
        want = 'implements' if rel.target_qname in abstract_qnames else 'inherits'
        if rel.kind != want:
            rel.kind = want
            retagged += 1

    return all_entities, all_relationships, {
        'files_parsed': sum(1 for p in root.rglob('*')
                            if p.is_file() and p.suffix in _ALL_EXTS),
        'entities': len(all_entities),
        'relationships': len(all_relationships),
        'resolved_cross_file': resolved_count,
        'ambiguous_unresolved': ambiguous_count,
        'aliases_known': len(alias_map),
        'alias_edges_expanded': alias_expanded,
        'alias_edges_dropped': alias_dropped,
        'inheritance_retagged': retagged,
        'interfaces': sum(1 for e in all_entities if e.kind == 'interface'),
        'abstract_classes': sum(1 for e in all_entities
                                if e.kind != 'interface' and e.attrs.get('abstract')),
        'cache_hits': cache_hits,
        'cache_misses': cache_misses,
        'workers': workers if use_pool else 1,
    }
