# CodeProbe v1.0.0

First public release. Point CodeProbe at a C++ codebase — even a partial,
non-compilable one — and get back an interactive HTML report of its
architecture plus an LLM design review.

## Highlights

### Parsing engine (tree-sitter)
- Entities: namespace / class / struct / interface / method / field, with
  fully qualified names and source locations.
- Six relationship kinds with per-line evidence: depends, associates,
  implements, aggregates, composes, inherits.
- Hardened on real projects (spdlog, OpenCASCADE, Eigen): export macros,
  typedef/using alias chains, templated bases, out-of-line methods,
  forward declarations, smart-pointer equivalents (`occ::handle`, etc.).
- Interface vs abstract decided by class shape (pure virtuals + data),
  not naming conventions.

### Architecture derivation (graph algorithms)
- Orchestrator scoring and utility detection.
- Dominator-tree workflow hierarchy with SCC cycle condensation.
- Abstraction folding (leaf families fold into their base).
- Architecture style detection: OOP / mixed / CRTP — CRTP codebases get
  an explicit warning instead of a silently wrong ranking.
- Phantom classes (implementation-only `.cxx`) participate in analysis
  but are excluded from diagrams.

### LLM design review
- Two-pass DesignCritic: per-subtree analysis, then module-level synthesis.
- Methodology fully overridable by dropping `skills/design_critic.md`.
- OpenAI-compatible and Anthropic backends; automatic 429 fallback chain;
  prompt-hash response cache (unchanged re-runs are free).
- Optional: scanning and the report work entirely offline.

### Interactive HTML report
- One self-contained file, no server: workflow tree, UML relationship
  diagram, design review.
- Diagram lays out once (cose-bilkent) and never re-shuffles; expand /
  collapse only toggles visibility and animates the camera. Nodes are
  draggable; a Reset button restores the layout.

### Performance
- Parse cache: Eigen/Core re-scan 5.3 s → 0.24 s (~22×).
- Multiprocess first scan: 5.3 s → 1.9 s on 4 cores (~2.7×).
- LLM cache: repeated prompts cost zero API calls.

## Getting started

```bash
pip install -r requirements.txt
cp .env.example .env          # add an API key (only for the design review)
python run.py init
python run.py analyze path/to/cpp/sources
python run.py report          # → outputs/report.html
```

## Documentation

- `architecture.md` — full design rationale: data flow, module map, the
  six relationships, ten design decisions with rejected alternatives,
  measured performance, known limits.
- `README.md` — install, commands, configuration.

## Known limits

- Method bodies are not analyzed; relationships come from signatures and
  fields.
- Template instantiations are not expanded (CRTP/metaprogramming is
  detected and warned about, not deeply modeled).
- `.sch` schema files get a minimal regex parser.
- The design review step needs an LLM API key; everything else is offline.
