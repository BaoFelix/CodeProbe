"""
baseline.py — freeze existing architecture debt so CI can enforce
"no NEW violations" without drowning in a legacy codebase's backlog.

The problem this solves: point the audit at a 7,000-class monolith and it
finds hundreds of real pre-existing violations. Failing a build on ALL of
them is useless — the team disables the check by lunchtime. The fix is a
BASELINE (a.k.a. ratchet / FreezingArchRule): snapshot today's findings,
then only report the ones that AREN'T in the snapshot.

Ratchet semantics — the bar can only tighten:
  · you cannot add a new violation (it isn't in the baseline → reported)
  · a fixed violation drops out and is re-frozen out on the next update
    (it can never silently come back)

The baseline is a plain JSON file meant to be committed to the repo, like
dependency-cruiser's baseline or ArchUnit's frozen store. Everything here
is pure I/O + set logic — no graph work, no LLM.
"""
import json
from pathlib import Path

DEFAULT_BASELINE_NAME = "architecture.baseline.json"


def load_baseline(path):
    """Return the frozen finding keys as a set (empty if no baseline)."""
    p = Path(path)
    if not p.exists():
        return set()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return set(data.get("frozen", []))
    except (json.JSONDecodeError, OSError):
        return set()


def save_baseline(path, findings):
    """Freeze the current findings' keys. Returns how many were frozen."""
    keys = sorted({f.key() for f in findings})
    Path(path).write_text(
        json.dumps({"frozen": keys, "count": len(keys)}, indent=2),
        encoding="utf-8")
    return len(keys)


def partition(findings, baseline_keys):
    """Split findings into (new, known) against a frozen key set.
    `new` = must be reported / fail CI; `known` = accepted existing debt."""
    new, known = [], []
    for f in findings:
        (known if f.key() in baseline_keys else new).append(f)
    return new, known


def resolved_keys(findings, baseline_keys):
    """Baseline entries that no longer appear — debt that got fixed.
    Surfaced so the user knows to re-freeze (the ratchet tightening)."""
    live = {f.key() for f in findings}
    return baseline_keys - live
