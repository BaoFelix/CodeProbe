# CodeProbe → Agentic: Design Note

> Companion to [`architecture.md`](architecture.md). That file describes the
> **engine** (parse → graph → critic → report). This file describes the
> **agentic layer** we want to add on top: a conversational Host that reads
> natural language, decides a strategy, and uses the engine's accurate facts
> to answer what the user actually wants — not always a fixed report.
>
> Status: PARTLY BUILT. The tool registry, tool-use, the agent-loop Host
> (`run.py chat`), and the deterministic architecture audit + its LLM layer
> are implemented; see "Implementation status" at the end.

---

## 1. Where we are today (and why it's not agentic)

Today `Pipeline` runs a **fixed two-step workflow**, hard-coded in Python:

```
run.py analyze  ─►  ScannerAgent  ─►  DesignCriticAgent  ─►  report
                    (tree-sitter)     (LLM only INSIDE this step)
                    └────── the order is written in if/else, not decided ──────┘
```

The LLM has **zero control over the flow**. It is called *inside* one step to
fill in analysis; it never decides *what to do next*. That is the precise
definition of "not agentic": the intelligence has no authority over control
flow.

```
Workflow (today):   a human hard-codes the steps   →  the LLM fills blanks
Agentic (goal):     the user speaks naturally       →  the LLM decides which
                    tools to call, in what order, looping until done
```

The engine is not the problem — it is an asset. What is missing is a layer
that turns "the user's intent" into "the right sequence of tool calls over
the facts we already have".

---

## 2. The three MCP roles, and why Host = Server here

MCP has three roles. Naming them removes most of the confusion:

| Role | What it is | In CodeProbe |
|---|---|---|
| **Host** | the program the user talks to — holds the LLM + the orchestration brain | **does not exist yet** (we build it) |
| **MCP Client** | lives inside the Host, speaks the MCP wire protocol to servers | **does not exist yet** (only needed for *external* servers) |
| **MCP Server** | exposes tools to the outside world | `tool/mcp_server.py` ✅ |

### The key decision: do NOT talk to your own server over the wire

The value of the MCP *protocol* is **crossing process boundaries** — letting
*someone else's* Host (Copilot, Claude Desktop) call your tools. Your own Host
calling your own tools over stdio would be ceremony for nothing.

So the architecture is: **one implementation of each tool in a shared
registry**; the Host calls it as a plain in-process function; the MCP server
is a thin wrapper around the same registry for external hosts.

```
        ┌──────────────────────────────────────────────────┐
        │  ONE tool implementation  (tool/tools.py)         │
        │  scan · get_overview · get_relationships ·        │
        │  get_workflow · get_findings · analyze · report   │
        └───────────────┬──────────────────────┬───────────┘
                        │                      │
         plain function │                      │  @mcp.tool() thin wrapper
             calls      ▼                      ▼
        ┌───────────────────────────┐   ┌────────────────────────┐
        │  Host  (our process)      │   │  MCP Server (external)  │
        │  LLM + agent loop         │   │  Copilot / Claude call  │
        │  ← user types NL here     │   └────────────────────────┘
        └───────────────────────────┘
```

> Your instinct — "Host and Server are one program in one process" — is
> correct and *should* be so. Just don't route your own calls through the
> network protocol. Share a registry instead.

---

## 3. How an LLM's reply becomes a real function call

This is the mechanism question, answered end to end. **You do not parse free
text.** Modern tool-use APIs return a *structured* call instruction.

It is a three-step handshake:

**① Send the tools' "manuals" alongside the user's question.**
```json
{
  "messages": [{"role": "user", "content": "who is the orchestrator here?"}],
  "tools": [
    {"name": "get_overview",
     "description": "Return module overview: orchestrator, style, counts",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "get_class",
     "description": "Return a class's methods and fields",
     "parameters": {"type": "object",
                    "properties": {"qname": {"type": "string"}},
                    "required": ["qname"]}}
  ]
}
```

**② The model does not reply with prose — it replies with a structured call.**
```json
{"tool_calls": [{"name": "get_overview", "arguments": {}}]}
```
(Anthropic's shape is `{"type":"tool_use","name":"get_overview","input":{}}`.
`llm.py` already supports both wire formats; each just needs its own parse.)

**③ Look the name up in a registry, run it, feed the result back.**
```python
REGISTRY = {                       # name → the actual Python function
    "get_overview": get_overview,
    "get_class":    get_class,
}

call   = resp["tool_calls"][0]
result = REGISTRY[call["name"]](**call["arguments"])   # text → function, one line
history.append({"role": "tool", "content": result})    # feed result back
# ask the model again; now it has the facts and can answer
```

So "text → function" relies on **(a)** the API returning a structured
name+args object and **(b)** a `{name: function}` dict lookup — no regex, no
guessing. Because Host and tools share a process, `REGISTRY[name](**args)` is
just a function call with zero protocol overhead.

> ⚠️ **The one hard prerequisite.** `LLMClient.generate()` today only does
> plain-text completion. Agentic behavior needs a new method that passes
> `tools=[...]` and parses `tool_calls` back. This is the linchpin of the
> whole effort (Phase 2 below).

---

## 4. The collaboration strategy: "Grounded Analyst"

This is the heart of the design, and it comes straight from a good instinct:

> The real asset is **not** the fixed report. It is the **accurate,
> pre-computed facts in the DB** — the high-level workflow, every
> relationship, the per-class responsibility analysis. The report is just
> *one rendering* of those facts.

So the strategy is: **treat the DB as a knowledge base (a "wiki").** The
orchestrator LLM reads the user's intent, retrieves the relevant accurate
facts, judges them through the lens of the user's own `skills/` conventions,
and answers in whatever form fits — often a direct answer, not a report.

```
        user's natural-language intent
                     │
                     ▼
          ┌────────────────────┐
          │  Orchestrator LLM  │  "what does the user actually want?
          │  (plan)            │   architecture-level? one class? a smell?
          └─────────┬──────────┘   a refactor plan?"
                    │ 1. LENS:     load the user's skill (arch style/templates)
                    │ 2. RETRIEVE: pull the relevant FACTS from the DB
                    │              (never let the LLM invent structure)
                    │ 3. ANALYZE:  reason over facts × lens
                    │ 4. ANSWER:   the format that fits — a paragraph,
                    │              a table, a list. Report only if asked.
                    ▼
   ┌──────────────────────────────────────────────────────┐
   │  DB = knowledge base (single source of truth)          │
   │  workflow tree · all relationships · per-class critic  │
   └──────────────────────────────────────────────────────┘
   The HTML report is now ONE output tool, not the mandatory endpoint.
```

### Two kinds of tools — the distinction that shapes the registry

| Kind | Behavior | Examples |
|---|---|---|
| **Retrieval** (deterministic) | pull already-computed facts from the DB — zero hallucination | `get_overview` · `get_workflow_tree` · `get_relationships(cls)` · `get_findings` · `query_db(sql)` |
| **On-demand analysis** (LLM) | when the DB has no ready answer, compute one *grounded in retrieved facts + the user's skill lens* | `analyze(question, lens=skill)` |

Why both? Because many questions are **not pre-computed**. "Which classes
violate the layering rule in my skill file?" cannot come from a fixed report —
it needs: retrieve the real relationships → apply the user's rule as a ruler →
analyze on the spot. Retrieval keeps answers grounded; on-demand analysis
keeps them flexible. A fixed set of specialized checks would be faster but
would answer only questions we predicted; the general `analyze` tool covers
the long tail. We keep both: specialized tools for common asks, `analyze` for
everything else.

### Worked example — why this beats a fixed report

> User: *"My architecture says the UI layer must not depend on the DB layer.
> Any violations?"*

```
Orchestrator:
  ① intent = architecture-level constraint check (not a single-class audit)
  ② load skills/design_critic.md → get the layering rule as the ruler
  ③ get_relationships()  → all real edges (DB facts, cannot be fabricated)
  ④ get_class(...)       → confirm which classes are UI vs DB layer
  ⑤ analyze: which edge has source in UI and target in DB → violation
  ⑥ answer directly:
     "2 violations:
        AppWindow → SqlConnection  (composes, AppWindow.cxx:88)
        Toolbar   → QueryCache     (depends,  Toolbar.cxx:45)"
     ← a direct answer, NOT stuffed into a report — because that is what
       the user asked for.
```

**Default to a direct conversational answer.** The report becomes an explicit
tool the user invokes ("now generate the report"). Everything bends toward the
user's need and answer quality — which is exactly the value an agentic layer
adds over a fixed pipeline.

---

## 5. Topology: Orchestrator–Worker, with a Pipeline nested inside

Applying the SRP test honestly (and the "don't over-split" rule), only two
roles are genuinely different: **(a)** understand NL + choose tools (new), and
**(b)** do the domain work (scan/review — already exists). So:

```
┌──────────────────────────────────────────────────────────┐
│  TOP: Orchestrator–Worker  ← the main structure           │
│                                                            │
│   user NL ─► [Orchestrator LLM] ─┬─► scan        (worker)  │
│                  ▲               ├─► retrieve/*   (worker)  │
│                  │               ├─► analyze      (worker)  │
│                  └── loop: feed  ├─► run_review   (worker)  │
│                      results back└─► generate_report        │
│                                                            │
│   scan → review is itself a PIPELINE (nested)              │
│   review's two passes are themselves a mini-pipeline       │
└──────────────────────────────────────────────────────────┘
```

Deliberately deferred (add only when a real need appears):

| Topology | Now? | Why / when |
|---|---|---|
| **Orchestrator–Worker** | ✅ build | exactly "understand NL, dispatch dynamically"; the industry default |
| **Pipeline** | ✅ have | keep scan→review as one "deep analysis" macro-tool |
| **Parallel fan-out** | 🟡 later | DesignCritic pass-1 (per-subtree) is independent and *could* fan out — an internal speed optimization, not a user-facing topology |
| **Critic / Debate** | 🔴 not yet | expensive; add only when finding-quality proves unstable and worth the cost |

---

## 6. The tool registry (concrete)

One module, `tool/tools.py`, is the single source of truth. Each tool is a
plain function plus a JSON-schema description. Both the Host and the MCP
server import it.

```
# Retrieval (deterministic, from DB — grounding)
get_overview()                       → orchestrator, style, counts
get_workflow_tree()                  → the dominator/responsibility tree
get_relationships(qname=?, kind=?)   → edges, optionally filtered
get_class(qname)                     → a class with its methods & fields
list_classes(filter=?)               → class names
get_findings(scope=?)                → DesignCritic results already in DB
query_db(sql)                        → read-only SELECT escape hatch

# Action / compute
scan_source(directory)               → (re)build the DB from source
run_design_review()                  → the two-pass critic (expensive)
analyze(question, lens=?)            → on-demand grounded analysis (the
                                       "LLM Wiki" answer engine)
generate_report()                    → render the fixed HTML (now optional)
```

`mcp_server.py` shrinks to thin `@mcp.tool()` wrappers over these — deleting
the duplicated logic it holds today.

---

## 7. The agent loop (the Host's core)

```python
def chat_turn(user_msg, history):
    history.append({"role": "user", "content": user_msg})
    while True:
        resp = llm.generate_with_tools(history, TOOL_SCHEMAS)   # Phase 2
        if resp.tool_calls:
            for call in resp.tool_calls:
                result = REGISTRY[call.name](**call.arguments)  # in-process
                history.append(tool_result(call, result))
            continue                     # let the model see results, decide next
        print(resp.text)                 # final answer — usually direct, not a report
        history.append({"role": "assistant", "content": resp.text})
        return
```

A REPL (`python run.py chat`) wraps this: read a line of NL, run `chat_turn`,
repeat. Conversation history persists across turns so the user can refine
("now only the ones in the rendering module").

---

## 8. Phased plan (avoid over-building)

```
Phase 1  Extract tool/tools.py registry (functions + JSON schemas).
         Refactor mcp_server.py into thin wrappers. No LLM change — safest
         first step, zero risk.
Phase 2  Add llm.generate_with_tools(): tool-use for both OpenAI and
         Anthropic formats. The linchpin.
Phase 3  Add tool/host.py agent loop + `python run.py chat` REPL.
         ← at this point CodeProbe is genuinely agentic.
Phase 4  The analyze() tool: grounded on-demand analysis with skill-as-lens.
         ← this delivers the "Grounded Analyst / LLM Wiki" strategy.
Phase 5  (optional) Fan out DesignCritic pass-1 for speed.
Phase 6  (optional) MCP Client: let the Host also consume EXTERNAL MCP
         servers (git, filesystem, ...).
Phase 7  (optional) A critic/debate pass to double-check design findings.
```

---

## 9. Implementation status

Built and verified (mock LLM where a key is needed):

| Piece | Where | Status |
|---|---|---|
| Tool registry (stable contract) | `tool/tools.py` | ✅ |
| Tool-use (both providers) | `tool/llm.py` `generate_with_tools` | ✅ |
| Agent-loop Host + `run.py chat` | `tool/host.py` | ✅ |
| Architecture audit — deterministic moat | `tool/architect/` (modules, checker, contract, audit) | ✅ |
| Universal checks | `no_module_cycle` · `god_module` · `inverted_core` | ✅ |
| User rules: prose → contract | `tool/architect/compiler.py` (+ `skills/architecture.md`) | ✅ |
| False-positive gate (fan-out) | `tool/architect/verifier.py` | ✅ |

Deliberately not built: a separate Synthesizer (the Host's LLM already
explains tool results); MCP-client consumption of external servers; a
debate pass. Real end-to-end `chat` against a live LLM is pending an API
key (wiring proven with a scripted fake LLM over the real tools).

Phases 1–4 deliver the full vision. Everything after is opt-in.

---

## 9. Open decisions

- **Conversation memory**: in-RAM per session first; persist to a `chat`
  table later if users want resumable sessions.
- **Cost guardrails**: `analyze` and `run_review` call the LLM. Consider a
  per-turn tool-call budget so a runaway plan can't burn the API.
- **Grounding discipline**: the orchestrator's system prompt must forbid
  inventing structure — every structural claim must trace to a retrieval tool
  result. This is what keeps answers trustworthy.
- **Fallback without native tool-use**: if a configured model lacks tool-use,
  fall back to a ReAct-style "emit one JSON action" prompt and parse it — less
  reliable, but keeps the feature working on any backend.
```
