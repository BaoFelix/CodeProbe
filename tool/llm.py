"""
llm.py — one front door for every LLM call.

The rest of the codebase only ever calls llm.generate(prompt). What
happens behind that call:

  1. CACHE CHECK   sha256(model + system + prompt) is looked up in the
                   llm_cache table. Hit → return instantly, zero cost.
                   (Set LLM_NO_CACHE=1 to bypass while tuning prompts.)
  2. API CALL      two wire formats supported:
                     "openai"    — OpenAI-compatible JSON (works with
                                   GitHub Models and most providers)
                     "anthropic" — Claude Messages API
                   stdlib urllib only; no requests dependency.
  3. 429 FALLBACK  rate-limited? walk LLM_FALLBACK_MODELS down the
                   chain (gpt-4o → gpt-4o-mini → ...) automatically.
  4. WRITE-THROUGH successful responses are cached for next time.

Why the cache hash includes the model name: switching models must
invalidate old answers — a cached gpt-4o-mini response is not an
acceptable answer to a gpt-4o question.
"""
import hashlib
import json
import os
import urllib.request
import urllib.error
from dataclasses import dataclass

from .config import (
    LLM_API_FORMAT, LLM_API_URL, LLM_API_KEY, LLM_MODEL,
    LLM_FALLBACK_MODELS
)


# ── Tool-use contract (provider-agnostic) ───────────────────────────
# The Host's agent loop speaks ONLY these shapes. Every OpenAI/Anthropic
# wire-format difference is hidden inside LLMClient below, so the Host
# never learns which provider is configured.

@dataclass
class ToolCall:
    id: str            # provider's call id — needed to reply with its result
    name: str          # tool name the model wants to run
    args: dict         # parsed arguments


@dataclass
class LLMResponse:
    text: str                    # assistant's text (may be empty)
    tool_calls: list             # list[ToolCall]; empty ⇒ this is the answer
    assistant_message: dict      # raw assistant msg to append back to history


class LLMClient:
    """
    LLM client — calls LLM API for analysis.

    Usage:
        llm = LLMClient()                      # use config.py defaults
        llm = LLMClient(api_url=..., ...)       # manual override
        response = llm.generate(prompt, tag='Vehicle')
    """

    def __init__(self, api_url=None, api_key=None, model=None,
                 api_format=None, cache=None):
        self.api_url = api_url or LLM_API_URL
        self.api_key = api_key or LLM_API_KEY
        self.model = model or LLM_MODEL
        self.api_format = api_format or LLM_API_FORMAT
        # Optional response cache: any object exposing
        #   llm_cache_get(prompt_hash, model) → response str or None
        #   llm_cache_put(prompt_hash, model, response)
        # Setting env LLM_NO_CACHE=1 forces a fresh call (for debugging
        # prompt changes that don't change prompt text).
        self.cache = cache
        self.cache_disabled = bool(os.environ.get('LLM_NO_CACHE'))

    def _prompt_hash(self, prompt, system_prompt):
        """Stable fingerprint of an LLM call. Includes both the user
        and system prompt, plus the model, so changing the model or
        either prompt yields a different cache key.
        """
        h = hashlib.sha256()
        h.update(self.model.encode())
        h.update(b'\x00')
        h.update((system_prompt or '').encode())
        h.update(b'\x00')
        h.update(prompt.encode())
        return h.hexdigest()

    def generate(self, prompt, system_prompt="", tag=""):
        """
        Send prompt to LLM, return response text.

        Args:
            prompt: user prompt content
            system_prompt: system prompt (optional)
            tag: label for logging

        Returns:
            str | None — AI response text, or None on failure
        """
        # ── cache lookup ─────────────────────────────────
        if self.cache is not None and not self.cache_disabled:
            try:
                key = self._prompt_hash(prompt, system_prompt)
                hit = self.cache.llm_cache_get(key, self.model)
                if hit is not None:
                    if tag:
                        print(f"    [{tag}] cache hit")
                    return hit
            except Exception:
                pass    # cache failure must not block the call

        # ── miss → real API call ─────────────────────────
        # _answered_by records which model actually produced the text —
        # a 429 may have walked the fallback chain mid-call.
        self._answered_by = self.model
        if self.api_format == "anthropic":
            response = self._api_call_anthropic(prompt, system_prompt)
        else:
            response = self._api_call_openai(prompt, system_prompt)

        # ── write through ────────────────────────────────
        # Only cache answers from the PRIMARY model: a fallback model's
        # answer stored under the primary's key would be served forever
        # as if the primary had said it — permanent cache poisoning for
        # one rate-limited moment.
        if (response is not None
                and self._answered_by == self.model
                and self.cache is not None
                and not self.cache_disabled):
            try:
                key = self._prompt_hash(prompt, system_prompt)
                self.cache.llm_cache_put(key, self.model, response)
            except Exception:
                pass
        return response

    # ─── Tool use (the agent loop's engine) ─────────────────────
    #
    # generate() is single-shot text. The agent loop needs the model to
    # be able to REQUEST a tool call and then see its result. That is a
    # stateful, multi-turn exchange, so — unlike generate() — it is NOT
    # cached (each step's messages differ).
    #
    # Contract:
    #   generate_with_tools(messages, tools, system_prompt) -> LLMResponse
    #   tool_result_message(tool_call, result_text)         -> dict
    # The Host appends resp.assistant_message, runs each tool, and appends
    # tool_result_message(...) for each — all provider-native dicts it
    # never has to inspect.

    def generate_with_tools(self, messages, tools, system_prompt=""):
        """One turn of the loop. `messages` is the running provider-native
        history; `tools` is the schema list from tools.tool_schemas().
        Returns an LLMResponse (text + tool_calls + the assistant message
        to append). On a 429 it walks the model fallback chain — the same
        resilience the batch generate() path has — so a rate-limited primary
        degrades to a weaker model instead of ending the agent's turn."""
        call = (self._tools_call_anthropic if self.api_format == "anthropic"
                else self._tools_call_openai)
        return call(messages, tools, system_prompt, model=self.model)

    def tool_result_message(self, tool_call, result_text):
        """Build the provider-native message that reports one tool's result
        back to the model."""
        if self.api_format == "anthropic":
            return {"role": "user",
                    "content": [{"type": "tool_result",
                                 "tool_use_id": tool_call.id,
                                 "content": result_text}]}
        return {"role": "tool",
                "tool_call_id": tool_call.id,
                "content": result_text}

    @staticmethod
    def _parse_args(raw):
        """Tool arguments arrive as a JSON string (OpenAI) or a dict
        (Anthropic). Normalize to a dict; never raise on junk."""
        if isinstance(raw, dict):
            return raw
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}

    def _post(self, url, body, headers):
        """POST json → parsed json. Returns (data, error_str, status_code).
        status_code is the HTTP status on an HTTPError (so callers can spot
        a 429), else None."""
        req = urllib.request.Request(
            url, data=json.dumps(body).encode("utf-8"),
            headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return json.loads(resp.read().decode("utf-8")), None, None
        except urllib.error.HTTPError as e:
            return (None,
                    f"HTTP {e.code}: {e.read().decode('utf-8','replace')[:200]}",
                    e.code)
        except urllib.error.URLError as e:
            return None, f"network: {e.reason}", None
        except (json.JSONDecodeError, KeyError, IndexError) as e:
            return None, f"parse: {e}", None

    @staticmethod
    def _tool_error(err):
        # The error text goes into the assistant message too: an EMPTY
        # assistant content appended to history breaks the next Anthropic
        # turn (empty content blocks are rejected), so one transient error
        # would poison every later turn of the conversation.
        text = f"[LLM error: {err}]"
        return LLMResponse(text=text, tool_calls=[],
                           assistant_message={"role": "assistant",
                                              "content": text})

    def _tools_call_openai(self, messages, tools, system_prompt, model):
        msgs = list(messages)
        if system_prompt:
            msgs = [{"role": "system", "content": system_prompt}] + msgs
        body = {
            "model": model,
            "messages": msgs,
            "tools": [{"type": "function", "function": t} for t in tools],
            "tool_choice": "auto",
            "temperature": 0.3,
            "max_tokens": 4096,
        }
        url = self.api_url.rstrip("/") + "/chat/completions"
        headers = {"Content-Type": "application/json",
                   "Authorization": f"Bearer {self.api_key}"}
        data, err, status = self._post(url, body, headers)
        if status == 429:                             # rate-limited → fall back
            nxt = self._next_fallback(model)
            if nxt:
                return self._tools_call_openai(messages, tools, system_prompt, nxt)
        if err:
            return self._tool_error(err)
        try:
            # A 200 with an empty choices list (content filtering, proxy
            # quirks) must degrade like any other error, not kill the REPL.
            msg = data["choices"][0]["message"]
            calls = [ToolCall(id=c["id"], name=c["function"]["name"],
                              args=self._parse_args(c["function"].get("arguments")))
                     for c in (msg.get("tool_calls") or [])]
        except (KeyError, IndexError, TypeError) as e:
            return self._tool_error(f"malformed response: {e}")
        return LLMResponse(text=msg.get("content") or "",
                           tool_calls=calls, assistant_message=msg)

    def _tools_call_anthropic(self, messages, tools, system_prompt, model):
        body = {
            "model": model,
            "max_tokens": 4096,
            "messages": list(messages),
            "tools": [{"name": t["name"], "description": t["description"],
                       "input_schema": t["parameters"]} for t in tools],
        }
        if system_prompt:
            body["system"] = system_prompt
        url = self.api_url.rstrip("/") + "/v1/messages"
        headers = {"Content-Type": "application/json",
                   "x-api-key": self.api_key,
                   "anthropic-version": "2023-06-01"}
        data, err, status = self._post(url, body, headers)
        if status == 429:                             # rate-limited → fall back
            nxt = self._next_fallback(model)
            if nxt:
                return self._tools_call_anthropic(messages, tools,
                                                  system_prompt, nxt)
        if err:
            return self._tool_error(err)
        try:
            blocks = data.get("content", [])
            text = "".join(b.get("text", "") for b in blocks
                           if b.get("type") == "text")
            calls = [ToolCall(id=b["id"], name=b["name"],
                              args=self._parse_args(b.get("input")))
                     for b in blocks if b.get("type") == "tool_use"]
        except (KeyError, TypeError) as e:
            return self._tool_error(f"malformed response: {e}")
        return LLMResponse(text=text, tool_calls=calls,
                           assistant_message={"role": "assistant",
                                              "content": blocks})

    # ─── OpenAI-Compatible Backend ──────────────────────────────

    def _api_call_openai(self, prompt, system_prompt="", model_override=None):
        """
        API mode: POST to OpenAI-compatible endpoint.
        Auto-fallback to next model in chain on 429 rate limit.
        """
        if not self.api_url or not self.api_key:
            print("  ✗ API mode requires LLM_API_URL and LLM_API_KEY")
            print("    Set in config.py, or use environment variables:")
            print("    $env:LLM_API_URL = 'https://models.inference.ai.azure.com'")
            print("    $env:LLM_API_KEY = 'your-token'")
            return None

        model = model_override or self.model

        # Build request
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        body = json.dumps({
            "model": model,
            "messages": messages,
            "temperature": 0.3,      # Low temperature = more stable output
            "max_tokens": 4096,
        }).encode('utf-8')

        url = self.api_url.rstrip('/') + '/chat/completions'
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )

        try:
            print(f"    → API call: {model} @ {self.api_url}")
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                content = data["choices"][0]["message"]["content"]
                self._answered_by = model      # may be a fallback model
                tokens = data.get("usage", {})
                if tokens:
                    print(f"    ✓ Got response (prompt={tokens.get('prompt_tokens','?')}"
                          f" completion={tokens.get('completion_tokens','?')} tokens)")
                return content
        except urllib.error.HTTPError as e:
            error_body = e.read().decode('utf-8', errors='replace')
            # 429 Rate Limit — auto-fallback to next model in chain
            if e.code == 429:
                next_model = self._next_fallback(model)
                if next_model:
                    print(f"  ⚠ {model} rate-limited (429), falling back to {next_model}")
                    return self._api_call_openai(prompt, system_prompt,
                                                 model_override=next_model)
                print(f"  ✗ All models rate-limited (429), cannot continue")
            print(f"  ✗ API error {e.code}: {error_body[:200]}")
            return None
        except urllib.error.URLError as e:
            print(f"  ✗ Network error: {e.reason}")
            return None
        except (json.JSONDecodeError, KeyError, IndexError) as e:
            print(f"  ✗ Failed to parse API response: {e}")
            return None

    # ─── Fallback Chain Helper ─────────────────────────────────

    def _next_fallback(self, current_model):
        """Return the next model in the fallback chain, or None."""
        chain = LLM_FALLBACK_MODELS
        try:
            idx = chain.index(current_model)
            if idx + 1 < len(chain):
                return chain[idx + 1]
        except ValueError:
            # Current model not in chain, try chain's first (if different)
            if chain and chain[0] != current_model:
                return chain[0]
        return None

    # ─── Anthropic Messages API Backend ──────────────────────

    def _api_call_anthropic(self, prompt, system_prompt="", model_override=None):
        """
        Anthropic mode: POST to Anthropic Messages API.
        Auto-fallback to next model in chain on 429 rate limit — same
        resilience the OpenAI-format path has always had.

        POST /v1/messages
        {
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 4096,
            "system": "...",
            "messages": [{"role": "user", "content": "..."}]
        }
        """
        if not self.api_url or not self.api_key:
            print("  ✗ Anthropic API mode requires LLM_API_URL and LLM_API_KEY")
            print("    $env:LLM_API_FORMAT = 'anthropic'")
            print("    $env:LLM_API_URL = 'https://api.anthropic.com'")
            print("    $env:LLM_API_KEY = 'sk-ant-...'")
            return None

        model = model_override or self.model
        body_dict = {
            "model": model,
            "max_tokens": 4096,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system_prompt:
            body_dict["system"] = system_prompt

        body = json.dumps(body_dict).encode('utf-8')

        url = self.api_url.rstrip('/') + '/v1/messages'
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )

        try:
            print(f"    → Anthropic API call: {model} @ {self.api_url}")
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                # Anthropic response format: {"content": [{"type":"text","text":"..."}], "usage": {...}}
                content = data["content"][0]["text"]
                self._answered_by = model      # may be a fallback model
                usage = data.get("usage", {})
                if usage:
                    print(f"    ✓ Got response (input={usage.get('input_tokens','?')}"
                          f" output={usage.get('output_tokens','?')} tokens)")
                return content
        except urllib.error.HTTPError as e:
            error_body = e.read().decode('utf-8', errors='replace')
            if e.code == 429:
                next_model = self._next_fallback(model)
                if next_model:
                    print(f"  ⚠ {model} rate-limited (429), falling back to {next_model}")
                    return self._api_call_anthropic(prompt, system_prompt,
                                                    model_override=next_model)
                print(f"  ✗ All models rate-limited (429), cannot continue")
            print(f"  ✗ Anthropic API error {e.code}: {error_body[:300]}")
            return None
        except urllib.error.URLError as e:
            print(f"  ✗ Network error: {e.reason}")
            return None
        except (json.JSONDecodeError, KeyError, IndexError) as e:
            print(f"  ✗ Failed to parse Anthropic response: {e}")
            return None

