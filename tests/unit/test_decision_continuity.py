from __future__ import annotations

from datetime import datetime

from pa_agent.ai.decision_continuity import (
    assess_plan_price_touches,
    audit_relation_fields,
    bars_elapsed_between,
    build_continuity_context,
    render_continuity_prompt_block,
)
from pa_agent.data.base import IndicatorBundle, KlineBar, KlineFrame


def _ms(iso: str) -> int:
    dt = datetime.strptime(iso, "%Y-%m-%d %H:%M:%S")
    return int(dt.timestamp() * 1000)


def _frame() -> KlineFrame:
    bars = (
        KlineBar(1, _ms("2026-07-13 10:10:00"), 104, 106, 103, 105, 10),
        KlineBar(2, _ms("2026-07-13 10:05:00"), 101, 104, 100, 103, 10),
        KlineBar(3, _ms("2026-07-13 10:00:00"), 99, 102, 98, 101, 10),
    )
    return KlineFrame(
        symbol="TEST",
        timeframe="5m",
        bars=bars,
        indicators=IndicatorBundle(ema20=(102.0,) * 3, atr14=(2.0,) * 3),
        snapshot_ts_local_ms=_ms("2026-07-13 10:15:00"),
    )


def _plan(entry: float = 103.5) -> dict:
    return {
        "order_direction": "做多",
        "order_type": "限价单",
        "entry_price": entry,
        "stop_loss_price": 99.0,
        "take_profit_price": 106.0,
        "take_profit_price_2": 110.0,
    }


def test_elapsed_bars_uses_floor_and_can_be_zero() -> None:
    current = _ms("2026-07-13 10:04:59")
    assert bars_elapsed_between("2026-07-13 10:00:00", current, "5m") == 0
    assert bars_elapsed_between("2026-07-13 10:00:00", _ms("2026-07-13 10:10:00"), "5m") == 2


def test_touch_scan_uses_only_bars_after_previous_snapshot_k1() -> None:
    frame = _frame()

    touches = assess_plan_price_touches(
        _plan(entry=100.5),
        frame,
        after_ts_open_ms=_ms("2026-07-13 10:05:00"),
    )

    assert touches["bars_scanned"] == 1
    assert touches["entry_touched"] is False


def test_csv_fallback_does_not_reuse_unrelated_record_timestamp(monkeypatch) -> None:
    monkeypatch.setattr(
        "pa_agent.ai.decision_continuity.load_last_trade_csv_row",
        lambda _symbol, _timeframe: {
            "record_time": "2026-07-13 10:00:00",
            **{key: str(value) for key, value in _plan().items()},
        },
    )
    previous_record = {
        "kline_data": [{"seq": 1, "ts_open": _ms("2026-07-13 10:05:00")}],
        "stage1_diagnosis": {"cycle_position": "spike", "direction": "bearish"},
        "stage2_decision": None,
    }

    context = build_continuity_context(
        frame=_frame(),
        stage1_json={"cycle_position": "normal_channel", "direction": "bullish"},
        previous_record=previous_record,
    )

    assert context["previous_source"] == "trade_csv"
    assert context["previous_latest_bar_ts"] is None
    assert context["touch_window_basis"] == "record_time_estimate"
    assert context["previous_cycle_position"] == ""


def test_prompt_reports_limits_without_auto_cancelling_or_claiming_fill() -> None:
    context = build_continuity_context(
        frame=_frame(),
        stage1_json={"cycle_position": "normal_channel", "direction": "bullish"},
        previous_record={
            "meta": {"timestamp_local_iso": "2026-07-13 10:05:00"},
            "kline_data": [{"seq": 1, "ts_open": _ms("2026-07-13 10:05:00")}],
            "stage1_diagnosis": {"cycle_position": "normal_channel", "direction": "bullish"},
            "stage2_decision": {"decision": _plan()},
        },
    )

    text = render_continuity_prompt_block(context)

    assert "不等于成交确认" in text
    assert "无法从 OHLC 确定盘中先后" in text
    assert "自动取消" not in text
    assert "冷却" not in text


def test_audit_relation_is_descriptive_only() -> None:
    fields = audit_relation_fields(
        {
            "record_time": "2026-07-13 10:00:00",
            "order_direction": "做空",
            "order_type": "限价单",
            "entry_price": "103.5",
            "stop_loss_price": "106",
            "take_profit_price": "100",
            "take_profit_price_2": "98",
        },
        _plan(),
        frame=_frame(),
    )

    assert fields["prev_plan_relation"] == "反向"
    assert fields["prev_plan_touch_basis"] == "record_time_estimate"
