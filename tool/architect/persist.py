"""
persist.py — serialize the deterministic architecture audit into one JSON
payload the DB stores and the report + LLM tiers consume.

Pure/deterministic: it turns (findings, ModuleGraph, decoupling plans) into
plain dicts — module nodes (with in-cycle / god-module highlighting), module
edges, findings, and decoupling plans. No LLM, no I/O.
"""
import networkx as nx


def audit_payload(findings, mg, plans, unresolved_pct=None):
    """(findings, ModuleGraph, list[DecouplePlan]) -> report/DB payload dict."""
    g = mg.graph

    in_cycle = set()
    for scc in nx.strongly_connected_components(g):
        if len(scc) > 1:
            in_cycle |= set(scc)
    god = {f.subject[0] for f in findings
           if f.kind == "god_module" and f.subject}

    nodes = [{
        "id": n,
        "members": sorted(mg.members.get(n, [])),
        "size": len(mg.members.get(n, [])),
        "in_degree": g.in_degree(n),
        "out_degree": g.out_degree(n),
        "in_cycle": n in in_cycle,
        "is_god": n in god,
    } for n in g.nodes]

    edges = [{
        "source": u, "target": v,
        "weight": d.get("weight", 1),
        "kinds": sorted(k for k in d.get("kinds", set()) if k),
        "evidence": d.get("evidence", [])[:8],
        "in_cycle": u in in_cycle and v in in_cycle,
    } for u, v, d in g.edges(data=True)]

    findings_out = [{
        "kind": f.kind, "severity": f.severity, "title": f.title,
        "detail": f.detail, "modules": f.modules,
        "subject": f.subject or f.modules, "evidence": f.evidence[:8],
    } for f in findings]

    plans_out = [{
        "modules": p.modules, "effort": p.effort, "kept_order": p.kept_order,
        "cuts": [{"source": c.source, "target": c.target, "weight": c.weight,
                  "mechanism": c.mechanism, "evidence": c.evidence[:6]}
                 for c in p.cuts],
    } for p in plans]

    return {
        "strategy": mg.strategy,
        "module_count": g.number_of_nodes(),
        "edge_count": g.number_of_edges(),
        "unresolved_pct": unresolved_pct,
        "nodes": nodes,
        "edges": edges,
        "findings": findings_out,
        "decoupling": plans_out,
    }
