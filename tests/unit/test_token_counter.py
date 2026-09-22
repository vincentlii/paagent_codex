from __future__ import annotations

import tiktoken

from pa_agent.ai import token_counter


def test_failed_encoding_lookup_is_cached_for_process_lifetime(monkeypatch) -> None:
    calls = 0

    def unavailable(_model_hint: str):
        nonlocal calls
        calls += 1
        raise OSError("offline")

    monkeypatch.setattr(tiktoken, "get_encoding", unavailable)
    token_counter._encoding.cache_clear()
    try:
        assert token_counter.estimate_tokens([{"content": "abcd"}]) == 1
        assert token_counter.estimate_tokens([{"content": "abcdefgh"}]) == 2
        assert calls == 1
    finally:
        token_counter._encoding.cache_clear()
