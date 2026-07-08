"""
host.py — the agentic Host: a conversational loop over the tool registry.

Responsibility (SRP): own the conversation and drive the ReAct-style agent
loop. It does NOT do analysis and does NOT know any provider's wire format —
it only moves messages between the user, the LLM (llm.py), and the tools
(tools.py).

The loop (single-threaded on purpose — each step depends on the previous
step's tool results, so there is nothing to parallelize):

    user text ─► LLM (+tool schemas) ─► either
        · a tool call  → run it, feed the result back, loop again
        · final text   → print it, done

MAX_STEPS bounds the loop so a confused plan can't burn the API forever.
"""
from .config import SOURCE_ROOT
from .tools import ToolContext, build_registry, tool_schemas, run_tool


MAX_STEPS = 12          # hard ceiling on tool-call rounds per user turn


SYSTEM_PROMPT = """\
You are CodeProbe, an assistant that diagnoses the architecture of C++
codebases. You answer by calling tools and reasoning over their results.

GROUNDING (non-negotiable): never invent classes, relationships, or
structure. Every structural claim you make must come from a tool result.
If you lack data, call a tool to get it — do not guess.

How to work:
- If the DB looks empty, call scan_source first. It is idempotent: a full
  scan runs only once, then everything reads from the DB. Do not rescan
  unless the user says the code changed.
- Prefer the cheap read tools (get_overview, list_classes,
  get_relationships, get_findings, query_db) to answer questions.
- CLASS relationships: get_relationships(class_qname=X) gives what X uses;
  direction='incoming' gives who uses X (reverse dependents); 'both' for
  the full neighbourhood. Use these instead of writing SQL for one class.
- ANALYZING or COMPARING specific classes ("is class A better designed than
  B?", "describe X", "compare the refactored X to the old Y"): call
  describe_class ONCE PER CLASS to get its methods/fields/size/bases/
  subclasses/dependencies, then reason over the concrete numbers and cite
  file:line. You DO have access to the code through these tools — never
  reply that you "cannot access the codebase" or hand back a generic
  checklist. If you lack a specific fact, call a tool for it.
- SCOPE / naming: class names collide across namespaces (e.g. two
  ResultProbeDeleter). Users type short names; the tools resolve them, but
  an ambiguous name comes back as a list of full scoped candidates — pick
  the one whose namespace fits the question (or ask the user which), and
  refer to classes by enough scope to be unambiguous.
- Call design_review only when the user wants a design critique.
- Call generate_report only when the user explicitly asks for a report.

MODULE questions ('module A depends on module B N times, what are the N?',
'which modules form a cycle', 'what does module X depend on'): use
module_dependencies (or architecture_audit for the health check). Modules
are COMPUTED (folder/namespace/community grouping) and are NOT a column in
the DB — do NOT try to derive them with SQL over file_path/SUBSTR/LIKE;
that will fail. module_dependencies returns the exact class-level
references with file:line behind any module edge.

query_db schema (there is no 'module' column anywhere; paths use forward
slashes '/'):
  entities(id, kind, name, qualified_name, parent_qname, file_path,
           start_line, end_line, signature, attrs)   -- kind in
           class|struct|interface|enum|method|field|namespace
  relationships(id, source_qname, target_qname, target_name, kind, level,
           evidence_file, evidence_line, evidence_text, attrs)
           -- target_qname is NULL when the target is external
Do NOT probe the schema with PRAGMA/sqlite_master — it is given above.

Style: answer directly and concisely in plain language. Cite file:line
evidence when you point at a problem. Do not dump raw tool output — explain
what it means. When you have enough to answer, STOP calling tools and give
the answer.
"""


class Host:
    """Holds one conversation and runs the agent loop over it."""

    def __init__(self, ctx=None, verbose=True):
        self.ctx = ctx or ToolContext.build(SOURCE_ROOT)
        self.registry = build_registry(self.ctx)
        self.schemas = tool_schemas(self.registry)
        self.history = []
        self.verbose = verbose

    def ask(self, user_msg: str) -> str:
        """Run one user turn to completion and return the final text."""
        self.history.append({"role": "user", "content": user_msg})
        for _ in range(MAX_STEPS):
            resp = self.ctx.llm.generate_with_tools(
                self.history, self.schemas, SYSTEM_PROMPT)
            self.history.append(resp.assistant_message)

            if not resp.tool_calls:
                return resp.text or "(no answer)"

            # Run each requested tool, appending its result so the model
            # sees it on the next turn. Order is preserved.
            for call in resp.tool_calls:
                if self.verbose:
                    print(f"    → {call.name}({_fmt_args(call.args)})")
                result = run_tool(self.registry, call.name, call.args, self.ctx)
                self.history.append(
                    self.ctx.llm.tool_result_message(call, result))

        return "(stopped: hit the step limit without a final answer)"

    def repl(self):
        """Interactive prompt. Ctrl-D / 'exit' to quit."""
        print("CodeProbe chat — ask about the C++ architecture. "
              "'exit' to quit.\n")
        while True:
            try:
                msg = input("you › ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not msg:
                continue
            if msg.lower() in ("exit", "quit"):
                break
            answer = self.ask(msg)
            print(f"\n{answer}\n")


def _fmt_args(args: dict) -> str:
    """Compact one-line render of tool args for the activity trace."""
    if not args:
        return ""
    return ", ".join(f"{k}={v!r}" for k, v in args.items())


def run_chat():
    """Entry point for `python run.py chat`."""
    Host().repl()
