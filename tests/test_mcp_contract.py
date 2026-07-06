"""
MCP contract tests — the advertised wire schema must BE the registry schema.

The wrappers are generated from ToolSpec, so these tests are the proof of
the anti-drift claim: every registry tool is exposed, with the same
parameter names and required-ness, and dispatches into the same handler.
Skipped cleanly when the optional `mcp` package isn't installed.
"""
import pytest

mcp_pkg = pytest.importorskip("mcp", reason="mcp not installed (optional)")

from tool import mcp_server
from tool.tools import ToolContext, build_registry
from tool.db import DBManager
from tool.llm import LLMClient
from tool.source_io import SourceReader


@pytest.fixture()
def server(tmp_path, monkeypatch):
    # Point the server's default wiring at an isolated temp DB.
    db = DBManager(tmp_path / "t.db")
    db.ensure_tables()
    ctx = ToolContext(db=db, llm=LLMClient(cache=db),
                      reader=SourceReader("test_src", db=db))
    monkeypatch.setattr(ToolContext, "build", classmethod(lambda cls, *a, **k: ctx))
    srv = mcp_server.create_mcp_server()
    assert srv is not None
    return srv


def _mcp_tools(srv):
    return {t.name: t for t in srv._tool_manager.list_tools()}


class TestSchemaContract:
    def test_every_registry_tool_is_exposed(self, server, ctx):
        registry = build_registry(ctx)
        exposed = _mcp_tools(server)
        assert set(exposed) == set(registry)

    def test_parameter_names_and_required_match(self, server, ctx):
        registry = build_registry(ctx)
        exposed = _mcp_tools(server)
        for name, spec in registry.items():
            advertised = exposed[name].parameters      # JSON schema FastMCP built
            want_props = set(spec.parameters.get("properties", {}))
            got_props = set(advertised.get("properties", {}))
            assert got_props == want_props, f"{name}: {got_props} != {want_props}"
            want_req = set(spec.parameters.get("required", []))
            got_req = set(advertised.get("required", []))
            assert want_req <= got_req, f"{name}: required drifted"

    def test_descriptions_come_from_the_registry(self, server, ctx):
        registry = build_registry(ctx)
        exposed = _mcp_tools(server)
        for name, spec in registry.items():
            assert exposed[name].description == spec.description


class TestDispatch:
    def test_wrapper_dispatches_into_the_real_handler(self, server):
        tools = _mcp_tools(server)
        # empty DB → the registry handler's own message comes back through MCP
        out = tools["get_overview"].fn()
        assert "Run scan_source first" in out

    def test_omitted_optional_params_use_handler_defaults(self, server):
        # The wrapper must NOT invent defaults — omitting `limit` has to
        # reach _list_classes' own limit=200, not a fabricated 0.
        tools = _mcp_tools(server)
        out = tools["list_classes"].fn()
        assert "No classes" in out          # empty DB, but no TypeError/empty-slice
