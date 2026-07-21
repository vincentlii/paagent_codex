"""Freeze analysis inputs and derive reproducible production task identities."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

from pa_agent.ai.higher_timeframe import serialize_higher_timeframe_context
from pa_agent.config.vpa import resolve_vpa_mode
from pa_agent.data.base import KlineFrame
from pa_agent.data.snapshot_bundle import compact_snapshot_context, serialize_frame_bars
from pa_agent.monitoring.analysis_runner import FrozenAnalysisPayload
from pa_agent.monitoring.scheduler import AnalysisTask


def _stable_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        default=str,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _validated_digest(value: str, name: str) -> str:
    text = str(value or "").strip().lower()
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise ValueError(f"{name} must be a SHA-256 hex digest")
    return text


def _frame_identity_payload(frame: KlineFrame) -> dict[str, Any]:
    context = compact_snapshot_context(frame)
    context.pop("snapshot_ts_local_ms", None)
    return {
        "symbol": frame.symbol,
        "timeframe": frame.timeframe,
        "bars": serialize_frame_bars(frame),
        "ema20": list(frame.indicators.ema20),
        "atr14": list(frame.indicators.atr14),
        "context": context,
    }


def _snapshot_hash(
    frame: KlineFrame,
    htf_context: dict[str, Any] | None,
) -> str:
    higher_frame = None
    if isinstance(htf_context, dict) and htf_context.get("status") == "available":
        candidate = htf_context.get("frame")
        if isinstance(candidate, KlineFrame):
            higher_frame = candidate
    return _stable_hash(
        {
            "current": _frame_identity_payload(frame),
            "htf_context": serialize_higher_timeframe_context(htf_context),
            "higher": (
                _frame_identity_payload(higher_frame)
                if higher_frame is not None
                else None
            ),
        }
    )


def _analysis_settings_payload(settings: Any) -> dict[str, Any]:
    provider = settings.provider.model_dump()
    provider.pop("api_key", None)
    provider.pop("api_key_encrypted", None)
    prompt = settings.prompt.model_dump()
    prompt.pop("vpa_enabled", None)
    prompt["vpa_mode"] = resolve_vpa_mode(settings.prompt)
    return {
        "provider": provider,
        "general": settings.general.model_dump(),
        "prompt": prompt,
        "validation": settings.validation.model_dump(),
    }


def analysis_cohort_id(settings: Any, analysis_fingerprint: str) -> str:
    """Identify the model, settings, code, and prompt version for one run."""
    runtime = _validated_digest(analysis_fingerprint, "analysis_fingerprint")
    return _stable_hash(
        {
            "contract": "production_analysis_v1",
            "settings": _analysis_settings_payload(settings),
            "analysis_fingerprint": runtime,
        }
    )


def analysis_runtime_fingerprint(
    *,
    project_root: Path,
    prompt_dir: Path,
) -> str:
    """Hash Python analysis code and prompt assets without Git metadata."""
    root = Path(project_root).resolve()
    prompts = Path(prompt_dir).resolve()
    candidates: list[tuple[str, Path]] = []
    source_root = root / "pa_agent"
    if source_root.is_dir():
        candidates.extend(
            (f"pa_agent/{path.relative_to(source_root).as_posix()}", path)
            for path in source_root.rglob("*.py")
            if path.is_file()
        )
    if prompts.is_dir():
        candidates.extend(
            (f"prompt_engineering/{path.relative_to(prompts).as_posix()}", path)
            for path in prompts.rglob("*")
            if path.is_file() and path.suffix.lower() in {".json", ".md", ".txt"}
        )
    if not candidates:
        raise ValueError("analysis runtime files are unavailable for fingerprinting")
    digest = hashlib.sha256()
    for relative, path in sorted(candidates, key=lambda item: item[0]):
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def create_frozen_analysis_task(
    frame: KlineFrame,
    htf_context: dict[str, Any] | None,
    settings: Any,
    *,
    source: str,
    analysis_fingerprint: str,
    venue: str | None = None,
    previous_record: Any | None = None,
    incremental_new_bar_count: int | None = None,
    created_at_ms: int | None = None,
) -> AnalysisTask:
    """Deep-freeze one validated snapshot before it enters the scheduler."""
    if not isinstance(frame, KlineFrame):
        raise TypeError("frame must be a KlineFrame")
    frozen_frame = copy.deepcopy(frame)
    frozen_htf = copy.deepcopy(htf_context)
    payload = FrozenAnalysisPayload(
        frame=frozen_frame,
        htf_context=frozen_htf or {},
        previous_record=copy.deepcopy(previous_record),
        incremental_new_bar_count=incremental_new_bar_count,
    )
    return AnalysisTask.create(
        source=source,
        venue=venue,
        symbol=frozen_frame.symbol,
        timeframe=frozen_frame.timeframe,
        snapshot_hash=_snapshot_hash(frozen_frame, frozen_htf),
        cohort_id=analysis_cohort_id(settings, analysis_fingerprint),
        payload=payload,
        created_at_ms=created_at_ms,
    )
