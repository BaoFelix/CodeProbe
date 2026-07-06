"""
Architecture-audit tests — the deterministic moat.

Each universal check gets: one synthetic graph that MUST trigger it, and
the clean fixture that must NOT (no false positives — the #1 trust rule).
"""
from tool.architect import (ModuleBuilder, Group, ArchRule, RuleContract,
                            run_architecture_audit, format_findings)
from conftest import make_class, make_rel


def audit(classes, rels, **kw):
    findings, mg = run_architecture_audit(classes, rels, **kw)
    return findings, mg


class TestModuleBuilder:
    def test_folder_grouping(self):
        classes = [make_class("A1", "A"), make_class("B1", "B")]
        mg = ModuleBuilder.build(classes, [], strategy="folder")
        assert mg.member_index == {"A1": "A", "B1": "B"}
        assert mg.strategy == "folder"

    def test_two_unrelated_util_folders_stay_distinct(self):
        # geometry/util and io/util must NOT merge into one phantom "util"
        # module — basename grouping would fabricate cycles/god-modules.
        classes = [
            {"qualified_name": "GeomHelper", "file_path": "src/geometry/util/GeomHelper.hxx"},
            {"qualified_name": "IoHelper",   "file_path": "src/io/util/IoHelper.hxx"},
        ]
        mg = ModuleBuilder.build(classes, [], strategy="folder")
        assert mg.member_index["GeomHelper"] != mg.member_index["IoHelper"]

    def test_explicit_groups_win(self):
        classes = [{"qualified_name": "AppView", "file_path": "ui/AppView.hxx"},
                   {"qualified_name": "DbConn", "file_path": "infra/DbConn.hxx"}]
        groups = [Group("UI", ["*View"]), Group("Infra", ["infra/**"])]
        mg = ModuleBuilder.build(classes, [], groups=groups)
        assert mg.member_index == {"AppView": "UI", "DbConn": "Infra"}
        assert mg.strategy == "explicit"

    def test_edges_aggregate_weight_kinds_evidence(self):
        classes = [make_class("A1", "A"), make_class("B1", "B")]
        rels = [make_rel("A1", "B1", "depends", "a.hxx", 1),
                make_rel("A1", "B1", "composes", "a.hxx", 2)]
        mg = ModuleBuilder.build(classes, rels, strategy="folder")
        edge = mg.graph["A"]["B"]
        assert edge["weight"] == 2
        assert edge["kinds"] == {"depends", "composes"}
        assert len(edge["evidence"]) == 2


class TestUniversalChecks:
    def test_module_cycle_detected(self):
        classes = [make_class("A1", "A"), make_class("B1", "B")]
        rels = [make_rel("A1", "B1"), make_rel("B1", "A1")]
        findings, _ = audit(classes, rels, strategy="folder")
        assert any(f.kind == "no_module_cycle" for f in findings)

    def test_god_module_detected(self):
        # H is depended on by all 4 other modules → god module.
        classes = [make_class(f"{m}1", m) for m in "ABCD"] + \
                  [make_class("H1", "H")]
        rels = [make_rel(f"{m}1", "H1") for m in "ABCD"]
        findings, _ = audit(classes, rels, strategy="folder")
        god = [f for f in findings if f.kind == "god_module"]
        assert god and "H" in god[0].modules

    def test_inverted_core_detected(self):
        # Core C (2 dependents) depends on volatile V (out-degree 2).
        classes = [make_class(n, m) for n, m in
                   [("A1", "A"), ("B1", "B"), ("C1", "C"),
                    ("V1", "V"), ("X1", "X"), ("Y1", "Y")]]
        rels = [make_rel("A1", "C1"), make_rel("B1", "C1"),
                make_rel("C1", "V1"),
                make_rel("V1", "X1"), make_rel("V1", "Y1")]
        findings, _ = audit(classes, rels, strategy="folder")
        inv = [f for f in findings if f.kind == "inverted_core"]
        assert inv and inv[0].modules == ["C", "V"]

    def test_forbid_dependency_user_rule(self):
        classes = [{"qualified_name": "AppView", "file_path": "ui/AppView.hxx"},
                   {"qualified_name": "DbConn", "file_path": "infra/DbConn.hxx"}]
        rels = [make_rel("AppView", "DbConn", "composes", "AppView.hxx", 88)]
        groups = [Group("UI", ["*View"]), Group("Infra", ["*Conn"])]
        contract = RuleContract(
            rules=[ArchRule("r1", "forbid_dependency",
                            {"from": "UI", "to": "Infra"}, "user",
                            "UI must not touch Infra")],
            groups=groups)
        findings, _ = audit(classes, rels, contract=contract, groups=groups)
        assert len(findings) == 1
        f = findings[0]
        assert f.kind == "forbid_dependency" and f.severity == "high"
        assert any("AppView.hxx:88" in e for e in f.evidence)

    def test_findings_carry_evidence(self):
        classes = [make_class("A1", "A"), make_class("B1", "B")]
        rels = [make_rel("A1", "B1", line=7), make_rel("B1", "A1", line=9)]
        findings, _ = audit(classes, rels, strategy="folder")
        for f in findings:
            assert f.evidence, f.title    # a finding without proof is banned


class TestNoFalsePositives:
    def test_clean_fixture_is_clean(self, scanned_ctx):
        classes = [dict(r) for r in scanned_ctx.db.get_classes()]
        rels = [dict(r) for r in scanned_ctx.db.get_relationships()]
        findings, mg = run_architecture_audit(classes, rels)
        assert findings == [], format_findings(findings, mg)
