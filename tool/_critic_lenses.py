"""
_critic_lenses.py — Analytical perspectives applied when reviewing a code
subtree. Each lens is a short directive used inside the analysis prompt.

These lenses are intentionally generic OOD references; the value comes
from the order in which they are composed elsewhere.
"""

# A — what the code is fundamentally trying to do, ignoring surface
# naming, framework noise, or accidental complexity.
LENS_ESSENCE = (
    "Identify the essential operations the code performs. "
    "State each as a verb-phrase describing WHAT, not HOW. "
    "Surface naming may be misleading — look at field accesses and "
    "outgoing relationships to infer real intent."
)

# B — chain the essential operations into a primary pipeline of stages
# that reads like a story from request to result.
LENS_PIPELINE = (
    "Synthesize the essential operations into a primary pipeline. "
    "Each stage handles one cohesive responsibility at one abstraction "
    "altitude. The pipeline should read top-to-bottom as a coherent flow."
)

# C — within each stage, classes/methods should cluster by what causes
# them to change (Single Responsibility's R).
LENS_COHESION = (
    "Group elements by reason-to-change. Things that change together for "
    "the same reason belong in the same component. Things that change "
    "for different reasons must be separated even if they currently "
    "share a file or class."
)

# D — stages should sit at consistent abstraction altitudes. A method
# that mixes high-level orchestration with low-level byte handling is
# crossing layers and must be split.
LENS_ALTITUDE = (
    "Stratify by abstraction altitude. Each component sits at one "
    "altitude — low-level data manipulation, mid-level resource "
    "management, high-level orchestration, or top-level request "
    "handling. A method spanning multiple altitudes is a layering "
    "violation and must be decomposed."
)

# E — interfaces emerge from observed multiplicity, not from speculation.
# Only promote a base abstraction when the data shows two or more
# concrete implementations of the same role.
LENS_INTERFACE = (
    "Promote interfaces ONLY where multiple concrete implementations "
    "of the same role exist in the analyzed subtree. Speculative "
    "interfaces (single implementation) are over-engineering."
)

# Quality lens applied to every output recommendation.
LENS_EVIDENCE = (
    "Every observation must reference specific class names, methods, "
    "or file:line locations from the input. Vague observations without "
    "evidence are not acceptable."
)
