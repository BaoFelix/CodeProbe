"""
tools.py — the tool registry.

ONE place that declares every capability the system can invoke, as a stable
contract:  (name, description, JSON-schema, handler).

Why this module exists (SRP):
  - The agentic Host (host.py) calls these in-process, in its agent loop.
  - The MCP server (mcp_server.py) can wrap the SAME specs for external hosts.
  Both share one definition so a tool never drifts between the two.

This module holds NO analysis logic. Every handler is a thin adapter that
delegates to the real workers (ScannerAgent, DesignCriticAgent, DBManager,
report). If you find yourself writing algorithm code here, it belongs in a
worker instead.

Contract (stable — other modules depend on these shapes):
    ToolContext   shared, injected dependencies (db / llm / reader / paths)
    ToolSpec      name · description · parameters(JSON schema) · handler
    build_registry(ctx)      -> {name: ToolSpec}
    tool_schemas(registry)   -> [ {name, description, parameters}, ... ]  (for the LLM)
    run_tool(registry, name, args) -> str                                 (dispatch)

Every handler returns a plain string — the text fed back to the LLM (or shown
to the user). Strings keep the contract trivial and provider-agnostic.
"""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .config import DB_PATH, OUTPUTS_DIR, SOURCE_ROOT
from .db import DBManager
from .llm import LLMClient
from .source_io import SourceReader
from .agents import ScannerAgent
from .design_critic import DesignCriticAgent


# ── Shared dependencies (injected, so tests can pass fakes) ──────────

@dataclass
class ToolContext:
    """The shared resources every tool may need. Built once and handed to
    each handler, so handlers stay pure functions of (ctx, **args)."""
    db: DBManager
    llm: LLMClient
    reader: SourceReader
    source_root: Path = SOURCE_ROOT
    outputs_dir: Path = OUTPUTS_DIR

    @classmethod
    def build(cls, source_root=None):
        """Default wiring from config — mirrors Pipeline.__init__ so the
        Host and the CLI share identical dependencies."""
        db = DBManager(DB_PATH)
        db.ensure_tables()
        reader = SourceReader(source_root or SOURCE_ROOT, db=db)
        llm = LLMClient(cache=db)
        return cls(db=db, llm=llm, reader=reader,
                   source_root=Path(source_root or SOURCE_ROOT))


# ── The tool contract ───────────────────────────────────────────────

@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict                      # JSON schema (OpenAI/Anthropic shape)
    handler: Callable                     # (ctx, **args) -> str

    def to_schema(self) -> dict:
        return {"name": self.name,
                "description": self.description,
                "parameters": self.parameters}


def _obj(properties=None, required=None):
    """Small helper for the common JSON-schema object shape."""
    return {"type": "object",
            "properties": properties or {},
            "required": required or []}


# ── Handlers (thin adapters over the real workers) ──────────────────

def _scan_source(ctx, directory="", force=False):
    """Full scan → entities + relationships into the DB.

    Idempotent by design: a full scan is expensive, so if the graph is
    already populated we skip and read from the DB instead. The LLM sees
    'already scanned' and moves on rather than paying to rescan. Pass
    force=true only to rebuild after the source changed.
    """
    if not force and ctx.db.get_classes():
        n = len(ctx.db.get_classes())
        return (f"Already scanned: {n} classes in the DB. "
                f"Reading from the DB (pass force=true to rescan).")
    scan_dir = directory or str(ctx.source_root)
    scanner = ScannerAgent(llm=ctx.llm, db=ctx.db, reader=ctx.reader)
    count = scanner.run(scan_dir)
    if not count:
        return f"No C++ classes found under {scan_dir}."
    return f"Scan complete: {count} classes registered from {scan_dir}."


def _get_overview(ctx):
    """High-level snapshot: class count, orchestrator, architecture style."""
    stats = ctx.db.get_stats()
    if not stats or not stats["total"]:
        return "The DB is empty. Run scan_source first."
    mi = ctx.db.get_module_info()
    lines = [f"classes: {stats['total']}",
             f"critic subtrees analyzed: {stats['analyzed']}"]
    if mi:
        lines += [f"orchestrator: {mi['orchestrator'] or '—'}",
                  f"style: {mi['style'] or 'oop'}"]
        if mi["style"] and mi["style"] != "oop" and mi["style_note"]:
            lines.append(f"style note: {mi['style_note']}")
    return "\n".join(lines)


def _list_classes(ctx, limit=200):
    """The class inventory (qualified names)."""
    rows = ctx.db.get_classes()
    if not rows:
        return "No classes. Run scan_source first."
    names = [r["qualified_name"] for r in rows]
    head = names[:limit]
    out = "\n".join(head)
    if len(names) > limit:
        out += f"\n… and {len(names) - limit} more (total {len(names)})"
    return out


def _get_relationships(ctx, class_qname="", limit=200):
    """Relationships, optionally for one class. Each line carries the
    relationship kind and evidence location — the grounding the caller
    needs to trust the answer."""
    rows = ctx.db.get_relationships(source_qname=class_qname or None)
    if not rows:
        where = f" for {class_qname}" if class_qname else ""
        return f"No relationships{where}. Run scan_source first."
    lines = []
    for r in rows[:limit]:
        tgt = r["target_qname"] or r["target_name"]
        loc = f" ({Path(r['evidence_file']).name}:{r['evidence_line']})" \
              if r["evidence_file"] else ""
        lines.append(f"{r['source_qname']} --{r['kind']}--> {tgt}{loc}")
    if len(rows) > limit:
        lines.append(f"… and {len(rows) - limit} more (total {len(rows)})")
    return "\n".join(lines)


def _design_review(ctx, force=False):
    """Run the two-pass LLM design review (class-level). Requires a scan.
    Idempotent: skips if results already exist unless force=true."""
    if not ctx.db.get_classes():
        return "Nothing to review. Run scan_source first."
    if not force and ctx.db.get_design_module():
        return ("Design review already exists in the DB — reading from it "
                "(pass force=true to re-run).")
    critic = DesignCriticAgent(llm=ctx.llm, db=ctx.db, reader=ctx.reader)
    if not critic.run():
        return "Design review failed."
    return "Design review complete."


def _get_findings(ctx):
    """Read the stored design-review results (module recommendations +
    per-subtree pain points) as text — no LLM call."""
    import json
    module = ctx.db.get_design_module()
    if not module or not module["parsed_json"]:
        return "No design review yet. Run design_review first."
    parsed = json.loads(module["parsed_json"])
    lines = ["# Recommendations"]
    for r in parsed.get("recommendations", []):
        lines.append(f"[{r.get('priority', '?')}] "
                     f"{r.get('title') or r.get('target', '')}")
    subs = ctx.db.get_design_subtrees() or []
    pains = []
    for s in subs:
        if not s["parsed_json"]:
            continue
        a = json.loads(s["parsed_json"])
        for p in a.get("pains", []):
            pains.append(f"[{s['subtree_root']}] "
                         f"{p.get('title') or p.get('category', 'issue')}"
                         f" — {p.get('where', '')}")
    if pains:
        lines.append("\n# Pain points")
        lines += pains
    return "\n".join(lines)


def _query_db(ctx, sql):
    """Read-only SELECT escape hatch over the DB. Rejects anything that
    isn't a plain SELECT — this is a read tool, never a mutation."""
    if not sql.strip().lower().startswith("select"):
        return "Only SELECT queries are allowed."
    try:
        rows = ctx.db._query_all(sql)
    except Exception as e:                       # surface SQL errors to the LLM
        return f"SQL error: {e}"
    if not rows:
        return "No rows."
    cols = rows[0].keys()
    out = [" | ".join(cols)]
    for r in rows[:50]:
        out.append(" | ".join(str(r[c]) for c in cols))
    if len(rows) > 50:
        out.append(f"… and {len(rows) - 50} more")
    return "\n".join(out)


def _generate_report(ctx):
    """Render the self-contained HTML report from whatever is in the DB."""
    from .report import generate_html_report
    out = ctx.outputs_dir / "report.html"
    ctx.outputs_dir.mkdir(parents=True, exist_ok=True)
    result = generate_html_report(ctx.db, out)
    return f"Report generated: {result}"


# ── Registry assembly ───────────────────────────────────────────────

def build_registry(ctx: ToolContext) -> dict:
    """Bind every ToolSpec's handler to the shared context. Returns
    {name: ToolSpec}. This list IS the tool surface of the whole system."""
    specs = [
        ToolSpec("scan_source",
                 "Full one-time scan of a C++ source directory into the DB "
                 "(entities + relationships). Idempotent: skips if already "
                 "scanned. Run this before analysis if the DB is empty.",
                 _obj({"directory": {"type": "string",
                                     "description": "source dir; empty = default"},
                       "force": {"type": "boolean",
                                 "description": "rescan even if data exists"}}),
                 _scan_source),
        ToolSpec("get_overview",
                 "High-level snapshot: class count, orchestrator, "
                 "architecture style. Cheap, read-only.",
                 _obj(), _get_overview),
        ToolSpec("list_classes",
                 "List the qualified names of all classes/structs/interfaces.",
                 _obj({"limit": {"type": "integer"}}), _list_classes),
        ToolSpec("get_relationships",
                 "List relationships (optionally for one class), each with "
                 "kind and file:line evidence.",
                 _obj({"class_qname": {"type": "string",
                                       "description": "filter by source class"},
                       "limit": {"type": "integer"}}),
                 _get_relationships),
        ToolSpec("design_review",
                 "Run the two-pass class-level LLM design review. Requires a "
                 "prior scan. Idempotent: skips if results exist.",
                 _obj({"force": {"type": "boolean"}}), _design_review),
        ToolSpec("get_findings",
                 "Read the stored design-review results (recommendations + "
                 "pain points) as text. No LLM call.",
                 _obj(), _get_findings),
        ToolSpec("query_db",
                 "Run a read-only SELECT over the DB (tables: entities, "
                 "relationships, module_info, design_critic_subtree, "
                 "design_critic_module).",
                 _obj({"sql": {"type": "string"}}, ["sql"]), _query_db),
        ToolSpec("generate_report",
                 "Render the self-contained interactive HTML report from the "
                 "current DB contents.",
                 _obj(), _generate_report),
    ]
    return {s.name: s for s in specs}


def tool_schemas(registry: dict) -> list:
    """The schema list to advertise to the LLM (tool-use)."""
    return [s.to_schema() for s in registry.values()]


def run_tool(registry: dict, name: str, args: dict, ctx: ToolContext) -> str:
    """Dispatch one tool call. Unknown tool / bad args become a message
    the LLM can read and recover from — never an exception that kills the
    loop."""
    spec = registry.get(name)
    if spec is None:
        return f"Unknown tool: {name}"
    try:
        return spec.handler(ctx, **(args or {}))
    except TypeError as e:
        return f"Bad arguments for {name}: {e}"
    except Exception as e:
        return f"Tool {name} failed: {e}"
