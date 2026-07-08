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
import json
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


def _resolve_class(ctx, name):
    """Map a possibly-short class name to full scoped qualified name(s).

    Class names collide across namespaces (e.g. two ResultProbeDeleter), so
    a bare short name can be ambiguous — the scope IS load-bearing. Return
    ALL matches and let the caller disambiguate: exact full qname -> [it];
    exact short name -> every class with it; then scoped-suffix, then
    substring. Empty list = no such class.
    """
    name = (name or "").strip()
    if not name:
        return []
    qnames = [c["qualified_name"] for c in (dict(r) for r in ctx.db.get_classes())]
    if name in qnames:
        return [name]
    low = name.lower()
    short = [q for q in qnames if q.split("::")[-1].lower() == low]
    if short:
        return short
    suf = [q for q in qnames if q.lower().endswith("::" + low)]
    if suf:
        return suf
    return [q for q in qnames if low in q.lower()]


def _describe_class(ctx, name):
    """Full grounded profile of ONE class for detailed / comparison analysis:
    kind, file, size, methods, fields, base classes, subclasses, what it
    uses and who uses it. Scope-aware: resolves a short name, and if it is
    ambiguous returns the scoped candidates instead of guessing."""
    matches = _resolve_class(ctx, name)
    if not matches:
        return f"No class matches '{name}'. Use list_classes to see names."
    if len(matches) > 1:
        return (f"'{name}' is ambiguous — {len(matches)} classes share that "
                f"name; ask again with the full scoped name:\n"
                + "\n".join(f"  - {m}" for m in matches))
    q = matches[0]
    row = ctx.db.get_entity(q)
    if not row:
        return f"'{q}' has no definition in scope (external or forward-declared)."
    attrs = json.loads(row["attrs"] or "{}")
    kind = row["kind"]
    loc = (row["end_line"] or 0) - (row["start_line"] or 0)
    # A method appears twice — the .hxx declaration and the .cxx definition
    # (the out-of-line body is written `Class::m` without the namespace, so
    # its qualified_name differs). Collapse by short name so the count is the
    # real method count, not double.
    methods = sorted({r["name"] for r in
                      ctx.db.get_entities(kind="method", parent_qname=q)})
    fields = sorted({r["name"] for r in
                     ctx.db.get_entities(kind="field", parent_qname=q)})
    out = [dict(r) for r in ctx.db.get_relationships(source_qname=q)]
    inc = [dict(r) for r in ctx.db.get_relationships(target_qname=q)]
    inh = lambda k: k in ("inherits", "implements")
    bases = sorted({(r["target_qname"] or r["target_name"]) for r in out if inh(r["kind"])})
    subs = sorted({r["source_qname"] for r in inc if inh(r["kind"])})
    uses_int = sorted({r["target_qname"] for r in out if not inh(r["kind"]) and r["target_qname"]})
    uses_ext = sorted({r["target_name"] for r in out if not inh(r["kind"]) and not r["target_qname"]})
    usedby = sorted({r["source_qname"] for r in inc if not inh(r["kind"])})

    def shorts(qs, n=20):
        return ", ".join(x.split("::")[-1] for x in qs[:n]) + (" …" if len(qs) > n else "")

    tag = {"interface": " «interface»", "struct": " «struct»"}.get(kind, "")
    L = [q, f"  kind: {kind}{tag}"]
    if row["file_path"]:
        L.append(f"  defined: {Path(row['file_path']).name}:{row['start_line']} "
                 f"(~{loc} lines)")
    if attrs.get("phantom"):
        L.append("  ! phantom: declaration never seen (inferred from .cxx methods)")
    L.append(f"  methods ({len(methods)}): {shorts(methods, 30)}"
             if methods else "  methods (0)")
    L.append(f"  fields ({len(fields)}): {shorts(fields, 30)}"
             if fields else "  fields (0)")
    if bases:
        L.append(f"  inherits/implements: {shorts(bases)}")
    if subs:
        L.append(f"  subclasses ({len(subs)}): {shorts(subs)}")
    ext = f"; +{len(uses_ext)} external SDK types" if uses_ext else ""
    L.append(f"  depends on {len(uses_int)} internal class(es): {shorts(uses_int)}{ext}"
             if uses_int else f"  depends on 0 internal classes{ext}")
    L.append(f"  used by {len(usedby)} internal class(es): {shorts(usedby)}"
             if usedby else "  used by 0 internal classes")
    return "\n".join(L)


def _get_relationships(ctx, class_qname="", limit=200, direction="outgoing"):
    """Relationships, optionally for one class. Each line carries the
    relationship kind and evidence location — the grounding the caller
    needs to trust the answer.

    direction (only meaningful with class_qname):
      · "outgoing" (default) — what the class depends on / uses.
      · "incoming"           — who depends on / uses the class (reverse).
      · "both"               — both sets, labelled.
    Reverse queries only see INTERNAL callers (a resolved source_qname);
    external SDK code that uses the class is not in scope."""
    cq = class_qname or None
    if cq:
        matches = _resolve_class(ctx, cq)
        if len(matches) > 1:
            return (f"'{class_qname}' is ambiguous — {len(matches)} classes "
                    f"share that name; pass the full scoped name:\n"
                    + "\n".join(f"  - {m}" for m in matches))
        if matches:
            cq = matches[0]
    if not cq:
        rows = ctx.db.get_relationships()
    elif direction == "incoming":
        rows = ctx.db.get_relationships(target_qname=cq)
    elif direction == "both":
        rows = (list(ctx.db.get_relationships(source_qname=cq)) +
                list(ctx.db.get_relationships(target_qname=cq)))
    else:
        rows = ctx.db.get_relationships(source_qname=cq)
    if not rows:
        where = f" for {class_qname} ({direction})" if cq else ""
        hint = ("" if not cq else
                " (nobody internal depends on it — external SDK callers are "
                "out of scope)" if direction == "incoming" else "")
        return f"No relationships{where}{hint}. Run scan_source first."
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


def _module_dependencies(ctx, from_module="", to_module="", strategy="auto"):
    """Deterministic module-level dependency inspector.

    Modules are NOT a DB column — they are computed here from the same
    grouping the audit uses (folder / namespace / community / explicit
    groups). This is the ONLY correct way to answer 'module A depends on
    module B N times — what are the N?': every module edge already carries
    the underlying class-level references with file:line evidence.

    · No args  → the full module dependency matrix (A → B, weight, kinds).
    · from+to  → the exact class-level references backing that one edge.
    """
    from .architect import run_architecture_audit, load_universal_contract
    classes = [dict(r) for r in ctx.db.get_classes()]
    if not classes:
        return "Nothing to analyze. Run scan_source first."
    rels = [dict(r) for r in ctx.db.get_relationships()]

    contract = load_universal_contract()
    groups = None
    prose = _load_arch_skill()
    if prose and _llm_ready(ctx):
        from .architect import RuleCompiler
        user = RuleCompiler(ctx.llm).compile(prose, classes)
        contract.rules.extend(user.rules)
        groups = user.groups or None

    _, mg = run_architecture_audit(
        classes, rels, strategy=strategy, contract=contract, groups=groups)
    g = mg.graph
    names = sorted(g.nodes)

    def resolve(q):
        # tolerant match: exact (case-insensitive), then suffix, then
        # substring — so 'Builder/ind', 'builder', 'ind' all land.
        ql = q.strip().lower()
        exact = [n for n in names if n.lower() == ql]
        if exact:
            return exact
        suf = [n for n in names if n.lower().endswith(ql) or ql.endswith(n.lower())]
        if suf:
            return suf
        return [n for n in names if ql in n.lower()]

    if not from_module and not to_module:
        if g.number_of_edges() == 0:
            return (f"Grouping '{mg.strategy}' produced {len(names)} module(s) "
                    f"with no cross-module dependencies: {', '.join(names)}")
        lines = [f"Module dependencies (grouping: {mg.strategy}; "
                 f"{len(names)} modules). Each row: A → B  weight×  [kinds]"]
        for s, t, d in sorted(g.edges(data=True),
                              key=lambda e: (-e[2]["weight"], e[0], e[1])):
            kinds = ", ".join(sorted(d["kinds"]))
            lines.append(f"  {s} → {t}   {d['weight']}×   [{kinds}]")
        lines.append("\nTo see the class-level references behind one edge, "
                     "call module_dependencies with from_module and to_module.")
        return "\n".join(lines)

    src = resolve(from_module) if from_module else names
    dst = resolve(to_module) if to_module else names
    if from_module and not src:
        return f"No module matches '{from_module}'. Modules: {', '.join(names)}"
    if to_module and not dst:
        return f"No module matches '{to_module}'. Modules: {', '.join(names)}"

    out = []
    for s in src:
        for t in dst:
            if s == t or not g.has_edge(s, t):
                continue
            d = g[s][t]
            out.append(f"{s} → {t}: {d['weight']} class-level "
                       f"reference(s) [{', '.join(sorted(d['kinds']))}]")
            for ev in d["evidence"]:
                out.append(f"    · {ev}")
    if not out:
        a = from_module or "(any)"
        b = to_module or "(any)"
        return (f"No dependency from module '{a}' to '{b}' under grouping "
                f"'{mg.strategy}'. Modules: {', '.join(names)}")
    return "\n".join(out)


def _architecture_conclusion(ctx):
    """The coherent, global architecture design conclusion. Runs the
    accumulative agent loop (Tier 2): it REUSES the per-module analyses
    from the DB (running/resuming Tier 1 first if needed) and weaves them
    into one prioritized system-level verdict — each module judged in the
    running context of the whole. Needs an LLM key and a prior audit."""
    if not ctx.db.get_arch_audit():
        return "No architecture audit yet. Run architecture_audit first."
    if not _llm_ready(ctx):
        return ("The global conclusion needs an LLM (set LLM_API_KEY). The "
                "deterministic findings are available via architecture_audit.")
    from .architect import ArchitectReviewer, synthesize_conclusion
    ArchitectReviewer(ctx.llm, ctx.db).run()      # ensure/resume Tier-1
    result = synthesize_conclusion(ctx.llm, ctx.db)
    if not result:
        return "Could not synthesize a conclusion (no module reviews)."
    lines = ["# Architecture conclusion", result["summary"], ""]
    if result.get("priorities"):
        lines.append("Priorities:")
        for i, p in enumerate(result["priorities"], 1):
            mods = ", ".join(p.get("modules", []))
            lines.append(f"  {i}. {p.get('title', '')}"
                         + (f"  [{mods}]" if mods else ""))
            if p.get("why"):
                lines.append(f"     why: {p['why']}")
    return "\n".join(lines)


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
                 "kind and file:line evidence. direction=outgoing (what the "
                 "class uses, default), incoming (who uses the class — reverse "
                 "dependents), or both. Short class names are resolved; an "
                 "ambiguous name returns the scoped candidates.",
                 _obj({"class_qname": {"type": "string",
                                       "description": "class name (short or scoped)"},
                       "direction": {"type": "string",
                                     "description": "outgoing|incoming|both"},
                       "limit": {"type": "integer"}}),
                 _get_relationships),
        ToolSpec("describe_class",
                 "Full profile of ONE class for detailed analysis or "
                 "comparing two classes: kind, file, size (lines), its "
                 "methods and fields, base classes, subclasses, what it "
                 "depends on and who depends on it — all grounded in the DB. "
                 "Use this (once per class) to analyze or compare specific "
                 "classes. Scope-aware: give a short name; if it collides "
                 "across namespaces the tool returns the scoped candidates so "
                 "you pick the right one. Requires a scan.",
                 _obj({"name": {"type": "string",
                                "description": "class name (short or full scoped)"}},
                      ["name"]),
                 _describe_class),
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
        ToolSpec("module_dependencies",
                 "Deterministic module-level dependency inspector — the "
                 "correct way to answer 'module A depends on module B N "
                 "times, what are the N?'. Modules are computed here (folder/"
                 "namespace/community), NOT stored as a DB column, so plain "
                 "SQL over entities CANNOT answer module questions. No args → "
                 "the full module→module dependency matrix; from_module + "
                 "to_module → the exact class-level references (with file:line) "
                 "backing that one module edge. No LLM. Requires a scan.",
                 _obj({"from_module": {"type": "string",
                                       "description": "source module name (tolerant match)"},
                       "to_module": {"type": "string",
                                     "description": "target module name (tolerant match)"},
                       "strategy": {"type": "string",
                                    "description": "auto|folder|namespace|community"}}),
                 _module_dependencies),
        ToolSpec("architecture_conclusion",
                 "The coherent GLOBAL architecture design verdict: an "
                 "accumulative loop weaves the per-module analyses into one "
                 "prioritized system-level conclusion (each module judged in "
                 "the context of the whole). Use when the user wants the big "
                 "picture / overall design assessment, not a single finding. "
                 "Needs an LLM key + a prior scan/audit.",
                 _obj(), _architecture_conclusion),
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
