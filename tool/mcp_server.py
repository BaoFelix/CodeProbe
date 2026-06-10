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

from .config import DB_PATH, SOURCE_ROOT, OUTPUTS_DIR
from .db import DBManager
from .source_io import SourceReader
from .llm import LLMClient
from .agents import ScannerAgent
from .design_critic import DesignCriticAgent


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
    reader = SourceReader(SOURCE_ROOT, db=db)
    llm = LLMClient(cache=db)

    scanner = ScannerAgent(llm=llm, db=db, reader=reader)
    critic = DesignCriticAgent(llm=llm, db=db, reader=reader)

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
    def design_review() -> str:
        """
        Run the holistic design review (two-pass LLM analysis) over the
        scanned codebase. Requires scan_source to have been run first.
        Returns a summary of recommendations.
        """
        if not critic.run():
            return "Design review failed. Has scan_source been run?"
        module = db.get_design_module()
        if module and module['parsed_json']:
            import json as _json
            parsed = _json.loads(module['parsed_json'])
            recs = parsed.get('recommendations', [])
            lines = [f"Design review complete: {len(recs)} recommendations"]
            for r in recs[:8]:
                lines.append(f"  [{r.get('priority','?')}] {r.get('title') or r.get('target','')}")
            return "\n".join(lines)
        return "Design review complete (no recommendations parsed)."

    @mcp.tool()
    def get_status() -> str:
        """
        Get current analysis progress: total classes, analyzed, pending.
        """
        stats = db.get_stats()
        tasks = db.get_classes()

        if not stats or stats['total'] == 0:
            return "Database is empty. Run scan_source first."

        orchestrator = (db.get_module_info() or {}).get('orchestrator')
        lines = [
            f"Total classes: {stats['total']}",
            f"Critic subtrees analyzed: {stats['analyzed']}",
        ]
        lines.append("\nClass list:")
        for t in tasks:
            qn = t['qualified_name']
            orch = ' [orchestrator]' if qn == orchestrator else ''
            lines.append(f"  {qn}{orch}")
        return "\n".join(lines)

    @mcp.tool()
    def query_db(sql: str) -> str:
        """
        Execute read-only SQL query on refactor database.
        Only SELECT statements allowed. Queryable tables: entities, relationships,
        module_info, design_critic_subtree, design_critic_module.
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
        print("  Tools: scan_source, design_review, get_status, query_db, generate_report")
        print("  Waiting for AI Agent connection...")
        server.run()
