from __future__ import annotations

import csv
import copy
import pytest
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path

from pa_agent.ai.decision_continuity import (
    assess_plan_price_touches,
    audit_relation_fields,
    bars_elapsed_between,
    build_continuity_context,
    load_last_trade_csv_row,
    render_continuity_prompt_block,
)
from pa_agent.data.base import IndicatorBundle, KlineBar, KlineFrame
from pa_agent.data.contracts import ResolvedInstrumentIdentity


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


def _waiting_history():
    identity = ResolvedInstrumentIdentity(
        provider="binance_usdm", requested_symbol="TEST", resolved_symbol="TEST",
        venue="BINANCE", instrument_type="perpetual",
    ).to_metadata()
    frame = replace(_frame(), data_metadata={**identity, "analysis_as_of_ms": _ms("2026-07-13 10:15:00")})
    previous = {
        "meta": {"run_id": "previous-run", "symbol": "TEST", "timeframe": "5m",
                 "timestamp_local_iso": "2026-07-13 10:10:30"},
        "snapshot_context": copy.deepcopy(identity),
        "analysis_pipeline": {"protocol_version": "analysis_pipeline_v1", "pipeline": "pa_vpa_joint"},
        "run_outcome": {"status": "completed", "official_path": "joint"},
        "analysis_timing": {"result_available_at_ms": _ms("2026-07-13 10:10:30")},
        "exception": None, "role_stop": None,
        "stage1_diagnosis": {"cycle_position": "normal_channel", "direction": "bullish"},
        "stage2_decision": {
            "analysis_basis": {"protocol_version": "pa_vpa_stage2_draft_v1"},
            "terminal": {"outcome": "wait"},
            "decision": {"order_type": "不下单", "reasoning": "K1在边界试探，等待后续闭盘拒绝。",
                         "watch_points": ["后续棒须收回K1高点上方；参照PA.BAR.K1。"],
                         "risk_assessment": "边界仍可能失效。"},
        },
        "kline_data": [dict(asdict(b), seq=i + 1) for i, b in enumerate(frame.bars[1:])],
    }
    return frame, previous


def test_successful_joint_wait_is_delivered_with_its_original_bar_identity():
    frame, previous = _waiting_history()
    original = copy.deepcopy(previous)
    context = build_continuity_context(
        frame=frame, stage1_json={"cycle_position": "normal_channel", "direction": "bullish"},
        previous_record=previous, joint_stage2=True,
    )
    text = render_continuity_prompt_block(context)
    assert context["has_previous_wait"]
    assert previous["stage2_decision"]["decision"]["reasoning"] in text
    assert previous["stage2_decision"]["decision"]["watch_points"][0] in text
    assert context["historical_bar_mapping"]["K1"] == {"ts_open_ms": _ms("2026-07-13 10:05:00"), "current_bar": "K2"}
    assert context["subsequent_closed_bars"] == [{"bar": "K1", "ts_open_ms": _ms("2026-07-13 10:10:00")}]
    assert "previous-run" in text
    assert previous == original


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


def test_analysis_never_reads_unfrozen_csv_as_a_history_fallback(monkeypatch) -> None:
    def forbidden(*args, **kwargs):
        raise AssertionError("analysis must not read mutable CSV")

    monkeypatch.setattr(
        "pa_agent.ai.decision_continuity.load_last_trade_csv_row",
        forbidden,
    )
    previous_record = {
        "kline_data": [{"seq": 1, "ts_open": _ms("2026-07-13 10:05:00")}],
        "stage1_diagnosis": {"cycle_position": "spike", "direction": "bearish"},
        "stage2_decision": None,
    }

    for version in (None, "decision_history_v2"):
        context = build_continuity_context(frame=_frame(), stage1_json={},
                                           previous_record=previous_record, history_context_version=version)
        assert not context["has_previous_plan"]


def test_last_trade_lookup_uses_the_explicit_runtime_directory(tmp_path: Path) -> None:
    state_trade_dir = tmp_path / "state" / "trade_records"
    state_trade_dir.mkdir(parents=True)
    path = state_trade_dir / "TEST_5m.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=["record_time", "symbol", "timeframe", "order_type"],
        )
        writer.writeheader()
        writer.writerow(
            {
                "record_time": "2026-07-13 10:00:00",
                "symbol": "TEST",
                "timeframe": "5m",
                "order_type": "限价单",
            }
        )

    row = load_last_trade_csv_row(
        "TEST",
        "5m",
        records_dir=state_trade_dir,
    )

    assert row is not None
    assert row["record_time"] == "2026-07-13 10:00:00"


def test_prompt_reports_limits_without_auto_cancelling_or_claiming_fill() -> None:
    frame, previous = _waiting_history()
    previous["stage2_decision"]["decision"] = _plan()
    previous["stage2_decision"]["terminal"]["outcome"] = "trade"
    context = build_continuity_context(
        frame=frame,
        stage1_json={"cycle_position": "normal_channel", "direction": "bullish"},
        previous_record=previous, joint_stage2=True,
    )

    text = render_continuity_prompt_block(context)

    assert "不等于成交确认" in text
    assert "同棒先后未知" in text
    assert "自动取消" not in text
    assert "冷却" not in text


@pytest.mark.parametrize("mutation", ["future_result", "missing_known_at", "cross_source", "reject",
                                      "failed", "stop", "same_cutoff", "outside_window", "wrong_timeframe"])
def test_untrusted_or_nonwaiting_history_does_not_enter_the_current_decision(mutation):
    frame, previous = _waiting_history()
    if mutation == "future_result":
        previous["analysis_timing"]["result_available_at_ms"] = _ms("2026-07-13 10:16:00")
    elif mutation == "missing_known_at":
        previous["analysis_timing"] = {}
    elif mutation == "cross_source":
        previous["snapshot_context"]["source_identity"]["venue"] = "OTHER"
    elif mutation == "reject":
        previous["stage2_decision"]["terminal"]["outcome"] = "reject"
    elif mutation == "failed":
        previous["run_outcome"]["status"] = "invalid_model_output"
    elif mutation == "stop":
        previous["role_stop"] = {"status": "incomplete"}
    elif mutation == "same_cutoff":
        previous["kline_data"][0]["ts_open"] = frame.bars[0].ts_open
    elif mutation == "wrong_timeframe":
        previous["meta"]["timeframe"] = "1h"
    else:
        previous["kline_data"][0]["ts_open"] = _ms("2026-07-13 09:55:00")
    context = build_continuity_context(frame=frame, stage1_json={}, previous_record=previous, joint_stage2=True)
    assert not context["has_previous_wait"]
    assert "等待后续闭盘拒绝" not in render_continuity_prompt_block(context)


def test_pa_only_cannot_receive_old_joint_wait_but_can_retest_a_pa_wait():
    frame, previous = _waiting_history()
    context = build_continuity_context(frame=frame, stage1_json={}, previous_record=previous)
    assert not context["has_previous_wait"]
    previous["stage2_decision"].pop("analysis_basis")
    previous["analysis_pipeline"]["pipeline"] = "pa_only"
    previous["run_outcome"]["official_path"] = "pa_only"
    context = build_continuity_context(frame=frame, stage1_json={}, previous_record=previous)
    assert context["has_previous_wait"]


def test_bar_mapping_never_relabels_an_old_reference_when_price_was_revised_or_outside_window():
    frame, previous = _waiting_history()
    previous["stage2_decision"]["decision"]["watch_points"] += ["K99不可见，K2位于旧区间。"]
    previous["kline_data"][1]["close"] += 0.1
    context = build_continuity_context(frame=frame, stage1_json={}, previous_record=previous, joint_stage2=True)
    assert context["historical_bar_mapping"]["K1"]["current_bar"] == "K2"
    assert context["historical_bar_mapping"]["K2"]["current_bar"] is None
    assert context["historical_bar_mapping"]["K99"] == {"ts_open_ms": None, "current_bar": None}
    assert "K99不可见" in render_continuity_prompt_block(context)


@pytest.mark.parametrize("mutation", ["wrong_path", "wrong_pipeline", "null_terminal", "malformed_bar",
                                      "unclosed_bar", "duplicate_seq", "duplicate_timestamp"])
def test_mismatched_or_malformed_history_is_omitted_without_breaking_current_analysis(mutation):
    frame, previous = _waiting_history()
    if mutation == "wrong_path":
        previous["run_outcome"]["official_path"] = "pa_only"
    elif mutation == "wrong_pipeline":
        previous["analysis_pipeline"]["pipeline"] = "unknown"
    elif mutation == "null_terminal":
        previous["stage2_decision"]["terminal"] = None
    elif mutation == "malformed_bar":
        previous["kline_data"].append({"seq": "invalid", "ts_open": "unknown"})
    elif mutation == "unclosed_bar":
        previous["kline_data"][0]["closed"] = False
    elif mutation == "duplicate_seq":
        previous["kline_data"].append({**previous["kline_data"][1], "seq": 1})
    else:
        previous["kline_data"].append({**previous["kline_data"][1], "seq": 3})
    context = build_continuity_context(frame=frame, stage1_json={}, previous_record=previous, joint_stage2=True)
    assert not context["has_previous_wait"]


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
