"""Regression tests for prediction rendering after its panel migration."""
from __future__ import annotations

import sys

import pytest
from PyQt6.QtWidgets import QApplication

from pa_agent.gui.future_trend_panel import FutureTrendPanel


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


@pytest.fixture
def panel(qapp: QApplication) -> FutureTrendPanel:
    widget = FutureTrendPanel()
    widget.show()
    qapp.processEvents()
    return widget


def test_missing_predictions_keep_both_modules_hidden(panel: FutureTrendPanel) -> None:
    panel.set_prediction({})
    assert not panel._bar_group.isVisible()
    assert not panel._cycle_group.isVisible()


def test_unpredictable_next_bar_renders_neutral_state(panel: FutureTrendPanel) -> None:
    panel.set_prediction(
        {
            "next_bar_prediction": {
                "direction": None,
                "probabilities": None,
                "reasoning": "证据不足，无法给出方向分布。",
                "unpredictable": True,
                "features_used": [],
            }
        }
    )
    assert panel._bar_group.isVisible()
    assert panel._bar_direction_label.text() == "不可预测"
    assert "#8b949e" in panel._bar_direction_label.styleSheet()


def test_next_bar_probability_display_and_dominant_color(panel: FutureTrendPanel) -> None:
    panel.set_prediction(
        {
            "next_bar_prediction": {
                "direction": "bullish",
                "probabilities": {"bullish": 70, "bearish": 20, "neutral": 10},
                "reasoning": "结构更支持下一根收涨。",
                "unpredictable": False,
                "features_used": ["stage1_diagnosis"],
            }
        }
    )
    line = panel._bar_direction_label.text()
    assert "阳线的概率为70%" in line
    assert "阴线的概率为20%" in line
    assert "中性的概率为10%" in line
    assert "#3fb950" in panel._bar_direction_label.styleSheet()
    assert panel._bar_reasoning_edit.toPlainText() == "结构更支持下一根收涨。"


def test_next_cycle_renders_ranked_candidates(panel: FutureTrendPanel) -> None:
    probabilities = {
        "spike": 5,
        "broad_channel": 10,
        "tight_channel": 35,
        "normal_channel": 20,
        "trading_range": 15,
        "trending_tr": 8,
        "breakout": 5,
        "reversal": 2,
    }
    panel.set_prediction(
        {
            "next_cycle_prediction": {
                "direction": "bearish",
                "probabilities": probabilities,
                "reasoning": "当前最可能延续为空头紧密通道。",
                "unpredictable": False,
                "features_used": ["stage1_diagnosis"],
            }
        }
    )
    assert panel._cycle_group.isVisible()
    assert "35%" in panel._chip_labels[0].text()
    assert "20%" in panel._chip_labels[1].text()
    assert "15%" in panel._chip_labels[2].text()
    assert panel._cycle_direction_label.text() == "方向：看跌"


def test_clear_hides_and_resets_prediction_modules(panel: FutureTrendPanel) -> None:
    test_next_bar_probability_display_and_dominant_color(panel)
    panel.clear()
    assert not panel._bar_group.isVisible()
    assert not panel._cycle_group.isVisible()
    assert panel._bar_direction_label.text() == "—"
    assert panel._bar_reasoning_edit.toPlainText() == ""
