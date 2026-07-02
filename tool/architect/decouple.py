"""
decouple.py — the decoupling surgeon: turn a module cycle into an ordered,
evidence-backed refactoring plan.

This is what separates "your modules A and B form a cycle" (a health check)
from "cut A→B — it's the cheapest edge (2 references), invert it behind an
interface, then refactor in this order" (a surgical plan). Pure graph
algorithms, no LLM: the plan is computed, not guessed.

The three ideas, in order:

1. WHICH edges to cut — minimum feedback arc set: the cheapest set of edges
   whose removal makes the cycle disappear. Edge cost = its weight = how
   many real class-level references it aggregates (= how much code you must
   touch). Exact minimum is NP-hard in general, but module cycles are tiny
   (2–5 modules), so we solve exactly by trying subsets in increasing cost
   and fall back to a classic greedy (repeatedly drop the lightest edge on
   a remaining cycle) only if a cycle is unusually large.

2. HOW to cut each edge — the prescription. Chosen from the underlying
   class edges' kinds: mostly-`inherits` coupling means "extract the shared
   base into a module both sides can depend on"; anything else gets the
   standard Dependency Inversion move: the SOURCE declares an interface for
   what it needs, the TARGET implements it, wiring happens at composition
   root. Each prescription lists the exact class references (file:line)
   that must change — the work items.

3. WHAT order to refactor in — after the cuts the subgraph is a DAG, so a
   topological order gives a sequence in which each module only depends on
   already-refactored ones: every step keeps the build green.
"""
from dataclasses import dataclass, field
from itertools import combinations

import networkx as nx

# Above this many edges in one SCC we stop trying exact subsets (the search
# is combinatorial) and use the greedy heuristic instead. Real module
# cycles are far smaller; this is a safety valve, not a tuning knob.
_EXACT_SEARCH_MAX_EDGES = 12


@dataclass
class Cut:
    """One edge to sever, with the concrete work items and the mechanism."""
    source: str                 # module that must stop depending...
    target: str                 # ...on this module
    weight: int                 # how many class-level references back it
    evidence: list              # 'ClassA --kind--> ClassB (file:line)' strings
    mechanism: str              # human prescription: HOW to remove this edge


@dataclass
class DecouplePlan:
    modules: list               # the cycle's members
    cuts: list                  # list[Cut], the minimum-cost cut set
    kept_order: list            # topological refactor order after the cuts
    effort: int                 # total references to change (sum of weights)


# ── 1. which edges to cut ───────────────────────────────────────────

def _min_feedback_edges(sub):
    """Smallest-total-weight edge set whose removal makes `sub` acyclic.

    Exact for small SCCs: try all subsets of size 1, 2, … ordered so the
    first acyclic hit is minimal in cost among that size (good enough —
    a 1-edge cut always beats any 2-edge cut in disruption, even at equal
    reference count). Greedy fallback for oversized cycles.
    """
    edges = list(sub.edges(data="weight"))
    if len(edges) <= _EXACT_SEARCH_MAX_EDGES:
        for size in range(1, len(edges) + 1):
            best = None
            for combo in combinations(edges, size):
                trial = sub.copy()
                trial.remove_edges_from([(u, v) for u, v, _ in combo])
                if nx.is_directed_acyclic_graph(trial):
                    cost = sum(w for _, _, w in combo)
                    if best is None or cost < best[0]:
                        best = (cost, combo)
            if best:
                return [(u, v) for u, v, _ in best[1]]
    # Greedy: while a cycle remains, drop its lightest edge.
    trial = sub.copy()
    cuts = []
    while not nx.is_directed_acyclic_graph(trial):
        cycle = nx.find_cycle(trial)
        u, v = min(cycle, key=lambda e: trial[e[0]][e[1]]["weight"])[:2]
        cuts.append((u, v))
        trial.remove_edge(u, v)
    return cuts


# ── 2. how to cut one edge ──────────────────────────────────────────

def _prescribe(mg, u, v):
    """Pick the refactoring mechanism for removing edge u→v, based on what
    the underlying class references actually are (the edge carries its
    aggregated relationship kinds)."""
    kinds = mg.graph[u][v].get("kinds", set())
    if kinds and kinds <= {"inherits", "implements"}:
        return (f"The coupling is inheritance: extract the shared base "
                f"class(es) out of '{v}' into a new module that both "
                f"'{u}' and '{v}' may depend on. Neither side loses the "
                f"hierarchy; the cycle loses its edge.")
    return (f"Dependency inversion: define an interface inside '{u}' "
            f"describing exactly what it needs from '{v}'; have the "
            f"concrete class(es) in '{v}' implement it; construct and "
            f"inject the implementation at the composition root. "
            f"'{u}' then compiles without ever seeing '{v}'.")


# ── 3. the plan ─────────────────────────────────────────────────────

def plan_decoupling(mg):
    """One DecouplePlan per module cycle in the graph (largest first).
    Deterministic — same graph in, same plan out."""
    plans = []
    for scc in sorted(nx.strongly_connected_components(mg.graph),
                      key=len, reverse=True):
        if len(scc) < 2:
            continue
        sub = mg.graph.subgraph(scc).copy()
        cut_edges = _min_feedback_edges(sub)
        cuts = [Cut(source=u, target=v,
                    weight=sub[u][v]["weight"],
                    evidence=mg.graph[u][v]["evidence"][:8],
                    mechanism=_prescribe(mg, u, v))
                for u, v in cut_edges]
        sub.remove_edges_from(cut_edges)
        # Refactor targets-first: a module is safe to touch once everything
        # it still depends on has been handled.
        order = list(reversed(list(nx.topological_sort(sub))))
        plans.append(DecouplePlan(
            modules=sorted(scc), cuts=cuts, kept_order=order,
            effort=sum(c.weight for c in cuts)))
    return plans


def format_plans(plans) -> str:
    """Render plans as the plain text the agent/CLI hands to the user."""
    if not plans:
        return "No module cycles found — nothing to decouple."
    out = []
    for i, p in enumerate(plans, 1):
        out.append(f"Decoupling plan {i}: cycle {' ↔ '.join(p.modules)}   "
                   f"(total effort: {p.effort} reference(s) to change)")
        for j, c in enumerate(p.cuts, 1):
            out.append(f"  Cut {j}: sever {c.source} → {c.target}   "
                       f"({c.weight} reference(s) — the cheapest cut)")
            out.append(f"    how: {c.mechanism}")
            out.append(f"    references to change:")
            out += [f"      · {e}" for e in c.evidence]
        out.append(f"  Refactor order (each step keeps the build green): "
                   f"{'  →  '.join(p.kept_order)}")
        out.append("")
    return "\n".join(out)
