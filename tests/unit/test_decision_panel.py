"""DecisionPanel rendering tests for the current PA/VPA ownership boundary."""
from __future__ import annotations

import sys
import time

import pytest
from PyQt6.QtWidgets import QApplication

from pa_agent.gui.decision_panel import DecisionPanel, _format_vpa_summary


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


@pytest.fixture
def panel(qapp: QApplication) -> DecisionPanel:
    widget = DecisionPanel()
    widget.show()
    qapp.processEvents()
    return widget


def _no_order_decision() -> dict:
    return {
        "order_type": "不下单",
        "order_direction": None,
        "entry_price": None,
        "take_profit_price": None,
        "stop_loss_price": None,
        "reasoning": "当前缺少可执行触发。",
        "diagnosis_confidence": 64,
        "diagnosis_confidence_reasoning": "市场背景可辨，但处于过渡段。",
        "trade_confidence": 38,
        "trade_confidence_reasoning": "等待下一根已收盘 K 线确认。",
    }


def _stage1_diagnosis() -> dict:
    return {
        "cycle_position": "trading_range",
        "alternative_cycle_position": "trending_tr",
        "direction": "bearish",
        "market_phase": "transitioning",
        "transition_risk": "medium",
        "htf_context": "高周期偏空，但当前位于区间下沿。",
        "key_signals": [],
    }


def test_no_order_renders_stage1_and_both_confidences(panel: DecisionPanel) -> None:
    panel.set_decision(_no_order_decision(), stage1_diagnosis=_stage1_diagnosis())

    assert "震荡偏空" in panel._trend_label.text()
    assert "下跌交易区间" in panel._cycle_label.text()
    assert "过渡" in panel._phase_label.text()
    assert "高周期偏空" in panel._htf_label.text()
    assert panel._diag_conf_bar.value() == 64
    assert "64 / 100" in panel._diag_conf_label.text()
    assert panel._conclusion_label.text() == "不下单"
    assert "38 / 100" in panel._trade_conf_inline_label.text()
    assert "观望" in panel._trade_conf_inline_label.text()
    assert not panel._trade_prices_row.isVisible()


def test_executable_order_renders_prices_metrics_and_reasoning(panel: DecisionPanel) -> None:
    decision = {
        **_no_order_decision(),
        "order_type": "限价单",
        "order_direction": "做多",
        "entry_price": 100.0,
        "take_profit_price": 104.0,
        "take_profit_price_2": 106.0,
        "stop_loss_price": 98.0,
        "estimated_win_rate": 55,
        "estimated_win_rate_reasoning": "基于当前 Setup。",
        "trade_confidence": 72,
        "trade_confidence_reasoning": "回测位与信号一致。",
        "reasoning": "等待价格回测后执行。",
    }

    panel.set_decision(decision, stage1_diagnosis=_stage1_diagnosis())

    assert panel._conclusion_label.text() == "限价单"
    assert panel._direction_inline_label.text() == "方向 做多"
    assert panel._entry_label.text() == "入场  100"
    assert panel._tp_label.text() == "TP1  104"
    assert panel._tp2_label.text() == "TP2  106"
    assert panel._sl_label.text() == "止损  98"
    assert "2.00" in panel._rr_inline_label.text()
    assert "55" in panel._win_rate_inline_label.text()
    assert "72 / 100" in panel._trade_conf_inline_label.text()
    assert "等待价格回测后执行" in panel._reasoning_edit.toPlainText()


def test_vpa_summary_uses_stage1_context_and_stage2_effect(panel: DecisionPanel) -> None:
    stage1 = _stage1_diagnosis()
    stage1["vpa_context"] = {
        "data_status": "available",
        "summary": "回测缩量，暂未出现明显抛压扩张。",
        "observations": [
            {
                "bar_range": "K3-K1",
                "facts": ["K1 volume_rvol=0.72"],
                "interpretation": "回测努力减弱。",
            }
        ],
        "background_relation": "supports",
        "entry_timing_relation": "challenges",
        "next_confirmation": {
            "state": "pending_closed_bar",
            "trigger": "观察下一根已收盘 K 线是否收回区间。",
            "requires_closed_bar": True,
        },
    }
    decision = {
        **_no_order_decision(),
        "vpa_impact": {
            "background_relation": "supports",
            "entry_timing_relation": "challenges",
            "evidence_used": ["K3-K1 回测缩量"],
            "decision_effect": "prefer_retest",
            "reason": "VPA 支持等待更优回测位置。",
        },
    }

    panel.set_decision(decision, stage1_diagnosis=stage1)

    text = panel._vpa_summary_label.text()
    assert panel._vpa_group.isVisible()
    assert "回测缩量" in text
    assert "背景/时机 background=supports / timing=challenges" in text
    assert "prefer_retest" in text
    assert "VPA 支持等待更优回测位置" in text


def test_vpa_summary_exposes_failed_arm_reason() -> None:
    text = _format_vpa_summary(
        None,
        None,
        {
            "status": "failed",
            "pair_id": "vpa-test",
            "vpa_arm": {
                "exception": {
                    "stage": "stage1",
                    "category": "c",
                    "message": "numeric market facts are not allowed",
                }
            },
            "comparison": {
                "observed_effect": {"classification": "vpa_arm_failed"}
            },
        },
    )

    assert "VPA arm 失败：stage1/c" in text
    assert "numeric market facts" in text
    assert "vpa_arm_failed" in text


def test_clear_resets_all_optional_sections(panel: DecisionPanel) -> None:
    test_vpa_summary_uses_stage1_context_and_stage2_effect(panel)

    panel.clear()

    assert panel._conclusion_label.text() == "等待分析"
    assert panel._trend_label.text() == "趋势：—"
    assert not panel._htf_label.isVisible()
    assert not panel._vpa_group.isVisible()
    assert panel._vpa_summary_label.text() == ""
    assert panel._reasoning_edit.toPlainText() == ""


def test_set_decision_render_performance(panel: DecisionPanel) -> None:
    decision = _no_order_decision()
    started = time.perf_counter()
    for _ in range(10):
        panel.set_decision(decision, stage1_diagnosis=_stage1_diagnosis())
    elapsed = (time.perf_counter() - started) / 10
    assert elapsed < 0.05, f"set_decision took {elapsed * 1000:.1f}ms per call"
