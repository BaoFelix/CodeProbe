# CodeProbe

AI-powered C++ design diagnostic tool. Analyzes class design quality using the Seven Sins framework and proposes refactoring plans.

## Features

- **Multi-Agent pipeline**: ScannerAgent → ResponsibilityAgent → DesignAgent
- **Seven Sins diagnosis**: God Class, Inheritance Hell, Abstraction Absence, Feature Envy, Circular Entanglement, Hidden State, Knowledge Leakage
- **Three-layer HTML report**: Module Workflow → Dependency Health → Pain Points & Proposals
- **Incremental analysis**: interrupt & resume, selective re-run with `--from=STEP`
- **Multiple LLM backends**: OpenAI-compatible APIs, Anthropic Claude

## Quick Start

```bash
# 1. Setup
cp .env.example .env        # Edit .env with your API key
python run.py init           # Initialize database

# 2. Analyze — provide path to your C++/schema source directory
python run.py analyze test_src/

# 3. Report
python run.py report         # Generate HTML report → outputs/report.html
```

## Commands

| Command | Description |
|---------|-------------|
| `init` | Reset database |
| `analyze <path>` | Full pipeline: scan → responsibility → design. `<path>` is a directory containing C++ source files, or a single header file. |
| `focus <class>` | Deep analysis of a single class |
| `status` | Progress dashboard |
| `report` | Generate HTML diagnostic report |

### Options

- `--from=STEP` — Re-run from a specific step (`scan`, `resp`, or `design`)

### Supported file formats

| Extension | Type |
|-----------|------|
| `.hxx` | C++ header |
| `.sch` | Schema header (treated as C++ header) |
| `.cxx` | C++ implementation |

The scanner reads `.hxx` and `.sch` files to discover class definitions, then looks for matching `.cxx` files for implementation details.

## Architecture

```
Pipeline (coordinator)
  ├── ScannerAgent    → reads C++/schema files, extracts class structure → DB
  ├── ResponsibilityAgent → per-class sin diagnosis via LLM → DB
  └── DesignAgent     → refactoring proposal via LLM → DB

Report (three-layer HTML)
  ├── Layer 1: Module Workflow (seed clustering + dependency absorption)
  ├── Layer 2: Dependency Health (6-level pyramid diagnosis)
  └── Layer 3: Pain Points & Proposals (sin-based, grouped by class)
```

## Configuration

Copy `.env.example` to `.env` and set your LLM API credentials:

```bash
# OpenAI-compatible (GitHub Models, etc.)
LLM_API_FORMAT=openai
LLM_API_URL=https://models.inference.ai.azure.com
LLM_API_KEY=your-token
LLM_MODEL=gpt-4o

```

## Requirements

- Python 3.10+
- No external dependencies for core functionality (uses stdlib `urllib`)
- Optional: `pip install mcp` for MCP server mode
