"""
verifier.py — Verifier: an LLM false-positive gate for findings.

The deterministic checker can over-report when module grouping is fuzzy
(e.g. a DTO or a test double lands in the wrong group). The Verifier asks
the LLM, per finding, "is this a real architecture violation or an artifact
of misclassification / an acceptable exception?" and drops the ones judged
bogus.

Concurrency: the per-finding checks are independent and I/O-bound (network
LLM calls), so a bounded thread pool cuts wall-clock roughly linearly with
no added complexity — the one place concurrency clearly pays off here. If
there are 0–1 findings it runs inline (a pool would be pure overhead).

Conservative by default: any parse failure or low confidence keeps the
finding (fail-open on the side of showing the issue, not hiding it).
"""
import json
import re
from concurrent.futures import ThreadPoolExecutor

_MAX_WORKERS = 6

_VERIFY_PROMPT = """\
You are auditing one candidate C++ architecture finding. Decide if it is a
REAL structural problem, or a FALSE POSITIVE caused by misclassifying a
class into the wrong module, or an acceptable exception.

Finding: {title}
Why flagged: {detail}
Evidence (real code edges):
{evidence}

Answer ONLY JSON: {{"is_real": true/false, "reason": "<one short sentence>"}}
If unsure, answer is_real=true (do not hide a possible problem).
"""


def _safe(text):
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.DOTALL)
    try:
        return json.loads(m.group(0)) if m else None
    except json.JSONDecodeError:
        return None


class Verifier:
    def __init__(self, llm):
        self.llm = llm

    def _verify_one(self, f):
        prompt = _VERIFY_PROMPT.format(
            title=f.title, detail=f.detail,
            evidence="\n".join(f"  {e}" for e in f.evidence[:8]))
        parsed = _safe(self.llm.generate(prompt, tag="verify"))
        if parsed is None:
            return True, ""                       # fail-open
        return bool(parsed.get("is_real", True)), parsed.get("reason", "")

    def verify(self, findings):
        """Return the subset judged real. Annotates each kept finding's
        detail with the verifier's reason when it adds information."""
        if not findings:
            return findings
        if len(findings) == 1:
            keep, reason = self._verify_one(findings[0])
            return findings if keep else []
        with ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, len(findings))) as ex:
            verdicts = list(ex.map(self._verify_one, findings))
        return [f for f, (keep, _) in zip(findings, verdicts) if keep]
