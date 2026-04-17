# Seven Sins of Code Design

> **Core Principle:** Good design = precise control of information flow.
> Every layer should know only what it needs. Every sin below is a violation of this principle —
> something knows too much about something it shouldn't.

---

## Sin 1: The God Class (A class knows too many responsibilities)

**Symptom:** One class handles multiple unrelated responsibilities. Changing one feature
forces modifications across unrelated methods. The class is the "go-to" place for everything.

**Detection:**
- Description test: describing the class requires "and" → too many responsibilities
- Change-reason test: list reasons to modify this class; more than 2 = violation
- Method grouping test: methods cluster into distinct groups that rarely call each other
- Quantitative: responsibilities ≥ 5 = God Class; 3–4 = warning

**Includes:** excessive member variables (> 15), mixed concerns in one file,
a class that appears in ≥ 3 responsibility groups.

**Redemption:** Split by change reason (vertical slice). Each new class = one reason to change.

---

## Sin 2: Inheritance Hell (A child knows too much about its parent)

**Symptom:** Deep inheritance chains where subclasses depend on parent implementation details.
Adding a feature means modifying multiple layers. Changing the base class breaks all descendants.

**Detection:**
- Inheritance depth > 3 levels
- Subclass adds more member variables than parent has
- Subclass overrides > 50% of parent methods (not extending, replacing)
- Cross-module inheritance (Lv-5 across module boundaries)

**Includes:** fragile base class problem, parallel inheritance hierarchies that change in sync,
inheriting purely for code reuse (not is-a relationship).

**Redemption:** Prefer composition over inheritance. Extract interfaces (Lv-2) at module boundaries.
Inheritance is only justified when all 5 conditions are met: is-a relationship, needs polymorphism,
reuses implementation, depth ≤ 3, virtual destructor.

---

## Sin 3: Abstraction Absence (A caller knows too much about implementation)

**Symptom:** Code depends on concrete classes everywhere. No interfaces, no contracts.
Understanding the flow requires reading every implementation detail. Adding a new variant
means modifying existing code instead of adding a new implementation.

**Detection:**
- ≥ 3 classes depend on the same concrete class → should extract interface
- Cross-group dependencies use Lv-4/5 instead of Lv-2
- No Lv-2 (Realization) relationships exist in the module
- Cannot write unit tests without instantiating the entire dependency chain

**Includes:** missing dependency inversion, hardcoded concrete types where polymorphism
would simplify extension, "modify existing code" instead of "add new code" for new requirements.

**Redemption:** Extract interfaces where multiple classes depend on the same concrete type.
Apply Dependency Inversion — high-level modules depend on abstractions, not details.
Only extract interfaces when there are (or will be) multiple implementations; otherwise,
a concrete class is fine (don't over-engineer).

---

## Sin 4: Feature Envy (A method knows too much about another class's data)

**Symptom:** A method in class A spends most of its time accessing data from class B —
calling B's getters, reading B's fields, computing results that logically belong to B.
The method is in the wrong home.

**Detection:**
- A method references another class's members/getters more than its own class's
- Moving the method to the other class would reduce parameter passing
- A class has methods that operate on data it doesn't own

**Includes:** data class + logic class pairs (one holds data, another does all the computation),
"utility" classes that exist only because the real owner refused to take its methods.

**Redemption:** Move the method to the class whose data it primarily uses.
If the method uses data from multiple classes, it may belong in the orchestrator
or indicates a missing abstraction.

---

## Sin 5: Circular Entanglement (Modules know too much about each other)

**Symptom:** Class A depends on B, B depends on C, C depends on A.
You cannot compile, test, or understand one without pulling in the others.
Every change ripples through the entire cycle.

**Detection:**
- Dependency graph contains cycles (graph algorithm: DFS/Tarjan)
- Non-orchestrator classes have bidirectional dependencies
- Cannot extract a single class into an independent module
- Build order is fragile — changing one header triggers recompilation of many files

**Includes:** hidden circular dependencies through transitive includes,
orchestrator bypass (non-orchestrators calling each other directly instead of going through
the orchestrator), mutual friend classes.

**Redemption:** Break cycles by extracting interfaces at the boundary.
Route all cross-group communication through the orchestrator.
Apply the Dependency Rule: dependencies point inward (toward abstractions), never outward.

---

## Sin 6: Hidden State Coupling (An object knows too much invisible context)

**Symptom:** Methods depend on hidden global state, singletons, or implicit call ordering.
The class "works" only when called in a specific sequence, but nothing enforces this.
Testing requires setting up invisible preconditions.

**Detection:**
- Methods read/write global variables or singletons
- Call-order dependency: method B fails if method A wasn't called first
- Constructor performs logic/IO/registration (not just assignment)
- Member variables that are really temporary computation state disguised as class state
- Count member variables that could be method parameters instead

**Includes:** state explosion (> 15 member variables), redundant state
(storing derived values that could be computed), mutable shared state across threads.

**Redemption:** Make dependencies explicit — pass context through parameters.
Keep components stateless whenever possible. If state is needed,
minimize it and make the valid state transitions explicit.
Apply RAII for resource management (C++ specific).

---

## Sin 7: Knowledge Leakage (The reader knows too little to understand)

**Symptom:** Code is write-only — the author understood it, but no one else can.
Variable names are meaningless, no comments explain "why", magic numbers appear everywhere.
This is the inverse sin: instead of code knowing too much, the human knows too little.

**Detection:**
- Method/variable names require reading the implementation to understand
- No comments explaining business rules or non-obvious decisions
- Magic numbers without named constants
- Method body > 30 lines without structural comments

**Includes:** misleading names (name says one thing, code does another),
outdated comments that contradict the code, abbreviations that only the author understands.

**Redemption:** Names should express intent. Comments explain "why", not "what".
Extract magic numbers into named constants. Use AI tools to generate documentation
for existing code. Make the code tell a story.

---

## Severity Ranking (for automated diagnosis)

```
When multiple sins are detected, prioritize by impact:

🔴 Critical (fix first — structural damage):
   Sin 1: God Class         → blocks all other refactoring
   Sin 5: Circular Entanglement → prevents independent evolution

🟡 Major (fix next — design degradation):
   Sin 2: Inheritance Hell   → fragile, hard to extend
   Sin 3: Abstraction Absence → cannot add new variants cleanly
   Sin 4: Feature Envy       → responsibilities in wrong place

🟢 Minor (fix last — maintainability):
   Sin 6: Hidden State       → testability and reliability
   Sin 7: Knowledge Leakage  → readability and onboarding
```

## Quantitative Thresholds (for code-level detection)

```
Metric                              Warning    Critical
─────────────────────────────────────────────────────────
Responsibility count per class        3-4        ≥ 5
Member variables per class            10-15      > 15
  of which "could be parameters"      > 30%      > 50%
Inheritance depth                     3          > 3
Subclass added members vs parent      > 100%     > 200%
Classes depending on same concrete    3          ≥ 5
Cycle length in dependency graph      2          ≥ 3
Non-orchestrator direct dependencies  2-3        ≥ 4
Method references to other class      > 50%      > 70%
```

## Module-Level Aggregation

```
Class-level sins roll up to module-level diagnosis:

Module Smell                    How to detect from DB
─────────────────────────────────────────────────────────────
God Module                      One responsibility group contains > 50% of classes
Responsibility Duplication      Different groups share the same responsibility_tags
Group Incoherence               Classes within one group have very different tags
Cross-Group Strong Coupling     Lv-4/5 dependencies across group boundaries
Missing Orchestrator            No class has significantly more outgoing than incoming deps
Orchestrator Bypass             Non-orchestrator-to-non-orchestrator direct deps
Interface Desert                Zero Lv-2 relationships in the entire module
Inheritance Sprawl              Total Lv-5 relationships > 50% of all relationships
```
