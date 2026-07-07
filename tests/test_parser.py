"""
Parser tests — the accuracy the whole system stands on.

Focus: the S1 body-call mining (the newest, subtlest logic) plus the
grounding guarantees every edge must carry (kind + file:line evidence).
"""
from pathlib import Path

from tool.ts_parser import parse_file, parse_project


def _by_via(rels, via):
    return [r for r in rels if (r.attrs or {}).get("via") == via]


class TestBodyCallMining:
    """S1: coupling that lives only inside method bodies."""

    def test_all_four_patterns_and_no_noise(self, tmp_path):
        # One method exercising every body construct we mine — and several
        # we must NOT mine (primitives, member calls, function names).
        src = tmp_path / "All.cxx"
        src.write_text("""
class Widget {}; class Gadget {}; class Gizmo {}; class Doohickey {};
class Factory { public: void Make(); };
void Factory::Make() {
    Widget* w = new Widget();                 // new expression
    auto g = static_cast<Gadget*>(nullptr);   // cast
    auto p = std::make_shared<Gizmo>();       // template argument
    double d = (double)3.14;                  // primitive cast: no edge
    int n = Doohickey::MAX_COUNT;             // scope access
    (void)w; (void)g; (void)p; (void)d; (void)n;
}
""")
        _, rels = parse_file(str(src))
        body = _by_via(rels, "body_call")
        names = {r.target_name for r in body}
        assert {"Widget", "Gadget", "Gizmo", "Doohickey"} <= names
        # noise must not leak: no primitives, no std wrappers, no
        # function/member names
        assert not names & {"double", "nullptr", "std", "make_shared",
                            "MAX_COUNT", "Make"}

    def test_member_calls_are_not_mined(self, tmp_path):
        # m_engine->Start() is coupling ALREADY captured by the field edge;
        # mining it again would double-count. The body miner must skip it.
        src = tmp_path / "Car.cxx"
        src.write_text("""
class Engine { public: void Start(); };
class Car { Engine* m_engine; public: void Go(); };
void Car::Go() { m_engine->Start(); }
""")
        _, rels = parse_file(str(src))
        assert _by_via(rels, "body_call") == []
        # ...while the field edge exists (composes/aggregates/associates)
        field_edges = [r for r in rels if r.source_qname == "Car"
                       and r.target_name == "Engine" and r.kind != "depends"]
        assert field_edges

    def test_fixture_body_edge_resolves_cross_file(self):
        # test_src/Vehicle.cxx uses DiagnosticTool ONLY in a body; the
        # target must resolve to the class declared in DiagnosticTool.hxx.
        _, rels, _ = parse_project("test_src", cache=None)
        body = _by_via(rels, "body_call")
        assert [(r.source_qname, r.target_qname) for r in body] == \
            [("Vehicle", "DiagnosticTool")]
        assert Path(body[0].evidence_file).name == "Vehicle.cxx"
        assert body[0].evidence_line > 0


class TestAliasKindReclassification:
    """An alias may hide a wrapper: the ownership kind must be judged from
    the alias's REAL type text, not the bare alias name."""

    def test_shared_ptr_alias_is_associates_not_composes(self, tmp_path):
        src = tmp_path / "Sinks.hxx"
        src.write_text("""
class Sink {};
using sink_ptr = std::shared_ptr<Sink>;
class Logger { sink_ptr m_sink; };
""")
        _, rels, _ = __import__("tool.ts_parser", fromlist=["parse_project"]) \
            .parse_project(str(tmp_path), cache=None)
        edge = next(r for r in rels if r.source_qname == "Logger"
                    and r.target_name == "Sink")
        # direct spelling std::shared_ptr<Sink> would be associates;
        # the alias must not smuggle it up to composes (level 4 vs 1)
        assert edge.kind == "associates", edge
        assert edge.attrs.get("alias_from") == "sink_ptr"

    def test_alias_chain_outermost_wrapper_wins(self, tmp_path):
        src = tmp_path / "Chain.hxx"
        src.write_text("""
class Foo {};
using FooPtr = Foo*;
using P = FooPtr;
class Owner { P m_p; };
""")
        _, rels, _ = __import__("tool.ts_parser", fromlist=["parse_project"]) \
            .parse_project(str(tmp_path), cache=None)
        edge = next(r for r in rels if r.source_qname == "Owner"
                    and r.target_name == "Foo")
        assert edge.kind == "associates", edge

    def test_bare_value_alias_stays_composes(self, tmp_path):
        src = tmp_path / "Val.hxx"
        src.write_text("""
class Foo {};
using V = Foo;
class Owner { V m_v; };
""")
        _, rels, _ = __import__("tool.ts_parser", fromlist=["parse_project"]) \
            .parse_project(str(tmp_path), cache=None)
        edge = next(r for r in rels if r.source_qname == "Owner"
                    and r.target_name == "Foo")
        assert edge.kind == "composes", edge


class TestParserAccuracyGuards:
    def test_same_file_short_name_collision_stays_unresolved(self, tmp_path):
        # Two classes named Handler in one file: binding to either would be
        # a guess. Same-file resolution must leave the target unresolved
        # (the safe direction) instead of last-parsed-wins.
        src = tmp_path / "Two.hxx"
        src.write_text("""
namespace A { class Handler {}; }
namespace B { class Handler {}; }
class User { Handler* m_h; };
""")
        from tool.ts_parser import parse_file
        _, rels = parse_file(str(src))
        edge = next(r for r in rels if r.source_qname == "User"
                    and r.target_name == "Handler")
        assert edge.target_qname is None

    def test_locals_in_bodies_are_not_method_entities(self, tmp_path):
        # `Widget w(x);` inside a method body must not become a method
        # entity of the enclosing class.
        src = tmp_path / "Local.hxx"
        src.write_text("""
class Widget { public: Widget(int); };
class Car {
public:
    void Go() {
        Widget w(1);
        (void)w;
    }
};
""")
        from tool.ts_parser import parse_file
        ents, _ = parse_file(str(src))
        car_methods = [e.name for e in ents
                       if e.kind == "method" and e.parent_qname == "Car"]
        assert "Go" in car_methods
        assert "w" not in car_methods

    def test_scanned_namespace_is_never_promoted_to_phantom(self, tmp_path):
        # free function out-of-line in a namespace that IS scanned →
        # no phantom class 'util' may appear.
        (tmp_path / "util.hxx").write_text("namespace util { void init(); }")
        (tmp_path / "util.cxx").write_text(
            '#include "util.hxx"\nvoid util::init() { }\n')
        from tool.ts_parser import parse_project
        ents, _, _ = parse_project(str(tmp_path), cache=None)
        phantoms = [e for e in ents if e.attrs.get("phantom")]
        assert not any(e.qualified_name == "util" for e in phantoms), phantoms

    def test_phantom_promotion_for_unseen_class(self, tmp_path):
        # lone .cxx with out-of-line methods of an unseen class → phantom
        # class materialized, flagged, participating in the graph.
        (tmp_path / "Impl.cxx").write_text("""
void Engine::Start() { }
void Engine::Stop() { }
""")
        from tool.ts_parser import parse_project
        ents, _, _ = parse_project(str(tmp_path), cache=None)
        eng = next(e for e in ents if e.qualified_name == "Engine")
        assert eng.attrs.get("phantom") is True
        assert eng.kind == "class"

    def test_template_free_function_does_not_fabricate_std_methods(self, tmp_path):
        # spdlog regression: `template<..> MACRO std::shared_ptr<X> f(...)`
        # mis-parses into a declarator name spanning the return type —
        # which fabricated methods under parent 'std', then a phantom
        # class 'std' that out-scored the real orchestrator.
        src = tmp_path / "x-inl.h"
        src.write_text("""
template<typename Factory>
SPDLOG_INLINE std::shared_ptr<int> stdout_color_mt(const char* name)
{
    return Factory::template create<int>(name);
}
""")
        from tool.ts_parser import parse_file
        ents, rels = parse_file(str(src))
        assert not any(e.parent_qname == "std" for e in ents), ents
        assert not any(" " in e.name for e in ents if e.kind == "method")

    def test_operator_methods_still_survive_the_space_guard(self, tmp_path):
        src = tmp_path / "op.cxx"
        src.write_text("""
bool Foo::operator==(const Foo& o) const { return true; }
void* Foo::operator new(unsigned long n) { return nullptr; }
""")
        from tool.ts_parser import parse_file
        ents, _ = parse_file(str(src))
        names = {e.name for e in ents if e.kind == "method"}
        assert any("operator" in n for n in names), names

    def test_stats_files_parsed_excludes_vendored(self, tmp_path):
        (tmp_path / "a.hxx").write_text("class A {};")
        vend = tmp_path / "third_party"
        vend.mkdir()
        (vend / "b.hxx").write_text("class B {};")
        from tool.ts_parser import parse_project
        ents, _, stats = parse_project(str(tmp_path), cache=None)
        assert stats["files_parsed"] == 1
        assert stats["skipped_vendored"] == 1
        assert not any(e.name == "B" for e in ents)


class TestGroundingGuarantees:
    """Every relationship must be traceable to real source."""

    def test_every_edge_has_evidence(self):
        _, rels, _ = parse_project("test_src", cache=None)
        assert rels
        for r in rels:
            assert r.evidence_file, r
            assert r.evidence_line and r.evidence_line > 0, r

    def test_known_relationships_present(self):
        # A few structural facts of the fixture that must never regress.
        _, rels, _ = parse_project("test_src", cache=None)
        triples = {(r.source_qname, r.kind, r.target_name) for r in rels}
        assert ("Boat", "inherits", "Vehicle") in triples
        assert ("Vehicle", "composes", "FuelTank") in triples
