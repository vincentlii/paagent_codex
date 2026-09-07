"""Headless-safe reconstruction of market frames from persisted records."""

from __future__ import annotations

from pa_agent.data.base import KlineBar, KlineFrame, normalize_kline_bar
from pa_agent.data.snapshot import compute_indicators
from pa_agent.util.timefmt import now_local_ms


def frame_from_record_klines(
    kline_data: list[dict],
    *,
    symbol: str,
    timeframe: str,
    snapshot_ts_local_ms: int | None = None,
    data_metadata: dict | None = None,
) -> KlineFrame:
    """Build an analysis frame from newest-first persisted K-line data."""
    rebased: list[KlineBar] = []
    for index, bar in enumerate(kline_data):
        rebased.append(
            normalize_kline_bar(
                KlineBar(
                    seq=int(bar.get("seq", index + 1)),
                    ts_open=bar["ts_open"],
                    open=bar["open"],
                    high=bar["high"],
                    low=bar["low"],
                    close=bar["close"],
                    volume=bar.get("volume", 0),
                    volume_state=bar.get("volume_state"),
                    amount=bar["amount"] if bar.get("amount") is not None else 0.0,
                    pct_chg=bar.get("pct_chg"),
                    closed=bool(bar.get("closed", True)),
                )
            )
        )
    if not rebased:
        raise ValueError("Record has no kline_data")
    return KlineFrame(
        symbol=symbol,
        timeframe=timeframe,
        bars=tuple(rebased),
        indicators=compute_indicators(rebased),
        snapshot_ts_local_ms=snapshot_ts_local_ms or now_local_ms(),
        data_metadata=dict(data_metadata or {}),
    )
