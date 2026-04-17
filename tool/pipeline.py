"""
pipeline.py — Multi-Agent Coordinator
═══════════════════════════════════════
AI concept: Multi-Agent Coordinator pattern
Key insight:
  - Pipeline doesn't do analysis itself → it coordinates agents
  - 3 agents: ScannerAgent → ResponsibilityAgent → DesignAgent
  - DB is shared memory → agents communicate indirectly via DB
  - LLMClient encapsulates call method → Pipeline doesn't care which LLM API is used
═══════════════════════════════════════
"""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

from .config import (
    PROJECT_ROOT, DB_PATH, OUTPUTS_DIR,
    SOURCE_ROOT, DEFAULT_MODULE
)
from .report import generate_focus_report
from .db import DBManager
from .reader import FileReader
from .prompts import PromptBuilder
from .llm import LLMClient
from .agents import ScannerAgent, ResponsibilityAgent, DesignAgent


class Pipeline:
    """
    Multi-Agent Coordinator — the pipeline's "brain".

    Responsibilities:
    1. Initialize agents (inject LLM and DB into each)
    2. Respond to CLI commands, dispatch to corresponding agents
    3. Manage project lifecycle (init → scan → resp → design)

    Does NOT:
    - Read files (delegated to agent's reader)
    - Call LLM (delegated to agent's llm)
    - Parse results (delegated to agent's parser)
    """

    def __init__(self, source_root=None):
        # Shared resources
        self.db = DBManager(DB_PATH)
        self.reader = FileReader(source_root or SOURCE_ROOT)
        self.prompts = self._init_prompt_builder()
        self.llm = LLMClient()

        # 3 agents — each shares the same LLM and DB
        self.scanner = ScannerAgent(
            llm=self.llm, db=self.db,
            reader=self.reader, prompts=self.prompts
        )
        self.resp_agent = ResponsibilityAgent(
            llm=self.llm, db=self.db,
            reader=self.reader, prompts=self.prompts
        )
        self.design_agent = DesignAgent(
            llm=self.llm, db=self.db,
            reader=self.reader, prompts=self.prompts
        )

    def _init_prompt_builder(self):
        """Load universal skills, inject into prompts."""
        universal_skills = {}
        skills_dir = PROJECT_ROOT / "skills" / "universal"
        if skills_dir.exists():
            for f in sorted(skills_dir.glob("*.md")):
                try:
                    universal_skills[f.stem] = f.read_text(encoding='utf-8')
                except OSError:
                    pass
        return PromptBuilder(universal_skills=universal_skills)

    # ─── Project initialization ─────────────────────────────────────────

    def init_project(self):
        """Initialize project: create directories, reset DB."""
        OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

        # Delete old DB, recreate tables
        if DB_PATH.exists():
            DB_PATH.unlink()
        self.db = DBManager(DB_PATH)
        self.db.ensure_tables()

        # Update all agents' DB references
        for agent in [self.scanner, self.resp_agent, self.design_agent]:
            agent.db = self.db

        print(f"  ✓ Project initialized (DB reset)")
        print(f"    DB:      {DB_PATH}")
        print(f"    Outputs: {OUTPUTS_DIR}")
        print(f"    Source:  {SOURCE_ROOT}")

    # ─── Internal steps (dispatched by run_full_analysis) ──────────────────

    def _scan_source(self, directory=None):
        """Step 1: Scan source directory, register all C++ classes to DB."""
        scan_dir = directory or SOURCE_ROOT
        print(f"\n  Scanning directory: {scan_dir}")
        count = self.scanner.run(scan_dir)
        if count == 0:
            print(f"    Note: No C++ classes found.")

    def _resp_analyze_all(self):
        """Step 2: Per-class responsibility analysis (parallel).
        Context comes from DB (dependencies, module info)."""
        classes = self.db.get_all_tasks()

        to_analyze = []
        for cls in classes:
            cn = cls['class_name']
            existing = self.db.get_responsibility(cn)
            if existing:
                print(f"  [skip] {cn}")
            else:
                to_analyze.append(cn)

        if not to_analyze:
            print("  [skip] All classes already analyzed")
            return True

        print(f"  Analyzing {len(to_analyze)} classes (parallel, max 3 workers)...")
        max_workers = min(3, len(to_analyze))
        failed = []

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_cn = {
                executor.submit(self.resp_agent.run, cn): cn
                for cn in to_analyze
            }
            for future in as_completed(future_to_cn):
                cn = future_to_cn[future]
                try:
                    result = future.result()
                    if not result:
                        failed.append(cn)
                except Exception as e:
                    print(f"  Error: {cn} raised {e}")
                    failed.append(cn)

        if failed:
            print(f"  Error: Responsibility analysis failed for {', '.join(failed)}")
            return False
        return True

    def _design_propose(self):
        """Step 3: Design proposal — based on responsibility analysis."""
        print(f"\n{'='*60}")
        print(f"  Design Proposal")
        print(f"{'='*60}")
        return self.design_agent.run()

    # ─── Progress dashboard ──────────────────────────────────────────

    def show_status(self):
        """Show progress dashboard."""
        stats = self.db.get_stats()
        tasks = self.db.get_all_tasks()

        if not stats or stats['total'] == 0:
            print("\n  Database is empty. Run init + scan first.")
            return

        total = stats['total'] or 0
        analyzed = stats['analyzed'] or 0
        pending = stats['pending'] or 0
        pct = analyzed * 100 // total if total else 0

        print(f"""
╔══════════════════════════════════════════════════════════╗
║  CodeProbe — Progress Dashboard                          ║
╠══════════════════════════════════════════════════════════╣
║  Total:   {total:<5}  Analyzed: {analyzed:<5} ({pct}%)  Pending: {pending:<5}  ║""")

        print(f"╠══════════════════════════════════════════════════════════╣")
        print(f"║  {'Class':<28} {'Methods':>7} {'Members':>7} {'Orch':>5}   ║")
        print(f"║  {'─'*28} {'─'*7} {'─'*7} {'─'*5}   ║")

        for t in tasks:
            name = t['class_name'][:28]
            methods = str(t['method_count'] or 0)
            members = str(t['member_count'] or 0)
            orch = '★' if t['is_orchestrator'] else '·'
            print(f"║  {name:<28} {methods:>7} {members:>7} {orch:>5}   ║")

        print(f"╚══════════════════════════════════════════════════════════╝")

        # Pipeline progress
        module_info = self.db.get_module_info()
        print(f"\n  Pipeline Progress:")

        if module_info:
            print(f"    Scan:           done ({module_info['class_count']} classes, "
                  f"orchestrator={module_info['orchestrator'] or '—'})")
            resps = self.db.get_all_responsibilities()
            resp_count = len(resps) if resps else 0
            print(f"    Responsibility: {resp_count}/{total} classes")
            design = self.db.get_latest_design()
            if design:
                print(f"    Design:         done (id={design['id']})")
            else:
                print(f"    Design:         pending")
        else:
            print(f"    Scan:           pending")
            print(f"    Responsibility: pending")
            print(f"    Design:         pending")

    # ─── New CLI: unified orchestration interface ─────────────────────────────────

    def run_full_analysis(self, path, from_step=None):
        """Full pipeline: scan → resp → design.
        Supports incremental execution: skip steps with existing DB results.
        from_step: re-run from a specific step (scan/resp/design).
        """
        path = Path(path).resolve()

        if path.is_file():
            directory = str(path.parent)
        elif path.is_dir():
            directory = str(path)
        else:
            print(f"  Error: path not found: {path}")
            return False

        # Ensure DB is initialized
        self.db.ensure_tables()

        # --from: delete DB records for specified step and subsequent steps
        if from_step:
            self._reset_from_step(from_step)

        # === Step 1: Scan ===
        print(f"\n{'='*60}")
        print(f"  Step 1/3: Scanning source files...")
        print(f"{'='*60}")

        existing = self.db.get_all_tasks()
        if existing:
            print(f"  [skip] Already have {len(existing)} classes in DB")
        else:
            self._scan_source(directory)
            existing = self.db.get_all_tasks()
            if not existing:
                print("  Error: No classes found after scanning.")
                return False

        # === Step 2: Per-class Responsibility Analysis ===
        print(f"\n{'='*60}")
        print(f"  Step 2/3: Per-class responsibility analysis...")
        print(f"{'='*60}")

        if not self._resp_analyze_all():
            return False

        # === Step 3: Design Proposal ===
        print(f"\n{'='*60}")
        print(f"  Step 3/3: Design proposal...")
        print(f"{'='*60}")

        design = self.db.get_latest_design()
        if design:
            print(f"  [skip] Design proposal already exists (id={design['id']})")
        else:
            result = self._design_propose()
            if not result:
                print("  Error: Design proposal generation failed.")
                return False

        # === Summary ===
        self._print_summary()
        return True

    def run_focus(self, class_name):
        """Single class deep analysis with module context."""
        self.db.ensure_tables()

        print(f"\n{'='*60}")
        print(f"  Focus: {class_name}")
        print(f"{'='*60}")

        # Auto-register if not in DB
        task = self.db.get_task(class_name)
        if not task:
            header, impl = self.reader.find_class_files(class_name)
            if header:
                self.db.register_class(
                    class_name,
                    header_path=str(header),
                    impl_path=str(impl) if impl else None
                )
                print(f"  Auto-registered {class_name} from {header.name}")
            else:
                print(f"  Error: Cannot find source files for {class_name}")
                return False

        result = self.resp_agent.run(class_name)
        if result:
            # Generate standalone HTML report
            report_path = OUTPUTS_DIR / f"focus_{class_name}.html"
            generate_focus_report(self.db, class_name, report_path)
            print(f"\n  Focus analysis complete for {class_name}")
            print(f"  Report: {report_path}")
        return bool(result)

    def generate_report(self):
        """Generate HTML visualization report."""
        from .report import generate_html_report
        output_path = OUTPUTS_DIR / "report.html"
        result = generate_html_report(self.db, output_path, self.reader)
        print(f"\n  ✓ HTML report generated: {result}")
        print(f"    Open in browser to view.")

    # ─── Internal helpers ─────────────────────────────────────────────

    def _print_summary(self):
        """Print analysis results summary."""
        print(f"\n{'='*60}")
        print(f"  Analysis Summary")
        print(f"{'='*60}")

        module_info = self.db.get_module_info()
        if not module_info:
            print("  No analysis results yet.")
            return

        # Module overview
        print(f"\n  Module: {module_info['module_name']}")
        print(f"    Orchestrator: {module_info['orchestrator'] or '—'}")
        print(f"    Classes: {module_info['class_count']}")

        # Dependencies summary
        deps = self.db.get_dependencies()
        if deps:
            print(f"    Dependencies: {len(deps)} relationships")

        # Responsibilities
        resps = self.db.get_all_responsibilities()
        if resps:
            print(f"\n  Responsibilities ({len(resps)} classes):")
            for r in resps:
                ideal = (r['ideal_responsibility'] or '—')[:70]
                print(f"    {r['class_name']}: {ideal}")

        # Design
        design = self.db.get_latest_design()
        if design:
            print(f"\n  Design Proposal:")
            if design['phase_plan']:
                for phase in design['phase_plan'].split('|'):
                    phase = phase.strip()
                    if phase:
                        print(f"    {phase}")
            if design['new_classes']:
                print(f"    New classes: {design['new_classes']}")

        print(f"\n  Reports: {OUTPUTS_DIR}")
        print(f"  Deep-dive: python run.py focus <class>")

    def _reset_from_step(self, step):
        """Delete DB records for specified step and subsequent steps, for --from restart."""
        VALID = {'scan', 'resp', 'design'}
        if step not in VALID:
            print(f"  Error: --from must be one of: {', '.join(sorted(VALID))}")
            return False

        if step == 'scan':
            self.db.delete_responsibilities()  # cascade: resp → design
            self.db.delete_all_tasks()
            print("  [reset] Cleared all data (scan + resp + design)")
        elif step == 'resp':
            self.db.delete_responsibilities()  # cascade: resp → design
            print("  [reset] Cleared responsibility + design")
        elif step == 'design':
            self.db.delete_design_proposals()
            print("  [reset] Cleared design proposals")
        return True
