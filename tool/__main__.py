"""
__main__.py — CLI command parser (supports python -m tool)
"""
from .pipeline import Pipeline
import sys


def main():
    args = sys.argv[1:] if len(sys.argv) > 1 else []

    # --from=step flag: re-run from a specific step
    from_step = None
    for arg in args[:]:
        if arg.startswith('--from='):
            from_step = arg.split('=', 1)[1]
            args.remove(arg)

    if not args:
        _print_usage()
        return

    cmd = args[0].lower()

    # mcp-server doesn't need Pipeline
    if cmd == 'mcp-server':
        from .mcp_server import run_mcp_server
        run_mcp_server()
        return

    # chat drives the agentic Host, not the fixed Pipeline
    if cmd == 'chat':
        from .host import run_chat
        run_chat()
        return

    # audit: the deterministic architecture check + decoupling plan.
    # Deliberately key-free — this is the moat, runnable with zero LLM.
    #   --baseline  freeze current findings as accepted debt
    #   --check     report only findings NOT in the baseline (CI gate;
    #               exit 1 when new findings exist)
    if cmd == 'audit':
        from .tools import ToolContext, build_registry, run_tool
        baseline = 'off'
        rest = args[1:]
        if '--baseline' in rest:
            baseline = 'update'; rest.remove('--baseline')
        if '--check' in rest:
            baseline = 'check'; rest.remove('--check')
        target = rest[0] if rest else None
        ctx = ToolContext.build(target)
        registry = build_registry(ctx)
        # Force a fresh scan: `audit <path>` must reflect THIS path, but the
        # shared DB may hold a previous run's graph (the idempotent scan
        # would otherwise skip and audit stale data).
        print(f"  {run_tool(registry, 'scan_source', {'directory': target or '', 'force': True}, ctx)}\n")
        audit = run_tool(registry, 'architecture_audit', {'baseline': baseline}, ctx)
        print(audit)
        if baseline != 'update':
            print()
            print(run_tool(registry, 'decoupling_plan', {}, ctx))
        # CI gate: non-zero exit when the baseline check found new findings
        if baseline == 'check':
            import re
            m = re.search(r'Baseline check: (\d+) NEW', audit)
            if m and int(m.group(1)) > 0:
                sys.exit(1)
        return

    if cmd == 'help':
        _print_usage()
        return

    pipeline = Pipeline()

    if cmd == 'init':
        pipeline.init_project()
    elif cmd == 'analyze':
        if len(args) < 2:
            print("  Usage: python run.py analyze <path>")
            print("  path can be a directory or a single .hxx/.h file")
            return
        pipeline.run_full_analysis(args[1], from_step=from_step)
    elif cmd == 'status':
        pipeline.show_status()
    elif cmd == 'report':
        pipeline.generate_report()
    else:
        print(f"  Unknown command: {cmd}")
        _print_usage()


def _print_usage():
    print("""
╔══════════════════════════════════════════════════════════╗
║  CodeProbe — AI-Powered C++ Design Diagnostic Tool      ║
║  Analyze class design quality & propose refactoring     ║
╚══════════════════════════════════════════════════════════╝

Usage: python run.py <command> [options]

Commands:
  init                    Initialize project (create DB tables)
  analyze <path>          Full pipeline (scan → design review)
                          <path> = directory, or a file (its parent
                          directory is scanned)
                          Formats: .hxx .h .hpp .cxx .cpp .c .sch
  audit [path]            Architecture audit + decoupling plan —
                          deterministic, needs NO LLM key
       --baseline         freeze current findings as accepted debt
       --check            report only NEW findings vs baseline
                          (exit 1 if any — a CI gate)
  status                  Show analysis progress dashboard
  report                  Generate the interactive HTML report
  chat                    Talk to the codebase (agentic; needs LLM API)
  mcp-server              Run as an MCP server for external AI hosts
                          (pip install mcp)

Options:
  --from=STEP             Re-run from step: scan or review

Workflow:
  python run.py init                              # first time setup
  python run.py audit src/                        # key-free architecture check
  python run.py analyze test_src/                 # scan + design review
  python run.py analyze test_src/ --from=review   # re-run the design review
  python run.py report                            # generate HTML report

The design review and chat need LLM API access — configure .env
(see .env.example). Scanning, audit, and the report work without it.
""")


if __name__ == '__main__':
    main()
