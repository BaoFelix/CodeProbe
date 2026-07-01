"""
compiler.py — RuleCompiler: plain-language architecture rules → RuleContract.

The ONLY LLM step in the architecture-audit path. It does one narrow job:
translate the user's prose (from skills/architecture.md) into the same
contract the built-in universal rules use — group definitions (as
deterministic match patterns) plus forbid_dependency rules.

Grounding discipline: the LLM only DRAFTS patterns and picks from a fixed
rule vocabulary. It never decides membership or violations — the
deterministic ModuleBuilder/StructuralChecker do that. So a bad LLM output
can mis-scope a rule, but it can never fabricate a violation.
"""
import json
import re

from .contract import RuleContract, ArchRule, Group


_COMPILE_PROMPT = """\
You translate a team's plain-language C++ architecture rules into a strict
JSON contract. Do NOT judge the code — only restate the rules formally.

Allowed rule kinds (use ONLY these):
  - forbid_dependency : {{"from": "<group>", "to": "<group>"}}
      "group A must not depend on group B"

Define every group the rules mention with match patterns. A pattern is an
fnmatch glob tested against a class's file path, short name, OR qualified
name. Examples: "ui/**", "*View", "*Widget", "Infra::*".

Here are some real classes (name — file) to ground your patterns:
{sample}

The rules to translate:
{prose}

Return ONLY this JSON (no prose, no markdown fence):
{{"groups":[{{"name":"UI","match":["ui/**","*View"]}}],
  "rules":[{{"kind":"forbid_dependency","from":"UI","to":"Infra",
             "text":"<the original sentence this came from>"}}]}}
"""


def _safe_json(text):
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


class RuleCompiler:
    def __init__(self, llm):
        self.llm = llm

    def compile(self, prose, classes) -> RuleContract:
        """prose: the user's rules text. classes: DB rows. Returns a
        RuleContract with groups + rules (empty if compilation fails —
        the caller falls back to the universal contract)."""
        sample = "\n".join(
            f"  {c['qualified_name']} — {c.get('file_path') or '?'}"
            for c in classes[:60])
        prompt = _COMPILE_PROMPT.format(sample=sample, prose=prose)
        parsed = _safe_json(self.llm.generate(prompt, tag="rule_compiler"))
        if not parsed:
            return RuleContract(rules=[], groups=[])

        groups = [Group(name=g["name"], match=g.get("match", []))
                  for g in parsed.get("groups", []) if g.get("name")]
        rules = []
        for i, r in enumerate(parsed.get("rules", [])):
            kind = r.get("kind")
            if kind == "forbid_dependency" and r.get("from") and r.get("to"):
                rules.append(ArchRule(
                    id=f"user.{i}", kind=kind,
                    params={"from": r["from"], "to": r["to"]},
                    source="user", text=r.get("text", "")))
        return RuleContract(rules=rules, groups=groups)
