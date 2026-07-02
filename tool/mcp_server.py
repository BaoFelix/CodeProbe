"""
mcp_server.py — expose the tool registry to EXTERNAL AI hosts over MCP.

Design decision (see tools.py's docstring): there is exactly ONE
implementation of every capability, in the tool registry. Two consumers
share it:

    our own Host (host.py)                       → in-process function calls
    external hosts (Copilot, Claude Desktop, …)  → these MCP wrappers

So every function below is a thin adapter: a typed signature (that is what
MCP uses to advertise the parameter schema to callers) plus one run_tool()
call. No analysis logic lives here — if a wrapper ever grows an `if`, that
logic belongs in tools.py or a worker instead.

  Install: pip install mcp
  Start:   python run.py mcp-server
"""
try:
    from mcp.server.fastmcp import FastMCP
    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False

from .tools import ToolContext, build_registry, run_tool


def create_mcp_server():
    """Build the FastMCP server over the shared tool registry.
    Returns the FastMCP instance, or None if mcp isn't installed."""
    if not MCP_AVAILABLE:
        print("  ✗ MCP package not installed. Run: pip install mcp")
        return None

    mcp = FastMCP(
        "codeprobe",
        description="CodeProbe — AI-powered C++ architecture diagnostics")

    ctx = ToolContext.build()
    registry = build_registry(ctx)

    def call(name, **args):
        return run_tool(registry, name, args, ctx)

    # ── thin wrappers (signature = the schema MCP advertises) ────────

    @mcp.tool()
    def scan_source(directory: str = "", force: bool = False) -> str:
        """Scan a C++ source directory into the DB (entities +
        relationships). Idempotent: skips if already scanned;
        force=True rescans."""
        return call("scan_source", directory=directory, force=force)

    @mcp.tool()
    def get_overview() -> str:
        """High-level snapshot: class count, orchestrator, architecture
        style. Cheap, read-only."""
        return call("get_overview")

    @mcp.tool()
    def list_classes(limit: int = 200) -> str:
        """List qualified names of all classes/structs/interfaces."""
        return call("list_classes", limit=limit)

    @mcp.tool()
    def get_relationships(class_qname: str = "", limit: int = 200) -> str:
        """List relationships (optionally for one class), each with kind
        and file:line evidence."""
        return call("get_relationships", class_qname=class_qname, limit=limit)

    @mcp.tool()
    def architecture_audit(strategy: str = "auto", verify: bool = False) -> str:
        """Architecture-level health check: module cycles, god modules,
        inverted dependencies, plus user rules from skills/architecture.md.
        Deterministic findings with file:line evidence."""
        return call("architecture_audit", strategy=strategy, verify=verify)

    @mcp.tool()
    def decoupling_plan(strategy: str = "auto") -> str:
        """For each module cycle: the cheapest edges to cut, the mechanism
        (dependency inversion / extract shared base), the exact references
        to change, and a build-safe refactor order."""
        return call("decoupling_plan", strategy=strategy)

    @mcp.tool()
    def design_review(force: bool = False) -> str:
        """Run the two-pass class-level LLM design review (requires a
        scan; idempotent unless force=True)."""
        return call("design_review", force=force)

    @mcp.tool()
    def get_findings() -> str:
        """Read stored design-review results (recommendations + pain
        points). No LLM call."""
        return call("get_findings")

    @mcp.tool()
    def query_db(sql: str) -> str:
        """Read-only SELECT over the DB (tables: entities, relationships,
        module_info, design_critic_subtree, design_critic_module)."""
        return call("query_db", sql=sql)

    @mcp.tool()
    def generate_report() -> str:
        """Render the self-contained interactive HTML report."""
        return call("generate_report")

    return mcp


def run_mcp_server():
    """Entry point for `python run.py mcp-server`."""
    server = create_mcp_server()
    if server is None:
        return
    print("  ▶ CodeProbe MCP server starting (stdio)…")
    server.run()
