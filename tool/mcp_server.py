"""
mcp_server.py — expose the tool registry to EXTERNAL AI hosts over MCP.

Design decision (see tools.py's docstring): there is exactly ONE
implementation of every capability, in the tool registry. Two consumers
share it:

    our own Host (host.py)                       → in-process function calls
    external hosts (Copilot, Claude Desktop, …)  → this MCP server

The wrappers are GENERATED from each ToolSpec's JSON schema rather than
hand-written. That closes both drift channels structurally: logic can't
drift (a wrapper is one dispatch call), and the advertised parameter
schema can't drift either (it *is* the registry schema, reflected into a
Python signature FastMCP reads). Add a tool to the registry and every
consumer — Host, MCP, tests — sees it identically, for free.

  Install: pip install mcp
  Start:   python run.py mcp-server
"""
import inspect
from typing import Optional

try:
    from mcp.server.fastmcp import FastMCP
    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False

from .tools import ToolContext, build_registry, run_tool


_TYPE_MAP = {"string": str, "integer": int, "boolean": bool, "number": float}


def _make_wrapper(spec, dispatch):
    """Build a function whose signature mirrors spec.parameters exactly.

    FastMCP derives the tool schema it advertises from the function's
    signature/annotations — so by manufacturing both from the ToolSpec,
    the wire schema is the registry schema by construction.

    Optional params default to None and are dropped before dispatch, so
    the HANDLER's own Python defaults stay the single source of default
    values (e.g. list_classes' limit=200) instead of being duplicated here.
    """
    props = spec.parameters.get("properties", {})
    required = set(spec.parameters.get("required", []))
    params, annotations = [], {}
    for name, meta in props.items():
        base = _TYPE_MAP.get(meta.get("type"), str)
        if name in required:
            default, ann = inspect.Parameter.empty, base
        else:
            default, ann = None, Optional[base]
        params.append(inspect.Parameter(
            name, inspect.Parameter.POSITIONAL_OR_KEYWORD,
            default=default, annotation=ann))
        annotations[name] = ann

    def wrapper(**kwargs):
        given = {k: v for k, v in kwargs.items() if v is not None}
        return dispatch(spec.name, **given)

    wrapper.__name__ = spec.name
    wrapper.__doc__ = spec.description
    wrapper.__signature__ = inspect.Signature(params, return_annotation=str)
    annotations["return"] = str
    wrapper.__annotations__ = annotations
    return wrapper


def create_mcp_server():
    """Build the FastMCP server over the shared tool registry.
    Returns the FastMCP instance, or None if mcp isn't installed."""
    if not MCP_AVAILABLE:
        print("  ✗ MCP package not installed. Run: pip install mcp")
        return None

    # NB: the modern mcp package renamed description= to instructions=;
    # passing positionally-safe kwargs keeps us working across versions.
    mcp = FastMCP(
        "codeprobe",
        instructions="CodeProbe — AI-powered C++ architecture diagnostics")

    ctx = ToolContext.build()
    registry = build_registry(ctx)

    def dispatch(name, **args):
        return run_tool(registry, name, args, ctx)

    for spec in registry.values():
        mcp.tool()(_make_wrapper(spec, dispatch))

    return mcp


def run_mcp_server():
    """Entry point for `python run.py mcp-server`."""
    server = create_mcp_server()
    if server is None:
        return
    print("  ▶ CodeProbe MCP server starting (stdio)…")
    server.run()
