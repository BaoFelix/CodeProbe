"""
arch_review.py — architecture-level LLM review, in two tiers.

Both tiers are GROUNDED: they read the deterministic audit (persisted by
architecture_audit) and only EXPLAIN it. The LLM never decides whether a
module is a god module or in a cycle — the graph already did.

  Tier 1 (ArchitectReviewer, default, CONCURRENT): one grounded assessment
     per module, anchored on that module's own findings/edges/decoupling.
     Fast (fan-out), resumable, per-module — but each module judged on its
     own. Stored in arch_module_review.

  Tier 2 (synthesize_conclusion, on-demand, ACCUMULATIVE): a sequential
     loop that REUSES the Tier-1 per-module analyses (no re-analysis) and
     weaves them into one coherent, prioritized system conclusion — each
     step judging a module in the running context of the whole. Cheap
     (reuses cache), coherent. Stored in design_critic_module as
     'architecture'.
"""
import json
from concurrent.futures import ThreadPoolExecutor

from ..config import LLM_MAX_WORKERS
from ..db import graph_fingerprint


def _safe_json(text):
    if not text:
        return None
    m = text.find("{")
    if m < 0:
        return None
    depth, end = 0, -1
    for i in range(m, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end < 0:
        return None
    try:
        return json.loads(text[m:end])
    except json.JSONDecodeError:
        return None


def _index_by_module(payload):
    """Group findings, module-edges, and decoupling plans by module name."""
    findings, edges, plans = {}, {}, {}
    for f in payload.get("findings", []):
        for m in f.get("modules", []):
            findings.setdefault(m, []).append(f)
    for e in payload.get("edges", []):
        edges.setdefault(e["source"], []).append(e)
        edges.setdefault(e["target"], []).append(e)
    for p in payload.get("decoupling", []):
        for m in p.get("modules", []):
            plans.setdefault(m, []).append(p)
    return findings, edges, plans


_MODULE_PROMPT = """\
You assess ONE module of a C++ system's architecture. Ground every claim
in the facts below — do not invent structure the facts don't state.

Module: {name}  ({size} classes)
Members: {members}
Dependency profile: depends on {out_degree} module(s); depended on by \
{in_degree} module(s).

Deterministic findings involving this module:
{findings}

Module dependencies (edges, with reference counts):
{edges}

Decoupling plan (if any):
{plans}

Return ONLY this JSON:
{{"role": "<one line: this module's job in the system>",
  "assessment": "<2-3 sentences on its architectural health, grounded in the facts>",
  "risks": ["<risk tied to a specific finding above>"],
  "recommendation": "<the single most important change, or 'none — healthy'>"}}
"""


class ArchitectReviewer:
    """Tier 1: concurrent, grounded, per-module architecture review."""

    def __init__(self, llm, db):
        self.llm = llm
        self.db = db

    def run(self):
        audit = self.db.get_arch_audit()
        if not audit:
            print("  ⚠ No architecture audit to review — run the audit first.")
            return False
        payload = audit["payload"]
        graph_hash = audit["graph_hash"] or graph_fingerprint(
            self.db.get_relationships())
        nodes = payload.get("nodes", [])
        if not nodes:
            print("  ⚠ No modules to review.")
            return False

        findings_by, edges_by, plans_by = _index_by_module(payload)

        # Resume: skip modules already reviewed for THIS graph. (Modules
        # are few, so a timeout/unparseable one is simply re-attempted next
        # run — no raw_response bookkeeping needed here.)
        stored = self.db.get_arch_module_reviews() or []
        done = {r["module_name"] for r in stored
                if (r["graph_hash"] or graph_hash) == graph_hash
                and r["parsed_json"]}
        todo = [n for n in nodes if n["id"] not in done]
        print(f"  [arch review] {len(nodes)} module(s); "
              f"{len(done)} done, {len(todo)} to do "
              f"(up to {LLM_MAX_WORKERS} in parallel)")

        def analyze_one(node):
            prompt = _MODULE_PROMPT.format(
                name=node["id"], size=node["size"],
                members=", ".join(node.get("members", [])[:20]) or "(none)",
                out_degree=node["out_degree"], in_degree=node["in_degree"],
                findings=_fmt_findings(findings_by.get(node["id"], [])),
                edges=_fmt_edges(node["id"], edges_by.get(node["id"], [])),
                plans=_fmt_plans(plans_by.get(node["id"], [])))
            resp = self.llm.generate(prompt, tag=f"arch_module_{node['id'][:16]}")
            return node["id"], _safe_json(resp), resp

        completed = 0
        if todo:
            with ThreadPoolExecutor(
                    max_workers=min(LLM_MAX_WORKERS, len(todo))) as ex:
                results = []
                for fut in [ex.submit(analyze_one, n) for n in todo]:
                    try:
                        results.append(fut.result())
                    except Exception as e:
                        print(f"    ✗ module worker failed: {e}")
            for mid, parsed, resp in results:
                self.db.save_arch_module_review(mid, parsed, graph_hash=graph_hash)
                if resp is not None:
                    completed += 1
                    print(f"    ✓ {mid}")

        if todo and completed == 0 and not done:
            print("  ✗ LLM unreachable — every module review failed.")
            return False
        return True


# ── formatting helpers (shared by the prompt builders) ──────────────

def _fmt_findings(findings):
    if not findings:
        return "  (none — this module is structurally clean)"
    return "\n".join(f"  - [{f['severity']}] {f['title']}: {f['detail']}"
                     for f in findings)


def _fmt_edges(name, edges):
    out = []
    for e in edges[:12]:
        arrow = "→" if e["source"] == name else "←"
        other = e["target"] if e["source"] == name else e["source"]
        cyc = " (CYCLE)" if e.get("in_cycle") else ""
        out.append(f"  {arrow} {other}  ×{e['weight']}{cyc}")
    return "\n".join(out) if out else "  (none)"


def _fmt_plans(plans):
    if not plans:
        return "  (none)"
    lines = []
    for p in plans:
        lines.append(f"  cycle {' ↔ '.join(p['modules'])} (effort {p['effort']}):")
        for c in p.get("cuts", []):
            lines.append(f"    cut {c['source']}→{c['target']}: {c['mechanism'][:120]}")
    return "\n".join(lines)


# ── Tier 2: accumulative global synthesis (on-demand) ───────────────

_SYNTH_STEP_PROMPT = """\
You are building ONE coherent architectural conclusion about a C++ system,
one module at a time, keeping a running summary.

Running conclusion so far:
{running}

Now fold in this module's pre-computed analysis (do NOT re-derive it):
Module: {name}
{module_json}

Return ONLY JSON:
{{"running": "<the updated running conclusion — a tight, coherent narrative
   of the system's architecture and its main structural problems so far,
   integrating this module. Keep it under ~200 words; merge, don't append>",
  "note": "<one sentence: what THIS module added to the picture>"}}
"""

_SYNTH_FINAL_PROMPT = """\
Turn this running architectural conclusion into a final, prioritized verdict.

Running conclusion:
{running}

Return ONLY JSON:
{{"summary": "<2-4 sentence system-level architectural assessment>",
  "priorities": [
     {{"title": "<the most important structural fix>",
       "why": "<grounded reason>",
       "modules": ["<involved modules>"]}}
  ]}}
"""


def synthesize_conclusion(llm, db):
    """Tier 2: accumulative loop over the Tier-1 per-module analyses (read
    from the DB — NOT recomputed) → one coherent, prioritized conclusion.
    Sequential by design: each module is judged in the running context of
    the whole. Persisted to design_critic_module as 'architecture'."""
    audit = db.get_arch_audit()
    reviews = {r["module_name"]: json.loads(r["parsed_json"])
               for r in (db.get_arch_module_reviews() or [])
               if r["parsed_json"]}
    if not audit or not reviews:
        return None
    graph_hash = audit["graph_hash"]
    payload = audit["payload"]

    # Order: most-problematic modules first, so the narrative builds from
    # the trouble spots outward (god modules, then in-cycle, then the rest).
    def severity_rank(n):
        return (0 if n["is_god"] else 1 if n["in_cycle"] else 2, -n["in_degree"])
    ordered = [n["id"] for n in sorted(payload["nodes"], key=severity_rank)
               if n["id"] in reviews]

    running = ""
    for mid in ordered:
        parsed = _safe_json(llm.generate(
            _SYNTH_STEP_PROMPT.format(
                running=running or "(nothing yet)",
                name=mid, module_json=json.dumps(reviews[mid], ensure_ascii=False)),
            tag=f"arch_synth_{mid[:16]}"))
        if parsed and parsed.get("running"):
            running = parsed["running"]      # accumulate (bounded, evolving)

    if not running:
        return None
    final = _safe_json(llm.generate(
        _SYNTH_FINAL_PROMPT.format(running=running), tag="arch_synth_final"))
    result = {"summary": (final or {}).get("summary", running),
              "priorities": (final or {}).get("priorities", []),
              "running": running}
    db.save_design_module("architecture", "arch-synth", "", result,
                          graph_hash=graph_hash)
    return result
