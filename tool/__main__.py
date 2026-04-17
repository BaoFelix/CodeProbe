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
    elif cmd == 'focus':
        if len(args) < 2:
            print("  Usage: python run.py focus <class_name>")
            return
        pipeline.run_focus(args[1])
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
  analyze <path>          Full analysis pipeline (scan → resp → design)
                          <path> = directory or .hxx/.sch file
                          Supported formats: .hxx, .sch, .cxx
  focus <class_name>      Deep analysis of a single class
  status                  Show analysis progress dashboard
  report                  Generate HTML report

Options:
  --from=STEP             Re-run from step: scan, resp, or design

Workflow:
  python run.py init                            # first time setup
  python run.py analyze test_src/               # full pipeline
  python run.py analyze test_src/ --from=resp   # re-run from responsibility step
  python run.py focus Vehicle                   # deep-dive into one class
  python run.py status                          # check progress
  python run.py report                          # generate HTML report

Requires LLM API access. Configure .env (see .env.example).
Supports interrupt & resume — re-run analyze to continue from last step.
""")


if __name__ == '__main__':
    main()
