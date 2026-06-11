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
| `analyze <path>` | Full pipeline: scan → design review. `<path>` is a directory or a single header file |
| `status` | Show analysis progress dashboard |
| `report` | Generate the interactive HTML report |
| `mcp-server` | Run as an MCP server so AI agents can drive the tool (`pip install mcp`) |

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
      │
      ▼
 db.py             SQLite = shared memory between all stages
      │
      ├────────────────────┐
      ▼                    ▼
 workflow.py          design_critic.py
 graph analysis       two-pass LLM review
      │                    │
      └─────────┬──────────┘
                ▼
 report/        one self-contained interactive HTML
```

Full design rationale — including the ten key design decisions and the
trade-offs behind them — is in [`architecture.md`](architecture.md).

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

## Customizing the design review

Drop a `skills/design_critic.md` file in the project root to replace the
built-in review methodology with your own prompt playbook — no code
changes required.

## Requirements

- Python 3.10+
- `tree-sitter`, `tree-sitter-cpp`, `networkx` (see `requirements.txt`)
- Optional: `mcp` for MCP server mode
- LLM calls use stdlib `urllib` — no HTTP client dependency

## Documentation

- [`architecture.md`](architecture.md) — presenter's guide to the whole
  system: data flow, module map, the six relationships, ten design
  decisions with the rejected alternatives, performance numbers, known limits.
- [`LearningLog.md`](LearningLog.md) — lessons learned while building it
  (in Chinese).
