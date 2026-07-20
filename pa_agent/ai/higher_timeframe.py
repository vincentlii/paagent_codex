"""Rendering and persistence helpers for optional PA higher-timeframe context."""
from __future__ import annotations

import math
from typing import Any

from pa_agent.ai.kline_references import extract_htf_reference_seqs
from pa_agent.data.base import KlineFrame
from pa_agent.data.datetime_ts import format_epoch_for_display

_MAX_DETAIL_BARS = 24


def _number(value: object, digits: int = 4) -> str:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return "N/A"
    return f"{numeric:.{digits}f}" if math.isfinite(numeric) else "N/A"


def _background_price_facts(frame: KlineFrame) -> str:
    bars = list(frame.bars)
    if not bars:
        return "无可用背景 K 线。"
    latest = bars[0]
    oldest = bars[-1]
    window_high = max(float(bar.high) for bar in bars)
    window_low = min(float(bar.low) for bar in bars)
    change_pct = None
    if float(oldest.close) != 0:
        change_pct = (float(latest.close) - float(oldest.close)) / float(oldest.close) * 100
    return (
        f"程序价格事实：窗口高={_number(window_high)}；窗口低={_number(window_low)}；"
        f"HTF.H1 收盘={_number(latest.close)}；HTF.H{len(bars)} 收盘={_number(oldest.close)}；"
        f"窗口收盘变化={_number(change_pct, 2)}%。"
    )


def render_higher_timeframe_context(context: dict[str, Any] | None) -> str:
    """Render HTF background with no volume field and no automatic gate effect."""
    if not isinstance(context, dict) or context.get("status") == "disabled":
        return ""
    status = str(context.get("status") or "unavailable")
    timeframe = str(context.get("timeframe") or "unknown")
    if status != "available":
        reason = str(context.get("reason") or "unavailable")
        return (
            "## 更高周期背景（程序状态）\n\n"
            f"状态：{status}；目标周期：{timeframe}；原因：{reason}。\n"
            "未提供高周期 K 线，禁止编造 HTF 趋势、位置或成交量结论。\n"
        )
    frame = context.get("frame")
    if not isinstance(frame, KlineFrame) or not frame.bars:
        return (
            "## 更高周期背景（程序状态）\n\n"
            f"状态：unavailable；目标周期：{timeframe}；原因：invalid_higher_timeframe_frame。\n"
        )

    bars = list(frame.bars)
    detail_bars = bars[:_MAX_DETAIL_BARS]
    lines = [
        "## 更高周期背景（程序提供，非独立硬 Gate）",
        "",
        f"当前周期：{context.get('current_timeframe', 'unknown')}；背景周期：{frame.timeframe}；"
        f"背景 K 线数：{len(frame.bars)}；逐棒明细仅展示最近 {len(detail_bars)} 根，"
        "全窗口仍用于程序价格事实。",
        "当前周期 PA 仍定义 cycle_position、direction、Setup 与 gate_result。"
        "高周期只用于长程结构、位置、关键摆动与成熟度背景；冲突只能提示谨慎/等待确认，"
        "不能单独翻转方向、拒绝或创造交易。",
        "VPA 仅使用当前入场周期的证据目录；不得把本块与当前周期 RVOL 作原始成交量比较。",
        "阶段一 `htf_context` 必须引用至少一个本块 `HTF.H#` 序号，"
        "并明确说明它如何支持、冲突或仅提供背景；"
        "不得用当前周期 K# 冒充高周期证据。",
        _background_price_facts(frame),
        "",
        "HTF.H# | 时间 | 开盘价 | 最高价 | 最低价 | 收盘价 | 阳阴 | EMA20 | ATR14",
        "------+--------------------+----------+----------+----------+----------+------+-----------+----------",
    ]
    for index, bar in enumerate(detail_bars):
        ema = frame.indicators.ema20[index]
        atr = frame.indicators.atr14[index]
        direction = "阳" if bar.close > bar.open else "阴" if bar.close < bar.open else "平"
        lines.append(
            f"HTF.H{bar.seq:<4} | {format_epoch_for_display(bar.ts_open, short=True):<19} | "
            f"{_number(bar.open):<8} | {_number(bar.high):<8} | {_number(bar.low):<8} | "
            f"{_number(bar.close):<8} | {direction:<4} | {_number(ema):<9} | {_number(atr):<8}"
        )
    return "\n".join(lines) + "\n"


def validate_higher_timeframe_output(
    stage1: dict[str, Any],
    context: dict[str, Any] | None,
) -> list[str]:
    """Require an auditable HTF citation when a real HTF frame was injected."""
    if not isinstance(context, dict) or context.get("status") != "available":
        return []
    frame = context.get("frame")
    if not isinstance(frame, KlineFrame) or not frame.bars:
        return []
    text = str(stage1.get("htf_context") or "")
    references = extract_htf_reference_seqs(text)
    if not references:
        return [
            "htf_context must cite at least one injected HTF.H# bar when HTF is available"
        ]
    available = {int(bar.seq) for bar in frame.bars}
    invalid = sorted(references - available)
    if invalid:
        refs = ", ".join(f"HTF.H{value}" for value in invalid)
        return [f"htf_context cites bars outside the injected HTF frame: {refs}"]
    return []


def serialize_higher_timeframe_context(context: dict[str, Any] | None) -> dict[str, Any] | None:
    """Persist compact HTF provenance while full prompt text remains in ``htf_text``."""
    if not isinstance(context, dict):
        return None
    frame = context.get("frame")
    payload: dict[str, Any] = {
        "status": context.get("status"),
        "timeframe": context.get("timeframe"),
        "current_timeframe": context.get("current_timeframe"),
        "reason": context.get("reason"),
        "metadata": dict(context.get("metadata") or {}),
    }
    if isinstance(frame, KlineFrame):
        payload.update(
            symbol=frame.symbol,
            bar_count=len(frame.bars),
            latest_closed_ts_open=frame.bars[0].ts_open if frame.bars else None,
        )
    return payload
