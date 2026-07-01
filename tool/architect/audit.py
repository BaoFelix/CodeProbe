"""
audit.py — the deterministic architecture audit (P2).

Ties the moat together with NO LLM:
    DB rows → ModuleBuilder → StructuralChecker(universal contract) → Findings

This is the zero-config, zero-API-key "architecture health check". Later
phases add the LLM layer (compile user rules, verify findings, explain in
prose) around this same core — but the core stays deterministic and
grounded.
"""
from .modules import ModuleBuilder
from .checker import StructuralChecker
from .contract import load_universal_contract


def run_architecture_audit(classes, relationships, strategy="auto",
                           contract=None):
    """Pure function: rows in, findings out. `classes` and `relationships`
    are DB rows (dict-like). Returns (findings, module_graph)."""
    mg = ModuleBuilder.build(classes, relationships, strategy=strategy)
    contract = contract or load_universal_contract()
    findings = StructuralChecker.check(contract, mg)
    # heaviest first: high severity, then more modules involved
    sev = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda f: (sev.get(f.severity, 1), -len(f.modules)))
    return findings, mg


def format_findings(findings, mg) -> str:
    """Render findings as plain text for the CLI / the agent's tool result."""
    header = (f"Modules: {mg.graph.number_of_nodes()} "
              f"(grouped by {mg.strategy}), "
              f"module dependencies: {mg.graph.number_of_edges()}")
    if not findings:
        return header + "\n✓ No architecture-level issues found."
    lines = [header, f"⚠ {len(findings)} architecture finding(s):", ""]
    for i, f in enumerate(findings, 1):
        lines.append(f"{i}. [{f.severity}] {f.title}")
        lines.append(f"   {f.detail}")
        if f.evidence:
            lines.append("   evidence:")
            lines += [f"     · {e}" for e in f.evidence[:5]]
            if len(f.evidence) > 5:
                lines.append(f"     · … and {len(f.evidence) - 5} more")
        lines.append("")
    return "\n".join(lines)
