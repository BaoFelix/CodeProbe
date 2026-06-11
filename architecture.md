# CodeProbe Architecture

> The presenter's guide. Read top-to-bottom once and you can explain
> the whole system; each section is one slide's worth of material.
> Companion file: [`LearningLog.md`](LearningLog.md) — the transferable
> lessons learned while building it.

---

## 1. One sentence

CodeProbe reads a C++ codebase (even a partial one), builds an accurate
entity-relationship graph with tree-sitter, derives the architecture
(orchestrator, utilities, workflow hierarchy) with graph algorithms,
has an LLM audit the design top-down, and ships everything as one
interactive HTML file.

## 2. The data flow (the only diagram you need)

```
 C++ sources (.hxx/.cxx/.h/.hpp/.sch — partial is fine)
      │
      ▼
 ┌─────────────────┐   tree-sitter CST → entities + relationships
 │ ts_parser.py    │   (cache: 22x re-scan · multiprocess first scan)
 └─────────────────┘
      │  entities: namespace/class/struct/interface/method/field
      │  relationships: 6 kinds × evidence lines
      ▼
 ┌─────────────────┐   SQLite = shared memory between all stages
 │ db.py           │   (entities, relationships, module_info,
 └─────────────────┘    critic results, parse cache, LLM cache)
      │
      ├──────────────────────────────┐
      ▼                              ▼
 ┌─────────────────┐         ┌──────────────────┐
 │ workflow.py     │         │ design_critic.py │
 │ graph analysis  │         │ two-pass LLM     │
 │ · orchestrator  │         │ · per-subtree    │
 │ · utilities     │         │ · module synth   │
 │ · dominator tree│         │ (skill-overridable)
 └─────────────────┘         └──────────────────┘
      │                              │
      └──────────────┬───────────────┘
                     ▼
            ┌─────────────────┐
            │ report/         │  one self-contained HTML:
            │ data + template │  1. Architecture (workflow tree)
            └─────────────────┘  2. Relationships (UML diagram)
                                 3. Design Review (LLM findings)
```

Two agents orchestrate this: **ScannerAgent** (no LLM — parsing + graph
math) and **DesignCriticAgent** (LLM). `pipeline.py` wires them;
`run.py analyze` is the entry point.

## 3. Module map

| File | Role | Lines |
|---|---|---|
| `tool/ts_parser.py` | The parsing engine. Queries, type classification, single-file pass, `.sch` regex fallback, project pass (cache/parallel/aliases/phantoms/resolution) | ~1000 |
| `tool/model.py` | `Entity` + `Relationship` dataclasses. **Single source of truth for the 6 relationship kinds** | 90 |
| `tool/workflow.py` | Graph analysis: orchestrator scoring, utility detection, style detection (oop/mixed/crtp), SCC condensation, abstraction folding, dominator tree | 370 |
| `tool/db.py` | SQLite layer. 7 tables incl. both caches | 480 |
| `tool/design_critic.py` | Two-pass LLM design audit; user-overridable via `skills/design_critic.md` | 385 |
| `tool/_critic_lenses.py` `_critic_phases.py` | The embedded review methodology, split across two files. **The order of analysis steps is the method** | 200 |
| `tool/llm.py` | One front door for LLM calls: cache → call → 429-fallback → write-through | 260 |
| `tool/agents.py` | ScannerAgent (scan + persist + summary) | 120 |
| `tool/pipeline.py` | 2-step coordinator + CLI behaviors (init/analyze/status/report) | 230 |
| `tool/report/data.py` | DB → JSON payload (pure function) | 400 |
| `tool/report/template.py` | The HTML/CSS/JS shell; payload injected as one token | 520 |
| `tool/source_io.py` | Encoding-tolerant file reading | 33 |
| `tool/mcp_server.py` | Optional MCP server so AI agents can drive the tool | 165 |

## 4. The six relationships (the vocabulary of the whole system)

| kind | level | meaning | detected from |
|---|---|---|---|
| `depends` | 0 | A uses B's code | B appears in A's method signatures |
| `associates` | 1 | weak reference | field `B*` / `shared_ptr<B>` / `occ::handle<B>` |
| `implements` | 2 | A realizes interface B | base B is abstract (real check, not name convention) |
| `aggregates` | 3 | A holds many B, shared | field `vector<B*>`-style container of pointers |
| `composes` | 4 | A owns B | field `B` by value / `unique_ptr<B>` |
| `inherits` | 5 | A is-a concrete B | base B has no pure virtuals |

Key decisions inside this table:
- **`#include` is NOT a dependency.** Includes mean "available", not
  "used". Only an actual appearance in a signature counts.
- **Multi-edges are kept.** If A composes B *and* aggregates B *and*
  calls B, that's three rows of evidence — the report shows all of
  them; the diagram renders the strongest and labels the rest.
- **interface vs abstract is decided by shape, not by name.** A class
  with pure virtuals and no data is an interface; with pure virtuals
  plus data/concrete methods it's an abstract base. The earlier
  "I-prefix" heuristic mislabeled real code (spdlog's `sink`) and died.

## 5. Ten design decisions worth explaining (with the rejected option)

1. **tree-sitter over libclang** — clang needs compilable code; users
   hand us partial code. An architecture tool needs robustness more
   than perfect type resolution. (Regex was the original approach;
   544 lines of it could not survive nesting/comments/templates.)

2. **`parent_qname` is a string, not a foreign key** — bulk inserts
   need no id lookups, debugging is readable, and we rebuild the whole
   graph each scan so referential integrity adds nothing.

3. **Evidence-per-row relationships** — `UNIQUE(source,target)` would
   force "keep the strongest". Keeping all rows preserves information
   the report and the LLM both use.

4. **Dominator tree for the workflow view** — "must every path to B
   pass through A?" is precisely "does A own B's responsibility?".
   Shared utilities float to the common dominator for free.

5. **Folding defaults to leaves-only** — collapsing `tcp_sink…` into
   `sink` cleans polymorphic noise, but folding everything to the top
   (the first attempt) flattened OpenCASCADE's `Geom_Curve → Conic →
   Circle` taxonomy into mush. Folding only leaf families (≥2 siblings)
   keeps intermediate layers.

6. **Style detection over false universality** — CRTP codebases (Eigen)
   invert the orchestrator signature: the architectural core is the
   most-INHERITED class, not the most-outgoing. We detect the style and
   display a warning instead of silently mis-ranking.

7. **Phantom classes: promoted in the engine, hidden in the UI** —
   out-of-line methods in a lone `.cxx` imply a class whose header we
   never saw. The engine materializes it (so its methods and the LLM
   review still work), but diagrams exclude it — only fully-defined
   code is what the user is analyzing.

8. **(mtime,size) cache fingerprint, not content hash** — hashing
   requires reading the file, which is most of the cost we're trying
   to skip. False match requires same-size same-mtime different
   content: not a real workflow.

9. **Layout once, never again** — the graph lays out with cose-bilkent
   a single time; expand/collapse toggles visibility and animates the
   camera. Re-running layouts on every click (two earlier attempts)
   destroyed the user's spatial memory. Users can drag nodes; a Reset
   button recovers.

10. **LLM methodology as data, not code** — the review method lives in
    prompt templates (`_critic_lenses` + `_critic_phases`); a user can
    replace it wholesale by dropping `skills/design_critic.md`. The
    embedded default keeps its know-how in the *ordering* of analysis
    steps, written in standard OOD vocabulary.

## 6. The DesignCritic (the LLM stage) in 60 seconds

```
Pass 1 — for EACH workflow subtree (bounded context):
    input : that subtree's classes, methods, fields, internal edges
    output: essence („what is this fundamentally doing"),
            ideal pipeline & components, pain points (file:line),
            current-code → ideal-component mappings        (JSON)

Pass 2 — once, over all Pass-1 essences + cross-subtree edges:
    output: module workflow, repeated-pattern observations,
            missing abstractions, prioritized recommendations
```

Why two passes: a single giant prompt loses local detail and blows the
context window; per-subtree passes keep precision, the synthesis pass
sees what no single subtree shows (e.g. "these two extractors share
the same 6-stage skeleton — extract a base class").

Both passes cache their responses (prompt-hash), so re-running costs
nothing if nothing changed.

## 7. Performance numbers (measured, not estimated)

| scenario | before | after |
|---|---|---|
| Eigen/Core re-scan (cache) | 5.3 s | 0.24 s (~22×) |
| Eigen/Core first scan (4 cores) | 5.3 s | 1.9 s (~2.7×) |
| spdlog re-scan | 0.47 s | 0.15 s |
| same LLM prompt twice | full API cost | 0 (cache) |

## 8. Known limits (say these up front when presenting)

- Method *bodies* are not analyzed — relationships come from
  signatures and fields. Call-graph extraction was considered and
  rejected: in C++ it requires overload resolution, and field/signature
  evidence already captured everything our shop fixtures needed.
- Template instantiations are not expanded (Eigen-style metaprogramming
  is detected and warned about, not deeply modeled).
- `.sch` private DSL gets a minimal regex parser (classes, superclass,
  fields) — deliberately, since tree-sitter has no grammar for it.
- The LLM stage needs an API key; everything else runs offline.
