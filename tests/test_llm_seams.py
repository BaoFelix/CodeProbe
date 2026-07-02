"""
Tests for every seam where an LLM touches the system — all offline.

  · generate_with_tools: both wire formats parse into the SAME neutral
    shapes (ToolCall / LLMResponse) — the Host never sees a provider.
  · RuleCompiler: prose+canned JSON → contract; garbage → empty contract.
  · Verifier: drops LLM-refuted findings; FAIL-OPEN on junk (never hide a
    possible problem because parsing failed).
"""
from tool.llm import LLMClient, LLMResponse
from tool.architect import RuleCompiler, Verifier, Finding
from conftest import CannedLLM


def make_openai(mock_reply):
    llm = LLMClient(api_url="http://x", api_key="k", model="m",
                    api_format="openai")
    llm._post = lambda url, body, headers: (mock_reply, None)
    return llm


def make_anthropic(mock_reply):
    llm = LLMClient(api_url="http://x", api_key="k", model="m",
                    api_format="anthropic")
    llm._post = lambda url, body, headers: (mock_reply, None)
    return llm


class TestToolUseParsing:
    def test_openai_tool_call(self):
        llm = make_openai({"choices": [{"message": {
            "role": "assistant", "content": None,
            "tool_calls": [{"id": "c1", "type": "function", "function": {
                "name": "scan_source",
                "arguments": '{"directory": "src"}'}}]}}]})
        r = llm.generate_with_tools([], [])
        assert r.tool_calls[0].name == "scan_source"
        assert r.tool_calls[0].args == {"directory": "src"}
        # the result message must carry the provider's call id back
        msg = llm.tool_result_message(r.tool_calls[0], "done")
        assert msg == {"role": "tool", "tool_call_id": "c1",
                       "content": "done"}

    def test_anthropic_tool_call(self):
        llm = make_anthropic({"content": [
            {"type": "text", "text": "checking"},
            {"type": "tool_use", "id": "t1", "name": "get_overview",
             "input": {}}]})
        r = llm.generate_with_tools([], [])
        assert r.text == "checking"
        assert r.tool_calls[0].name == "get_overview"
        msg = llm.tool_result_message(r.tool_calls[0], "ok")
        assert msg["role"] == "user"
        assert msg["content"][0]["tool_use_id"] == "t1"

    def test_final_text_turn(self):
        llm = make_openai({"choices": [{"message": {
            "role": "assistant", "content": "the answer"}}]})
        r = llm.generate_with_tools([], [])
        assert r.text == "the answer" and not r.tool_calls

    def test_http_error_degrades_not_raises(self):
        llm = LLMClient(api_url="http://x", api_key="k", model="m",
                        api_format="openai")
        llm._post = lambda u, b, h: (None, "HTTP 429: rate limited")
        r = llm.generate_with_tools([], [])
        assert "LLM error" in r.text and not r.tool_calls

    def test_parse_args_tolerates_junk(self):
        assert LLMClient._parse_args("not json") == {}
        assert LLMClient._parse_args("") == {}
        assert LLMClient._parse_args({"a": 1}) == {"a": 1}


class TestRuleCompiler:
    GOOD = ('{"groups":[{"name":"UI","match":["*View"]}],'
            '"rules":[{"kind":"forbid_dependency","from":"UI","to":"DB",'
            '"text":"UI must not touch DB"}]}')

    def test_prose_becomes_contract(self):
        contract = RuleCompiler(CannedLLM(self.GOOD)).compile(
            "UI must not touch DB",
            [{"qualified_name": "V", "file_path": "v.hxx"}])
        assert contract.groups[0].name == "UI"
        rule = contract.rules[0]
        assert rule.kind == "forbid_dependency"
        assert rule.params == {"from": "UI", "to": "DB"}
        assert rule.source == "user"
        assert rule.text == "UI must not touch DB"   # traceability

    def test_garbage_yields_empty_contract(self):
        contract = RuleCompiler(CannedLLM("sorry, no JSON here")).compile(
            "rules", [])
        assert contract.rules == [] and contract.groups == []

    def test_unknown_rule_kinds_are_dropped(self):
        js = '{"groups":[],"rules":[{"kind":"must_be_stateless","from":"A"}]}'
        contract = RuleCompiler(CannedLLM(js)).compile("x", [])
        assert contract.rules == []      # never pretend to check the uncheckable


class TestVerifier:
    def _finding(self):
        return Finding("r", "god_module", "God module: X", "d", ["X"], ["e"])

    def test_refuted_finding_is_dropped(self):
        kept = Verifier(CannedLLM('{"is_real": false, "reason": "DTO"}')) \
            .verify([self._finding()])
        assert kept == []

    def test_confirmed_finding_is_kept(self):
        kept = Verifier(CannedLLM('{"is_real": true}')).verify(
            [self._finding()])
        assert len(kept) == 1

    def test_fail_open_on_junk(self):
        # If the LLM answers garbage we KEEP the finding — hiding a possible
        # problem because parsing failed would be the worst failure mode.
        kept = Verifier(CannedLLM("¯\\_(ツ)_/¯")).verify([self._finding()])
        assert len(kept) == 1
