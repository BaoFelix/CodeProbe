#!/usr/bin/env python3
"""
design_critic_smoke.py — verify DesignCriticAgent runs end-to-end with
a fake LLM that returns canned JSON, then check the report payload
includes the critic output.
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tool.db import DBManager
from tool.source_io import SourceReader
from tool.agents import ScannerAgent
from tool.design_critic import DesignCriticAgent
from tool.report.v2_data import build_payload


DB = "outputs/critic_smoke.db"


class FakeLLM:
    """Returns canned subtree analyses + a module synthesis."""
    def __init__(self):
        self.calls = []
        self.subtree_counter = 0

    def generate(self, prompt, system_prompt="", tag=""):
        self.calls.append(tag)
        if tag.startswith("critic_subtree_"):
            self.subtree_counter += 1
            return json.dumps({
                "essence": f"Subtree {self.subtree_counter} coordinates X",
                "pipeline": [
                    {"name": "Input", "altitude": "top",
                     "responsibility": "accept request"},
                    {"name": "Process", "altitude": "mid",
                     "responsibility": "transform data"},
                ],
                "components": [
                    {"stage": "Input", "name": "RequestParser",
                     "role": "decode caller intent", "multiple_impls": False},
                    {"stage": "Process", "name": "Pipeline",
                     "role": "orchestrate transform", "multiple_impls": True},
                ],
                "pains": [
                    {"what": "altitude mixed in main method",
                     "where": "Workshop.hxx:42",
                     "category": "mixed-altitude"},
                ],
                "mappings": [
                    {"current": "Garage::Workshop",
                     "ideal_component": "Pipeline",
                     "fit": "partial",
                     "reason": "carries L1+L3 concerns"},
                ],
            })
        if tag == "critic_module":
            return json.dumps({
                "module_workflow": [
                    {"stage": "Accept",
                     "description": "intake requests",
                     "source_subtrees": ["Garage::Workshop"]},
                ],
                "cross_observations": [
                    {"pattern": "all subtrees share input parsing",
                     "affected_subtrees": ["Garage::Workshop", "Vehicle"],
                     "suggestion": "extract shared RequestParser"},
                ],
                "missing_abstractions": [
                    {"role": "Pipeline",
                     "current_implementations":
                         ["Garage::Workshop", "Vehicle"],
                     "suggested_interface": "IPipeline"},
                ],
                "recommendations": [
                    {"priority": "high",
                     "target": "Garage::Workshop",
                     "action": "extract Pipeline interface",
                     "expected_impact": "decouples X from Y",
                     "evidence": "see mappings in Workshop subtree"},
                ],
            })
        return "{}"


def main():
    os.makedirs("outputs", exist_ok=True)
    if os.path.exists(DB):
        os.remove(DB)
    db = DBManager(DB); db.ensure_tables()

    scanner = ScannerAgent(llm=None, db=db,
                           reader=SourceReader("test_src", db=db), prompts=None)
    scanner.run("test_src")

    fake = FakeLLM()
    critic = DesignCriticAgent(llm=fake, db=db, reader=None, prompts=None)
    assert critic.run(), "critic failed"
    print(f"  ✓ critic ran {len(fake.calls)} LLM calls: {fake.calls}")

    # Verify DB rows
    subs = db.get_design_subtrees()
    mod = db.get_design_module()
    assert subs, "no subtree results saved"
    assert mod, "no module result saved"
    print(f"  ✓ DB has {len(subs)} subtree analyses + module synthesis")

    # Verify report payload picks them up
    payload = build_payload(db)
    critic_payload = payload['critic']
    assert critic_payload['module'], "module missing from payload"
    assert critic_payload['subtrees'], "subtrees missing from payload"
    recs = critic_payload['module']['recommendations']
    assert len(recs) == 1 and recs[0]['priority'] == 'high'
    print(f"  ✓ payload includes {len(critic_payload['subtrees'])} subtrees "
          f"and {len(recs)} recommendations")

    # Generate HTML and check it embeds the data
    from tool.report import generate_html_report
    out = generate_html_report(db, "outputs/critic_smoke.html")
    html = out.read_text()
    assert "Pain points" in html
    assert "critic-recs" in html
    assert "Garage::Workshop" in html
    print(f"  ✓ HTML report {out} written ({out.stat().st_size:,} bytes)")

    os.remove(DB)
    print("\nDesign critic smoke: PASS")


if __name__ == "__main__":
    main()
