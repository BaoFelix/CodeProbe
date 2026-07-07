"""
Baseline / ratchet tests — freeze legacy debt, gate only NEW violations.

The three properties that make it a real ratchet:
  · a NEW violation is reported (fails CI); a frozen one is suppressed,
  · a fixed violation surfaces as 'resolved' (so the bar can tighten),
  · finding identity is stable across runs (keyed on kind+modules, not
    line numbers), so shifting evidence doesn't look like a new finding.
"""
from tool.architect import (Finding, save_baseline, load_baseline,
                            partition, resolved_keys)


def _f(kind, modules, line=1):
    return Finding("r", kind, f"{kind} {modules}", "detail",
                   list(modules), [f"x.hxx:{line}"])


class TestFindingKey:
    def test_key_is_kind_plus_sorted_modules(self):
        assert _f("no_module_cycle", ["B", "A"]).key() == "no_module_cycle:A+B"

    def test_key_ignores_evidence_lines(self):
        # same structural problem, evidence moved → same key
        assert _f("god_module", ["H"], line=10).key() \
            == _f("god_module", ["H"], line=999).key()


class TestFreezeAndCheck:
    def test_freeze_then_no_new(self, tmp_path):
        bl = tmp_path / "baseline.json"
        day1 = [_f("no_module_cycle", ["A", "B"]), _f("god_module", ["H"])]
        assert save_baseline(bl, day1) == 2
        frozen = load_baseline(bl)
        new, known = partition(day1, frozen)
        assert new == [] and len(known) == 2       # nothing new same day

    def test_new_violation_is_reported(self, tmp_path):
        bl = tmp_path / "baseline.json"
        save_baseline(bl, [_f("god_module", ["H"])])
        frozen = load_baseline(bl)
        today = [_f("god_module", ["H"]), _f("inverted_core", ["C", "V"])]
        new, known = partition(today, frozen)
        assert [f.key() for f in new] == ["inverted_core:C+V"]
        assert [f.key() for f in known] == ["god_module:H"]

    def test_fixed_violation_shows_as_resolved(self, tmp_path):
        bl = tmp_path / "baseline.json"
        save_baseline(bl, [_f("no_module_cycle", ["A", "B"]),
                           _f("god_module", ["H"])])
        frozen = load_baseline(bl)
        today = [_f("god_module", ["H"])]          # cycle got fixed
        assert resolved_keys(today, frozen) == {"no_module_cycle:A+B"}

    def test_missing_baseline_is_empty_set(self, tmp_path):
        assert load_baseline(tmp_path / "nope.json") == set()

    def test_corrupt_baseline_is_empty_not_crash(self, tmp_path):
        bad = tmp_path / "b.json"
        bad.write_text("{ not valid json")
        assert load_baseline(bad) == set()
