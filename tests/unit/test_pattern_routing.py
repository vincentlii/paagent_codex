from __future__ import annotations

from pa_agent.ai.pattern_routing import normalize_detected_patterns
from pa_agent.ai.router import route_strategy_files


def test_pattern_normalization_only_uses_explicit_model_tags() -> None:
    assert normalize_detected_patterns([" WEDGE ", "wedge", "H2"]) == ["wedge", "h2"]
    assert normalize_detected_patterns("wedge") == []


def test_router_does_not_infer_patterns_from_free_text() -> None:
    stage1 = {
        "cycle_position": "unknown",
        "direction": "neutral",
        "detected_patterns": [],
        "entry_setup": "clear wedge H2 failed breakout",
        "key_signals": ["wedge", "failed breakout"],
    }

    assert route_strategy_files(stage1) == []


def test_router_loads_brooks_knowledge_from_validated_context() -> None:
    files = route_strategy_files(
        {
            "cycle_position": "normal_channel",
            "direction": "bullish",
            "detected_patterns": ["wedge", "h2"],
        }
    )

    assert "上涨通道分析识别.txt" in files
    assert "上涨通道交易策略.txt" in files
    assert "文件14-楔形形态分析交易.txt" in files
    assert "文件19-H1H2-L1L2计数.txt" in files
    assert len(files) == len(set(files))


def test_alternative_cycle_only_adds_knowledge_and_never_returns_decision() -> None:
    files = route_strategy_files(
        {
            "cycle_position": "normal_channel",
            "alternative_cycle_position": "trading_range",
            "direction": "bearish",
            "detected_patterns": [],
        }
    )

    assert "下跌通道交易策略.txt" in files
    assert "震荡区间交易策略.txt" in files
    assert all(isinstance(name, str) and name.endswith(".txt") for name in files)
