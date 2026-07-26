"""Reproducible facts about the previous decision and subsequent price ranges.

This module never decides whether a prior plan remains valid.  It reports facts
that can be recomputed from the stored plan and closed OHLC bars; the LLM owns
the contextual interpretation and the current decision.
"""
from __future__ import annotations

import csv
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from pa_agent.config.paths import TRADE_RECORDS_DIR

_REL_LABELS_ZH = {
    "same_direction": "同向",
    "opposite_direction": "反向",
    "current_no_order": "本轮无单",
    "first_plan": "首个方案",
    "both_no_order": "前后均无单",
}


def _parse_price(raw: object) -> float | None:
    if raw is None or raw == "":
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def order_direction_sign(direction: str | None) -> int:
    text = str(direction or "").strip().lower()
    if "多" in text or text in {"long", "buy", "bullish"}:
        return 1
    if "空" in text or text in {"short", "sell", "bearish"}:
        return -1
    return 0


def is_order_plan(decision: dict | None) -> bool:
    if not isinstance(decision, dict):
        return False
    order_type = str(decision.get("order_type") or "").strip().lower()
    return order_type not in {"", "不下单", "none", "null"}


def _timeframe_minutes(timeframe: str) -> int:
    raw = str(timeframe or "").strip()
    if re.fullmatch(r"\d+M", raw):
        return max(1, int(raw[:-1])) * 43200
    text = raw.lower()
    match = re.fullmatch(r"(\d+)\s*m", text)
    if match:
        return max(1, int(match.group(1)))
    match = re.fullmatch(r"(\d+)\s*h", text)
    if match:
        return max(1, int(match.group(1))) * 60
    match = re.fullmatch(r"(\d+)\s*d", text)
    if match:
        return max(1, int(match.group(1))) * 1440
    match = re.fullmatch(r"(\d+)\s*w", text)
    if match:
        return max(1, int(match.group(1))) * 10080
    return 5


def _parse_local_iso_ms(raw: str | None) -> int | None:
    text = str(raw or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return int(datetime.fromisoformat(text).timestamp() * 1000)
    except (TypeError, ValueError, OSError):
        return None


def bars_elapsed_between(
    previous_time_iso: str | None,
    current_ms: int | None,
    timeframe: str,
    *,
    fallback: int = 0,
) -> int:
    """Return a deterministic estimate based on record time and timeframe."""
    previous_ms = _parse_local_iso_ms(previous_time_iso)
    if previous_ms is None or current_ms is None:
        return max(0, int(fallback))
    duration_ms = _timeframe_minutes(timeframe) * 60 * 1000
    return max(0, math.floor((int(current_ms) - previous_ms) / duration_ms))


def load_last_trade_csv_row(
    symbol: str,
    timeframe: str,
    *,
    records_dir: Path | None = None,
) -> dict[str, str] | None:
    safe_symbol = str(symbol).replace("/", "-").replace("\\", "-")
    safe_timeframe = str(timeframe).replace("/", "-")
    root = records_dir if records_dir is not None else TRADE_RECORDS_DIR
    path = root / f"{safe_symbol}_{safe_timeframe}.csv"
    if not path.is_file():
        return None
    try:
        with path.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
    except OSError:
        return None
    return rows[-1] if rows else None


def decision_from_previous_record(previous_record: Any) -> dict[str, Any] | None:
    if previous_record is None:
        return None
    stage2 = getattr(previous_record, "stage2_decision", None)
    if stage2 is None and isinstance(previous_record, dict):
        stage2 = previous_record.get("stage2_decision")
    if not isinstance(stage2, dict):
        return None
    decision = stage2.get("decision")
    return decision if isinstance(decision, dict) else None


def previous_record_time_iso(previous_record: Any) -> str | None:
    if previous_record is None:
        return None
    meta = getattr(previous_record, "meta", None)
    if meta is None and isinstance(previous_record, dict):
        meta = previous_record.get("meta")
    if isinstance(meta, dict):
        return meta.get("timestamp_local_iso")
    return getattr(meta, "timestamp_local_iso", None) if meta is not None else None


def previous_record_latest_bar_ts(previous_record: Any) -> int | None:
    """Return the prior snapshot's K1 open time in milliseconds when recorded."""
    if previous_record is None:
        return None
    kline_data = getattr(previous_record, "kline_data", None)
    if kline_data is None and isinstance(previous_record, dict):
        kline_data = previous_record.get("kline_data")
    if not isinstance(kline_data, list):
        return None
    candidates: list[tuple[int, int]] = []
    from pa_agent.data.datetime_ts import ts_open_to_ms

    for index, item in enumerate(kline_data):
        if not isinstance(item, dict):
            continue
        try:
            seq = int(item.get("seq", index + 1))
            timestamp = int(ts_open_to_ms(float(item["ts_open"])))
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        candidates.append((seq, timestamp))
    if not candidates:
        return None
    explicit_k1 = [timestamp for seq, timestamp in candidates if seq == 1]
    return explicit_k1[0] if explicit_k1 else max(timestamp for _, timestamp in candidates)


def stage1_from_previous_record(previous_record: Any) -> dict[str, Any] | None:
    if previous_record is None:
        return None
    stage1 = getattr(previous_record, "stage1_diagnosis", None)
    if stage1 is None and isinstance(previous_record, dict):
        stage1 = previous_record.get("stage1_diagnosis")
    return stage1 if isinstance(stage1, dict) else None


def _first_touch_bar(bars_oldest_first: list[Any], price: float | None) -> int | None:
    if price is None:
        return None
    for bar in bars_oldest_first:
        low = float(getattr(bar, "low"))
        high = float(getattr(bar, "high"))
        if low <= price <= high:
            return int(getattr(bar, "seq", 0) or 0)
    return None


def assess_plan_price_touches(
    decision: dict | None,
    frame: Any,
    *,
    max_bars: int | None = None,
    after_ts_open_ms: int | None = None,
) -> dict[str, Any]:
    """Measure whether closed-bar ranges crossed stored plan prices.

    A touch does not prove an exchange fill, and multiple prices touched in one
    bar have unknown intrabar order.  Those limits are reported explicitly.
    """
    empty = {
        "bars_scanned": 0,
        "entry_touched": False,
        "entry_touch_bar": None,
        "stop_touched": False,
        "stop_touch_bar": None,
        "target1_touched": False,
        "target1_touch_bar": None,
        "target2_touched": False,
        "target2_touch_bar": None,
        "same_bar_ambiguity": False,
    }
    if not is_order_plan(decision):
        return empty

    bars = list(getattr(frame, "bars", ()) or ())
    if after_ts_open_ms is not None:
        from pa_agent.data.datetime_ts import ts_open_to_ms

        bars = [
            bar
            for bar in bars
            if int(ts_open_to_ms(float(getattr(bar, "ts_open")))) > int(after_ts_open_ms)
        ]
    elif max_bars is not None:
        bars = bars[: max(0, int(max_bars))]
    if not bars:
        return empty

    oldest_first = list(reversed(bars))
    levels = {
        "entry": _parse_price((decision or {}).get("entry_price")),
        "stop": _parse_price((decision or {}).get("stop_loss_price")),
        "target1": _parse_price((decision or {}).get("take_profit_price")),
        "target2": _parse_price((decision or {}).get("take_profit_price_2")),
    }
    touches = {name: _first_touch_bar(oldest_first, price) for name, price in levels.items()}
    touched_bars = [seq for seq in touches.values() if seq is not None]
    return {
        "bars_scanned": len(bars),
        "entry_touched": touches["entry"] is not None,
        "entry_touch_bar": touches["entry"],
        "stop_touched": touches["stop"] is not None,
        "stop_touch_bar": touches["stop"],
        "target1_touched": touches["target1"] is not None,
        "target1_touch_bar": touches["target1"],
        "target2_touched": touches["target2"] is not None,
        "target2_touch_bar": touches["target2"],
        "same_bar_ambiguity": len(touched_bars) != len(set(touched_bars)),
    }


def _decision_from_csv_row(row: dict[str, str]) -> dict[str, Any]:
    return {
        "order_direction": row.get("order_direction"),
        "order_type": row.get("order_type"),
        "entry_price": row.get("entry_price"),
        "stop_loss_price": row.get("stop_loss_price"),
        "take_profit_price": row.get("take_profit_price"),
        "take_profit_price_2": row.get("take_profit_price_2"),
        "invalidation_condition": row.get("invalidation_condition"),
    }


def build_continuity_context(
    *,
    frame: Any,
    stage1_json: dict,
    previous_record: Any | None = None,
) -> dict[str, Any]:
    """Build a fact-only previous-plan context for Stage 2."""
    symbol = str(getattr(frame, "symbol", "") or "")
    timeframe = str(getattr(frame, "timeframe", "") or "")
    previous_decision = decision_from_previous_record(previous_record)
    previous_stage1 = stage1_from_previous_record(previous_record)
    previous_time = previous_record_time_iso(previous_record)
    previous_bar_ts = previous_record_latest_bar_ts(previous_record)
    previous_source = "analysis_record"

    if previous_decision is None:
        row = load_last_trade_csv_row(symbol, timeframe)
        if row:
            previous_decision = _decision_from_csv_row(row)
            previous_time = row.get("record_time")
            previous_bar_ts = None
            previous_stage1 = None
            previous_source = "trade_csv"

    estimated_bars_since = bars_elapsed_between(
        previous_time,
        getattr(frame, "snapshot_ts_local_ms", None),
        timeframe,
    )
    if previous_bar_ts is not None:
        touches = assess_plan_price_touches(
            previous_decision,
            frame,
            after_ts_open_ms=previous_bar_ts,
        )
        touch_window_basis = "snapshot_bar_timestamp"
    else:
        touches = assess_plan_price_touches(
            previous_decision,
            frame,
            max_bars=estimated_bars_since,
        )
        touch_window_basis = "record_time_estimate"
    previous_cycle = str((previous_stage1 or {}).get("cycle_position") or "")
    previous_direction = str((previous_stage1 or {}).get("direction") or "")
    current_cycle = str(stage1_json.get("cycle_position") or "")
    current_direction = str(stage1_json.get("direction") or "")

    return {
        "has_previous_plan": is_order_plan(previous_decision),
        "previous_decision": previous_decision or {},
        "previous_time": previous_time,
        "previous_source": previous_source,
        "estimated_closed_bars_elapsed": estimated_bars_since,
        "touch_window_basis": touch_window_basis,
        "previous_latest_bar_ts": previous_bar_ts,
        "timeframe": timeframe,
        "previous_cycle_position": previous_cycle,
        "current_cycle_position": current_cycle,
        "cycle_label_changed": bool(previous_cycle and current_cycle and previous_cycle != current_cycle),
        "previous_direction": previous_direction,
        "current_direction": current_direction,
        "direction_label_changed": bool(
            previous_direction and current_direction and previous_direction != current_direction
        ),
        **touches,
    }


def render_continuity_prompt_block(context: dict[str, Any]) -> str:
    """Render facts and epistemic limits without prescribing the decision."""
    if not context.get("has_previous_plan"):
        return "## 上一轮方案事实\n\n未找到可执行的上一轮订单方案；本轮独立判断。"

    previous = context.get("previous_decision") or {}
    lines = [
        "## 上一轮方案与后续价格事实",
        "",
        f"记录时间：{context.get('previous_time') or '未知'}；来源：{context.get('previous_source')}",
        (
            f"按上次快照 K1 时间戳筛选，实际扫描其后 {context.get('bars_scanned')} 根已收盘 K 线。"
            if context.get("touch_window_basis") == "snapshot_bar_timestamp"
            else f"缺少上次快照 K1 时间戳；按记录时间与周期估算后扫描最近 "
            f"{context.get('bars_scanned')} 根（估算经过约 "
            f"{context.get('estimated_closed_bars_elapsed')} 根）。"
        ),
        f"上一方案：{previous.get('order_direction')} {previous.get('order_type')}，"
        f"entry={previous.get('entry_price')}，stop={previous.get('stop_loss_price')}，"
        f"TP1={previous.get('take_profit_price')}，TP2={previous.get('take_profit_price_2')}。",
        f"价格范围触达：entry={context.get('entry_touched')}({context.get('entry_touch_bar')})；"
        f"stop={context.get('stop_touched')}({context.get('stop_touch_bar')})；"
        f"TP1={context.get('target1_touched')}({context.get('target1_touch_bar')})；"
        f"TP2={context.get('target2_touched')}({context.get('target2_touch_bar')})。",
        f"阶段一标签变化：cycle={context.get('cycle_label_changed')}；"
        f"direction={context.get('direction_label_changed')}。",
        "",
        "这些是 OHLC 范围与前后 JSON 的可复算比较，不等于成交确认、计划失效或新 Setup。",
        "若同一根 K 线触及多个价位，无法从 OHLC 确定盘中先后；"
        f"本次 same_bar_ambiguity={context.get('same_bar_ambiguity')}。",
        "请结合背景、位置、触发后的价格行为和当前 Setup，独立判断延续、取消、重定价或反向；"
        "在 reasoning 中说明使用了哪些事实。",
    ]
    return "\n".join(lines)


def _relation(previous: dict | None, current: dict | None) -> str:
    previous_has_plan = is_order_plan(previous)
    current_has_plan = is_order_plan(current)
    if not previous_has_plan:
        return "first_plan" if current_has_plan else "both_no_order"
    if not current_has_plan:
        return "current_no_order"
    previous_sign = order_direction_sign(str((previous or {}).get("order_direction") or ""))
    current_sign = order_direction_sign(str((current or {}).get("order_direction") or ""))
    return "same_direction" if previous_sign == current_sign else "opposite_direction"


def audit_relation_fields(
    previous_row: dict[str, str] | None,
    current_decision: dict | None,
    *,
    frame: Any,
) -> dict[str, str]:
    """Return fact-only fields for the trade CSV."""
    previous_decision = _decision_from_csv_row(previous_row) if previous_row else None
    relation = _relation(previous_decision, current_decision)
    if not previous_row:
        return {
            "prev_plan_relation": _REL_LABELS_ZH[relation],
            "prev_plan_entry": "",
            "estimated_bars_since_prev_plan": "",
            "prev_plan_entry_touched": "",
            "prev_plan_stop_touched": "",
            "prev_plan_target1_touched": "",
            "prev_plan_target2_touched": "",
            "prev_plan_touch_ambiguity": "",
            "prev_plan_touch_basis": "",
        }

    bars_since = bars_elapsed_between(
        previous_row.get("record_time"),
        getattr(frame, "snapshot_ts_local_ms", None) if frame is not None else None,
        getattr(frame, "timeframe", "") if frame is not None else "",
    )
    touches = assess_plan_price_touches(previous_decision, frame, max_bars=bars_since)
    previous_entry = _parse_price((previous_decision or {}).get("entry_price"))
    return {
        "prev_plan_relation": _REL_LABELS_ZH[relation],
        "prev_plan_entry": str(previous_entry) if previous_entry is not None else "",
        "estimated_bars_since_prev_plan": str(bars_since),
        "prev_plan_entry_touched": str(bool(touches["entry_touched"])).lower(),
        "prev_plan_stop_touched": str(bool(touches["stop_touched"])).lower(),
        "prev_plan_target1_touched": str(bool(touches["target1_touched"])).lower(),
        "prev_plan_target2_touched": str(bool(touches["target2_touched"])).lower(),
        "prev_plan_touch_ambiguity": str(bool(touches["same_bar_ambiguity"])).lower(),
        "prev_plan_touch_basis": "record_time_estimate",
    }
