"""
agents.py — Agent definitions
═══════════════════════════════════════
AI concept: Multi-Agent (collaborative agents)
Key insight:
  - Each Agent does one thing → "Keep them focused, context is your budget"
  - Agent inputs and outputs are explicit
  - Agents don't communicate directly → they collaborate via DB (shared memory)
  - Pipeline (Coordinator) decides who to call and in what order
═══════════════════════════════════════
"""
import re
from pathlib import Path

from .ts_parser import parse_project
from .workflow import build_graph, score_nodes, classify_utility, detect_style


class BaseAgent:
    """
    Agent base class — all agents share LLM and DB.

    Each agent's pattern:
    1. Read input from DB (task info)
    2. Execute its specialized operation
    3. Write results back to DB
    """

    def __init__(self, llm, db, reader=None, prompts=None):
        self.llm = llm
        self.db = db
        self.reader = reader
        self.prompts = prompts

    def run(self, *args, **kwargs):
        raise NotImplementedError

    def _parse_fields(self, response, field_patterns):
        """Generic defensive field parser.

        field_patterns: dict of {key: regex_pattern_with_one_group}
        Returns dict with matched values (empty string if not found).
        """
        result = {}
        for key, pattern in field_patterns.items():
            match = re.search(pattern, response, re.IGNORECASE)
            result[key] = match.group(1).strip() if match else ''
        return result


class ScannerAgent(BaseAgent):
    """
    Agent-A: Scan source directory, extract entities + relationships, store to DB.

    Input: source directory path
    Output: entities + relationships in the graph tables, plus a
            module_info summary row (orchestrator, style, counts).
    No LLM — pure tree-sitter + graph analysis.
    """

    def run(self, directory=None):
        print(f"\n  [ScannerAgent] Scanning with tree-sitter engine...")

        scan_dir = Path(directory) if directory else self.reader.source_root
        entities, relationships, stats = parse_project(str(scan_dir), cache=self.db)

        if not entities:
            print(f"  ⚠ No C++ entities found.")
            return 0

        # ── Orchestrator scoring + style detection ───────────
        g = build_graph(entities, relationships)
        style, note = detect_style(entities, relationships, g)
        scores = score_nodes(g)
        if scores:
            top = max(scores.items(), key=lambda kv: kv[1]['score'])
            orchestrator = top[0]
        else:
            orchestrator = None

        # ── Persist to entity-relationship tables ─────────────
        self.db.clear_graph()
        n_e = self.db.save_entities(entities)
        n_r = self.db.save_relationships(relationships)

        # Class count = how many classes/structs/interfaces we saved.
        class_count = sum(1 for e in entities
                          if e.kind in ('class', 'struct', 'interface'))

        self.db.save_module_info(
            module_name='default',
            directory=str(scan_dir),
            orchestrator=orchestrator,
            file_count=stats['files_parsed'],
            class_count=class_count,
            style=style,
            style_note=note,
        )

        print(f"  ✓ entities={n_e}  relationships={n_r}  "
              f"files={stats['files_parsed']}")
        print(f"  ✓ resolved cross-file: {stats['resolved_cross_file']}, "
              f"aliases expanded: {stats['alias_edges_expanded']}")
        print(f"  ✓ cache: {stats['cache_hits']} hits, {stats['cache_misses']} misses")
        print(f"  ✓ style: {style}" + (f" — {note[:80]}…" if note else ""))
        print(f"  ✓ top orchestrator candidate: {orchestrator}")

        # Visual sanity: a few classes with their dependencies.
        for cls in self.db.get_classes()[:12]:
            tgts = [r['target_qname'] or r['target_name']
                    for r in self.db.get_relationships(source_qname=cls['qualified_name'])]
            arrow = ' → ' + ', '.join(tgts)[:80] if tgts else ''
            hdr = Path(cls['file_path']).name if cls['file_path'] else ''
            print(f"    + {cls['qualified_name'][:32]:32s} {hdr:22s}{arrow}")
        if class_count > 12:
            print(f"    … and {class_count - 12} more")

        return class_count
