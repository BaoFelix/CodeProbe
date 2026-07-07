"""architect — architecture-level (module) diagnosis.

Public surface:
    run_architecture_audit(classes, relationships) -> (findings, module_graph)
    format_findings(findings, module_graph) -> str
"""
from .audit import run_architecture_audit, format_findings
from .contract import (RuleContract, ArchRule, Group, Finding,
                       load_universal_contract)
from .modules import ModuleBuilder, ModuleGraph
from .checker import StructuralChecker, RULE_CHECKERS
from .compiler import RuleCompiler
from .verifier import Verifier
from .decouple import plan_decoupling, format_plans, DecouplePlan, Cut
from .baseline import (load_baseline, save_baseline, partition,
                       resolved_keys, DEFAULT_BASELINE_NAME)
from .persist import audit_payload

__all__ = [
    "run_architecture_audit", "format_findings",
    "RuleContract", "ArchRule", "Group", "Finding", "load_universal_contract",
    "ModuleBuilder", "ModuleGraph", "StructuralChecker", "RULE_CHECKERS",
    "RuleCompiler", "Verifier",
    "plan_decoupling", "format_plans", "DecouplePlan", "Cut",
    "load_baseline", "save_baseline", "partition", "resolved_keys",
    "DEFAULT_BASELINE_NAME", "audit_payload",
]
