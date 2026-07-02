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
