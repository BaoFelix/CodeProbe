"""
contract.py — the rule contract (the枢纽 / hinge of the whole capability).

Two sources produce the SAME contract:
  · the built-in universal principles (load_universal_contract) — zero config
  · the user's plain-language rules, compiled by an LLM (a later phase)

The StructuralChecker consumes a RuleContract and does not care where it
came from. That separation is what lets us ship a zero-config "health check"
today and add user-defined rules later without touching the checker.

Everything here is plain data — no logic, no I/O.
"""
from dataclasses import dataclass, field


@dataclass
class Group:
    """A named architectural region the user cares about (e.g. "UI",
    "Infra"). `match` is a list of patterns; a class belongs if ANY matches
    its file path, short name, or qualified name (fnmatch globs). Membership
    is resolved deterministically at check time — the LLM only drafts these
    patterns, it never decides membership itself."""
    name: str
    match: list = field(default_factory=list)


@dataclass
class ArchRule:
    """One checkable architecture rule. `kind` selects a checker in
    checker.RULE_CHECKERS; `params` are that checker's knobs."""
    id: str
    kind: str
    params: dict = field(default_factory=dict)
    source: str = "universal"      # "universal" | "user"
    text: str = ""                 # the user's original sentence, if any


@dataclass
class RuleContract:
    rules: list = field(default_factory=list)    # list[ArchRule]
    groups: list = field(default_factory=list)   # list[Group] (user-defined)


@dataclass
class Finding:
    """One architecture-level problem, grounded in real edges.

    `modules` names the parts involved; `evidence` is a list of
    'ClassA --kind--> ClassB (file:line)' strings — the proof the caller
    can click through. No LLM may invent a Finding: every one is emitted by
    the deterministic checker from a real edge."""
    rule_id: str
    kind: str
    title: str
    detail: str
    modules: list = field(default_factory=list)
    evidence: list = field(default_factory=list)
    severity: str = "medium"       # "high" | "medium" | "low"


def load_universal_contract() -> RuleContract:
    """The built-in, no-config architecture principles. Each maps to a
    deterministic module-graph check. Kept deliberately small — a few
    checks that are always right beat many that are flaky."""
    return RuleContract(rules=[
        ArchRule("u.cycle", "no_module_cycle", source="universal"),
        ArchRule("u.god", "god_module",
                 params={"min_dependents": 3, "ratio": 0.6},
                 source="universal"),
        ArchRule("u.inverted", "inverted_core",
                 params={"margin": 0.3}, source="universal"),
    ])
