"""
pipeline.py — Multi-Agent Coordinator
═══════════════════════════════════════
AI concept: Multi-Agent Coordinator pattern
Key insight:
  - Pipeline doesn't do analysis itself → it coordinates agents
  - 2 agents: ScannerAgent (tree-sitter, no LLM) → DesignCriticAgent (LLM)
  - DB is shared memory → agents communicate indirectly via DB
  - LLMClient encapsulates call method → Pipeline doesn't care which LLM API is used
═══════════════════════════════════════
"""
from pathlib import Path

from .config import DB_PATH, OUTPUTS_DIR, SOURCE_ROOT
from .db import DBManager
from .source_io import SourceReader
from .llm import LLMClient
from .agents import ScannerAgent
from .design_critic import DesignCriticAgent


class Pipeline:
    """
    Multi-Agent Coordinator — the pipeline's "brain".

    Responsibilities:
    1. Initialize agents (inject LLM and DB into each)
    2. Respond to CLI commands, dispatch to corresponding agents
    3. Manage project lifecycle (init → scan → critic → report)
    """

    def __init__(self, source_root=None):
        # Shared resources
        self.db = DBManager(DB_PATH)
        self.reader = SourceReader(source_root or SOURCE_ROOT, db=self.db)
        self.llm = LLMClient(cache=self.db)

        self.scanner = ScannerAgent(llm=self.llm, db=self.db,
                                    reader=self.reader)
        self.critic = DesignCriticAgent(llm=self.llm, db=self.db,
                                        reader=self.reader)

    # ─── Project initialization ─────────────────────────────────────────

    def init_project(self):
        """Initialize project: create directories, reset DB."""
        OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

        if DB_PATH.exists():
            DB_PATH.unlink()
        self.db = DBManager(DB_PATH)
        self.db.ensure_tables()

        for agent in (self.scanner, self.critic):
            agent.db = self.db

        print(f"  ✓ Project initialized (DB reset)")
        print(f"    DB:      {DB_PATH}")
        print(f"    Outputs: {OUTPUTS_DIR}")
        print(f"    Source:  {SOURCE_ROOT}")

    # ─── Steps ──────────────────────────────────────────────────────────

    def _scan_source(self, directory=None):
        """Step 1: Scan source directory, build the entity-relationship graph."""
        scan_dir = directory or SOURCE_ROOT
        print(f"\n  Scanning directory: {scan_dir}")
        count = self.scanner.run(scan_dir)
        if count == 0:
            print(f"    Note: No C++ classes found.")

    # ─── Progress dashboard ──────────────────────────────────────────

    def show_status(self):
        """Show progress dashboard."""
        stats = self.db.get_stats()
        tasks = self.db.get_classes()
        mi = self.db.get_module_info()
        orchestrator = mi['orchestrator'] if mi else None
        kid_rows = self.db._query_all(
            "SELECT parent_qname, kind, COUNT(*) AS n FROM entities "
            "WHERE parent_qname IS NOT NULL GROUP BY parent_qname, kind")
        method_counts = {r['parent_qname']: r['n'] for r in kid_rows if r['kind'] == 'method'}
        field_counts = {r['parent_qname']: r['n'] for r in kid_rows if r['kind'] == 'field'}

        if not stats or stats['total'] == 0:
            print("\n  Database is empty. Run init + analyze first.")
            return

        total = stats['total'] or 0
        analyzed = stats['analyzed'] or 0

        print(f"""
╔══════════════════════════════════════════════════════════╗
║  CodeProbe — Progress Dashboard                          ║
╠══════════════════════════════════════════════════════════╣
║  Classes: {total:<5}  Critic subtrees analyzed: {analyzed:<5}        ║""")

        print(f"╠══════════════════════════════════════════════════════════╣")
        print(f"║  {'Class':<28} {'Methods':>7} {'Members':>7} {'Orch':>5}   ║")
        print(f"║  {'─'*28} {'─'*7} {'─'*7} {'─'*5}   ║")

        for t in tasks:
            qname = t['qualified_name']
            name = qname[:28]
            methods = str(method_counts.get(qname, 0))
            members = str(field_counts.get(qname, 0))
            orch = '★' if qname == orchestrator else '·'
            print(f"║  {name:<28} {methods:>7} {members:>7} {orch:>5}   ║")

        print(f"╚══════════════════════════════════════════════════════════╝")

        if mi:
            print(f"\n  Scan: done ({mi['class_count']} classes, "
                  f"orchestrator={mi['orchestrator'] or '—'}, "
                  f"style={mi['style'] or 'oop'})")
            module = self.db.get_design_module()
            print(f"  Design review: {'done' if module else 'pending'}")

    # ─── Unified orchestration interface ─────────────────────────────────

    def run_full_analysis(self, path, from_step=None):
        """Full pipeline: scan → design review.
        Supports incremental execution: skip steps with existing DB results.
        from_step: re-run from a specific step (scan / review).
        """
        path = Path(path).resolve()

        if path.is_file():
            directory = str(path.parent)
        elif path.is_dir():
            directory = str(path)
        else:
            print(f"  Error: path not found: {path}")
            return False

        self.db.ensure_tables()

        if from_step and not self._reset_from_step(from_step):
            # Invalid --from value: abort loudly. Continuing would skip
            # both steps as "already exists" and end with a summary that
            # looks like a successful re-run the user never asked for.
            return False

        # === Step 1: Scan ===
        print(f"\n{'='*60}")
        print(f"  Step 1/2: Scanning source files...")
        print(f"{'='*60}")

        existing = self.db.get_classes()
        if existing:
            print(f"  [skip] Already have {len(existing)} classes in DB")
        else:
            self._scan_source(directory)
            existing = self.db.get_classes()
            if not existing:
                print("  Error: No classes found after scanning.")
                return False

        # === Step 2: Holistic design review ===
        print(f"\n{'='*60}")
        print(f"  Step 2/2: Holistic design review...")
        print(f"{'='*60}")

        if self.db.get_design_module():
            print(f"  [skip] Design review already exists "
                  f"(use --from=review to re-run)")
        elif not self.critic.run():
            return False

        self._print_summary()
        return True

    def generate_report(self):
        """Generate HTML visualization report."""
        from .report import generate_html_report
        output_path = OUTPUTS_DIR / "report.html"
        result = generate_html_report(self.db, output_path)
        print(f"\n  ✓ HTML report generated: {result}")
        print(f"    Open in browser to view.")

    # ─── Internal helpers ─────────────────────────────────────────────

    def _print_summary(self):
        """Print analysis results summary."""
        print(f"\n{'='*60}")
        print(f"  Analysis Summary")
        print(f"{'='*60}")

        mi = self.db.get_module_info()
        if not mi:
            print("  No analysis results yet.")
            return

        print(f"\n  Module: {mi['module_name']}")
        print(f"    Orchestrator: {mi['orchestrator'] or '—'}")
        print(f"    Classes: {mi['class_count']}")
        if mi['style'] and mi['style'] != 'oop':
            print(f"    ⚠ Style: {mi['style']} — {mi['style_note'] or ''}")

        deps = self.db.get_relationships()
        if deps:
            print(f"    Dependencies: {len(deps)} relationships")

        module = self.db.get_design_module()
        if module:
            import json
            parsed = json.loads(module['parsed_json']) if module['parsed_json'] else {}
            recs = parsed.get('recommendations', [])
            if recs:
                print(f"\n  Top recommendations:")
                for r in recs[:5]:
                    print(f"    [{r.get('priority','?'):6s}] {r.get('title') or r.get('target','')}")

        print(f"\n  Reports: {OUTPUTS_DIR}  (run: python run.py report)")

    def _reset_from_step(self, step):
        """Delete DB records for the given step onward, for --from restart."""
        VALID = {'scan', 'review'}
        if step not in VALID:
            print(f"  Error: --from must be one of: {', '.join(sorted(VALID))}")
            return False

        if step == 'scan':
            self.db.delete_design_critic()
            self.db.delete_all_tasks()
            print("  [reset] Cleared all data (scan + review)")
        elif step == 'review':
            self.db.delete_design_critic()
            print("  [reset] Cleared design review")
        return True
