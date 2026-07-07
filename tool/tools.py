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
from dataclasses import dataclass
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


def _resolution_note(rels):
    """Disclose how much of the graph is actually connected. Unresolved
    cross-file targets are dropped from analysis (we under-connect rather
    than fabricate), so a high unresolved fraction means absence of
    findings must be read with caution — the dangerous failure mode of a
    trust tool is confident silence, so we surface it."""
    total = len(rels)
    if not total:
        return None
    unresolved = sum(1 for r in rels if not r["target_qname"])
    pct = 100.0 * unresolved / total
    note = f"relationships: {total} ({unresolved} unresolved targets, {pct:.0f}%)"
    if pct > 25:
        note += (" — coverage caution: many cross-file names did not "
                 "resolve; treat the ABSENCE of findings carefully.")
    return note


def _get_overview(ctx):
    """High-level snapshot: class count, orchestrator, architecture style,
    and graph-coverage disclosure."""
    stats = ctx.db.get_stats()
    if not stats or not stats["total"]:
        return "The DB is empty. Run scan_source first."
    mi = ctx.db.get_module_info()
    lines = [f"classes: {stats['total']}",
             f"critic subtrees analyzed: {stats['analyzed']}"]
    note = _resolution_note(ctx.db.get_relationships())
    if note:
        lines.append(note)
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
    Idempotent — but staleness-aware: a stored review counts as cached
    only while the relationship graph it was computed from is still the
    graph in the DB. After a rescan changes the graph, the review re-runs
    automatically instead of serving stale conclusions."""
    from .db import graph_fingerprint
    if not ctx.db.get_classes():
        return "Nothing to review. Run scan_source first."
    existing = ctx.db.get_design_module()
    if not force and existing:
        current = graph_fingerprint(ctx.db.get_relationships())
        stored = existing["graph_hash"] if "graph_hash" in existing.keys() \
            else None
        if stored == current:
            return ("Design review already exists and the graph is "
                    "unchanged — reading from it (pass force=true to "
                    "re-run anyway).")
        # fall through: graph changed (or legacy row without a hash)
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


def _llm_ready(ctx):
    """Is a real LLM endpoint configured? Governs whether the optional
    LLM steps (compile user rules / verify) run — the universal audit
    itself never needs one."""
    return bool(getattr(ctx.llm, "api_key", "") and
                getattr(ctx.llm, "api_url", ""))


def _load_arch_skill():
    """Read the user's plain-language architecture rules, if present.
    Only a file named exactly `architecture.md` counts — the shipped
    `architecture.example.md` template is deliberately not loaded.
    Anchored at the project's skills/ dir (NOT the CWD — an MCP host may
    launch us from anywhere)."""
    from .config import SKILLS_DIR
    if not SKILLS_DIR.is_dir():
        return None
    for p in SKILLS_DIR.rglob("architecture.md"):
        try:
            return p.read_text(encoding="utf-8")
        except OSError:
            continue
    return None


def _baseline_path(ctx):
    # The baseline is a per-project file the user commits and CI reads, so
    # it belongs where the command is run (the repo root), not inside our
    # install. cwd is the predictable, CI-friendly convention.
    from .architect import DEFAULT_BASELINE_NAME
    return Path.cwd() / DEFAULT_BASELINE_NAME


def _architecture_audit(ctx, strategy="auto", verify=False, baseline="off"):
    """Architecture-level (module) health check.

    Core is deterministic: group classes into modules, find structural
    problems (cycles, god modules, plus any rules the user declared) — each
    with file:line evidence. No API key needed for the built-in checks.

    baseline mode (CI ratchet — freeze legacy debt, gate only NEW issues):
      · "off"    (default) report every finding
      · "update" freeze the current findings as the accepted baseline
      · "check"  report only findings NOT in the baseline (+ note how many
                 pre-existing were suppressed and how many got resolved)

    Optional LLM steps (only if an endpoint is configured):
      · skills/architecture.md present → compile the user's plain-language
        rules into the contract (adds forbid_dependency + explicit groups).
      · verify=true → drop LLM-judged false positives.
    Requires a prior scan."""
    from .architect import (run_architecture_audit, format_findings,
                            load_universal_contract, RuleCompiler, Verifier,
                            load_baseline, save_baseline, partition,
                            resolved_keys)
    classes = [dict(r) for r in ctx.db.get_classes()]
    if not classes:
        return "Nothing to audit. Run scan_source first."
    rels = [dict(r) for r in ctx.db.get_relationships()]

    contract = load_universal_contract()
    groups = None
    prose = _load_arch_skill()
    if prose and _llm_ready(ctx):
        user = RuleCompiler(ctx.llm).compile(prose, classes)
        contract.rules.extend(user.rules)
        groups = user.groups or None

    findings, mg = run_architecture_audit(
        classes, rels, strategy=strategy, contract=contract, groups=groups)

    if verify and findings and _llm_ready(ctx):
        findings = Verifier(ctx.llm).verify(findings)

    note = _resolution_note(rels)

    # Persist the deterministic result so the report and the LLM tiers can
    # read it. Skip persistence in baseline check/update (those are CI
    # queries, not the canonical audit) so they don't overwrite the report's
    # view with a filtered finding set.
    if baseline == "off":
        from .architect import plan_decoupling, audit_payload
        from .db import graph_fingerprint
        unresolved_pct = None
        if rels:
            unresolved_pct = round(
                100.0 * sum(1 for r in rels if not r["target_qname"]) / len(rels), 1)
        payload = audit_payload(findings, mg, plan_decoupling(mg), unresolved_pct)
        ctx.db.save_arch_audit(payload, graph_hash=graph_fingerprint(rels))

    prefix = (note + "\n") if note else ""

    if baseline == "update":
        n = save_baseline(_baseline_path(ctx), findings)
        return prefix + (f"Baseline frozen: {n} existing finding(s) accepted "
                         f"as debt at {_baseline_path(ctx)}. Future audits in "
                         f"'check' mode report only NEW findings.")
    if baseline == "check":
        frozen = load_baseline(_baseline_path(ctx))
        new, known = partition(findings, frozen)
        resolved = resolved_keys(findings, frozen)
        head = (f"Baseline check: {len(new)} NEW, {len(known)} known "
                f"(suppressed), {len(resolved)} resolved since baseline.\n")
        if resolved:
            head += ("  ↓ resolved (re-run with baseline=update to lock in):\n"
                     + "".join(f"    ✓ {k}\n" for k in sorted(resolved)))
        body = (format_findings(new, mg) if new
                else "✓ No new architecture findings vs the baseline.")
        return prefix + head + body

    return prefix + format_findings(findings, mg)


def _decoupling_plan(ctx, strategy="auto"):
    """Turn each module cycle into an ordered surgical plan: which edge to
    cut (minimum feedback set — the cheapest cut), how to cut it (dependency
    inversion or extract-shared-base, chosen from the real edge kinds), the
    exact class references to change (file:line), and a refactor order that
    keeps the build green. Deterministic, no LLM. Requires a scan."""
    from .architect import ModuleBuilder, plan_decoupling, format_plans
    classes = [dict(r) for r in ctx.db.get_classes()]
    if not classes:
        return "Nothing to plan. Run scan_source first."
    rels = [dict(r) for r in ctx.db.get_relationships()]
    mg = ModuleBuilder.build(classes, rels, strategy=strategy)
    return format_plans(plan_decoupling(mg))


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
        ToolSpec("architecture_audit",
                 "Architecture-level health check: groups classes into "
                 "modules and finds structural problems (module cycles, god "
                 "modules) with file:line evidence. Deterministic, no LLM. "
                 "Use for big-picture/system-shape questions. Requires a scan.",
                 _obj({"strategy": {"type": "string",
                                    "description": "auto|folder|namespace|community"},
                       "verify": {"type": "boolean",
                                  "description": "LLM-verify findings to drop false positives"},
                       "baseline": {"type": "string",
                                    "description": "off|update|check — CI ratchet: "
                                    "freeze legacy debt, gate only new findings"}}),
                 _architecture_audit),
        ToolSpec("decoupling_plan",
                 "For each module cycle, compute a surgical decoupling plan: "
                 "the cheapest edge(s) to cut, the mechanism (dependency "
                 "inversion / extract shared base), the exact file:line "
                 "references to change, and a build-safe refactor order. "
                 "Deterministic, no LLM. Use when the user asks HOW to break "
                 "a cycle, not just whether one exists.",
                 _obj({"strategy": {"type": "string",
                                    "description": "auto|folder|namespace|community"}}),
                 _decoupling_plan),
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
