"""
reader.py — C++ source file reader and structure extractor
═══════════════════════════════════════
AI concept: RAG (Retrieval-Augmented Generation) — Step 1: Retrieval
Key insight:
  - Traditional RAG uses vector DB for semantic search
  - Our "lightweight RAG": read C++ files → regex extract structure → feed only structure to AI
  - Effect: 2000-line file → extract class names/methods/members/dependencies → AI sees only key info
═══════════════════════════════════════
"""
import re
from pathlib import Path
from .config import HEADER_EXTS, IMPL_EXTS


class FileReader:
    """Read C++ source and extract structural info (RAG retrieval phase)."""

    def __init__(self, source_root):
        self.source_root = Path(source_root)

    def read_file(self, filepath):
        """Read file content with automatic encoding detection."""
        filepath = Path(filepath)
        if not filepath.exists():
            return None, 0

        for encoding in ['utf-8', 'latin-1', 'cp1252']:
            try:
                content = filepath.read_text(encoding=encoding)
                line_count = content.count('\n') + 1
                return content, line_count
            except (UnicodeDecodeError, OSError):
                continue
        return None, 0

    def find_class_files(self, class_name):
        """Find header and implementation files by class name."""
        header = None
        impl = None
        class_lower = class_name.lower()

        if not self.source_root.exists():
            return header, impl

        for f in self.source_root.rglob('*'):
            if not f.is_file():
                continue
            stem_lower = f.stem.lower()

            # Match strategy: filename contains class name, or class name contains filename
            if class_lower in stem_lower or stem_lower in class_lower:
                if f.suffix in HEADER_EXTS and header is None:
                    header = f
                elif f.suffix in IMPL_EXTS and impl is None:
                    impl = f

            if header and impl:
                break

        return header, impl

    def scan_directory(self, directory=None):
        """
        Scan directory and discover all C++ classes.

        This is the "Agent-A: scan files, identify classes" implementation.
        Results are stored in DB for downstream analysis agents.
        """
        scan_dir = Path(directory) if directory else self.source_root
        if not scan_dir.exists():
            return []

        classes = []
        seen_classes = set()

        for f in sorted(scan_dir.rglob('*')):
            if not f.is_file() or f.suffix not in HEADER_EXTS:
                continue

            content, lines = self.read_file(f)
            if not content:
                continue

            # Extract class definitions (supports inheritance, export macros)
            class_defs = re.findall(
                r'^\s*class\s+(?:__declspec\([^)]*\)\s+)?(?:[A-Z_]{2,}EXPORT\s+)?(\w+)\s*'
                r'(?::\s*(?:public|protected|private)\s+[\w:]+)?',
                content, re.MULTILINE
            )
            # Exclude forward declarations (class Foo;)
            fwd = set(re.findall(r'^\s*class\s+(\w+)\s*;', content, re.MULTILINE))
            class_defs = [cn for cn in class_defs if cn not in fwd]

            for cn in class_defs:
                if cn in seen_classes:
                    continue
                seen_classes.add(cn)

                # Find corresponding implementation file
                impl = None
                for ext in IMPL_EXTS:
                    candidate = f.with_suffix(ext)
                    if candidate.exists():
                        impl = candidate
                        break

                classes.append({
                    'class_name': cn,
                    'header': f,
                    'impl': impl,
                    'header_lines': lines,
                })

        return classes

    def extract_structure(self, content, local_stems=None):
        """
        Extract structural information from C++ code.

        RAG core: no need to feed AI entire code, just extract key structure:
        - #include dependencies → coupling assessment
        - Member variables (m_*) → state assessment
        - Method signatures → API assessment
        - Inheritance → design assessment

        local_stems: set of all header file stems in this module (lowercase), for is_local detection.
        """
        if not content:
            return {}

        # --- Basic extraction (backward-compatible string lists) ---
        raw_includes = re.findall(r'#include\s*[<"]([^>"]+)[>"]', content)
        # Forward declarations: standard C++ (class Foo;) + .sch (forward_declare class ...)
        forward_decls = re.findall(r'\bclass\s+([\w:]+)\s*;', content)
        # .sch forward_declare: add last segment (forward_declare class UGS::Plot → Plot)
        sch_fwd = re.findall(r'forward_declare\s+(?:class|struct)\s+([\w:]+)', content)
        for fqn in sch_fwd:
            short = fqn.split('::')[-1]
            if short not in forward_decls:
                forward_decls.append(short)
            # Also add each FQN segment (prevent namespace parts like UGS from being treated as classes)
            for part in fqn.split('::'):
                if part not in forward_decls:
                    forward_decls.append(part)
        raw_member_vars = list(set(re.findall(r'\b(m_\w+)\b', content)))

        # Strip comment lines (// prefix, * prefix, changelog date lines) before extracting classes
        _stripped = '\n'.join(
            line for line in content.split('\n')
            if not re.match(r'\s*//', line)
            and not re.match(r'\s*\*', line)
            and not re.match(r'\s*\d{2}-\w{3}-\d{4}', line)
        )
        classes = re.findall(
            r'(?<!\w)class\s+(?:__declspec\([^)]*\)\s+)?(?:[A-Z_]{2,}EXPORT\s+)?(\w+)',
            _stripped
        )
        # Exclude forward declarations (class Foo; — including inline namespace style)
        forward_decl_set = set(forward_decls)
        # Exclude C++ keywords, namespace names, short noise tokens
        _noise = {'class', 'struct', 'enum', 'namespace', 'public', 'protected',
                  'private', 'virtual', 'static', 'const', 'void', 'int', 'char',
                  'bool', 'double', 'float', 'long', 'short', 'unsigned',
                  'if', 'while', 'for', 'switch', 'return', 'delete', 'new',
                  'throw', 'catch', 'sizeof', 'template', 'typename'}
        classes = [c for c in classes
                   if c not in forward_decl_set
                   and c not in _noise
                   and not c.isupper()  # ALL_CAPS = macro/constant
                   and len(c) > 2  # exclude short noise like '09'
                   and c[0].isupper()  # class names start with uppercase
                   ]

        # --- Inheritance extraction (enhanced) ---
        # Match "class ChildClass : public ParentClass"
        # Also match .sch "superclass ParentClass"
        inheritance_pairs = re.findall(
            r'class\s+(?:__declspec\([^)]*\)\s+)?(?:[A-Z_]{2,}EXPORT\s+)?(\w+)\s*'
            r':\s*(?:public|protected|private)\s+([\w:]+)',
            content
        )
        # .sch superclass syntax: "superclass ParentName" inside a class block
        sch_super = re.findall(r'^\s*superclass\s+(\w+)', content, re.MULTILINE)

        raw_base_classes = [parent for _, parent in inheritance_pairs]
        raw_base_classes.extend(sch_super)

        # Rich base_classes: child→parent direction info
        base_classes_rich = []
        for child, parent in inheritance_pairs:
            # Strip namespace prefix, take last segment
            parent_short = parent.split('::')[-1]
            base_classes_rich.append({
                'name': parent_short,
                'child': child,
                'direction': 'parent',
            })
        # For .sch superclass, the child is the class defined in this file
        if sch_super and classes:
            # The main class in .sch is typically the first class found
            sch_child = classes[0] if classes else 'unknown'
            for parent in sch_super:
                base_classes_rich.append({
                    'name': parent,
                    'child': sch_child,
                    'direction': 'parent',
                })

        # --- includes enrichment (is_local detection) ---
        includes_rich = []
        for inc in raw_includes:
            inc_stem = Path(inc).stem.lower()
            is_local = False
            if local_stems:
                is_local = inc_stem in local_stems
            includes_rich.append({
                'name': inc,
                'is_local': is_local,
            })

        # --- Method signature extraction ---
        methods = re.findall(
            r'(?:virtual\s+)?(?:static\s+)?'
            r'(?:[\w:*&<>,\s]+?)\s+(\w+)\s*\([^)]*\)',
            content
        )
        keywords = {'if', 'while', 'for', 'switch', 'return', 'class',
                     'delete', 'new', 'throw', 'catch', 'sizeof'}
        methods = [m for m in methods if m not in keywords]

        # --- Full method signature extraction (with param types, for association analysis) ---
        method_signatures = re.findall(
            r'(?:virtual\s+)?(?:static\s+)?'
            r'([\w:*&<>,\s]+?)\s+(\w+)\s*\(([^)]*)\)',
            content
        )

        # --- Full member variable extraction (with type declarations, for composition/aggregation analysis) ---
        # Match: Type* m_xxx or Type m_xxx or unique_ptr<Type> m_xxx etc.
        member_var_decls = re.findall(
            r'([\w:]+(?:<[\w:,\s*&]+>)?[\s*&]*)\s+(m_\w+)',
            content
        )

        result = {
            # Backward-compatible: string lists
            'includes': raw_includes,
            'forward_decls': forward_decls,
            'member_vars': raw_member_vars,
            'classes': classes,
            'base_classes': raw_base_classes,
            'methods': methods,
            # Enhanced: structured data
            'base_classes_rich': base_classes_rich,
            'includes_rich': includes_rich,
            'method_signatures': method_signatures,
            'member_var_decls': member_var_decls,
        }

        return result

    def build_module_map(self, directory=None):
        """
        Build module-level global view — for architecture analysis.

        Returns a list where each element is a structural summary of one header file.
        Keeps all original fields (backward-compatible), with additions:
        - dependencies: dependency list classified by Lv
        - base_classes_rich: inheritance with direction info
        - includes_rich: includes with is_local flag
        """
        scan_dir = Path(directory) if directory else self.source_root
        if not scan_dir.exists():
            return []

        # Pass 1: collect all header file stems in module (for is_local detection)
        local_stems = set()
        header_files = []
        for f in sorted(scan_dir.rglob('*')):
            if f.is_file() and f.suffix in HEADER_EXTS:
                local_stems.add(f.stem.lower())
                header_files.append(f)

        # Pass 2: extract structure from each file
        file_entries = []
        for f in header_files:
            content, lines = self.read_file(f)
            if not content:
                continue

            structure = self.extract_structure(content, local_stems=local_stems)
            if not structure.get('classes'):
                continue

            file_entries.append({
                'file': f,
                'content': content,
                'lines': lines,
                'structure': structure,
            })

        # Pass 3: build global class name set (for dependency classification)
        all_classes = set()
        class_to_file = {}  # class_name → file stem (lowercase)
        for entry in file_entries:
            for cn in entry['structure']['classes']:
                all_classes.add(cn)
                class_to_file[cn] = entry['file'].stem.lower()

        # Pass 4: compute classified dependencies for each class in each file
        module_map = []
        for entry in file_entries:
            structure = entry['structure']
            f = entry['file']

            # All classes in current file
            file_classes = set(structure['classes'])

            # Compute classified dependencies for each class in this file
            per_class_deps = {}
            for cn in structure['classes']:
                deps = self._classify_dependencies(
                    cn, structure, file_classes, all_classes,
                    class_to_file, f.stem.lower(), header_path=f
                )
                per_class_deps[cn] = deps

            module_map.append({
                # Backward-compatible fields
                'file': str(f),
                'classes': structure['classes'],
                'includes': structure['includes'],
                'member_vars': structure['member_vars'],
                'methods': structure['methods'],
                'base_classes': structure['base_classes'],
                'lines': entry['lines'],
                # Enhanced fields
                'structure': structure,
                'base_classes_rich': structure['base_classes_rich'],
                'includes_rich': structure['includes_rich'],
                'dependencies': per_class_deps,
            })

        return module_map

    def _classify_dependencies(self, class_name, structure, file_classes,
                               all_classes, class_to_file, current_stem,
                               header_path=None):
        """Classify dependencies for a single class (Lv-0 to Lv-5).

        Returns list: [{"target": "ClassName", "level": "Lv-X", "source": "origin",
                         "target_is_external": bool}]
        """
        deps = []
        seen = set()  # Avoid duplicates (same target keeps strongest relationship)

        # --- Lv-5 Inheritance / Lv-2 Realization ---
        for bc in structure.get('base_classes_rich', []):
            if bc.get('child') != class_name:
                continue
            parent = bc['name']
            if parent in seen or parent in file_classes:
                # Record inheritance even for same-file base classes
                pass
            # I-prefixed names treated as interfaces (Lv-2 Realization)
            if parent.startswith('I') and len(parent) > 1 and parent[1].isupper():
                level = 'Lv-2'
                source = 'interface_impl'
            else:
                level = 'Lv-5'
                source = 'base_classes'
            deps.append({
                'target': parent,
                'level': level,
                'source': source,
                'target_is_external': parent not in all_classes,
            })
            seen.add(parent)

        # --- Lv-4 Composition / Lv-3 Aggregation / Lv-1 Association ---
        # Analyze member variable declarations referencing module-local classes
        _container_ptr_re = re.compile(
            r'(?:vector|list|set|map|array)\s*<\s*(?:[\w:]+\s*[*&]|shared_ptr|unique_ptr)')
        for type_str, var_name in structure.get('member_var_decls', []):
            for known_class in all_classes:
                if known_class == class_name or known_class in seen:
                    continue
                if known_class in type_str:
                    if 'unique_ptr' in type_str or ('*' not in type_str and 'shared_ptr' not in type_str):
                        # Value type or unique_ptr → Lv-4 Composition
                        level, source = 'Lv-4', 'member_composition'
                    elif _container_ptr_re.search(type_str):
                        # Container holding pointers (vector<X*>, list<shared_ptr<X>>) → Lv-3 Aggregation
                        level, source = 'Lv-3', 'member_container_ptr'
                    else:
                        # Raw pointer or shared_ptr → Lv-1 Association
                        level, source = 'Lv-1', 'member_ptr'
                    deps.append({
                        'target': known_class,
                        'level': level,
                        'source': source,
                        'target_is_external': False,
                    })
                    seen.add(known_class)

        # --- Lv-0 Dependency: module-local classes in method params/return types ---
        for ret_type, method_name, params in structure.get('method_signatures', []):
            sig_text = ret_type + ' ' + params
            for known_class in all_classes:
                if known_class == class_name or known_class in seen:
                    continue
                if known_class in sig_text:
                    deps.append({
                        'target': known_class,
                        'level': 'Lv-0',
                        'source': 'method_params',
                        'target_is_external': False,
                    })
                    seen.add(known_class)

        # --- Lv-0 Dependency: included but no member/param reference ---
        for inc in structure.get('includes_rich', []):
            if not inc['is_local']:
                continue
            inc_stem = Path(inc['name']).stem.lower()
            # Infer class from include filename
            for known_class in all_classes:
                if known_class == class_name or known_class in seen:
                    continue
                if known_class.lower() in inc_stem:
                    deps.append({
                        'target': known_class,
                        'level': 'Lv-0',
                        'source': 'includes',
                        'target_is_external': False,
                    })
                    seen.add(known_class)

        # --- Impl file scanning: scan .cxx to find deps not visible in header ---
        if header_path:
            for ext in IMPL_EXTS:
                impl_path = Path(header_path).with_suffix(ext)
                if impl_path.exists():
                    impl_content, _ = self.read_file(impl_path)
                    if impl_content:
                        # Extract impl file #include list
                        impl_includes = re.findall(
                            r'#include\s*[\"<](.+?)[\">]', impl_content)
                        for inc_name in impl_includes:
                            inc_stem = Path(inc_name).stem.lower()
                            for known_class in all_classes:
                                if known_class == class_name or known_class in seen:
                                    continue
                                if known_class.lower() in inc_stem:
                                    deps.append({
                                        'target': known_class,
                                        'level': 'Lv-0',
                                        'source': 'impl_include',
                                        'target_is_external': False,
                                    })
                                    seen.add(known_class)
                    break  # Only use the first matching impl file

        return deps

    def find_orchestrator(self, module_map):
        """Identify orchestrator: class with most outgoing deps and fewest incoming.

        Score = outgoing - incoming. Highest score wins.
        Returns class_name string (or None if no classes).
        """
        outgoing = {}
        incoming = {}
        for entry in module_map:
            for cn, deps in entry.get('dependencies', {}).items():
                internal = [d for d in deps if not d.get('target_is_external', False)]
                outgoing[cn] = len(internal)
                for d in internal:
                    incoming[d['target']] = incoming.get(d['target'], 0) + 1

        best = None
        best_score = -999
        for cls in outgoing:
            score = outgoing.get(cls, 0) - incoming.get(cls, 0)
            if score > best_score:
                best_score = score
                best = cls
        return best

    def get_orchestrators(self, directory=None):
        """Identify orchestrators: classes with the most dependencies on other module-local classes.

        Returns list (sorted by dep count descending):
        [{"class_name": "X", "dep_count": N, "targets": ["A", "B", ...]}]
        """
        module_map = self.build_module_map(directory)

        class_dep_counts = []
        for entry in module_map:
            for cn, deps in entry.get('dependencies', {}).items():
                # Only count cross-file dependencies (exclude same-file)
                targets = [d['target'] for d in deps]
                if targets:
                    class_dep_counts.append({
                        'class_name': cn,
                        'dep_count': len(targets),
                        'targets': targets,
                    })

        class_dep_counts.sort(key=lambda x: x['dep_count'], reverse=True)
        return class_dep_counts

    def read_class_source(self, class_name, max_impl_lines=None):
        """Read header + impl for a class, return combined source string.

        max_impl_lines: if set, truncate impl to this many lines.
        Returns (source_content: str, header_path, impl_path) — source_content
        is empty string if nothing found.
        """
        header, impl = self.find_class_files(class_name)
        parts = []
        if header:
            content, lines = self.read_file(header)
            if content:
                parts.append(f"// === Header: {header.name} ({lines} lines) ===\n{content}")
        if impl:
            content, lines = self.read_file(impl)
            if content:
                if max_impl_lines and lines > max_impl_lines:
                    preview = "\n".join(content.split("\n")[:max_impl_lines])
                    parts.append(
                        f"// === Impl: {impl.name} ({lines} lines, first {max_impl_lines}) ===\n{preview}"
                    )
                else:
                    parts.append(f"// === Impl: {impl.name} ({lines} lines) ===\n{content}")

        return "\n\n".join(parts), header, impl

    def get_file_preview(self, content, max_lines=200):
        """Get first N lines preview of a file (for large-file summary mode)."""
        lines = content.split('\n')
        preview = '\n'.join(lines[:max_lines])
        return preview, len(lines)
