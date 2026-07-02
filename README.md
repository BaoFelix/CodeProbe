# CodeProbe

AI-powered C++ architecture diagnostic tool. Point it at a C++ codebase —
even a partial one — and it builds an accurate entity-relationship graph
with tree-sitter, derives the architecture (orchestrator, utilities,
workflow hierarchy) with graph algorithms, has an LLM audit the design
top-down, and ships everything as one interactive HTML report.

## Features

- **Accurate parsing via tree-sitter** — no compiler needed; partial /
  non-compilable code is fine. Hardened on real projects (spdlog, OpenCASCADE,
  Eigen): export macros, typedef aliases, templated bases, out-of-line
  methods, forward declarations all handled.
- **Six UML relationship kinds** — depends / associates / implements /
  aggregates / composes / inherits, each backed by evidence lines from the
  actual source.
- **Architecture derivation** — orchestrator scoring, utility detection,
  dominator-tree workflow hierarchy, SCC cycle condensation, abstraction
  folding, architecture style detection (OOP / mixed / CRTP with warning).
- **Two-pass LLM design review** — per-subtree analysis then module-level
  synthesis; methodology is user-overridable via `skills/design_critic.md`.
- **Interactive HTML report** — one self-contained file: workflow tree,
  draggable UML relationship diagram (layout once, camera animates), and
  the design review. No server needed.
- **Fast re-runs** — parse cache (~22× on re-scan), multiprocess first
  scan, prompt-hash LLM cache (re-running an unchanged review is free).
- **Multiple LLM backends** — OpenAI-compatible APIs (GitHub Models etc.)
  and Anthropic Claude. Scanning and reporting work fully offline.

## Quick Start

```bash
# 0. Install dependencies
pip install -r requirements.txt

# 1. Setup
cp .env.example .env         # add your LLM API key (only needed for the design review)
python run.py init           # initialize the database

# 2. Analyze — point at your C++ source directory
python run.py analyze path/to/your/cpp/sources

# 3. Report
python run.py report         # → outputs/report.html, open in any browser
```

## Commands

| Command | Description |
|---------|-------------|
| `init` | Initialize / reset the database |
| `analyze <path>` | Fixed pipeline: scan → design review. `<path>` is a directory or a single header file |
| `chat` | Talk to the codebase — an agentic loop that picks tools from your question (needs an LLM API key) |
| `status` | Show analysis progress dashboard |
| `report` | Generate the interactive HTML report |
| `mcp-server` | Run as an MCP server so AI agents can drive the tool (`pip install mcp`) |

### `chat` — the agentic way to use it

`analyze` runs a fixed pipeline; `chat` lets you just ask. A Host loop sends
your question plus the available tools to the LLM, which decides what to run
— scan, retrieve the graph, audit the architecture, review class design,
query the DB, or render a report — and answers you directly.

```bash
python run.py init
python run.py chat
# you › scan test_src, then tell me if the module architecture is healthy
```

Two altitudes, picked by your question:
- **Architecture level** — module cycles, god modules, inverted (unstable)
  dependencies, and any rules you declare. Ask *how* to break a cycle and
  the decoupling planner answers surgically: the cheapest edge(s) to cut
  (minimum feedback set), the mechanism (dependency inversion / extract
  shared base), the exact `file:line` references to change, and a
  build-safe refactor order. All of it is deterministic (graph algorithms;
  every finding carries evidence); the LLM only explains it. Runs even
  without an API key via the built-in checks.
- **Class level** — the two-pass design review (per-class critique).

### Options

- `--from=STEP` — re-run from a specific step: `scan` or `review`.
  E.g. `python run.py analyze src/ --from=review` re-runs only the LLM
  review (cheap thanks to the LLM cache).

### Supported file formats

`.hxx` `.h` `.hpp` (headers) · `.cxx` `.cpp` (implementations) · `.sch`
(schema DSL, minimal regex parser). Vendored/bundled/external directories
are excluded automatically.

## How it works

```
 C++ sources (partial is fine)
      │
      ▼
 ts_parser.py      tree-sitter → entities + 6 relationship kinds
      │            (signatures, fields, inheritance, body-call usage)
      ▼
 db.py             SQLite = shared memory between all stages
      │
      ├────────────────────┬─────────────────────┐
      ▼                    ▼                     ▼
 workflow.py          design_critic.py      architect/
 graph analysis       two-pass LLM review   module audit +
      │                    │                decoupling plans
      └─────────┬──────────┴─────────────────────┘
                ▼
 report/ · chat (host.py) · MCP — three ways to consume the same tools
```

## Configuration

Copy `.env.example` to `.env` and set your LLM credentials. Two examples:

```bash
# OpenAI-compatible (GitHub Models — free tier available)
LLM_API_FORMAT=openai
LLM_API_URL=https://models.inference.ai.azure.com
LLM_API_KEY=your-github-token
LLM_MODEL=gpt-4o

# Anthropic Claude
LLM_API_FORMAT=anthropic
LLM_API_URL=https://api.anthropic.com
LLM_API_KEY=sk-ant-...
LLM_MODEL=claude-sonnet-4-20250514
```

Useful environment switches:

- `LLM_FALLBACK_MODELS` — comma-separated fallback chain used automatically
  on 429 rate limits.
- `LLM_NO_CACHE=1` — bypass the LLM response cache (useful while tuning
  prompts).

The LLM key is only needed for the design review step; scanning,
architecture derivation, and the HTML report all run offline.

## Customizing

Two `skills/` files let you tailor the analysis without touching code:

- **`skills/design_critic.md`** — replace the built-in class-level review
  methodology with your own prompt playbook. See
  [`skills/README.md`](skills/README.md).
- **`skills/architecture.md`** — declare your architecture rules in plain
  language (e.g. "the UI layer must not depend on the database layer"). The
  architecture audit compiles them into checkable rules and reports any
  violations with `file:line` evidence, alongside the built-in universal
  checks. Copy `skills/architecture.example.md` to get started.

## Tests

```bash
pip install pytest
python -m pytest tests/     # offline, keyless, ~1 second
```

The deterministic core (parser, module audit, decoupling planner) is tested
exactly; every LLM seam is tested with fakes — the suite never touches the
network.

## Requirements

- Python 3.10+
- `tree-sitter`, `tree-sitter-cpp`, `networkx` (see `requirements.txt`)
- Optional: `mcp` for MCP server mode; `pytest` for the test suite
- LLM calls use stdlib `urllib` — no HTTP client dependency
