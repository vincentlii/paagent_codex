"""Price-only venue/product facts tied to a confirmed snapshot boundary."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pa_agent.data.contracts import ResolvedInstrumentIdentity


def market_context_for_snapshot(
    identity: ResolvedInstrumentIdentity, as_of_ms: int,
) -> dict[str, object]:
    if identity.provider != "binance_usdm":
        return {}
    if identity.resolved_symbol in {"BTCUSDT", "ETHUSDT"}:
        return {"instrument_label": f"Binance {identity.resolved_symbol} 永续合约",
                "reference_session": "continuous_crypto"}
    if identity.resolved_symbol != "XAUUSDT":
        return {}
    result: dict[str, object] = {
        "instrument_label": "Binance 黄金永续合约",
        "underlying_reference": "gold_price_reference_not_global_spot_market",
        "trading_session": "continuous_contract",
        "as_of_ms": int(as_of_ms),
        "schedule_source": "binance_notice_20260915",
        "schedule_url": "https://www.binance.com/en/support/announcement/detail/f69c05ebdba7422e9c614685abe84dc5",
        "holiday_calendar_status": "unavailable",
        "reference_session": "unknown_historical_schedule",
        "reference_index_mode": "unknown",
    }
    at = datetime.fromtimestamp(as_of_ms / 1000, tz=UTC)
    if at < datetime(2026, 9, 15, 21, tzinfo=UTC):
        return result
    try:
        local = at.astimezone(ZoneInfo("America/New_York"))
    except ZoneInfoNotFoundError:
        result["reference_session"] = "unknown_timezone"
        return result
    day, hour = local.weekday(), local.hour
    weekend = (day == 4 and hour >= 17) or day == 5 or (day == 6 and hour < 18)
    result["reference_session"] = "weekend" if weekend else "regular_weekly_hours"
    result["reference_index_mode"] = (
        "orderbook_ewma" if weekend else "third_party_standard_unless_holiday"
    )
    return result
