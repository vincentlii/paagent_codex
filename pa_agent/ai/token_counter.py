"""Token estimation using tiktoken."""
from __future__ import annotations

import logging
from functools import lru_cache

logger = logging.getLogger(__name__)


@lru_cache(maxsize=8)
def _encoding(model_hint: str):
    try:
        import tiktoken  # type: ignore[import]

        return tiktoken.get_encoding(model_hint)
    except Exception as exc:
        logger.warning("tiktoken unavailable (%s); using char/4 fallback", exc)
        return None


def estimate_tokens(messages: list[dict], model_hint: str = "cl100k_base") -> int:
    """Estimate the number of tokens in *messages* using tiktoken.

    Uses the cl100k_base encoding (GPT-4 / DeepSeek compatible).
    Returns an integer >= 0. Falls back to character-count / 4 if tiktoken
    is unavailable.
    """
    enc = _encoding(model_hint)
    if enc is None:
        total_chars = sum(len(str(m.get("content", ""))) for m in messages)
        return max(1, total_chars // 4)

    total = 0
    for msg in messages:
        # Each message has ~4 overhead tokens (role + separators)
        total += 4
        for key, value in msg.items():
            if isinstance(value, str):
                total += len(enc.encode(value))
    total += 2  # reply priming
    return total
