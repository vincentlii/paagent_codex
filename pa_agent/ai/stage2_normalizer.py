"""Shape-only normalization for Stage-2 model output.

The model owns every trading interpretation and order field. Invalid aliases,
missing fields, and contradictions are rejected by validation instead of being
silently rewritten here.
"""
from __future__ import annotations

import copy
from typing import Any

from pa_agent.ai.trace_normalize import normalize_stage2_traces
from pa_agent.ai.vpa_sanitizer import strip_vpa_fields


def normalize_stage2(
    obj: dict[str, Any],
    *,
    kline_frame: Any = None,
    skip_next_bar: bool = False,
) -> dict[str, Any]:
    """Return a copy with notation-only changes and feature-flag filtering."""
    out = strip_vpa_fields(copy.deepcopy(obj))
    normalize_stage2_traces(out, default_max_seq=_max_bar_seq(kline_frame))
    if skip_next_bar:
        out.pop("next_bar_prediction", None)
    return out


def _max_bar_seq(frame: Any) -> int | None:
    bars = getattr(frame, "bars", None) if frame is not None else None
    if not bars:
        return None
    seqs = [int(bar.seq) for bar in bars if getattr(bar, "seq", None)]
    return max(seqs) if seqs else None
