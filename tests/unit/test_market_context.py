from datetime import UTC, datetime

import pytest

from pa_agent.data.market_context import market_context_for_snapshot
from pa_agent.data.contracts import ResolvedInstrumentIdentity


def _identity(symbol="XAUUSDT", kind="tradifi_perpetual"):
    return ResolvedInstrumentIdentity(provider="binance_usdm", requested_symbol=symbol,
        resolved_symbol=symbol, venue="BINANCE", instrument_type=kind)


@pytest.mark.parametrize("utc,session,mode", [
    ("2026-10-09T20:59:00", "regular_weekly_hours", "third_party_standard_unless_holiday"),
    ("2026-10-09T21:00:00", "weekend", "orderbook_ewma"),
    ("2026-10-11T21:59:00", "weekend", "orderbook_ewma"),
    ("2026-10-11T22:00:00", "regular_weekly_hours", "third_party_standard_unless_holiday"),
    ("2026-11-06T22:00:00", "weekend", "orderbook_ewma"),
    ("2026-09-01T22:00:00", "unknown_historical_schedule", "unknown"),
])
def test_gold_reference_clock_is_frozen_dst_aware_and_does_not_guess_holidays(utc,session,mode):
    at = int(datetime.fromisoformat(utc).replace(tzinfo=UTC).timestamp() * 1000)
    result = market_context_for_snapshot(_identity(), at)
    assert result["reference_session"] == session
    assert result["reference_index_mode"] == mode
    assert result["holiday_calendar_status"] == "unavailable"
    assert result["instrument_label"] == "Binance 黄金永续合约"
    assert result["schedule_source"] == "binance_notice_20260915"
    assert "volume" not in str(result)


def test_crypto_is_not_given_gold_reference_hours():
    result = market_context_for_snapshot(_identity("BTCUSDT", "linear_perpetual"), 1_800_000_000_000)
    assert result["reference_session"] == "continuous_crypto"
    assert "reference_index_mode" not in result
