"""
Shared fixtures for the CodeProbe test suite.

Testing philosophy (mirrors the code's design):
  · The deterministic core (parser, graph checks, decoupling planner) is
    tested EXACTLY — same input, same output, no tolerance.
  · The LLM seams (RuleCompiler, Verifier, Host loop, tool-use parsing)
    are tested with FAKES — scripted responses, mocked HTTP — so the whole
    suite runs offline, keyless, in seconds.
  · test_src/ is the one real-parse integration fixture; it is scanned
    ONCE per test session (scan is the slow step) and shared read-only.
"""
import tempfile
from pathlib import Path

import pytest

from tool.db import DBManager
from tool.llm import LLMClient, LLMResponse, ToolCall
from tool.source_io import SourceReader
from tool.tools import ToolContext, build_registry, run_tool


# ── synthetic row builders (what the DB would hand back) ─────────────

def make_class(name, module):
    """A class row: module membership is derived from the folder path."""
    return {"qualified_name": name, "file_path": f"src/{module}/{name}.hxx"}


def make_rel(source, target, kind="depends", file="x.hxx", line=1):
    return {"source_qname": source, "target_qname": target,
            "target_name": target, "kind": kind,
            "evidence_file": file, "evidence_line": line}


# ── fake LLMs ────────────────────────────────────────────────────────

class CannedLLM:
    """generate() always returns the same canned text — for RuleCompiler /
    Verifier tests where one JSON answer is enough."""
    api_key = "fake"
    api_url = "http://fake"

    def __init__(self, response):
        self.response = response

    def generate(self, prompt, system_prompt="", tag=""):
        return self.response


class ScriptedLLM:
    """generate_with_tools() plays back a fixed script of LLMResponse
    objects — lets Host-loop tests drive REAL tools with zero network."""
    api_key = ""
    api_url = ""

    def __init__(self, script):
        self.script = script
        self.i = 0

    def generate_with_tools(self, messages, tools, system_prompt=""):
        resp = self.script[min(self.i, len(self.script) - 1)]
        self.i += 1
        return resp

    def tool_result_message(self, tool_call, result):
        return {"role": "tool", "tool_call_id": tool_call.id,
                "content": result}


def tool_step(name, args=None, call_id="c1"):
    """One scripted 'the model wants to call a tool' turn."""
    return LLMResponse(text="", tool_calls=[ToolCall(call_id, name, args or {})],
                       assistant_message={"role": "assistant", "content": None})


def final_step(text):
    """One scripted 'the model answers' turn."""
    return LLMResponse(text=text, tool_calls=[],
                       assistant_message={"role": "assistant", "content": text})


# ── contexts ─────────────────────────────────────────────────────────

def fresh_ctx(tmp_path, llm=None, source_root="test_src"):
    """A ToolContext over a brand-new temp DB (mutating tests use this)."""
    db = DBManager(tmp_path / "t.db")
    db.ensure_tables()
    return ToolContext(db=db, llm=llm or LLMClient(cache=db),
                       reader=SourceReader(source_root, db=db),
                       source_root=Path(source_root))


@pytest.fixture()
def ctx(tmp_path):
    """Per-test empty context."""
    return fresh_ctx(tmp_path)


@pytest.fixture(scope="session")
def scanned_ctx():
    """test_src scanned ONCE for the whole session. Treat as read-only."""
    tmp = Path(tempfile.mkdtemp(prefix="codeprobe-tests-"))
    context = fresh_ctx(tmp)
    registry = build_registry(context)
    result = run_tool(registry, "scan_source", {"directory": "test_src"},
                      context)
    assert "Scan complete" in result, result
    return context
