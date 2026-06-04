#!/usr/bin/env python3
"""
Verify LLMClient's prompt→response cache without hitting a real API.

We monkey-patch the API call methods to a recorder; first call hits
the recorder (cache miss), second call with the same prompt should be
served from the DB cache (recorder not called again).
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tool.db import DBManager
from tool.llm import LLMClient


DB = "outputs/llm_cache_smoke.db"


def main():
    os.makedirs("outputs", exist_ok=True)
    if os.path.exists(DB):
        os.remove(DB)
    db = DBManager(DB)
    db.ensure_tables()

    # Bare-minimum client wired to the cache; replace the real API
    # call with a recorder so we can count invocations.
    llm = LLMClient(api_url="http://fake", api_key="x", model="test-model",
                    api_format="openai", cache=db)
    call_count = {'n': 0}

    def fake_call(prompt, system_prompt="", model_override=None):
        call_count['n'] += 1
        return f"response to: {prompt[:30]}..."

    llm._api_call_openai = fake_call

    # First call → miss
    r1 = llm.generate("analyze class Workshop", tag="t1")
    assert r1 and r1.startswith("response to:")
    assert call_count['n'] == 1, "first call should have hit the API"
    print(f"  ✓ first call: API invoked (count={call_count['n']})")

    # Second call, identical prompt → hit
    r2 = llm.generate("analyze class Workshop", tag="t2")
    assert r2 == r1, "cached response must match original"
    assert call_count['n'] == 1, f"second call should be served from cache; got n={call_count['n']}"
    print(f"  ✓ second call: cache hit (API count still {call_count['n']})")

    # Different prompt → miss
    r3 = llm.generate("analyze class Vehicle", tag="t3")
    assert r3 and r3 != r1
    assert call_count['n'] == 2
    print(f"  ✓ different prompt: API invoked (count={call_count['n']})")

    # Same prompt, different model → miss
    llm.model = "other-model"
    r4 = llm.generate("analyze class Workshop", tag="t4")
    assert call_count['n'] == 3
    print(f"  ✓ same prompt + different model: API invoked (count={call_count['n']})")

    # LLM_NO_CACHE forces fresh call
    llm.model = "test-model"
    llm.cache_disabled = True
    r5 = llm.generate("analyze class Workshop", tag="t5")
    assert call_count['n'] == 4
    print(f"  ✓ LLM_NO_CACHE bypasses cache (count={call_count['n']})")

    # Re-enable, same prompt → still hits (cache persists)
    llm.cache_disabled = False
    r6 = llm.generate("analyze class Workshop", tag="t6")
    assert call_count['n'] == 4, "cache should still have the original response"
    print(f"  ✓ re-enabled cache still serves (count still {call_count['n']})")

    os.remove(DB)
    print("\nLLM cache smoke: PASS")


if __name__ == "__main__":
    main()
