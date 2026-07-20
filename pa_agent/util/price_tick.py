"""Deterministic price-format facts derived from a K-line snapshot."""
from __future__ import annotations

from typing import Any


def infer_display_increment_from_frame(kline_frame: Any) -> float | None:
    """Infer the smallest displayed decimal increment, not an exchange tick size."""
    bars = getattr(kline_frame, "bars", None) if kline_frame is not None else None
    if not bars:
        return None

    max_decimals = 0
    for bar in bars:
        for attr in ("open", "high", "low", "close"):
            try:
                value = float(getattr(bar, attr))
            except (TypeError, ValueError):
                continue
            text = f"{value:.12f}".rstrip("0")
            if "." in text:
                max_decimals = max(max_decimals, len(text.split(".")[1]))

    if max_decimals <= 0:
        return 1.0
    return 10 ** (-min(max_decimals, 6))


def format_breakout_price_hint(kline_frame: Any) -> str:
    """Render an advisory price-format fact without claiming venue tick metadata."""
    increment = infer_display_increment_from_frame(kline_frame)
    if increment is None:
        return ""
    increment_s = f"{increment:g}"
    return (
        f"**突破单定价事实**：当前 OHLC 显示的最小小数增量为 {increment_s}；"
        "这只是快照精度，不等同于交易场所的真实 tick size。做多时 "
        f"`entry_price` 必须 **严格大于** `entry_basis_bar` 的 high，"
        "做空时必须 **严格低于** low。`entry_rule` 须写明引用棒、极点与价格。"
        "程序只验证引用和价格关系，不会替你改写订单；真实执行前仍须由交易接口"
        "按品种 tick size 校验。若使用“极点外 1 跳”措辞，必须同时注明"
        "“真实 tick size 未确认，执行前校验”；禁止把显示增量称为已确认的一跳。"
    )
