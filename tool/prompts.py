"""
prompts.py — Prompt template engine
═══════════════════════════════════════
AI concept: Prompt Engineering
Key insight:
  - System Prompt: set AI's role and constraints
  - Structured output: require fixed format → easy to parse
  - Few-shot: give AI example output format
  - Context injection: feed AI only what it needs (not full source)
  - Token budget: estimate prompt size, control limits
═══════════════════════════════════════
"""
from pathlib import Path


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  SYSTEM CONTEXT — Shared context for all prompts (programmatic SKILL)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SYSTEM_CONTEXT = """\
You are analyzing C++ classes in a large-scale industrial codebase.
Refactoring goal: stateless, service-like, reusable, testable.
Coding conventions: PascalCase methods, m_ prefix for members.
Constraints: backward compatible (deprecate, don't delete).
"""


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  PROMPT #02 — Class analysis (enhanced, with design standard scoring)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

ANALYZE_CLASS = """\
## Context
{system_context}
I'm refactoring the {module} module. Analyze the attached C++ class.

## Source Code

### Header ({header_name}, {header_lines} lines)
```cpp
{header_content}
```

{impl_section}

## Task — Design Standard Analysis

### Stateless Check (S1-S4)
- S1: Count all member variables (m_*). List each with its type.
- S2: For each m_ variable — could it be a function parameter instead? (yes/no + reason)
- S3: Constructor complexity — only assignment, or executes logic/IO/registration?
- S4: Call-order dependency — do any methods require specific calling sequence?

### Service-like Check (V1-V4)
- V1: Are ALL method inputs in parameter list? Or do methods read hidden global/singleton state?
- V2: Could this class become a namespace of free functions? Why or why not?
- V3: Thread safety — any shared mutable state?
- V4: Side effects — do public methods change class state?

### Coupling Assessment
- Count #include dependencies (exclude standard library)
- List all concrete class dependencies (not interfaces/base)
- Circular dependencies?
- Rate: Low (≤2 deps) / Medium (3-5) / High (>5 or bidirectional)

## Required Output Format

IMPORTANT: Start your response with EXACTLY this block (parseable by my tool):

```
SCORES: stateless={{1-5}}/5 service={{1-5}}/5 coupling={{1-5}}/5
DEPENDENCIES: ClassA, ClassB, ClassC
MEMBER_VARS: m_var1(type,param:yes/no), m_var2(type,param:yes/no)
KEY_FINDING: one-sentence summary of biggest design issue
REFACTOR_ACTION_1: most impactful specific refactoring step
REFACTOR_ACTION_2: second most impactful step
```

Then provide your detailed analysis below.
"""


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  PROMPT — Large file summary (Code Query mode)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SUMMARIZE_LARGE_FILE = """\
## Context
{system_context}

## Task
This C++ file is {line_count} lines — too large for detailed analysis.
Generate a STRUCTURAL SUMMARY for later use by another AI agent.

Include:
1. All class definitions (name + base classes)
2. All public method signatures (return type + name + params)
3. All member variables with types
4. All #include dependencies
5. Key design patterns or anti-patterns observed

## Source (first {preview_lines} lines)
```cpp
{preview_content}
```

## Output Format
Concise structural markdown, max 150 lines. This will be fed to another AI.
"""


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  PROMPT — Dependency graph (post-analysis summary of all classes)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

DEPENDENCY_MAP = """\
## Context
{system_context}
I've analyzed {num_classes} classes in the {module} module.

## Class Summaries
{class_summaries}

## Task
1. Draw a dependency graph: which classes depend on which
2. Identify circular dependencies (critical!)
3. Suggest refactoring ORDER — leaf nodes (least dependencies) first
4. Identify highest-coupling classes that need priority decomposition

## Output Format
```
REFACTOR_ORDER: Class1, Class2, Class3, ...
CIRCULAR_DEPS: ClassA <-> ClassB, ...
HIGHEST_COUPLING: ClassName (reason)
```
Then detailed analysis.
"""


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  PROMPT — Architecture analysis (module-level bird's eye view)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

ANALYZE_ARCHITECTURE = """\
## Context
{system_context}

I need an ARCHITECTURE-LEVEL analysis of the {module} module before diving into individual classes.

## Module Overview
Total files: {file_count}
Total classes: {class_count}

## File-by-File Structure
{module_map_text}

## Task — Architecture Analysis

1. **Core Workflow**: What is the PRIMARY data processing flow through this module?
   - Entry point classes → intermediate processing → output classes
   - Which classes form the "spine" of the workflow?

2. **Responsibility Clusters**: Group classes by responsibility:
   - Data holders (pure state)
   - Processors (transform data)
   - Managers/Coordinators (lifecycle, orchestration)
   - Utilities (helpers, formatting)
   - Interfaces/Abstractions

3. **Architecture Issues**:
   - God classes (too many responsibilities)?
   - Missing abstractions (concrete dependencies everywhere)?
   - Circular dependency chains?
   - Parallel hierarchies that should be merged?

4. **Key Classes**: List the 5-8 most important classes and their role in the architecture.

## Required Output Format

```
CORE_WORKFLOW: ClassA -> ClassB -> ClassC -> ...
CORE_CLASSES: Class1, Class2, Class3, ...
ARCH_ISSUES: issue1 | issue2 | issue3
CLUSTERS:
  DATA: ClassX, ClassY
  PROCESSOR: ClassA, ClassB
  MANAGER: ClassM, ClassN
  UTILITY: ClassU, ClassV
```

Then provide your detailed architecture analysis below.
"""


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  PROMPT — Responsibility analysis (single class, with architecture context)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

ANALYZE_RESPONSIBILITY = """\
## Context
{system_context}

## Design Analysis Skills
{skills_context}

## Module Context
{module_context}

## Target Class: {class_name}

### Source Code
```cpp
{source_content}
```

## Task — Responsibility & Sin Diagnosis

Analyze this class using the Seven Sins framework and SRP detection rules above.

### Step 1: Responsibility Identification
List EVERY distinct responsibility this class currently has.
Use verb phrases: "Manages X", "Computes Y", "Stores Z", "Formats W".

### Step 2: SRP Assessment
- Apply the **Description Test**: describe the class in one sentence. Need "and"?
- Apply the **Change-Reason Test**: list all reasons to modify this class.
- Apply the **Method Grouping Test**: cluster methods into groups.
- Count responsibilities. 1-2 = healthy, 3-4 = warning, ≥5 = God Class.

### Step 3: Seven Sins Diagnosis
For EACH sin that applies (skip those that don't):
- Name the sin
- Cite specific evidence from the source code
- Rate severity: 🔴 Critical / 🟡 Major / 🟢 Minor

### Step 4: Extract Candidates
Methods or method groups that should be extracted. For each:
- What methods to extract
- Suggested new class/function name
- What data they need (parameters vs member access)

### Step 5: Responsibility Tags
Assign 1-3 short tags (2-4 words each) — independent change reasons.
Tags must be REUSABLE across classes. Use verb+noun format.
Do NOT use the class name as a tag.

## Required Output Format

```
ACTUAL_RESPONSIBILITIES: resp1 | resp2 | resp3
IDEAL_RESPONSIBILITY: one sentence describing what this class SHOULD do
SRP_VIOLATIONS: violation1 | violation2
SIN_DIAGNOSIS: Sin1Name(severity,evidence) | Sin2Name(severity,evidence)
EXTRACT_CANDIDATES: MethodGroup1 -> NewClassName | MethodGroup2 -> FreeFunction
RESPONSIBILITY_TAGS: tag1 | tag2 | tag3
```

Then provide your detailed analysis below.
"""


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  PROMPT — Design proposal (based on architecture + responsibility analysis)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

PROPOSE_DESIGN = """\
## Context
{system_context}

## Refactoring Design Guide
{refactoring_guide}

## Module Overview
{module_overview}

## Dependency Map
{dep_summary}

## Seven Sins Diagnosis Summary
{sin_summary}

## Responsibility Analysis (all classes)
{responsibility_context}

## Task — Design Proposal

Based on the dependency map, sin diagnoses, and responsibility analyses above,
propose a concrete refactoring design using vertical and horizontal cuts:

1. **Vertical Cut** (split by change reason):
   For each God Class, list distinct change reasons and the new class each becomes.

2. **Horizontal Cut** (layer by abstraction):
   For mixed-abstraction groups: separate policy (orchestrator) from logic (service)
   from detail (infrastructure).

3. **Phase Plan**: Break into independently shippable phases:
   - Phase 1: Extract stateless utilities (low risk)
   - Phase 2: Introduce interfaces at boundaries (medium risk)
   - Phase 3: Split God Classes by change reason (high risk)
   - Phase 4: Restructure workflow (highest risk)

4. **New Classes/Functions**: For each new entity:
   - Name, single responsibility (one sentence)
   - Key methods, what existing code moves here

5. **Interface Extraction**: Only where ≥3 classes depend on same concrete.
   Do NOT over-abstract.

## Required Output Format

```
PHASE_PLAN: Phase1: description | Phase2: description | Phase3: description
NEW_CLASSES: NewClass1(responsibility) | NewClass2(responsibility)
INTERFACES: IInterface1(methods) | IInterface2(methods)
EFFORT_TOTAL: X hours estimated
```

Then provide the detailed design proposal with vertical/horizontal cut rationale.
"""


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  PROMPT — Tradeoff evaluation (challenge the design proposal)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

EVALUATE_TRADEOFF = """\
## Context
{system_context}

## Full Context
### Architecture Analysis
{architecture_context}

### Design Proposal
{design_context}

## Tradeoff Question
{question}

## Task — Tradeoff Evaluation

Evaluate the tradeoffs of the proposed design with respect to the question above:

1. **Pros**: Benefits of the proposed approach
2. **Cons**: Risks, costs, and downsides
3. **Alternatives**: Other approaches considered and why they were rejected/accepted
4. **Recommendation**: Final recommendation with justification
5. **Backward Compatibility**: Impact on existing code and migration effort

## Required Output Format

```
RECOMMENDATION: brief one-line recommendation
RISK_LEVEL: Low/Medium/High
EFFORT_IMPACT: +X% / -X% compared to original estimate
```

Then provide the detailed tradeoff analysis.
"""


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Prompt Builder — Assemble final prompts
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class PromptBuilder:
    """Prompt factory — assembles templates + code + context into final prompts."""

    def __init__(self, universal_skills=None):
        """
        universal_skills: dict of {skill_name: content} from skills/universal/*.md
        """
        self.system_context = SYSTEM_CONTEXT
        self.universal_skills = universal_skills or {}

    def build_analysis_prompt(self, class_name, header_content,
                              impl_content=None, header_name="",
                              impl_name="", header_lines=0,
                              impl_lines=0, module="AcrossIteration"):
        """
        Build class analysis prompt.

        Prompt Engineering principles:
        1. Context first (give AI background)
        2. Source in code blocks (clear formatting)
        3. Task items listed separately (reduce omissions)
        4. Output format strictly defined (easy to parse)
        """
        # Handle implementation file (RAG: truncate if too large)
        impl_section = ""
        if impl_content:
            if impl_lines > 500:
                preview = "\n".join(impl_content.split("\n")[:200])
                impl_section = (
                    f"### Implementation ({impl_name}, {impl_lines} lines "
                    f"— first 200 shown)\n```cpp\n{preview}\n```\n"
                    f"*[Truncated. Focus on header API + visible implementation.]*"
                )
            else:
                impl_section = (
                    f"### Implementation ({impl_name}, {impl_lines} lines)"
                    f"\n```cpp\n{impl_content}\n```"
                )

        prompt = ANALYZE_CLASS.format(
            system_context=self.system_context,
            module=module,
            header_name=header_name,
            header_lines=header_lines,
            header_content=header_content,
            impl_section=impl_section,
        )
        return prompt

    def build_summary_prompt(self, content, line_count, preview_lines=200):
        """Build large file summary prompt (Code Query mode)."""
        preview = "\n".join(content.split("\n")[:preview_lines])
        return SUMMARIZE_LARGE_FILE.format(
            system_context=self.system_context,
            line_count=line_count,
            preview_lines=preview_lines,
            preview_content=preview,
        )

    def build_dependency_map_prompt(self, class_summaries,
                                    module="AcrossIteration"):
        """Build module dependency graph prompt (used after all classes are analyzed)."""
        summaries_text = "\n\n".join(
            f"### {s['class_name']}\n"
            f"- Scores: S={s['stateless_score']}/5 "
            f"V={s['service_score']}/5 C={s['coupling_score']}/5\n"
            f"- Dependencies: {s['dependencies']}\n"
            f"- Summary: {s['ai_analysis']}"
            for s in class_summaries
        )
        return DEPENDENCY_MAP.format(
            system_context=self.system_context,
            num_classes=len(class_summaries),
            module=module,
            class_summaries=summaries_text,
        )

    def build_architecture_prompt(self, module_map, module="AcrossIteration"):
        """Build architecture analysis prompt — module-level bird's eye view."""
        file_count = len(module_map)
        class_count = sum(len(entry['classes']) for entry in module_map)

        lines = []
        for entry in module_map:
            fname = Path(entry['file']).name
            classes_str = ", ".join(entry['classes'])
            includes_str = ", ".join(entry['includes'][:10])  # Show at most 10 includes
            members_str = ", ".join(entry['member_vars'][:8])
            methods_str = ", ".join(entry['methods'][:8])
            bases_str = ", ".join(entry['base_classes']) if entry['base_classes'] else "none"

            lines.append(
                f"### {fname} ({entry['lines']} lines)\n"
                f"- Classes: {classes_str}\n"
                f"- Inherits: {bases_str}\n"
                f"- Includes: {includes_str}\n"
                f"- Members: {members_str}\n"
                f"- Methods: {methods_str}"
            )

        module_map_text = "\n\n".join(lines)

        return ANALYZE_ARCHITECTURE.format(
            system_context=self.system_context,
            module=module,
            file_count=file_count,
            class_count=class_count,
            module_map_text=module_map_text,
        )

    def build_responsibility_prompt(self, class_name, source_content,
                                     module_context):
        """Build responsibility analysis prompt — single class with module context + skills."""
        # Assemble skills context from universal_skills (compact: limit total to ~3000 chars)
        skills_parts = []
        budget = 3000
        for name in ('seven_sins', 'srp_detection'):
            if name in self.universal_skills:
                text = self.universal_skills[name]
                if len(text) > budget:
                    text = text[:budget] + "\n... [truncated]"
                    budget = 0
                else:
                    budget -= len(text)
                skills_parts.append(text)
        skills_context = "\n\n---\n\n".join(skills_parts) if skills_parts else "No skills loaded."

        return ANALYZE_RESPONSIBILITY.format(
            system_context=self.system_context,
            skills_context=skills_context,
            module_context=module_context,
            class_name=class_name,
            source_content=source_content,
        )

    def build_design_prompt(self, module_info, dep_summary,
                            resp_summary, sin_summary):
        """Build design proposal prompt — with refactoring guide skill injection."""
        # Module overview
        if module_info:
            module_overview = (
                f"Module: {module_info['module_name']}\n"
                f"Orchestrator: {module_info['orchestrator'] or '—'}\n"
                f"Classes: {module_info['class_count']}"
            )
        else:
            module_overview = "No module info available."

        # Refactoring guide skill (compact)
        guide = self.universal_skills.get('refactoring_guide', 'No refactoring guide loaded.')

        return PROPOSE_DESIGN.format(
            system_context=self.system_context,
            refactoring_guide=guide,
            module_overview=module_overview,
            dep_summary=dep_summary,
            sin_summary=sin_summary,
            responsibility_context=resp_summary,
        )

    def build_tradeoff_prompt(self, architecture_context,
                              design_context, question):
        """Build tradeoff evaluation prompt — challenge the design proposal."""
        return EVALUATE_TRADEOFF.format(
            system_context=self.system_context,
            architecture_context=architecture_context,
            design_context=design_context,
            question=question,
        )

    @staticmethod
    def estimate_tokens(text):
        """Rough token estimate (1 token ≈ 4 chars, for English/code)."""
        return len(text) // 4

    def build_with_budget(self, build_fn, budget_key, max_tokens=6000, **kwargs):
        """Generic token budget control. Calls build_fn(**kwargs), truncates budget_key if over limit."""
        prompt = build_fn(**kwargs)
        tokens = self.estimate_tokens(prompt)
        if tokens <= max_tokens:
            return prompt, tokens
        print(f"    ⚡ Prompt too large ({tokens} tokens > {max_tokens}), truncating {budget_key}...")
        skeleton_kwargs = {**kwargs, budget_key: ''}
        skeleton = build_fn(**skeleton_kwargs)
        budget_chars = (max_tokens - self.estimate_tokens(skeleton)) * 4
        if budget_chars > 400:
            kwargs[budget_key] = kwargs[budget_key][:budget_chars] + '\n// ... [truncated for token budget]'
        prompt = build_fn(**kwargs)
        tokens = self.estimate_tokens(prompt)
        print(f"    After truncation: ~{tokens} tokens")
        return prompt, tokens
