from __future__ import annotations

from pa_agent.ai.kline_references import (
    K_RANGE_RE,
    K_REFERENCE_RE,
    REFERENCE_TOKEN_RE,
)


def test_kline_reference_parser_accepts_chinese_adjacent_text() -> None:
    text = "K1区间与 K20-K1 的比较。"

    assert [match.group(1) for match in K_REFERENCE_RE.finditer(text)] == ["1", "20", "1"]


def test_kline_reference_parser_rejects_embedded_ascii_identifiers() -> None:
    text = "AK1 K1foo K1.5 K1。 H2区间 L3"

    assert [match.group(0) for match in REFERENCE_TOKEN_RE.finditer(text)] == ["K1", "H2", "L3"]


def test_kline_range_parser_accepts_chinese_adjacent_text_and_dash_variants() -> None:
    text = (
        "\u5468\u671fK8-K1\u4e2d\uff0cK6\u2013K4\u4e0eK3\u2014K1\u3001"
        "K9\uff0dK7\u5747\u53ef\u590d\u7b97\u3002"
    )

    assert [match.groups() for match in K_RANGE_RE.finditer(text)] == [
        ("8", "1"),
        ("6", "4"),
        ("3", "1"),
        ("9", "7"),
    ]


def test_plain_k_reference_parser_does_not_extract_bars_from_pa_fact_ids() -> None:
    text = "PA.PAIR.K1.K2 PA.COMPARE.K1.PRIOR5 PA.WINDOW.K8; K4 is explicit."

    assert [match.group(1) for match in K_REFERENCE_RE.finditer(text)] == ["4"]
