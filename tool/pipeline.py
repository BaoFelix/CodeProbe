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
        from .architect import ArchitectReviewer
        self.arch_reviewer = ArchitectReviewer(llm=self.llm, db=self.db)

    # ─── Project initialization ─────────────────────────────────────────

    def init_project(self):
        """Initialize project: create directories, reset DB."""
        OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

        if DB_PATH.exists():
            DB_PATH.unlink()
        self.db = DBManager(DB_PATH)
        self.db.ensure_tables()

        for agent in (self.scanner, self.critic, self.arch_reviewer):
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
        """Architecture-first pipeline:
          1. Scan sources into the graph.
          2. Deterministic architecture audit + decoupling plans (persisted).
          3. Architecture-level LLM review (grounded, concurrent per module).
        Class-level design review is now on-demand (the `design_review`
        tool), not part of the default run.
        from_step re-runs from: scan / review (review = step 3 onward).
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
            return False

        # === Step 1: Scan ===
        print(f"\n{'='*60}\n  Step 1/3: Scanning source files...\n{'='*60}")
        existing = self.db.get_classes()
        if existing:
            print(f"  [skip] Already have {len(existing)} classes in DB")
        else:
            self._scan_source(directory)
            existing = self.db.get_classes()
            if not existing:
                print("  Error: No classes found after scanning.")
                return False

        # === Step 2: Deterministic architecture audit (no LLM key needed) ==
        print(f"\n{'='*60}\n  Step 2/3: Architecture audit + decoupling...\n{'='*60}")
        self._run_architecture_audit()

        # === Step 3: Architecture-level LLM review (grounded) ===
        print(f"\n{'='*60}\n  Step 3/3: Architecture review (grounded)...\n{'='*60}")
        if self.db.get_arch_module_reviews():
            print("  [skip] Architecture review exists (use --from=review "
                  "to re-run)")
        else:
            self.arch_reviewer.run()      # degrades gracefully without a key

        self._print_summary()
        return True

    def _run_architecture_audit(self):
        """Step 2: deterministic module audit + decoupling plans → DB.
        No LLM; the report and Step 3 read what this persists."""
        from .architect import (run_architecture_audit, plan_decoupling,
                                audit_payload)
        from .db import graph_fingerprint
        classes = [dict(r) for r in self.db.get_classes()]
        rels = [dict(r) for r in self.db.get_relationships()]
        findings, mg = run_architecture_audit(classes, rels)
        unresolved_pct = (round(100.0 * sum(1 for r in rels
                                            if not r["target_qname"]) / len(rels), 1)
                          if rels else None)
        payload = audit_payload(findings, mg, plan_decoupling(mg), unresolved_pct)
        self.db.save_arch_audit(payload, graph_hash=graph_fingerprint(rels))
        print(f"  ✓ {payload['module_count']} modules, "
              f"{len(payload['findings'])} finding(s), "
              f"{len(payload['decoupling'])} decoupling plan(s)")

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
            self.db.delete_arch()
            self.db.delete_all_tasks()
            print("  [reset] Cleared all data (scan + review)")
        elif step == 'review':
            # Step 3 is the architecture review; clear it (Tier-1 per-module
            # + Tier-2 conclusion) so it re-runs. The deterministic audit
            # (Step 2) always regenerates anyway.
            self.db.delete_arch_reviews()
            print("  [reset] Cleared architecture review")
        return True
