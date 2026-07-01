"""
checker.py — StructuralChecker: run each rule as a graph query over the
module graph. THIS IS THE MOAT.

No LLM. Every Finding is born from a real module edge that is itself backed
by class edges with file:line. The LLM (later phases) only verifies and
explains what this produces — it can never invent a Finding.

Adding a rule = adding one function to RULE_CHECKERS. Each checker has the
signature:  (rule, module_graph) -> list[Finding].
"""
import math

import networkx as nx

from .contract import Finding


def check_no_module_cycle(rule, mg):
    """Modules that depend on each other (directly or transitively) form a
    strongly-connected component of size > 1. That means they cannot be
    built, tested, owned, or deployed independently — a classic structural
    tangle."""
    g = mg.graph
    out = []
    for scc in nx.strongly_connected_components(g):
        if len(scc) < 2:
            continue
        mods = sorted(scc)
        ev = []
        for u in scc:
            for v in g.successors(u):
                if v in scc:
                    ev += g[u][v]["evidence"]
        out.append(Finding(
            rule_id=rule.id, kind=rule.kind, severity="high",
            title=f"Module cycle: {' ↔ '.join(mods)}",
            detail=("These modules depend on each other, so none can be "
                    "changed, tested, or reused in isolation. Break the "
                    "cycle by extracting the shared piece or inverting one "
                    "dependency behind an interface."),
            modules=mods, evidence=ev[:12]))
    return out


def check_god_module(rule, mg):
    """A module that almost everything depends on. If it changes, the blast
    radius is the whole system. Flagged when its in-degree (how many other
    modules depend on it) is both an outlier and absolute-large."""
    g = mg.graph
    m = g.number_of_nodes()
    if m < 4:                       # too small to have a meaningful hub
        return []
    ratio = rule.params.get("ratio", 0.6)
    floor = rule.params.get("min_dependents", 3)
    threshold = max(floor, math.ceil(ratio * (m - 1)))
    out = []
    for node in g.nodes:
        deps = list(g.predecessors(node))
        if len(deps) >= threshold:
            ev = []
            for d in deps:
                ev += g[d][node]["evidence"]
            out.append(Finding(
                rule_id=rule.id, kind=rule.kind, severity="high",
                title=f"God module: {node}",
                detail=(f"{len(deps)} of {m - 1} other modules depend on "
                        f"{node}. It is a single point of change for the "
                        f"whole system; consider splitting it or hiding it "
                        f"behind a narrow interface."),
                modules=[node] + sorted(deps), evidence=ev[:12]))
    return out


def check_forbid_dependency(rule, mg):
    """A user rule: module `from` must not depend on module `to`. Flags the
    edge if it exists. `from`/`to` are group (module) names produced by the
    explicit grouping the RuleCompiler set up."""
    g = mg.graph
    frm, to = rule.params.get("from"), rule.params.get("to")
    if not frm or not to or not g.has_edge(frm, to):
        return []
    return [Finding(
        rule_id=rule.id, kind=rule.kind, severity="high",
        title=f"Forbidden dependency: {frm} → {to}",
        detail=(rule.text or f"{frm} must not depend on {to}.")
               + f"  ({g[frm][to]['weight']} offending reference(s))",
        modules=[frm, to], evidence=g[frm][to]["evidence"][:12])]


# The registry: rule.kind → checker. Extend here to add a rule.
RULE_CHECKERS = {
    "no_module_cycle": check_no_module_cycle,
    "god_module": check_god_module,
    "forbid_dependency": check_forbid_dependency,
}


class StructuralChecker:
    @staticmethod
    def check(contract, module_graph):
        """Run every rule in the contract that we have a checker for.
        Unknown kinds are skipped silently (a later, richer checker or the
        LLM path may handle them)."""
        findings = []
        for rule in contract.rules:
            fn = RULE_CHECKERS.get(rule.kind)
            if fn:
                findings.extend(fn(rule, module_graph))
        return findings
