from __future__ import annotations

import pytest

from pa_agent.ai.kline_features import (
    classify_close_position_zone,
    compute_kline_geometry_features,
)
from pa_agent.data.base import IndicatorBundle, KlineBar, KlineFrame


def _frame() -> KlineFrame:
    bars = (
        KlineBar(1, 3000, 10, 12, 9, 11, 100),
        KlineBar(2, 2000, 9, 13, 8, 10, 90),
        KlineBar(3, 1000, 9, 9, 9, 9, 80),
    )
    return KlineFrame(
        symbol="TEST",
        timeframe="1m",
        bars=bars,
        indicators=IndicatorBundle(ema20=(10.0, 9.5, float("nan")), atr14=(2.0, 2.0, 2.0)),
        snapshot_ts_local_ms=4000,
    )


def test_geometry_measurements_are_reproducible() -> None:
    fact = compute_kline_geometry_features(_frame())[0]

    assert fact.candle_direction == "bullish"
    assert fact.full_range == 3.0
    assert fact.body_size == 1.0
    assert fact.body_ratio == pytest.approx(1 / 3, abs=1e-6)
    assert fact.close_position == pytest.approx(2 / 3, abs=1e-6)
    assert fact.close_zone == "middle_half"
    assert fact.range_atr_ratio == 1.5
    assert fact.close_minus_ema_atr == 0.5
    assert fact.relation_to_previous == "contained_by_previous"


def test_zero_range_is_reported_without_inventing_ratios() -> None:
    fact = compute_kline_geometry_features(_frame())[2]

    assert fact.full_range == 0.0
    assert fact.body_ratio is None
    assert fact.close_position is None
    assert fact.close_zone == "unavailable"
    assert fact.relation_to_previous == "none"


def test_limit_does_not_change_measurement_order() -> None:
    facts = compute_kline_geometry_features(_frame(), limit=2)

    assert [fact.seq for fact in facts] == [1, 2]


@pytest.mark.parametrize(
    ("position", "expected"),
    [
        (0.0, "lower_quartile"),
        (0.25, "lower_quartile"),
        (0.5, "middle_half"),
        (0.75, "upper_quartile"),
        (1.0, "upper_quartile"),
        (None, "unavailable"),
    ],
)
def test_close_zone_is_an_explicit_scale_free_geometry_contract(
    position: float | None,
    expected: str,
) -> None:
    assert classify_close_position_zone(position) == expected
