"""TradingView outbound connectivity probe."""
from __future__ import annotations

from unittest.mock import MagicMock, patch
import threading
import time

from pa_agent.data.tradingview_connectivity import check_tradingview_connectivity


def _mock_tv_ok() -> tuple[MagicMock, MagicMock]:
    mock_df = MagicMock()
    mock_df.empty = False
    mock_interval = MagicMock()
    mock_interval.in_1_minute = object()
    return mock_interval, mock_df


def test_check_tradingview_connectivity_ok() -> None:
    mock_interval, mock_df = _mock_tv_ok()
    with (
        patch("tvDatafeed.Interval", mock_interval),
        patch("tvDatafeed.TvDatafeed") as tv_cls,
    ):
        tv_cls.return_value.get_hist.return_value = mock_df
        ok, detail = check_tradingview_connectivity(
            timeout_s=5.0, max_attempts=3, retry_delay_s=0.0
        )
    assert ok is True
    assert detail is None


def test_check_tradingview_connectivity_empty_data() -> None:
    mock_df = MagicMock()
    mock_df.empty = True
    mock_interval = MagicMock()
    mock_interval.in_1_minute = object()
    with (
        patch("tvDatafeed.Interval", mock_interval),
        patch("tvDatafeed.TvDatafeed") as tv_cls,
    ):
        tv_cls.return_value.get_hist.return_value = mock_df
        ok, detail = check_tradingview_connectivity(
            timeout_s=5.0, max_attempts=1, retry_delay_s=0.0
        )
    assert ok is False
    assert detail


def test_check_tradingview_connectivity_retries_then_succeeds() -> None:
    mock_interval, mock_df = _mock_tv_ok()
    side_effects = [RuntimeError("transient"), mock_df]
    with (
        patch("tvDatafeed.Interval", mock_interval),
        patch("tvDatafeed.TvDatafeed") as tv_cls,
        patch("pa_agent.data.tradingview_connectivity.time.sleep"),
    ):
        tv_cls.return_value.get_hist.side_effect = side_effects
        ok, detail = check_tradingview_connectivity(
            timeout_s=5.0, max_attempts=3, retry_delay_s=0.0
        )
    assert ok is True
    assert detail is None
    assert tv_cls.return_value.get_hist.call_count == 2


def test_check_tradingview_connectivity_exhausts_retries() -> None:
    mock_interval = MagicMock()
    mock_interval.in_1_minute = object()
    with (
        patch("tvDatafeed.Interval", mock_interval),
        patch("tvDatafeed.TvDatafeed") as tv_cls,
        patch("pa_agent.data.tradingview_connectivity.time.sleep"),
    ):
        tv_cls.return_value.get_hist.side_effect = RuntimeError("still down")
        ok, detail = check_tradingview_connectivity(
            timeout_s=5.0, max_attempts=3, retry_delay_s=0.0
        )
    assert ok is False
    assert detail is not None
    assert "已自动重试 3 次" in detail


def test_connectivity_timeout_returns_without_waiting_for_blocked_probe() -> None:
    release = threading.Event()
    mock_interval = MagicMock()
    mock_interval.in_1_minute = object()
    with (
        patch("tvDatafeed.Interval", mock_interval),
        patch("tvDatafeed.TvDatafeed") as tv_cls,
    ):
        tv_cls.return_value.get_hist.side_effect = lambda **_kwargs: release.wait(2.0)
        started = time.monotonic()
        ok, detail = check_tradingview_connectivity(
            timeout_s=0.02,
            max_attempts=1,
            retry_delay_s=0.0,
        )
        elapsed = time.monotonic() - started
        release.set()

    assert ok is False
    assert detail
    assert elapsed < 0.5
