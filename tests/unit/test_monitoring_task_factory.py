from __future__ import annotations

from types import SimpleNamespace

from pa_agent.config.settings import Settings
from pa_agent.data.base import IndicatorBundle, KlineBar, KlineFrame
from pa_agent.experiments.pa_vpa_aabb import (
    analysis_runtime_fingerprint as experiment_runtime_fingerprint,
)
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


def test_production_and_experiment_share_runtime_fingerprint_algorithm(tmp_path) -> None:
    source = tmp_path / "pa_agent"
    prompts = tmp_path / "prompt_engineering"
    source.mkdir()
    prompts.mkdir()
    (source / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    (prompts / "core.txt").write_text("prompt", encoding="utf-8")

    production = analysis_runtime_fingerprint(
        project_root=tmp_path,
        prompt_dir=prompts,
    )
    experiment = experiment_runtime_fingerprint(tmp_path)

    assert experiment == production


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


def test_task_identity_includes_prompt_relevant_incremental_context() -> None:
    settings = Settings()

    def previous(confidence: int):
        return SimpleNamespace(
            meta=SimpleNamespace(
                model_dump=lambda: {
                    "timestamp_local_iso": "2026-01-01T00:00:00",
                    "symbol": "XAUUSD",
                    "timeframe": "1h",
                }
            ),
            kline_data=[{"seq": 1, "ts_open": 8_000}],
            stage1_messages=[{"role": "user", "content": "prior facts"}],
            stage1_response={"content": "prior diagnosis"},
            stage1_diagnosis={"cycle_position": "trading_range"},
            stage2_decision={"decision": {"trade_confidence": confidence}},
            strategy_files_used=["trading_range.txt"],
        )

    first = create_frozen_analysis_task(
        _frame(),
        None,
        settings,
        source="tradingview",
        analysis_fingerprint="e" * 64,
        previous_record=previous(55),
        incremental_new_bar_count=1,
    )
    changed = create_frozen_analysis_task(
        _frame(),
        None,
        settings,
        source="tradingview",
        analysis_fingerprint="e" * 64,
        previous_record=previous(65),
        incremental_new_bar_count=1,
    )

    assert changed.cohort_id == first.cohort_id
    assert changed.snapshot_hash != first.snapshot_hash
    assert changed.task_id != first.task_id


def test_gui_only_settings_do_not_change_analysis_cohort() -> None:
    baseline = Settings()
    gui_changed = baseline.model_copy(deep=True)
    gui_changed.general.stream_pane_font_pt += 1
    analysis_changed = baseline.model_copy(deep=True)
    analysis_changed.general.decision_stance = "aggressive"

    baseline_task = create_frozen_analysis_task(
        _frame(),
        None,
        baseline,
        source="tradingview",
        analysis_fingerprint="f" * 64,
    )
    gui_task = create_frozen_analysis_task(
        _frame(),
        None,
        gui_changed,
        source="tradingview",
        analysis_fingerprint="f" * 64,
    )
    analysis_task = create_frozen_analysis_task(
        _frame(),
        None,
        analysis_changed,
        source="tradingview",
        analysis_fingerprint="f" * 64,
    )

    assert gui_task.cohort_id == baseline_task.cohort_id
    assert gui_task.task_id == baseline_task.task_id
    assert analysis_task.cohort_id != baseline_task.cohort_id
    assert analysis_task.task_id != baseline_task.task_id
