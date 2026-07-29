"""Rendering and persistence helpers for optional PA higher-timeframe context."""
from __future__ import annotations

import math
import re
from typing import Any

from pa_agent.ai.kline_references import extract_htf_reference_seqs
from pa_agent.data.base import KlineFrame
from pa_agent.data.datetime_ts import format_epoch_for_display

_MAX_DETAIL_BARS = 24
_HTF_FIELD_RE = re.compile(
    r"HTF\.H\s*(?P<seq>\d+).{0,16}?"
    r"(?P<field>open|high|low|close|开盘(?:价)?|最高(?:价|点)?|"
    r"最低(?:价|点)?|收盘(?:价)?|高点|低点)",
    re.IGNORECASE,
)
_LOWER_SPATIAL_RE = re.compile(
    r"下方(?:磁力位|目标位|位置|水平|关键位)|"
    r"\b(?:downside|lower)\s+(?:magnet|target|level)\b",
    re.IGNORECASE,
)
_UPPER_SPATIAL_RE = re.compile(
    r"上方(?:磁力位|目标位|位置|水平|关键位)|"
    r"\b(?:upside|upper|overhead)\s+(?:magnet|target|level)\b",
    re.IGNORECASE,
)
_HTF_FIELD_NAMES = {
    "open": "open",
    "开盘": "open",
    "开盘价": "open",
    "high": "high",
    "最高": "high",
    "最高价": "high",
    "最高点": "high",
    "高点": "high",
    "low": "low",
    "最低": "low",
    "最低价": "low",
    "最低点": "low",
    "低点": "low",
    "close": "close",
    "收盘": "close",
    "收盘价": "close",
}
_CLAUSE_SPLIT_RE = re.compile(r"[。！？!?;；\n]+|\.\s+")


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
        "上方/下方等空间词一律相对当前周期 K1 收盘价，不得把已在当前价上方的 HTF 点位称为下方目标，反之亦然。",
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
    *,
    current_frame: KlineFrame | None = None,
) -> list[str]:
    """Reject only citations that contradict the injected HTF frame."""
    if not isinstance(context, dict) or context.get("status") != "available":
        return []
    frame = context.get("frame")
    if not isinstance(frame, KlineFrame) or not frame.bars:
        return []
    text = str(stage1.get("htf_context") or "")
    references = extract_htf_reference_seqs(text)
    if not references:
        return []
    available = {int(bar.seq) for bar in frame.bars}
    invalid = sorted(references - available)
    if invalid:
        refs = ", ".join(f"HTF.H{value}" for value in invalid)
        return [f"htf_context cites bars outside the injected HTF frame: {refs}"]
    if not isinstance(current_frame, KlineFrame):
        return []
    current = next(
        (
            bar
            for bar in current_frame.bars
            if int(bar.seq) == 1 and bool(bar.closed)
        ),
        None,
    )
    if current is None:
        return []
    htf_by_seq = {int(bar.seq): bar for bar in frame.bars}
    errors: list[str] = []
    for clause in _CLAUSE_SPLIT_RE.split(text):
        direction = (
            "lower"
            if _LOWER_SPATIAL_RE.search(clause)
            else "upper"
            if _UPPER_SPATIAL_RE.search(clause)
            else None
        )
        matches = list(_HTF_FIELD_RE.finditer(clause))
        if direction is None or len(matches) != 1:
            continue
        match = matches[0]
        bar = htf_by_seq.get(int(match.group("seq")))
        field = _HTF_FIELD_NAMES.get(match.group("field").lower())
        if bar is None or field is None:
            continue
        level = float(getattr(bar, field))
        current_close = float(current.close)
        valid_side = level < current_close if direction == "lower" else level > current_close
        if not valid_side:
            errors.append(
                f"htf_context spatial relation for HTF.H{bar.seq}.{field} "
                f"conflicts with current K1.close"
            )
    return errors


def collect_higher_timeframe_warnings(
    stage1: dict[str, Any],
    context: dict[str, Any] | None,
) -> list[str]:
    """Report an omitted HTF citation without rejecting otherwise legal output."""
    if not isinstance(context, dict) or context.get("status") != "available":
        return []
    frame = context.get("frame")
    if not isinstance(frame, KlineFrame) or not frame.bars:
        return []
    text = str(stage1.get("htf_context") or "")
    if extract_htf_reference_seqs(text):
        return []
    return ["htf_context omitted an auditable HTF.H# citation"]


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
