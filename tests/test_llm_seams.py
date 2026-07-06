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
    llm._post = lambda url, body, headers: (mock_reply, None, None)
    return llm


def make_anthropic(mock_reply):
    llm = LLMClient(api_url="http://x", api_key="k", model="m",
                    api_format="anthropic")
    llm._post = lambda url, body, headers: (mock_reply, None, None)
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
        llm._post = lambda u, b, h: (None, "network: down", None)
        r = llm.generate_with_tools([], [])
        assert "LLM error" in r.text and not r.tool_calls

    def test_429_walks_the_fallback_chain(self):
        # The agent loop must survive a rate-limited primary by degrading to
        # the next model — same resilience as the batch generate() path.
        llm = LLMClient(api_url="http://x", api_key="k", model="primary",
                        api_format="openai")
        llm._next_fallback = lambda m: "backup" if m == "primary" else None
        seen = []

        def fake_post(url, body, headers):
            seen.append(body["model"])
            if body["model"] == "primary":
                return None, "HTTP 429: rate limited", 429
            return {"choices": [{"message": {"role": "assistant",
                                             "content": "recovered"}}]}, None, None

        llm._post = fake_post
        r = llm.generate_with_tools([], [])
        assert seen == ["primary", "backup"]       # it actually fell back
        assert r.text == "recovered"

    def test_429_with_no_fallback_returns_error(self):
        llm = LLMClient(api_url="http://x", api_key="k", model="only",
                        api_format="openai")
        llm._next_fallback = lambda m: None
        llm._post = lambda u, b, h: (None, "HTTP 429", 429)
        r = llm.generate_with_tools([], [])
        assert "LLM error" in r.text

    def test_parse_args_tolerates_junk(self):
        assert LLMClient._parse_args("not json") == {}
        assert LLMClient._parse_args("") == {}
        assert LLMClient._parse_args({"a": 1}) == {"a": 1}


class TestBatchGenerateResilience:
    def test_fallback_answer_never_poisons_primary_cache(self, tmp_path):
        # A fallback model's answer must NOT be cached under the primary
        # model's key — that would serve the weaker answer forever.
        from tool.db import DBManager
        db = DBManager(tmp_path / "c.db")
        db.ensure_tables()
        llm = LLMClient(api_url="http://x", api_key="k", model="primary",
                        api_format="openai", cache=db)

        def fake_api(prompt, system_prompt="", model_override=None):
            llm._answered_by = "backup"     # simulate 429 → fallback answered
            return "weak answer"
        llm._api_call_openai = fake_api
        assert llm.generate("q") == "weak answer"
        key = llm._prompt_hash("q", "")
        assert db.llm_cache_get(key, "primary") is None    # not poisoned

    def test_primary_answer_is_cached(self, tmp_path):
        from tool.db import DBManager
        db = DBManager(tmp_path / "c.db")
        db.ensure_tables()
        llm = LLMClient(api_url="http://x", api_key="k", model="primary",
                        api_format="openai", cache=db)

        def fake_api(prompt, system_prompt="", model_override=None):
            llm._answered_by = "primary"
            return "good answer"
        llm._api_call_openai = fake_api
        llm.generate("q")
        assert db.llm_cache_get(llm._prompt_hash("q", ""), "primary") \
            == "good answer"

    def test_malformed_200_degrades_not_crashes(self):
        # empty choices list from a filtering proxy must not kill the loop
        llm = make_openai({"choices": []})
        r = llm.generate_with_tools([], [])
        assert "LLM error" in r.text and not r.tool_calls

    def test_error_message_carries_text_in_assistant_content(self):
        # empty assistant content poisons the next Anthropic turn — the
        # error text must ride in the message itself
        llm = LLMClient(api_url="http://x", api_key="k", model="m",
                        api_format="anthropic")
        llm._post = lambda u, b, h: (None, "network: down", None)
        r = llm.generate_with_tools([], [])
        assert r.assistant_message["content"]          # non-empty
        assert "LLM error" in r.assistant_message["content"]


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
