"""
mcp_server.py — MCP Server (let AI agents call the tool pipeline directly)
═══════════════════════════════════════
AI concept: MCP (Model Context Protocol)
Key insight:
  - MCP = a standard protocol that lets AI know what tools are available
  - Without MCP: you're the middleman, manually copy-pasting to AI
  - With MCP: AI calls your tools directly, no human relay needed
  - FastMCP: library that simplifies MCP Server development with decorators

  Install: pip install mcp
  Start: python run.py mcp-server
  Config: VS Code settings.json → mcp.servers → register this server
═══════════════════════════════════════
"""

# Check if mcp package is installed
try:
    from mcp.server.fastmcp import FastMCP
    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False

from .config import DB_PATH, SOURCE_ROOT, DEFAULT_MODULE, PROJECT_ROOT, OUTPUTS_DIR
from .db import DBManager
from .reader import FileReader
from .prompts import PromptBuilder
from .llm import LLMClient
from .agents import ScannerAgent, ResponsibilityAgent


def create_mcp_server():
    """
    Create and configure MCP Server.

    MCP Server's exposed tools appear in Copilot Agent's tool list.
    Copilot reads tool descriptions and decides when to call which tool.

    Returns: FastMCP instance (call .run() to start)
    """
    if not MCP_AVAILABLE:
        print("  ✗ MCP package not installed. Run: pip install mcp")
        print("  Install it, then restart MCP server.")
        return None

    # Create FastMCP server instance
    mcp = FastMCP(
        "codeprobe",
        description="CodeProbe — AI-powered C++ design diagnostic tool"
    )

    # Shared resources (used by all tools)
    db = DBManager(DB_PATH)
    reader = FileReader(SOURCE_ROOT)
    llm = LLMClient(backend="api")  # MCP mode uses API backend

    # Load skills (align with pipeline.py)
    universal_skills = {}
    skills_dir = PROJECT_ROOT / "skills" / "universal"
    if skills_dir.exists():
        for f in sorted(skills_dir.glob("*.md")):
            try:
                universal_skills[f.stem] = f.read_text(encoding='utf-8')
            except OSError:
                pass
    prompts = PromptBuilder(
        universal_skills=universal_skills,
    )

    scanner = ScannerAgent(llm=llm, db=db, reader=reader, prompts=prompts)
    resp_agent = ResponsibilityAgent(llm=llm, db=db, reader=reader, prompts=prompts)

    # ─── Tool definitions ────────────────────────────────────────────

    @mcp.tool()
    def scan_source(directory: str = "") -> str:
        """
        Scan C++ source directory, discover all class definitions and register to database.
        If no directory parameter given, uses the default source path.
        """
        scan_dir = directory if directory else None
        count = scanner.run(scan_dir)
        return f"Scan complete, found and registered {count} C++ classes"

    @mcp.tool()
    def analyze_class(class_name: str) -> str:
        """
        Analyze a C++ class's responsibilities and design issues.
        Class must first be registered to database via scan_source or register.
        Returns responsibility analysis results.
        """
        result = resp_agent.run(class_name)
        if not result:
            return f"Analysis failed: {class_name}. Confirm the class is registered in database."

        resp = db.get_responsibility(class_name)
        if resp:
            return (
                f"Analysis complete: {class_name}\n"
                f"Actual: {resp['actual_responsibilities']}\n"
                f"Ideal: {resp['ideal_responsibility']}\n"
                f"SRP violations: {resp['srp_violations']}"
            )
        return f"Analysis complete: {class_name}"

    @mcp.tool()
    def get_status() -> str:
        """
        Get current analysis progress: total classes, analyzed, pending.
        """
        stats = db.get_stats()
        tasks = db.get_all_tasks()

        if not stats or stats['total'] == 0:
            return "Database is empty. Run scan_source first."

        lines = [
            f"Total classes: {stats['total']}",
            f"Analyzed: {stats['analyzed']}",
            f"Pending: {stats['pending']}",
        ]
        lines.append("\nClass list:")
        for t in tasks:
            orch = ' [orchestrator]' if t['is_orchestrator'] else ''
            lines.append(f"  {t['class_name']}{orch}")
        return "\n".join(lines)

    @mcp.tool()
    def query_db(sql: str) -> str:
        """
        Execute read-only SQL query on refactor database.
        Only SELECT statements allowed. Queryable tables: classes, dependencies, module_info,
        responsibility_analysis, design_proposals.
        """
        sql_stripped = sql.strip().upper()
        if not sql_stripped.startswith("SELECT"):
            return "Security restriction: only SELECT queries allowed."

        import sqlite3
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(sql)
            rows = cur.fetchall()
            conn.close()

            if not rows:
                return "Query returned no results"

            columns = rows[0].keys()
            lines = [" | ".join(columns)]
            lines.append("-" * len(lines[0]))
            for row in rows[:50]:
                lines.append(" | ".join(str(row[c]) for c in columns))
            return "\n".join(lines)
        except sqlite3.Error as e:
            return f"SQL error: {e}"

    @mcp.tool()
    def register_class(
        class_name: str,
        header_path: str = "",
        impl_path: str = ""
    ) -> str:
        """
        Manually register a C++ class to the database.
        class_name: class name (required)
        header_path: header file path (optional)
        impl_path: implementation file path (optional)
        """
        db.register_class(
            class_name,
            header_path=header_path or None,
            impl_path=impl_path or None,
            module=DEFAULT_MODULE
        )
        return f"Registered: {class_name}"

    @mcp.tool()
    def generate_report() -> str:
        """Generate HTML diagnostic report from analysis results in database.
        Returns path to generated report.html file."""
        from .report import generate_html_report
        output_path = OUTPUTS_DIR / "report.html"
        result = generate_html_report(db, output_path)
        return f"Report generated: {result}"

    return mcp


def run_mcp_server():
    """Start MCP server. Called by __main__.py's mcp-server command."""
    server = create_mcp_server()
    if server:
        print("  ✓ MCP Server starting...")
        print("  Tools: scan_source, analyze_class, get_status, query_db, register_class, generate_report")
        print("  Waiting for AI Agent connection...")
        server.run()
