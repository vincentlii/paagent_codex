"""Probe whether this machine can reach TradingView via tvdatafeed."""
from __future__ import annotations

import logging
import queue
import threading
import time

logger = logging.getLogger(__name__)

_DEFAULT_PROBE_TIMEOUT_S = 20.0
_DEFAULT_PROBE_ATTEMPTS = 3
_DEFAULT_RETRY_DELAY_S = 3.0


def _probe_once(*, timeout_s: float) -> tuple[bool, str | None, bool]:
    """Run one bounded probe in a daemon worker.

    tvdatafeed does not expose a dependable cancellation API. A daemon worker
    lets the caller honor its deadline without making process shutdown wait for
    an uninterruptible connectivity check.
    """
    result: queue.Queue[tuple[bool, str | None, bool]] = queue.Queue(maxsize=1)

    def _probe() -> None:
        try:
            from tvDatafeed import Interval, TvDatafeed  # type: ignore[import]

            tv = TvDatafeed()
            frame = tv.get_hist(
                symbol="XAUUSD",
                exchange="OANDA",
                interval=Interval.in_1_minute,
                n_bars=2,
            )
            if frame is None or getattr(frame, "empty", True):
                raise RuntimeError("TradingView returned empty data")
            result.put_nowait((True, None, False))
        except ImportError as exc:
            result.put_nowait((False, str(exc), False))
        except Exception as exc:  # noqa: BLE001
            result.put_nowait((False, str(exc), True))

    worker = threading.Thread(
        target=_probe,
        name="tradingview-connectivity-probe",
        daemon=True,
    )
    worker.start()
    worker.join(max(0.0, float(timeout_s)))
    if worker.is_alive():
        logger.warning(
            "TradingView connectivity probe timed out after %.1fs", timeout_s
        )
        return False, "connection timeout", True
    try:
        outcome = result.get_nowait()
    except queue.Empty:
        return False, "TradingView probe ended without a result", True
    if outcome[0]:
        return outcome
    if outcome[2]:
        logger.warning("TradingView connectivity probe failed: %s", outcome[1])
    else:
        logger.warning(
            "TradingView connectivity probe: tvDatafeed not installed: %s",
            outcome[1],
        )
    return outcome


def check_tradingview_connectivity(
    *,
    timeout_s: float = _DEFAULT_PROBE_TIMEOUT_S,
    max_attempts: int = _DEFAULT_PROBE_ATTEMPTS,
    retry_delay_s: float = _DEFAULT_RETRY_DELAY_S,
) -> tuple[bool, str | None]:
    """Try a minimal OANDA:XAUUSD fetch with bounded retries."""
    attempts = max(1, int(max_attempts))
    last_detail: str | None = None

    for attempt in range(1, attempts + 1):
        ok, detail, retryable = _probe_once(timeout_s=timeout_s)
        if ok:
            logger.info(
                "TradingView connectivity probe succeeded on attempt %d/%d",
                attempt,
                attempts,
            )
            return True, None

        last_detail = detail
        if not retryable or attempt >= attempts:
            break
        logger.info(
            "TradingView connectivity probe attempt %d/%d failed (%s); "
            "retrying in %.1fs",
            attempt,
            attempts,
            detail or "unknown",
            retry_delay_s,
        )
        time.sleep(max(0.0, retry_delay_s))

    if attempts > 1 and last_detail:
        last_detail = f"{last_detail}（已自动重试 {attempts} 次）"
    return False, last_detail
