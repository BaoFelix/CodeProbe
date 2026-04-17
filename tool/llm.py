"""
llm.py — LLM call abstraction layer
═══════════════════════════════════════
AI concept: LLM API Integration
Key insight:
  - API uses OpenAI-compatible format → works with GitHub Models / any compatible endpoint
  - Also supports Anthropic Messages API (Claude)
  - Uses stdlib urllib, no need for requests package
  - Pipeline doesn't care "how to call AI", just calls llm.generate(prompt)
═══════════════════════════════════════
"""
import json
import time
import urllib.request
import urllib.error

from .config import (
    LLM_API_FORMAT, LLM_API_URL, LLM_API_KEY, LLM_MODEL,
    LLM_FALLBACK_MODELS, OUTPUTS_DIR
)


class LLMClient:
    """
    LLM client — calls LLM API for analysis.

    Usage:
        llm = LLMClient()                      # use config.py defaults
        llm = LLMClient(api_url=..., ...)       # manual override
        response = llm.generate(prompt, tag='Vehicle')
    """

    def __init__(self, api_url=None, api_key=None, model=None,
                 api_format=None):
        self.api_url = api_url or LLM_API_URL
        self.api_key = api_key or LLM_API_KEY
        self.model = model or LLM_MODEL
        self.api_format = api_format or LLM_API_FORMAT

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
        if self.api_format == "anthropic":
            return self._api_call_anthropic(prompt, system_prompt)
        return self._api_call_openai(prompt, system_prompt)

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

    def _api_call_anthropic(self, prompt, system_prompt=""):
        """
        Anthropic mode: POST to Anthropic Messages API.

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

        body_dict = {
            "model": self.model,
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
            print(f"    → Anthropic API call: {self.model} @ {self.api_url}")
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                # Anthropic response format: {"content": [{"type":"text","text":"..."}], "usage": {...}}
                content = data["content"][0]["text"]
                usage = data.get("usage", {})
                if usage:
                    print(f"    ✓ Got response (input={usage.get('input_tokens','?')}"
                          f" output={usage.get('output_tokens','?')} tokens)")
                return content
        except urllib.error.HTTPError as e:
            error_body = e.read().decode('utf-8', errors='replace')
            print(f"  ✗ Anthropic API error {e.code}: {error_body[:300]}")
            return None
        except urllib.error.URLError as e:
            print(f"  ✗ Network error: {e.reason}")
            return None
        except (json.JSONDecodeError, KeyError, IndexError) as e:
            print(f"  ✗ Failed to parse Anthropic response: {e}")
            return None

