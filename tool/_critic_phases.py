"""
_critic_phases.py — phase prompt templates.

Phase 1 — subtree analysis: leaf-up. Each independent subtree of the
dominator forest is analyzed in isolation to keep the LLM context bounded
and the local picture sharp.

Phase 2 — module synthesis: takes Phase 1 essences and produces the
module-level view, cross-subtree observations, and prioritized
recommendations.

The order in which lenses are referenced inside each template is part of
the methodology and must not be reshuffled.
"""

# Used at the start of every analysis turn — sets role and output discipline.
SYSTEM_PREAMBLE = (
    "You are a senior software architect reviewing code. Be precise, "
    "evidence-based, and concise. Reference specific class names and "
    "methods from the input — never make claims unsupported by the data."
)


SUBTREE_TEMPLATE = """\
{preamble}

# Subtree under review

Root: {root_qname}

Classes in subtree:
{classes_block}

Methods of interest:
{methods_block}

Field signatures:
{fields_block}

Internal relationships:
{relations_block}

# Analysis directives

Perform these steps strictly in order. Do not skip steps; do not
combine them.

Step 1 — {lens_essence}

Step 2 — {lens_pipeline}

Step 3 — Enumerate current implementation pain points. Look for:
methods that span multiple abstraction altitudes; class names that
diverge from what their methods actually do; missing components that
are implied by the relationships but not present; redundant code
across sibling classes; unclear ownership transfers.

Step 4 — Propose the ideal decomposition.
   - {lens_cohesion}
   - {lens_altitude}
   - {lens_interface}

Step 5 — Map each existing class and major method to its ideal
location in the decomposition. Mark mismatches: where current code
sits in the wrong stage, the wrong altitude, or the wrong cohesion
group.

{lens_evidence}

# Output

Respond with a single JSON object on one line, fields:

  essence:  string — one sentence on what this subtree fundamentally does.
  pipeline: list of {{name, altitude, responsibility}} — primary stages.
  components: list of {{stage, name, role, multiple_impls}} —
              the ideal decomposition. `multiple_impls` is true ONLY when
              the input data shows ≥2 concrete implementations of this role.
  pains: list of {{title, what, where, category}} — pain points found.
         `title` is a SHORT (<=8 words) summary headline of the problem.
         `what` is the full explanation.
         `category` ∈ {{mixed-altitude, naming-mismatch, missing-component,
                       duplication, ownership-unclear, other}}.
  mappings: list of {{current, ideal_component, fit, reason}} —
            current ∈ class or class::method qualified name.
            fit ∈ {{good, partial, poor}}.

Output ONLY the JSON, no commentary.
"""


MODULE_TEMPLATE = """\
{preamble}

# Module synthesis

The following per-subtree analyses were produced independently. Your
task is to synthesize a module-level view.

Subtree essences and pipelines:
{subtree_summaries}

Cross-subtree relationships (edges spanning subtrees):
{cross_relations_block}

# Synthesis directives

Strict order:

Step 1 — Synthesize a module-level primary pipeline. The module
pipeline should sit one altitude above the subtree pipelines and
should reference subtree essences as its stages.

Step 2 — Detect cross-subtree patterns.
   - Two or more subtrees whose pipelines are structurally similar
     suggest a missing base abstraction.
   - Two or more subtrees that re-implement the same low-level idiom
     suggest a missing shared utility.
   - {lens_interface}

Step 3 — Identify missing abstractions implied by the data.

Step 4 — Provide prioritized recommendations. Each recommendation must:
   - Reference specific subtree roots, classes, or methods.
   - State the expected impact (what becomes easier; what risk reduces).
   - Be actionable: a refactor a competent engineer could carry out
     without further clarification.

{lens_evidence}

# Output

Respond with a single JSON object on one line, fields:

  module_workflow: list of {{stage, description, source_subtrees}}
  cross_observations: list of {{pattern, affected_subtrees, suggestion}}
  missing_abstractions: list of {{role, current_implementations,
                                  suggested_interface}}
  recommendations: list of {{title, priority, target, action,
                             expected_impact, evidence}}
                   `title` is a SHORT (<=8 words) summary headline.
                   priority ∈ {{high, medium, low}}.

Output ONLY the JSON, no commentary.
"""
