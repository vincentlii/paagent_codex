"""Frozen prior opinions and reproducible subsequent price/time observations.

This module never decides whether a prior plan remains valid.  It reports facts
that can be recomputed from the stored plan and closed OHLC bars; the LLM owns
the contextual interpretation and the current decision.
"""
from __future__ import annotations

import csv
import copy
import hashlib
import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from pa_agent.config.paths import TRADE_RECORDS_DIR

HISTORY_CONTEXT_VERSION = "decision_history_v2"

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


def _legacy_plan_context(
    *,
    frame: Any,
    stage1_json: dict,
    previous_record: Any | None = None,
) -> dict[str, Any]:
    """Build a fact-only previous-plan context for Stage 2."""
    timeframe = str(getattr(frame, "timeframe", "") or "")
    previous_decision = decision_from_previous_record(previous_record)
    previous_stage1 = stage1_from_previous_record(previous_record)
    previous_time = previous_record_time_iso(previous_record)
    previous_bar_ts = previous_record_latest_bar_ts(previous_record)
    previous_source = "analysis_record"

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


def _record_field(record: Any, name: str, default=None):
    return record.get(name, default) if isinstance(record, dict) else getattr(record, name, default)


def _plain(value: Any) -> dict:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value if isinstance(value, dict) else {}


def build_continuity_context(
    *, frame: Any, stage1_json: dict, previous_record: Any | None = None,
    joint_stage2: bool = False, history_context_version: str | None = HISTORY_CONTEXT_VERSION,
) -> dict[str, Any]:
    """Freeze prior reasoning as an opinion, never as current evidence or an order command."""
    from pa_agent.ai.vpa_sanitizer import price_only_history
    from pa_agent.data.bar_close_wait import timeframe_to_seconds
    from pa_agent.data.contracts import ResolvedInstrumentIdentity
    from pa_agent.data.datetime_ts import ts_open_to_ms

    if history_context_version not in (None, HISTORY_CONTEXT_VERSION):
        raise ValueError("unsupported decision history context version")
    stage2 = _plain(_record_field(previous_record, "stage2_decision"))
    if not joint_stage2 and stage2 and not price_only_history(stage2):
        previous_record = None
    if history_context_version is None:
        # Accepted pre-version host tasks retain plan-only semantics, without
        # consulting mutable/unbound CSV state at analysis time.
        return _legacy_plan_context(frame=frame, stage1_json=stage1_json, previous_record=previous_record)
    empty = {"history_context_version": HISTORY_CONTEXT_VERSION, "has_previous_plan": False,
             "has_previous_wait": False, "history_status": "unavailable"}
    meta = _plain(_record_field(previous_record, "meta"))
    outcome = _plain(_record_field(previous_record, "run_outcome"))
    timing = _plain(_record_field(previous_record, "analysis_timing"))
    old_identity = ResolvedInstrumentIdentity.from_value(_record_field(previous_record, "snapshot_context"))
    identity = ResolvedInstrumentIdentity.from_value(frame.data_metadata)
    available = timing.get("result_available_at_ms")
    as_of = frame.data_metadata.get("analysis_as_of_ms")
    cutoff = previous_record_latest_bar_ts(previous_record)
    duration = timeframe_to_seconds(frame.timeframe)
    decision = decision_from_previous_record(previous_record)
    terminal = _plain(stage2.get("terminal")).get("outcome")
    protocol = _plain(stage2.get("analysis_basis")).get("protocol_version", stage2.get("protocol_version"))
    pipeline = _plain(_record_field(previous_record, "analysis_pipeline"))
    if protocol is None and pipeline.get("pipeline") in {"pa_only", "pa_shadow"}:
        protocol = "pa_canonical_v2"
    prior_joint = protocol == "pa_vpa_stage2_draft_v1"
    if (not meta.get("run_id") or meta.get("symbol") != frame.symbol
            or meta.get("timeframe") != frame.timeframe or identity is None or old_identity is None
            or not identity.same_instrument(old_identity)
            or _record_field(previous_record, "exception") is not None
            or _record_field(previous_record, "role_stop") is not None
            or outcome.get("status") not in {"completed", "completed_with_warnings", "degraded_pa_only"}
            or outcome.get("official_path") not in {"pa_only", "joint"}
            or (outcome.get("official_path") == "joint") != prior_joint
            or pipeline.get("pipeline") not in ({"pa_vpa_joint"} if prior_joint else {"pa_only", "pa_shadow"})
            or type(available) is not int or type(as_of) is not int or duration is None
            or cutoff is None or not cutoff + duration * 1000 <= available <= as_of
            or not frame.bars or cutoff >= ts_open_to_ms(frame.bars[0].ts_open)
            or protocol not in {"pa_draft_v2", "pa_canonical_v2", "pa_vpa_stage2_draft_v1"}
            or not decision or terminal not in {"wait", "trade"}
            or (terminal == "wait") == is_order_plan(decision)):
        return empty
    current = {int(ts_open_to_ms(b.ts_open)): b for b in frame.bars
               if b.closed and ts_open_to_ms(b.ts_open) + duration * 1000 <= as_of}
    if int(cutoff) not in current:
        return {**empty, "history_status": "anchor_outside_window"}
    # Only model-owned explanation fields cross the boundary, unabridged. No
    # old joint_analysis, probabilities, execution policy or VPA IDs become facts.
    historical_decision = {key: copy.deepcopy(value) for key, value in decision.items()
                           if key in {"reasoning", "watch_points", "risk_assessment", "entry_rule",
                                      "invalidation_condition", "order_type", "order_direction",
                                      "entry_price", "stop_loss_price", "take_profit_price", "take_profit_price_2"}}
    text = json.dumps(historical_decision, ensure_ascii=False)
    referenced = {1, *(int(x) for x in re.findall(r"(?<![A-Za-z0-9])K\s*(\d+)", text))}
    old_bars = _record_field(previous_record, "kline_data", [])
    mapping = {f"K{seq}": {"ts_open_ms": None, "current_bar": None} for seq in sorted(referenced)}
    seen_sequences: set[int] = set()
    seen_timestamps: set[int] = set()
    for index, bar in enumerate(old_bars):
        if not isinstance(bar, dict):
            return empty
        try:
            seq = int(bar.get("seq", index + 1))
            timestamp = int(ts_open_to_ms(bar["ts_open"]))
        except (KeyError, TypeError, ValueError, OverflowError):
            return empty
        if (bar.get("closed", True) is not True or seq <= 0
                or seq in seen_sequences or timestamp in seen_timestamps):
            return empty
        seen_sequences.add(seq)
        seen_timestamps.add(timestamp)
        if seq not in referenced:
            continue
        match = current.get(timestamp)
        same_price = match is not None and all(bar.get(key) == getattr(match, key) for key in ("open", "high", "low", "close"))
        mapping[f"K{seq}"] = {"ts_open_ms": timestamp,
                               "current_bar": f"K{match.seq}" if same_price else None}
    price_bars = [{key: b.get(key) for key in ("seq", "ts_open", "open", "high", "low", "close")}
                  for b in old_bars]
    result = _legacy_plan_context(frame=frame, stage1_json=stage1_json, previous_record=previous_record)
    result.update(history_context_version=HISTORY_CONTEXT_VERSION, history_status="available",
                  has_previous_wait=terminal == "wait", previous_decision=historical_decision,
                  previous_run_id=meta["run_id"], previous_result_available_at_ms=available,
                  previous_price_bars_digest=hashlib.sha256(json.dumps(price_bars, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                  previous_protocol=protocol, source_identity=identity.to_dict(),
                  historical_bar_mapping=mapping,
                  subsequent_closed_bars=[{"bar": f"K{b.seq}", "ts_open_ms": timestamp}
                                          for timestamp, b in sorted(current.items()) if timestamp > cutoff])
    return result


def render_continuity_prompt_block(context: dict[str, Any]) -> str:
    """Render facts and epistemic limits without prescribing the decision."""
    if context.get("history_context_version") == HISTORY_CONTEXT_VERSION:
        if context.get("history_status") != "available":
            return "## 上轮决策复核\n\n没有可核验的同源历史决策，本轮依据当前快照判断。"
        keys = ("previous_run_id", "previous_protocol", "source_identity", "previous_latest_bar_ts",
                "previous_result_available_at_ms", "previous_price_bars_digest", "previous_decision",
                "historical_bar_mapping", "subsequent_closed_bars")
        payload = {key: context[key] for key in keys}
        if context["has_previous_plan"]:
            payload["price_range_touches"] = {key: context[key] for key in (
                "entry_touched", "stop_touched", "target1_touched", "target2_touched", "same_bar_ambiguity")}
        return ("## 上轮决策复核\n\n" + json.dumps(payload, ensure_ascii=False, indent=2)
                + "\n以上是上轮历史意见及时间映射，不是本轮事实或执行指令。旧K#/PA/HTF/VPA引用属于旧快照；"
                "current_bar=null表示出窗、缺失或价格已修订，不猜映射或顺序。相对‘下一根’以原快照为锚；"
                "窗口未覆盖的响应保持未知。重新读取当前目录证据并使用本轮引用，不复制旧ID。"
                "\n结合当前Stage 1、Router与本轮被授权证据，在现有decision.reasoning复核有实质影响的等待条件："
                "已满足、仍未满足、已失效或无法判断，并说明依据；不要仅把‘等下一根’滚到新的K1。"
                "watch_points只保留仍有意义的条件，可更改或放弃旧假设；没有可检验条件时直说，不强凑。"
                "确认完成不自动授予交易资格，仍独立评价当前Setup、结构Entry/SL、目标与交易价值。"
                "历史价格触达不等于成交确认，同棒先后未知；本轮建议不取消或修改旧挂单/持仓。")
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
        "请在当前 Stage 1/Router 权限内独立形成新方案，在 reasoning 中说明使用了哪些事实；"
        "这些比较不取消或修改已有挂单/持仓。",
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
