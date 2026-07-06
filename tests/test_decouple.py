"""
Decoupling-planner tests — the "surgical plan" logic.

The three properties that make the plan trustworthy:
  1. it cuts the CHEAPEST edges (minimum feedback set by reference count),
  2. it picks the mechanism from the REAL relationship kinds,
  3. the refactor order is build-safe (dependencies before dependents).
"""
from tool.architect import ModuleBuilder, plan_decoupling, format_plans
from conftest import make_class, make_rel


def plans_for(classes, rels):
    mg = ModuleBuilder.build(classes, rels, strategy="folder")
    return plan_decoupling(mg)


class TestCutSelection:
    def test_asymmetric_two_cycle_cuts_cheaper_direction(self):
        # A→B has 1 reference, B→A has 3 → must cut A→B (effort 1, not 3).
        classes = [make_class("A1", "A"), make_class("A2", "A"),
                   make_class("A3", "A"), make_class("B1", "B")]
        rels = [make_rel("A1", "B1", "depends", "A1.hxx", 10),
                make_rel("B1", "A1", "composes"), make_rel("B1", "A2"),
                make_rel("B1", "A3")]
        (plan,) = plans_for(classes, rels)
        assert len(plan.cuts) == 1
        cut = plan.cuts[0]
        assert (cut.source, cut.target) == ("A", "B")
        assert plan.effort == 1

    def test_three_ring_cuts_lightest_edge(self):
        # ring A→B(×2) → C(×1 via B→C) → A(×3): lightest is B→C.
        classes = [make_class("A1", "A"), make_class("A2", "A"),
                   make_class("B1", "B"),
                   make_class("C1", "C"), make_class("C2", "C"),
                   make_class("C3", "C")]
        rels = [make_rel("A1", "B1"), make_rel("A2", "B1"),
                make_rel("B1", "C1"),
                make_rel("C1", "A1"), make_rel("C2", "A1"),
                make_rel("C3", "A2")]
        (plan,) = plans_for(classes, rels)
        assert [(c.source, c.target) for c in plan.cuts] == [("B", "C")]

    def test_no_cycle_no_plan(self):
        classes = [make_class("A1", "A"), make_class("B1", "B")]
        rels = [make_rel("A1", "B1")]
        assert plans_for(classes, rels) == []
        assert "No module cycles" in format_plans([])


class TestPrescription:
    def test_inheritance_coupling_gets_extract_base(self):
        # X→Y is one inherits reference (kind-cost 3); Y→X is four depends
        # references (cost 4) — the inherits side is still the cheaper cut,
        # and an inheritance cut must prescribe extract-shared-base.
        classes = [make_class("X1", "X"), make_class("Y1", "Y")]
        rels = [make_rel("X1", "Y1", "inherits")] + \
               [make_rel("Y1", "X1", "depends", f"y{i}.hxx", i)
                for i in range(4)]
        (plan,) = plans_for(classes, rels)
        cut = plan.cuts[0]
        assert (cut.source, cut.target) == ("X", "Y")
        assert "extract the shared base" in cut.mechanism

    def test_equal_refs_prefers_cutting_depends_over_inherits(self):
        # Same reference count both ways (1 vs 1), but one direction is
        # inheritance: the planner must sever the depends edge — cutting a
        # base-class relationship is the structurally harder surgery.
        classes = [make_class("X1", "X"), make_class("Y1", "Y")]
        rels = [make_rel("X1", "Y1", "inherits"),
                make_rel("Y1", "X1", "depends")]
        (plan,) = plans_for(classes, rels)
        assert (plan.cuts[0].source, plan.cuts[0].target) == ("Y", "X")
        assert "Dependency inversion" in plan.cuts[0].mechanism

    def test_usage_coupling_gets_dependency_inversion(self):
        classes = [make_class("A1", "A"), make_class("B1", "B")]
        rels = [make_rel("A1", "B1", "depends"),
                make_rel("B1", "A1", "composes"),
                make_rel("B1", "A1", "depends", "b2.hxx", 2)]
        (plan,) = plans_for(classes, rels)
        assert "Dependency inversion" in plan.cuts[0].mechanism

    def test_cut_lists_concrete_references(self):
        classes = [make_class("A1", "A"), make_class("B1", "B")]
        rels = [make_rel("A1", "B1", "depends", "A1.hxx", 10),
                make_rel("B1", "A1", "composes"),
                make_rel("B1", "A1", "depends", "b.hxx", 3)]
        (plan,) = plans_for(classes, rels)
        assert any("A1.hxx:10" in e for e in plan.cuts[0].evidence)


class TestRefactorOrder:
    def test_order_is_dependencies_first(self):
        # After cutting B→C, remaining edges: A→B, C→A.
        # Safe order: B (depends on nothing), then A (on B), then C (on A).
        classes = [make_class("A1", "A"), make_class("A2", "A"),
                   make_class("B1", "B"),
                   make_class("C1", "C"), make_class("C2", "C"),
                   make_class("C3", "C")]
        rels = [make_rel("A1", "B1"), make_rel("A2", "B1"),
                make_rel("B1", "C1"),
                make_rel("C1", "A1"), make_rel("C2", "A1"),
                make_rel("C3", "A2")]
        (plan,) = plans_for(classes, rels)
        order = plan.kept_order
        assert order.index("B") < order.index("A") < order.index("C")
