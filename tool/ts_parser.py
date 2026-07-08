"""
ts_parser.py — the C++ parsing engine, built on tree-sitter.

WHAT THIS FILE DOES
  Turns raw C++ source files into two lists:
    entities      — every named thing: namespace / class / struct /
                    interface / method / field
    relationships — who uses whom, in 6 kinds from weak to strong:
                    depends(0) < associates(1) < implements(2)
                    < aggregates(3) < composes(4) < inherits(5)

WHY TREE-SITTER (the key technology choice)
  - vs regex:    regex can't see nesting, comments, strings, templates.
                 We replaced a 544-line regex reader with this engine
                 and accuracy problems disappeared.
  - vs libclang: clang is more precise BUT requires code that compiles
                 (all headers present, all macros resolvable). Real
                 users hand us partial code — a few .cxx files without
                 their .hxx. tree-sitter parses anything and never
                 gives up; that robustness is worth more than clang's
                 extra precision for an architecture-level tool.

HOW IT'S ORGANIZED (top to bottom)
  1. Queries        — tree-sitter S-expression patterns that find
                      class/method/field/inheritance nodes in the CST.
  2. Type analysis  — classify_field_type() maps a field's type text
                      to a relationship kind (unique_ptr<X> = composes,
                      vector<X*> = aggregates, X* = associates ...).
  3. parse_file()   — single-file pass: entities + relationships,
                      same-file targets resolved immediately.
  4. parse_sch_file() — regex fallback for the .sch private DSL
                      (tree-sitter has no grammar for it; the DSL is
                      simple enough that regex is the right tool here).
  5. parse_project() — whole-directory pass: cache lookup, parallel
                      parsing, alias expansion, phantom-class
                      promotion, cross-file target resolution,
                      interface re-tagging.

REAL-WORLD HARDENING (each of these came from a bug found on a real
codebase — spdlog, OpenCASCADE, Eigen, or Siemens Simcenter):
  - export macros   (class SPDLOG_API logger) stripped before parsing
  - typedef/using   aliases expanded so vector<sink_ptr> reaches sink
  - templated bases (class X : Base<T>) captured in inheritance
  - out-of-line     (void Foo::bar(){...}) methods attributed to Foo
  - phantom classes promoted when only the .cxx is in scope
  - vendored dirs   (third_party/, bundled/...) skipped by default
  - '= 0' disambiguation: pure-virtual vs default arg vs body code

PERFORMANCE
  - parse cache: (mtime, size, PARSER_VERSION) fingerprint per file;
    a re-scan of an unchanged tree is ~22x faster. We deliberately do
    NOT hash file content — hashing requires reading the file, which
    is most of what we were trying to skip.
  - multiprocess: cache misses are parsed in parallel across CPU
    cores (cache I/O stays in the main process because SQLite
    connections can't cross process boundaries).
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
# A method's function_declarator is NOT always a direct child of its
# declaration: a pointer/reference RETURN TYPE (`Graph* Foo::bar()`,
# `Widget& Foo::ref()`, `Thing** Foo::pp()`) wraps it in one or more
# pointer_declarator / reference_declarator nodes. Missing these silently
# drops every pointer-returning method — extremely common in real C++
# (factories, accessors) — losing its signature AND its body-call edges.
# So each of the three outer forms is matched at the direct, *, ** and &
# nesting levels.
def _method_query():
    outers = ('field_declaration', 'declaration', 'function_definition')
    wraps = (
        '(function_declarator declarator: (_) @name)',
        '(pointer_declarator (function_declarator declarator: (_) @name))',
        '(pointer_declarator (pointer_declarator '
            '(function_declarator declarator: (_) @name)))',
        '(reference_declarator (function_declarator declarator: (_) @name))',
    )
    return Query(CPP, "\n".join(
        f"({o} {w}) @decl" for o in outers for w in wraps))


_METHOD_IN_CLASS_QUERY = _method_query()

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


def _inside_function_body(node):
    """True when `node` sits inside some function's compound_statement —
    i.e. it's a local, not a class member. Walk stops at the first
    container so an out-of-line definition itself (whose parents are
    namespace/translation_unit) is NOT flagged."""
    cur = node.parent
    while cur is not None:
        if cur.type == 'compound_statement':
            return True
        if cur.type in ('class_specifier', 'struct_specifier',
                        'namespace_definition', 'translation_unit'):
            return False
        cur = cur.parent
    return False


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


# Everything a body-mined name must NOT be. Broader than the signature
# filter: bodies also mention containers/smart-pointers as *values*
# (`std::vector<X> tmp;`) where only X is the real coupling.
_BODY_NOISE = (_PRIMITIVES | _KEYWORDS | _STD_NOISE
               | set(_CONTAINER_NAMES) | set(_SMART_PTRS) | {'std'})


def _body_type_names(body):
    """Mine USER-TYPE references from a method body (compound_statement).

    Why not just reuse _type_names_in on the whole body: a body is mostly
    identifiers that are NOT type references (locals, member calls,
    function names — `Foo::Create()` would yield `Create`). And coupling
    through member fields (`m_engine->Start()`) is already captured by the
    field edges. So we cherry-pick the only places where a body introduces
    a coupling the class doesn't already declare:

        · local declarations      Foo f;   Foo* p = ...;
        · new expressions         new Foo(...)
        · casts / sizeof          static_cast<Foo*>(x), (Foo)y
        · scope access            Foo::Create(), Foo::CONSTANT   ← the SCOPE
        · template arguments      std::make_shared<Foo>(...)

    Yields (name, row) pairs — row is the 0-based source line of the
    reference, so each edge carries precise evidence.
    """
    def _emit(names_iter, node):
        row = node.start_point[0]
        for n in names_iter:
            if n not in _BODY_NOISE:
                yield n, row

    def _walk(node):
        t = node.type
        if t in ('declaration', 'new_expression'):
            ty = node.child_by_field_name('type')
            if ty is not None:
                yield from _emit(_type_names_in(ty), ty)
            for c in node.children:            # initializer may nest more
                if c is not ty:
                    yield from _walk(c)
            return
        if t in ('type_descriptor', 'template_argument_list'):
            # casts, sizeof, and explicit template args all wrap their
            # types in these two node kinds.
            yield from _emit(_type_names_in(node), node)
            return
        if t == 'qualified_identifier':
            # `A::Foo::Create` — the innermost SCOPE segment (`Foo`) is the
            # type being used; the last segment is a member name, skip it.
            segs = node.text.decode().split('::')
            if len(segs) >= 2:
                scope = segs[-2].split('<')[0].strip()
                if scope and scope not in _BODY_NOISE:
                    yield scope, node.start_point[0]
            for c in node.children:            # make_shared<Foo> template args
                yield from _walk(c)
            return
        for c in node.children:
            yield from _walk(c)

    yield from _walk(body)


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
    # If there's no enclosing class (free function), we skip it — UNLESS
    # the declarator name is itself qualified (`void Foo::bar() {…}`,
    # i.e. an out-of-line method definition). That pattern dominates
    # .cxx files: classes are declared in .hxx and their methods are
    # defined here. We split the qualified name to recover parent + short.
    for _idx, caps in QueryCursor(_METHOD_IN_CLASS_QUERY).matches(root):
        decl = caps['decl'][0]
        if _inside_function_body(decl):
            # A declaration nested in some method's body is a LOCAL
            # (`Widget w(x);`), not a method of the enclosing class —
            # without this guard it becomes a bogus method entity.
            continue
        name_node = caps['name'][0]
        raw_name = name_node.text.decode()
        if ' ' in raw_name and 'operator' not in raw_name:
            # A real declarator name never contains a space (except
            # `operator new` etc.). Template free functions like
            # `template<..> SPDLOG_INLINE std::shared_ptr<X> make(...)`
            # can mis-parse into a name spanning the return type — on
            # spdlog that fabricated methods under a phantom class 'std'
            # that then out-scored the real orchestrator.
            continue
        parent_qname = _enclosing_container_qname(
            decl, ('class_specifier', 'struct_specifier'))
        if parent_qname is None:
            # Out-of-line definition: name carries Class::method (or
            # NS::Class::method). Otherwise it's a true free function — skip.
            if '::' not in raw_name:
                continue
            parts = raw_name.split('::')
            short_name = parts[-1]
            # Compose parent from any enclosing namespace + the prefix of raw_name.
            ns_parent = _enclosing_container_qname(decl, ('namespace_definition',))
            inferred_parent = '::'.join(parts[:-1])
            full_parent = (f'{ns_parent}::{inferred_parent}'
                           if ns_parent else inferred_parent)
            qualified_name = f'{full_parent}::{short_name}'
        else:
            short_name = raw_name.split('::')[-1]  # in case of nested ctor
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
    # target resolution. A short name declared TWICE in one file
    # (e.g. A::Handler and B::Handler) is ambiguous — binding to
    # whichever class was parsed last would silently attach edges to
    # the wrong type, so collisions resolve to nothing here and the
    # global pass (which applies the same only-if-unique rule) decides.
    _sf_names = {}
    for e in entities:
        if e.kind in ('class', 'struct', 'interface'):
            _sf_names.setdefault(e.name, set()).add(e.qualified_name)
    same_file_index = {name: next(iter(qns))
                       for name, qns in _sf_names.items() if len(qns) == 1}
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
        if _inside_function_body(decl):
            continue          # locals are body-call territory, not signatures
        name_node = caps['name'][0]
        raw_name = name_node.text.decode()
        if ' ' in raw_name and 'operator' not in raw_name:
            continue          # mis-parsed declarator (see entity pass)
        in_class = any(a in ('class_specifier', 'struct_specifier')
                       for a in _ancestor_types(decl))
        if in_class:
            parent = _enclosing_container_qname(decl, _CONTAINER_TYPES)
        elif '::' in raw_name:
            # Out-of-line definition `void Foo::bar() {…}` — recover the
            # class from the qualified declarator (same rule as the
            # entity pass), so phantom classes participate in depends
            # edges exactly as if their .hxx were in scope.
            parts = raw_name.split('::')
            ns_parent = _enclosing_container_qname(
                decl, ('namespace_definition',))
            inferred = '::'.join(parts[:-1])
            parent = f'{ns_parent}::{inferred}' if ns_parent else inferred
        else:
            continue   # true free function
        if parent is None:
            continue

        # Mine names from the SIGNATURE only — return type and parameter
        # list. Skip the function body (compound_statement): in a .cxx
        # function_definition the body is full of call expressions and
        # local variables whose identifiers are not type references.
        # Also exclude the declarator's own name segments: in an
        # out-of-line definition `void Foo::bar(...)` the qualified
        # declarator would otherwise be mined as a bogus dependency.
        own_segments = set(raw_name.split('::'))
        sig_names = set()
        for child in decl.children:
            if child.type == 'compound_statement':
                continue
            sig_names.update(_type_names_in(child))
        for name in sig_names - own_segments:
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

        # ── body-call edges ──────────────────────────────────
        # Signatures miss coupling that lives only inside the body
        # (locals, statics, news, casts) — exactly what matters when
        # judging how expensive an edge is to cut. Same dedupe set:
        # if the signature already declared the type, one edge is enough.
        body = next((c for c in decl.children
                     if c.type == 'compound_statement'), None)
        if body is not None:
            for name, row in _body_type_names(body):
                if name in own_segments:
                    continue
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
                    evidence_line=row + 1,
                    evidence_text=_line_text(source, row),
                    attrs={'via': 'body_call'},
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
# v4: body-call depends edges (locals / news / casts / scope access).
# v5: locals-in-bodies no longer become method entities; same-file
#     short-name collisions resolve to nothing instead of last-wins;
#     alias expansion re-judges the ownership kind.
# v6: reject mis-parsed declarator names containing spaces (template free
#     functions were fabricating methods under a phantom 'std' class).
# v7: merge bare vs namespaced duplicate classes (.cxx `using namespace`
#     out-of-line methods / .sch schemas) into their namespaced counterpart.
# v8: match pointer/reference-returning methods (Graph* Foo::bar()) —
#     their function_declarator is wrapped in pointer/reference_declarator,
#     so signatures + body-call edges were being dropped for all of them.
PARSER_VERSION = 9


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


def _alias_kind(alias_map, name, _depth=0):
    """The ownership kind hidden behind an alias: walk the chain and let
    the OUTERMOST wrapper decide (`using P = FooPtr; using FooPtr = Foo*`
    → associates). A bare-name hop (`using V = Foo`) carries no wrapper
    signal, so keep walking; returns None when the whole chain is bare —
    the caller's original judgement then stands."""
    if _depth > 8 or name not in alias_map:
        return None
    text = alias_map[name].strip()
    bare = ('<' not in text and not text.rstrip().endswith('*')
            and '&' not in text)
    if not bare:
        judged = classify_field_type(text)
        return judged[0] if judged else None
    return _alias_kind(alias_map, text.split('::')[-1], _depth + 1)


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

    # ── phantom classes: out-of-line method definitions reference a
    # class declared in a .hxx we may not have seen (very common when
    # someone shares only .cxx files). Materialize placeholder class
    # entities for those parent_qnames so the rest of the graph can
    # treat them as first-class participants.
    # Scanned namespaces are already in known_qnames (the namespace
    # entity carries that qualified_name), so `void util::init()` never
    # promotes a phantom when `namespace util` appears ANYWHERE in the
    # scanned set. When it appears nowhere, "method of unseen class" vs
    # "free function in unseen namespace" is genuinely undecidable from
    # one .cxx — we promote, and the phantom flag + report exclusion
    # bound the damage. (An earlier comment promised a smarter guard;
    # there is no decidable one, so this states the honest rule.)
    known_qnames = {e.qualified_name for e in all_entities}
    needed = {}
    for e in all_entities:
        if e.kind not in ('method', 'field') or not e.parent_qname:
            continue
        if e.parent_qname in known_qnames:
            continue
        needed.setdefault(e.parent_qname, e.file_path)
    for pqname, file_path in needed.items():
        all_entities.append(Entity(
            kind='class',
            name=pqname.split('::')[-1],
            qualified_name=pqname,
            parent_qname='::'.join(pqname.split('::')[:-1]) or None,
            file_path=file_path,
            start_line=0, end_line=0,
            attrs={'phantom': True,
                   'reason': 'declaration not seen, inferred from out-of-line methods'},
        ))

    # ── deduplicate bare vs fully-qualified entities ────────────
    # The SAME entity is often parsed twice: fully-qualified from its .hxx
    # (UGS::SimulationPost::Foo, or ...Foo::bar for a method), and BARE from
    # a .cxx out-of-line definition written `Foo::bar` / `Outer::Inner`
    # without the enclosing namespace (or from a .sch schema). Left
    # unmerged: bare CLASSES form a spurious "(root)" module, bare NESTED
    # classes look like separate top-scope types, and every METHOD
    # double-counts (declaration + out-of-line definition under different
    # qualified names). Merge each bare entity into its UNIQUE
    # fully-qualified counterpart of the same kind — one whose qualified
    # name has more :: segments and ends with exactly the bare name.
    class_kinds = ('class', 'struct', 'interface')
    mergeable = class_kinds + ('method', 'field')

    # index full names by (kind, last-segment) for cheap suffix lookup.
    by_tail = {}
    for e in all_entities:
        if e.kind in mergeable:
            by_tail.setdefault((e.kind, e.qualified_name.split('::')[-1]),
                               set()).add(e.qualified_name)

    rename = {}                       # bare qualified_name -> full qualified_name
    for e in all_entities:
        if e.kind not in mergeable:
            continue
        q = e.qualified_name
        qs = q.split('::')
        cands = [fq for fq in by_tail.get((e.kind, qs[-1]), ())
                 if fq != q and fq.split('::')[-len(qs):] == qs]
        if len(set(cands)) == 1:
            rename[q] = cands[0]
    # collapse chains (bare -> mid -> full) so everything lands on the tip.
    for k in list(rename):
        seen = {k}
        v = rename[k]
        while v in rename and v not in seen:
            seen.add(v)
            v = rename[v]
        rename[k] = v

    if rename:
        for e in all_entities:        # rewrite own name + parent link
            if e.qualified_name in rename:
                e.qualified_name = rename[e.qualified_name]
            if e.parent_qname in rename:
                e.parent_qname = rename[e.parent_qname]
        for r in all_relationships:   # redirect edges onto the real node
            if r.source_qname in rename:
                r.source_qname = rename[r.source_qname]
            if r.target_qname in rename:
                r.target_qname = rename[r.target_qname]

        # Collapse entities that now share (qualified_name, kind) — the .hxx
        # declaration and the .cxx out-of-line definition. Keep the richest
        # copy: a REAL beats a phantom, then the LARGEST line span (the
        # out-of-line body, so get_source still returns the implementation).
        def _rank(e):
            span = (e.end_line or 0) - (e.start_line or 0)
            return (0 if (e.attrs or {}).get('phantom') else 1, span)
        best = {}
        for e in all_entities:
            key = (e.qualified_name, e.kind)
            if key not in best or _rank(e) > _rank(best[key]):
                best[key] = e
        # a survivor is REAL if ANY of its copies was real
        real_keys = {(e.qualified_name, e.kind) for e in all_entities
                     if not (e.attrs or {}).get('phantom')}
        for key, e in best.items():
            if key in real_keys and (e.attrs or {}).get('phantom'):
                e.attrs.pop('phantom', None)
                e.attrs.pop('reason', None)
        deduped_removed = len(all_entities) - len(best)
        all_entities = list(best.values())

        seen_edges, merged = set(), []
        for r in all_relationships:   # the two copies may share edges
            key = (r.source_qname, r.target_qname or r.target_name, r.kind,
                   r.evidence_file, r.evidence_line)
            if key in seen_edges:
                continue
            seen_edges.add(key)
            merged.append(r)
        all_relationships = merged
    else:
        deduped_removed = 0

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
            # Re-judge the ownership KIND from the alias's real type text.
            # classify_field_type originally saw only the bare alias name
            # (`sink_ptr m_sink;` → composes by default), but the alias may
            # hide a wrapper: `using sink_ptr = shared_ptr<sink>` is
            # associates, not composes. Field-kind edges only — a `depends`
            # from a signature stays a depends whatever the alias wraps.
            if rel.kind in ('composes', 'aggregates', 'associates'):
                new_kind = _alias_kind(alias_map, rel.attrs['alias_from'])
                if new_kind and new_kind != rel.kind:
                    rel.kind = new_kind
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
        # Files that actually went through the parser (cache hit or miss)
        # — NOT a re-walk of the tree, which would count vendored-skipped
        # and parse-failed files as parsed.
        'files_parsed': cache_hits + cache_misses,
        'skipped_vendored': skipped_vendored,
        'deduped_bare': deduped_removed,
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
