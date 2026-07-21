from __future__ import annotations

from pa_agent.config.settings import Settings
from pa_agent.data.base import IndicatorBundle, KlineBar, KlineFrame
from pa_agent.monitoring.task_factory import (
    analysis_runtime_fingerprint,
    create_frozen_analysis_task,
)


def _frame(*, close: float = 100.0, snapshot_ms: int = 10_000) -> KlineFrame:
    bars = tuple(
        KlineBar(
            seq=index + 1,
            ts_open=9_000 - index * 1_000,
            open=99.0,
            high=102.0,
            low=98.0,
            close=close if index == 0 else 100.0,
            volume=1000.0 + index,
            closed=True,
        )
        for index in range(20)
    )
    return KlineFrame(
        symbol="XAUUSD",
        timeframe="1h",
        bars=bars,
        indicators=IndicatorBundle(
            ema20=tuple(100.0 for _ in bars),
            atr14=tuple(2.0 for _ in bars),
        ),
        snapshot_ts_local_ms=snapshot_ms,
        data_metadata={
            "source": "tradingview",
            "venue": "OANDA",
            "source_identity": {
                "provider": "tradingview",
                "requested_symbol": "XAUUSD",
                "resolved_symbol": "XAUUSD",
                "venue": "OANDA",
            },
        },
    )


def test_task_identity_ignores_capture_clock_but_changes_with_market_facts() -> None:
    settings = Settings()
    first = create_frozen_analysis_task(
        _frame(snapshot_ms=10_000),
        None,
        settings,
        source="tradingview",
        venue="OANDA",
        analysis_fingerprint="a" * 64,
    )
    recaptured = create_frozen_analysis_task(
        _frame(snapshot_ms=99_000),
        None,
        settings,
        source="tradingview",
        venue="OANDA",
        analysis_fingerprint="a" * 64,
    )
    changed = create_frozen_analysis_task(
        _frame(close=101.0, snapshot_ms=99_000),
        None,
        settings,
        source="tradingview",
        venue="OANDA",
        analysis_fingerprint="a" * 64,
    )

    assert recaptured.snapshot_hash == first.snapshot_hash
    assert recaptured.task_id == first.task_id
    assert changed.snapshot_hash != first.snapshot_hash
    assert changed.task_id != first.task_id


def test_effective_vpa_mode_changes_analysis_cohort_not_market_snapshot() -> None:
    off_settings = Settings()
    soft_settings = Settings()
    soft_settings.prompt.vpa_mode = "soft"

    off = create_frozen_analysis_task(
        _frame(),
        None,
        off_settings,
        source="tradingview",
        venue="OANDA",
        analysis_fingerprint="b" * 64,
    )
    soft = create_frozen_analysis_task(
        _frame(),
        None,
        soft_settings,
        source="tradingview",
        venue="OANDA",
        analysis_fingerprint="b" * 64,
    )

    assert soft.snapshot_hash == off.snapshot_hash
    assert soft.cohort_id != off.cohort_id
    assert soft.task_id != off.task_id


def test_runtime_fingerprint_covers_source_and_prompt_assets(tmp_path) -> None:
    source = tmp_path / "pa_agent"
    prompts = tmp_path / "prompts"
    source.mkdir()
    prompts.mkdir()
    (source / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    prompt = prompts / "core.txt"
    prompt.write_text("first", encoding="utf-8")

    first = analysis_runtime_fingerprint(
        project_root=tmp_path,
        prompt_dir=prompts,
    )
    prompt.write_text("second", encoding="utf-8")
    second = analysis_runtime_fingerprint(
        project_root=tmp_path,
        prompt_dir=prompts,
    )

    assert first != second


def test_frozen_payload_does_not_share_mutable_metadata_with_fetch_frame() -> None:
    frame = _frame()
    task = create_frozen_analysis_task(
        frame,
        None,
        Settings(),
        source="tradingview",
        venue="OANDA",
        analysis_fingerprint="c" * 64,
    )

    frame.data_metadata["venue"] = "MUTATED"

    assert task.payload.frame.data_metadata["venue"] == "OANDA"
