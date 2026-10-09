"""Structural and cross-field validation for model-owned conclusions.

These checks verify references and internal consistency. They do not classify
bars, patterns, trend, momentum, setups, or whether an interpretation is correct.
"""
from __future__ import annotations

import re
from typing import Any

from pa_agent.ai.decision_tree import (
    STAGE1_GATE_NODE_IDS,
    normalize_bar_range,
)
from pa_agent.ai.factual_grounding import (
    contains_retry_deliberation,
    local_close_position_claim_kinds,
    negated_window_extreme_claim_kinds,
    validate_htf_inference_only_narrative,
    validate_pa_inference_only_narrative,
    window_extreme_claim_kinds,
)
from pa_agent.ai.kline_features import classify_close_position_zone
from pa_agent.ai.kline_references import K_RANGE_RE, K_REFERENCE_RE
from pa_agent.ai.price_levels import parse_price_level_bounds

BAR_BY_BAR_TARGET_COUNT = 5
STAGE1_MANDATORY_GATE_NODES = STAGE1_GATE_NODE_IDS

_CYCLE_BRANCH_ALIASES = {
    "tr": "trading_range",
    "交易区间": "trading_range",
    "普通交易区间": "trading_range",
    "趋势型交易区间": "trending_tr",
    "极端交易区间": "extreme_tr",
    "尖峰": "spike",
    "微型通道": "micro_channel",
    "窄通道": "tight_channel",
    "正常通道": "normal_channel",
    "宽通道": "broad_channel",
}
_DIRECTION_BRANCH_ALIASES = {
    "bull": "bullish",
    "bear": "bearish",
    "多头": "bullish",
    "空头": "bearish",
    "中性": "neutral",
    "上涨": "bullish",
    "下跌": "bearish",
}
_EMA_SLOPE_WORD_RE = re.compile(
    r"EMA\s*20.*?(?:上行|下行|走高|走低|斜率|slope|rising|falling)",
    re.IGNORECASE,
)
_EMA_UP_RE = re.compile(r"上行|走高|rising|upward", re.IGNORECASE)
_EMA_DOWN_RE = re.compile(r"下行|走低|falling|downward", re.IGNORECASE)
_NUMBER_RE = re.compile(r"(?<![A-Za-z0-9])[-+]?\d+(?:\.\d+)?")
_VAGUE_NUMERIC_LEG_COUNT_RE = re.compile(
    r"(?:反弹|回撤|上涨|下跌|推进|push|pullback|rally|decline).{0,12}"
    r"(?:超过|多于|more\s+than)\s*\d+\s*(?:根(?:K线)?|bars?)",
    re.IGNORECASE,
)
_ONE_TICK_RE = re.compile(r"\b(?:1|one)\s*ticks?\b|(?:1\s*跳|一跳)", re.IGNORECASE)
_TICK_DISCLOSURE_RE = re.compile(
    r"(?:tick\s*size|跳动).*?(?:未确认|未知|待.{0,6}校验|执行前.{0,6}校验|"
    r"unverified|unknown|validate|verify)",
    re.IGNORECASE,
)
_PA_VOLUME_RE = re.compile(
    r"\bvolume\b|\brvol\b|\brelative\s+activity\b|"
    r"\u6210\u4ea4\u91cf|\u91cf\u80fd|\u653e\u91cf|\u7f29\u91cf|\u9ad8\u91cf|\u4f4e\u91cf",
    re.IGNORECASE,
)
_BULL_COUNT_PATTERNS = (
    re.compile(
        r"(?:has|with|contains?|only|just)\s*(\d+)\s*(?:bullish|bull)\s*bars?",
        re.IGNORECASE,
    ),
    re.compile(r"(?<![A-Za-z0-9\u7b2c])(\d+)\s*(?:\u6839)?(?:\u9633\u7ebf|\u591a\u5934(?:K\u7ebf|\u68d2)?)"),
)
_BEAR_COUNT_PATTERNS = (
    re.compile(
        r"(?:has|with|contains?|only|just)\s*(\d+)\s*(?:bearish|bear)\s*bars?",
        re.IGNORECASE,
    ),
    re.compile(r"(?<![A-Za-z0-9\u7b2c])(\d+)\s*(?:\u6839)?(?:\u9634\u7ebf|\u7a7a\u5934(?:K\u7ebf|\u68d2)?)"),
)
_EXPLICIT_CONTINUOUS_BULL_BARS_RE = re.compile(
    r"consecutive\s+(?:bull(?:ish)?)\s+(?:bars?|candles?)|"
    r"\u8fde\u7eed\s*(?:\d+\s*\u6839\s*)?(?:\u9633\u7ebf|\u591a\u5934K\u7ebf)",
    re.IGNORECASE,
)
_EXPLICIT_CONTINUOUS_BEAR_BARS_RE = re.compile(
    r"consecutive\s+(?:bear(?:ish)?)\s+(?:bars?|candles?)|"
    r"\u8fde\u7eed\s*(?:\d+\s*\u6839\s*)?(?:\u9634\u7ebf|\u7a7a\u5934K\u7ebf)",
    re.IGNORECASE,
)
_EXACT_BAR_COUNT_RE = re.compile(
    r"(?<![A-Za-z0-9_\u7b2c])\d+\s*(?:\u6839(?:K\u7ebf|\u8721\u70db|\u68d2)?|bars?\b)",
    re.IGNORECASE,
)
_PA_BOUNDED_EXTREME_FACT_RE = re.compile(
    r"(?<![A-Za-z0-9_.])PA\.(?:WINDOW\.K\d+|COMPARE\.K1\.PRIOR\d+|"
    r"PAIR\.K\d+\.K\d+)(?![A-Za-z0-9_.])",
    re.IGNORECASE,
)
_FACT_CLAUSE_SPLIT_RE = re.compile(
    r"[\u3002\uFF01\uFF1F!?;\uFF1B\n]+|\.\s+"
)
_LIMIT_CONFIRMATION_PREREQUISITE_RE = re.compile(
    r"(?:等待|等|需|必须).{0,24}(?:收盘|K线|bar).{0,16}(?:确认|站稳).{0,12}(?:后|才)|"
    r"(?:activate|enter|place).{0,20}(?:only\s+)?after.{0,20}(?:closed\s+bar|confirmation)",
    re.IGNORECASE,
)
_INSIDE_PREVIOUS_CLAIM_RE = re.compile(
    r"\binside\s+(?:price\s+)?bar\b|"
    r"(?:内包棒|内包线)|"
    r"被(?:前(?:一根|棒)|上一根)(?:K线|棒)?(?:完全)?包含|"
    r"(?:contained|enclosed)\s+(?:by|within)\s+(?:the\s+)?previous\s+bar",
    re.IGNORECASE,
)
_OUTSIDE_PREVIOUS_CLAIM_RE = re.compile(
    r"\boutside\s+(?:price\s+)?bar\b|"
    r"(?:外包棒|外包线)|"
    r"(?:完全)?包含(?:了)?(?:前(?:一根|棒)|上一根)(?:K线|棒)?|"
    r"contains?\s+(?:the\s+)?previous\s+bar",
    re.IGNORECASE,
)
_INSIDE_EXPLICIT_PAIR_RE = re.compile(
    r"(?<![A-Za-z0-9_])K\s*(\d+)(?![A-Za-z0-9_.])(?:"
    r".{0,24}?(?:被)\s*(?:K线)?\s*K\s*(\d+)(?![A-Za-z0-9_.])"
    r".{0,8}?(?:包含|包住)|"
    r".{0,24}?(?:内包于)\s*(?:K线)?\s*K\s*(\d+)(?![A-Za-z0-9_.])|"
    r".{0,24}?(?:contained\s+(?:by|within)|inside(?:\s+of)?)\s*"
    r"(?:K线)?\s*K\s*(\d+)(?![A-Za-z0-9_.])"
    r")",
    re.IGNORECASE,
)
_OUTSIDE_EXPLICIT_PAIR_RE = re.compile(
    r"(?<![A-Za-z0-9_])K\s*(\d+)(?![A-Za-z0-9_.]).{0,24}?"
    r"(?:包含|外包|contains?|outside(?:\s+of)?)\s*"
    r"(?:K线)?\s*K\s*(\d+)(?![A-Za-z0-9_.])",
    re.IGNORECASE,
)
_NEGATION_NEAR_GEOMETRY_RE = re.compile(
    r"\b(?:not|never|isn't|isnt|aren't|arent|doesn't|doesnt|didn't|didnt)\b|"
    r"(?:\u4e0d\u662f|\u5e76\u975e|\u672a(?:\u88ab)?|\u6ca1\u6709|\u4e0d(?:\u88ab|\u5305\u542b))",
    re.IGNORECASE,
)
_BAR_BREAK_RANGE_RE = re.compile(
    r"(?<![A-Za-z0-9_])K\s*(\d+)(?![A-Za-z0-9_.]).{0,32}?"
    r"(?:broke|breaks?|exceeded|\u7a81\u7834|\u8d8a\u8fc7).{0,20}?"
    r"(?<![A-Za-z0-9_])K\s*(\d+)\s*[-\u2013\u2014\uFF0D]\s*K?\s*(\d+)"
    r"(?![A-Za-z0-9_.]).{0,12}?"
    r"(high|low|\u9ad8\u70b9|\u4f4e\u70b9)",
    re.IGNORECASE,
)
_EXPLICIT_BAR_PRICE_PATTERNS = (
    re.compile(
        r"(?<![A-Za-z0-9_])K\s*(?P<seq>\d+)(?![A-Za-z0-9_.])"
        r"\s*(?:的\s*)?"
        r"(?P<field>open|high|low|close|开盘(?:价)?|最高(?:价|点)?|"
        r"最低(?:价|点)?|收盘(?:价)?|高点|低点)"
        r"\s*(?:为|是|at|was|is|=|:|：)?\s*"
        r"(?P<price>[-+]?(?:\d+(?:\.\d+)?|\.\d+))",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?<![A-Za-z0-9_])K\s*(?P<seq>\d+)(?![A-Za-z0-9_.])"
        r"\s*(?:的\s*)?"
        r"(?P<price>[-+]?(?:\d+(?:\.\d+)?|\.\d+))\s*"
        r"(?P<field>open|high|low|close|开盘(?:价)?|最高(?:价|点)?|"
        r"最低(?:价|点)?|收盘(?:价)?|高点|低点)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?P<field>open|high|low|close|开盘(?:价)?|最高(?:价|点)?|"
        r"最低(?:价|点)?|收盘(?:价)?|高点|低点)\s*(?:从|自)\s*"
        r"(?<![A-Za-z0-9_])K\s*(?P<seq>\d+)(?![A-Za-z0-9_.])"
        r"\s*(?:的\s*)?(?P<price>[-+]?(?:\d+(?:\.\d+)?|\.\d+))",
        re.IGNORECASE,
    ),
)
_BAR_PRICE_FIELD_NAMES = {
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

_GROUPED_K_REFERENCE_RE = re.compile(
    r"(?<![A-Za-z0-9_])K\s*\d+(?:\s*[/\uff0f\u3001,\uff0c]\s*K\s*\d+)+",
    re.IGNORECASE,
)
_NEGATED_BAR_BREAK_RE = re.compile(
    r"(?:\u5c1a\u672a|\u5e76\u672a|\u672a\u80fd?|\u6ca1\u6709|\u4e0d\u80fd|\bnot\b|\bfailed\s+to\b)"
    r"[^,\uFF0C;\uFF1B\u3002]{0,8}?(?:\u7a81\u7834|\u8d8a\u8fc7|broke|breaks?|exceeded)",
    re.IGNORECASE,
)
_GROUPED_BAR_PRICE_RANGE_RE = re.compile(
    r"(?P<refs>(?<![A-Za-z0-9_])K\s*\d+"
    r"(?:\s*[/\uff0f\u3001,\uff0c]\s*K\s*\d+)+)"
    r"\s*(?:\u7684\s*)?"
    r"(?P<field>open|high|low|close|\u5f00\u76d8(?:\u4ef7)?|\u6700\u9ad8(?:\u4ef7|\u70b9)?|"
    r"\u6700\u4f4e(?:\u4ef7|\u70b9)?|\u6536\u76d8(?:\u4ef7)?|\u9ad8\u70b9|\u4f4e\u70b9)"
    r"\s*(?:\u533a\u95f4|\u8303\u56f4|\u533a\u57df|range)?\s*"
    r"(?P<first>[-+]?(?:\d+(?:\.\d+)?|\.\d+))\s*"
    r"[-\u2013\u2014~\u81f3\u5230]\s*"
    r"(?P<second>[-+]?(?:\d+(?:\.\d+)?|\.\d+))",
    re.IGNORECASE,
)

_EXPLICIT_WINDOW_PRICE_RANGE_RE = re.compile(
    r"K(?P<left>\d+)\s*[-\u2013\u2014~\u81f3\u5230]\s*K(?P<right>\d+)"
    r"(?P<context>[^\u3002\uff1b;.!?\uff01\uff1f\n]{0,120})",
    re.I,
)
_PRICE_REGION_RE = re.compile(
    r"(?P<first>-?\d+(?:\.\d+)?)\s*[-\u2013\u2014~\u81f3\u5230]\s*"
    r"(?P<second>-?\d+(?:\.\d+)?)\s*"
    r"(?:\u533a\u57df|\u533a\u95f4|\u8303\u56f4|range)",
    re.I,
)


def validate_stage1_coherence(
    stage1: dict[str, Any],
    *,
    kline_frame: Any = None,
) -> list[str]:
    errors: list[str] = []
    trace = stage1.get("gate_trace")
    if not isinstance(trace, list):
        return errors

    if stage1.get("gate_result") == "proceed":
        present = _trace_node_ids(trace)
        missing = [node for node in STAGE1_MANDATORY_GATE_NODES if node not in present]
        if missing:
            errors.append("gate_result=proceed 缺少 gate_trace 节点: " + ", ".join(missing))

    errors.extend(_validate_trace(trace, kline_frame=kline_frame, path="gate_trace"))
    errors.extend(
        validate_skipped_node_consistency(
            trace,
            path_prefix="gate_trace",
            mandatory_nodes=STAGE1_MANDATORY_GATE_NODES
            if stage1.get("gate_result") == "proceed"
            else (),
        )
    )

    cycle = _normalize_cycle_branch(stage1.get("cycle_position"))
    alternative = _normalize_cycle_branch(stage1.get("alternative_cycle_position"))
    node_12 = _find_trace_item(trace, "1.2")
    if node_12 and node_12.get("answer") == "是":
        branch = _normalize_cycle_branch(node_12.get("branch"))
        if branch and cycle and branch not in {cycle, alternative}:
            errors.append(
                f"gate_trace 1.2 branch={branch!r} 与 cycle_position={cycle!r} 不一致"
            )

    direction = _normalize_direction_branch(stage1.get("direction"))
    node_23 = _find_trace_item(trace, "2.3")
    if node_23 and not node_23.get("skipped"):
        branch = _normalize_direction_branch(node_23.get("branch"))
        if branch and direction and branch != direction:
            errors.append(
                f"gate_trace 2.3 branch={branch!r} 与 direction={direction!r} 不一致"
            )

    summary = stage1.get("bar_by_bar_summary")
    if isinstance(summary, list):
        bar_count = _max_bar_seq(kline_frame)
        for index, item in enumerate(summary):
            if not isinstance(item, dict):
                continue
            seq = _parse_k_seq(item.get("bar"))
            if seq is None or (bar_count and not 1 <= seq <= bar_count):
                errors.append(f"bar_by_bar_summary[{index}].bar 不在当前K线窗口")
    errors.extend(_validate_stage1_bar_geometry(stage1, kline_frame))
    errors.extend(_validate_explicit_bar_price_claims(stage1, kline_frame))
    errors.extend(_validate_explicit_window_price_range_claims(stage1, kline_frame))
    errors.extend(_validate_stage1_structured_facts(stage1, kline_frame))
    return errors


def collect_stage1_quality_warnings(
    stage1: dict[str, Any],
    *,
    kline_frame: Any = None,
) -> list[str]:
    """Return non-safety prose and completeness findings without rejecting output."""
    warnings: list[str] = []
    summary = stage1.get("bar_by_bar_summary")
    if isinstance(summary, list):
        bar_count = _max_bar_seq(kline_frame)
        expected = min(BAR_BY_BAR_TARGET_COUNT, bar_count) if bar_count else None
        if expected is not None and len(summary) != expected:
            warnings.append(
                f"bar_by_bar_summary has {len(summary)} items; preferred {expected}"
            )
    warnings.extend(_collect_stage1_narrative_warnings(stage1, kline_frame))
    warnings.extend(_validate_stage1_narrative_geometry(stage1, kline_frame))
    warnings.extend(_validate_explicit_direction_runs(stage1, kline_frame))
    return warnings


def validate_stage2_coherence(
    stage2: dict[str, Any],
    stage1: dict[str, Any],
    *,
    kline_frame: Any = None,
) -> list[str]:
    if stage2.get("gate_shortcircuited"):
        return []
    errors: list[str] = []
    trace = stage2.get("decision_trace")
    if isinstance(trace, list):
        errors.extend(_validate_trace(trace, kline_frame=kline_frame, path="decision_trace"))

    decision = stage2.get("decision")
    if isinstance(decision, dict) and decision.get("order_type") in {"限价单", "突破单", "市价单"}:
        stage1_direction = str(stage1.get("direction") or "").lower()
        order_direction = decision.get("order_direction")
        conflicts = (
            (stage1_direction == "bullish" and order_direction == "做空")
            or (stage1_direction == "bearish" and order_direction == "做多")
        )
        if conflicts:
            errors.append(
                "order_direction 与阶段一 direction 相反；同一快照必须回到阶段一重做方向诊断"
            )
    errors.extend(_validate_stage2_bar_references(stage2, kline_frame))
    errors.extend(_validate_explicit_bar_price_claims(stage2, kline_frame))
    errors.extend(_validate_explicit_window_price_range_claims(stage2, kline_frame))
    return errors


def collect_stage2_quality_warnings(
    stage2: dict[str, Any],
    *,
    kline_frame: Any = None,
) -> list[str]:
    """Return prose-level PA grounding findings without blocking a safe decision."""
    warnings: list[str] = []
    price_only_stage2 = {
        key: value for key, value in stage2.items() if key != "vpa_impact"
    }
    for path, text in _narrative_strings(price_only_stage2):
        if _PA_VOLUME_RE.search(text):
            warnings.append(f"{path} violates the PA price-only evidence contract")
    warnings.extend(_validate_tick_claims(stage2, kline_frame))
    decision = stage2.get("decision")
    if isinstance(decision, dict) and decision.get("order_type") == "限价单":
        entry_rule = str(decision.get("entry_rule") or "")
        if _LIMIT_CONFIRMATION_PREREQUISITE_RE.search(entry_rule):
            warnings.append(
                "decision.entry_rule describes confirmation before a passive limit; "
                "execution still follows order_type=限价单, so move a true prerequisite "
                "to watch_points and wait for the next PA run"
            )
    return warnings


def _validate_stage2_bar_references(stage2: dict[str, Any], frame: Any) -> list[str]:
    if frame is None:
        return []
    known = {int(getattr(bar, "seq", 0)) for bar in getattr(frame, "bars", ())}
    bar_analysis = stage2.get("bar_analysis")
    if not isinstance(bar_analysis, dict):
        return []
    errors: list[str] = []
    for field in ("signal_bar", "entry_bar"):
        value = bar_analysis.get(field)
        raw = value.get("bar") if isinstance(value, dict) else None
        if raw is None:
            continue
        seq = _parse_k_seq(raw)
        if seq is None:
            errors.append(f"bar_analysis.{field}.bar must be a K{{n}} reference")
        elif seq not in known:
            errors.append(
                f"bar_analysis.{field}.bar K{seq} not found in current K-line frame"
            )
    return errors


def _validate_explicit_bar_price_claims(value: Any, frame: Any) -> list[str]:
    """Check only explicit K#/OHLC/value claims against the frozen bars."""
    if frame is None:
        return []
    bars = {
        int(getattr(bar, "seq", 0)): bar
        for bar in getattr(frame, "bars", ()) or ()
        if int(getattr(bar, "seq", 0) or 0) > 0
    }
    errors: list[str] = []
    seen: set[tuple[str, int, str, str]] = set()
    for path, text in _narrative_strings(value):
        grouped_reference_spans = [
            match.span() for match in _GROUPED_K_REFERENCE_RE.finditer(text)
        ]
        for match in _GROUPED_BAR_PRICE_RANGE_RE.finditer(text):
            field = _BAR_PRICE_FIELD_NAMES.get(match.group("field").lower())
            seqs = [
                int(item)
                for item in re.findall(r"K\s*(\d+)", match.group("refs"), re.IGNORECASE)
            ]
            if field is None or not seqs or any(seq not in bars for seq in seqs):
                continue
            first_raw = match.group("first")
            second_raw = match.group("second")
            stated_low, stated_high = sorted((float(first_raw), float(second_raw)))
            actual_values = [float(getattr(bars[seq], field)) for seq in seqs]
            actual_low, actual_high = min(actual_values), max(actual_values)
            low_tolerance = 0.5 * (10 ** (-len(first_raw.partition(".")[2]))) + 1e-12
            high_tolerance = 0.5 * (10 ** (-len(second_raw.partition(".")[2]))) + 1e-12
            if (
                abs(stated_low - actual_low) > low_tolerance
                or abs(stated_high - actual_high) > high_tolerance
            ):
                errors.append(
                    f"{path} grouped K {field} range states {first_raw}-{second_raw}, "
                    f"but frozen grouped range is {actual_low:.12g}-{actual_high:.12g}"
                )
        for pattern in _EXPLICIT_BAR_PRICE_PATTERNS:
            for match in pattern.finditer(text):
                if any(start <= match.start() < end for start, end in grouped_reference_spans):
                    continue
                seq = int(match.group("seq"))
                field = _BAR_PRICE_FIELD_NAMES.get(match.group("field").lower())
                bar = bars.get(seq)
                if field is None or bar is None:
                    continue
                raw_price = match.group("price")
                key = (path, seq, field, raw_price)
                if key in seen:
                    continue
                seen.add(key)
                stated = float(raw_price)
                actual = float(getattr(bar, field))
                decimals = len(raw_price.partition(".")[2])
                tolerance = 0.5 * (10 ** (-decimals)) + 1e-12
                if abs(stated - actual) > tolerance:
                    errors.append(
                        f"{path} K{seq}.{field} states {raw_price}, "
                        f"but frozen bar value is {actual:.12g}"
                    )
    return errors


def _validate_stage1_bar_geometry(stage1: dict[str, Any], frame: Any) -> list[str]:
    """Check only exact bar relationships; semantic thresholds stay with the LLM."""
    if frame is None:
        return []
    from pa_agent.ai.kline_features import compute_kline_geometry_features

    features = {feature.seq: feature for feature in compute_kline_geometry_features(frame)}
    checks: list[tuple[str, Any, Any]] = []
    errors: list[str] = []
    bar_analysis = stage1.get("bar_analysis")
    if isinstance(bar_analysis, dict):
        if str(bar_analysis.get("last_closed_bar") or "").strip().upper() != "K1":
            errors.append("bar_analysis.last_closed_bar must be K1")
        signal_bar = bar_analysis.get("signal_bar")
        if isinstance(signal_bar, dict):
            if signal_bar.get("bar") is None:
                if signal_bar.get("quality") != "invalid":
                    errors.append(
                        "bar_analysis.signal_bar.quality must be invalid when bar is null"
                    )
            else:
                signal_seq = _parse_k_seq(signal_bar.get("bar"))
                if signal_seq is None:
                    errors.append("bar_analysis.signal_bar.bar must be a K{n} reference")
                elif signal_seq not in features:
                    errors.append(
                        f"bar_analysis.signal_bar.bar K{signal_seq} not found in current K-line frame"
                    )
        checks.append(
            (
                "bar_analysis.bar_type",
                bar_analysis.get("last_closed_bar"),
                bar_analysis.get("bar_type"),
            )
        )
    for index, item in enumerate(stage1.get("bar_by_bar_summary") or []):
        if isinstance(item, dict):
            checks.append(
                (
                    f"bar_by_bar_summary[{index}].bar_type",
                    item.get("bar"),
                    item.get("bar_type"),
                )
            )

    for path, bar_ref, label_value in checks:
        seq = _parse_k_seq(bar_ref)
        feature = features.get(seq) if seq is not None else None
        if feature is None:
            continue
        label = str(label_value or "")
        if label == "trend_bull" and feature.candle_direction != "bullish":
            errors.append(f"{path}=trend_bull conflicts with program candle direction")
        elif label == "trend_bear" and feature.candle_direction != "bearish":
            errors.append(f"{path}=trend_bear conflicts with program candle direction")
    return errors


def _validate_explicit_window_price_range_claims(value: Any, frame: Any) -> list[str]:
    """Check only an explicit K-window plus numeric price-range assertion."""
    if frame is None:
        return []
    bars = {
        int(getattr(bar, "seq", 0)): bar
        for bar in getattr(frame, "bars", ()) or ()
        if int(getattr(bar, "seq", 0) or 0) > 0
    }
    errors: list[str] = []
    for path, text in _narrative_strings(value):
        for window_match in _EXPLICIT_WINDOW_PRICE_RANGE_RE.finditer(text):
            region_match = _PRICE_REGION_RE.search(window_match.group("context"))
            if region_match is None:
                continue
            left = int(window_match.group("left"))
            right = int(window_match.group("right"))
            selected = [
                bars.get(seq)
                for seq in range(min(left, right), max(left, right) + 1)
            ]
            if not selected or any(bar is None for bar in selected):
                continue
            first_raw = region_match.group("first")
            second_raw = region_match.group("second")
            claimed_low, claimed_high = sorted((float(first_raw), float(second_raw)))
            actual_low = min(float(bar.low) for bar in selected)
            actual_high = max(float(bar.high) for bar in selected)
            low_decimals = len(first_raw.partition(".")[2])
            high_decimals = len(second_raw.partition(".")[2])
            low_tolerance = 0.5 * (10 ** (-low_decimals)) + 1e-12
            high_tolerance = 0.5 * (10 ** (-high_decimals)) + 1e-12
            if (
                abs(claimed_low - actual_low) > low_tolerance
                or abs(claimed_high - actual_high) > high_tolerance
            ):
                errors.append(
                    f"{path} claims K{left}-K{right} price range "
                    f"{claimed_low:.12g}-{claimed_high:.12g}, but frozen window is "
                    f"{actual_low:.12g}-{actual_high:.12g}"
                )
    return errors


def _validate_explicit_direction_runs(value: Any, frame: Any) -> list[str]:
    """Check locally bound all-bull/all-bear candle claims against OHLC."""
    if frame is None:
        return []
    bars = {
        int(getattr(bar, "seq", 0)): bar
        for bar in getattr(frame, "bars", ()) or ()
        if int(getattr(bar, "seq", 0) or 0) > 0
    }
    errors: list[str] = []
    seen: set[tuple[str, int, int, str]] = set()
    for path, text in _narrative_strings(value):
        clauses = [
            clause.strip()
            for clause in _FACT_CLAUSE_SPLIT_RE.split(text)
            if clause.strip()
        ] or [text]
        for clause in clauses:
            range_matches = list(K_RANGE_RE.finditer(clause))
            for index, match in enumerate(range_matches):
                # A candle-run assertion belongs only to the immediately
                # preceding range. Price-path language such as "continuous
                # decline" is contextual PA interpretation, not a claim that
                # every candle has the same direction.
                scope_end = (
                    range_matches[index + 1].start()
                    if index + 1 < len(range_matches)
                    else len(clause)
                )
                local_scope = clause[match.end() : min(scope_end, match.end() + 32)]
                expected = (
                    "bullish"
                    if _EXPLICIT_CONTINUOUS_BULL_BARS_RE.search(local_scope)
                    else "bearish"
                    if _EXPLICIT_CONTINUOUS_BEAR_BARS_RE.search(local_scope)
                    else None
                )
                if expected is None:
                    continue
                left, right = int(match.group(1)), int(match.group(2))
                low, high = min(left, right), max(left, right)
                key = (path, low, high, expected)
                if key in seen:
                    continue
                seen.add(key)
                selected = [bars.get(seq) for seq in range(low, high + 1)]
                if not selected or any(bar is None for bar in selected):
                    continue
                valid = all(
                    float(bar.close) > float(bar.open)
                    if expected == "bullish"
                    else float(bar.close) < float(bar.open)
                    for bar in selected
                )
                if not valid:
                    errors.append(
                        f"{path} continuous {expected} claim for K{left}-K{right} "
                        "conflicts with frozen candle directions"
                    )
    return errors


def _validate_stage1_narrative_geometry(
    stage1: dict[str, Any],
    frame: Any,
) -> list[str]:
    """Report explicit inside/outside-bar claims with a unique bar owner."""
    if frame is None:
        return []
    from pa_agent.ai.kline_features import compute_kline_geometry_features

    features = {feature.seq: feature for feature in compute_kline_geometry_features(frame)}
    narratives: list[tuple[str, object, int | None]] = []
    owned_paths: set[str] = set()
    for index, item in enumerate(stage1.get("bar_by_bar_summary") or []):
        if isinstance(item, dict):
            path = f"bar_by_bar_summary[{index}].reason"
            narratives.append(
                (
                    path,
                    item.get("reason"),
                    _parse_k_seq(item.get("bar")),
                )
            )
            owned_paths.add(path)
    bar_analysis = stage1.get("bar_analysis")
    if isinstance(bar_analysis, dict):
        signal_bar = bar_analysis.get("signal_bar")
        if isinstance(signal_bar, dict):
            path = "bar_analysis.signal_bar.reason"
            narratives.append(
                (
                    path,
                    signal_bar.get("reason"),
                    _parse_k_seq(signal_bar.get("bar")),
                )
            )
            owned_paths.add(path)

    narratives.extend(
        (path, text, None)
        for path, text in _narrative_strings(stage1)
        if path not in owned_paths
    )

    errors: list[str] = []
    for path, raw_text, target_seq in narratives:
        text = str(raw_text or "")
        errors.extend(
            _validate_explicit_geometry_pairs(
                text,
                path=path,
                frame=frame,
            )
        )
        if target_seq is None:
            references = {
                int(match.group(1)) for match in K_REFERENCE_RE.finditer(text)
            }
            target_seq = next(iter(references)) if len(references) == 1 else None
        feature = features.get(target_seq) if target_seq is not None else None
        if feature is None:
            continue
        inside_match = _INSIDE_PREVIOUS_CLAIM_RE.search(text)
        outside_match = _OUTSIDE_PREVIOUS_CLAIM_RE.search(text)
        if (
            inside_match
            and not _geometry_claim_is_negated(text, inside_match)
            and feature.relation_to_previous != "contained_by_previous"
        ):
            errors.append(
                f"{path} claims K{target_seq} is contained_by_previous, "
                "but exact program geometry disagrees"
            )
        if (
            outside_match
            and not _geometry_claim_is_negated(text, outside_match)
            and feature.relation_to_previous != "contains_previous"
        ):
            errors.append(
                f"{path} claims K{target_seq} contains_previous, "
                "but exact program geometry disagrees"
            )
    return errors


def _validate_explicit_geometry_pairs(
    text: str,
    *,
    path: str,
    frame: Any,
) -> list[str]:
    bars = {
        int(getattr(bar, "seq", 0)): bar
        for bar in getattr(frame, "bars", ()) or ()
        if int(getattr(bar, "seq", 0) or 0) > 0
    }
    errors: list[str] = []
    for relation, pattern in (
        ("contained_by", _INSIDE_EXPLICIT_PAIR_RE),
        ("contains", _OUTSIDE_EXPLICIT_PAIR_RE),
    ):
        for match in pattern.finditer(text):
            if _geometry_claim_is_negated(text, match):
                continue
            groups = [value for value in match.groups() if value is not None]
            target_seq = int(groups[0])
            reference_seq = int(groups[1])
            target = bars.get(target_seq)
            reference = bars.get(reference_seq)
            if target is None or reference is None:
                continue
            contained = (
                float(target.high) <= float(reference.high)
                and float(target.low) >= float(reference.low)
            )
            contains = (
                float(target.high) >= float(reference.high)
                and float(target.low) <= float(reference.low)
            )
            valid = contained if relation == "contained_by" else contains
            if not valid:
                errors.append(
                    f"{path} claims K{target_seq} {relation} K{reference_seq}, "
                    "but exact program geometry disagrees"
                )
    return errors


def _geometry_claim_is_negated(text: str, match: re.Match[str]) -> bool:
    """Conservatively exclude negated prose from affirmative fact checks."""
    nearby = text[max(0, match.start() - 24) : match.end()]
    return bool(_NEGATION_NEAR_GEOMETRY_RE.search(nearby))


def _collect_stage1_narrative_warnings(
    stage1: dict[str, Any],
    frame: Any,
) -> list[str]:
    errors: list[str] = []
    evidence = stage1.get("pa_evidence")
    declared_fact_refs = (
        {str(fact_id).upper() for fact_id in (evidence.get("fact_refs") or [])}
        if isinstance(evidence, dict)
        else set()
    )
    fact_catalog = None
    for path, text, target_seq in _stage1_narratives(stage1):
        if contains_retry_deliberation(text):
            errors.append(f"{path} contains retry repair deliberation instead of a final conclusion")
        if path == "htf_context":
            errors.extend(validate_htf_inference_only_narrative(text, path=path))
        elif (
            path == "bar_analysis.signal_bar.reason"
            or path.startswith("bar_by_bar_summary[")
            or path.startswith("gate_trace[")
        ):
            errors.extend(validate_pa_inference_only_narrative(text, path=path))
        if _PA_VOLUME_RE.search(text):
            errors.append(f"{path} violates the PA price-only evidence contract")
        if _VAGUE_NUMERIC_LEG_COUNT_RE.search(text) and not K_RANGE_RE.search(text):
            errors.append(f"{path} numeric leg count requires an explicit K#-K# range")
        local_claims = local_close_position_claim_kinds(text)
        clauses = [
            clause.strip()
            for clause in _FACT_CLAUSE_SPLIT_RE.split(text)
            if clause.strip()
        ] or [text]
        clause_claims = [
            (
                clause,
                negated_window_extreme_claim_kinds(clause),
                window_extreme_claim_kinds(clause),
            )
            for clause in clauses
        ]
        if (
            local_claims
            or any(negated or window for _, negated, window in clause_claims)
        ) and fact_catalog is None and frame is not None:
            from pa_agent.ai.fact_catalog import build_analysis_fact_catalog

            fact_catalog = build_analysis_fact_catalog(frame)
        errors.extend(
            _validate_local_close_position_claims(
                text,
                path=path,
                target_seq=target_seq,
                fact_catalog=fact_catalog,
            )
        )
        for clause, negated_window_claims, window_claims in clause_claims:
            errors.extend(
                _validate_negated_window_extreme_claims(
                    clause,
                    claims=negated_window_claims,
                    path=path,
                    target_seq=target_seq,
                    fact_catalog=fact_catalog,
                    declared_fact_refs=declared_fact_refs,
                )
            )
            if (
                window_claims
                and not _has_bounded_extreme_evidence(
                    clause,
                    claims=window_claims,
                    target_seq=target_seq,
                    fact_catalog=fact_catalog,
                    declared_fact_refs=declared_fact_refs,
                )
            ):
                errors.append(
                    f"{path} contains an unbounded new-high/new-low claim; cite a concrete "
                    "K#-K# comparison or a matching PA.PAIR/PA.WINDOW/PA.COMPARE fact"
                )
        errors.extend(_validate_count_restatements(text, path=path))
        errors.extend(_validate_bar_break_claims(text, path=path, frame=frame))
        errors.extend(_validate_ema_claim(text, path=path, frame=frame))
    errors.extend(_validate_tick_claims(stage1, frame))
    return errors


def _validate_stage1_structured_facts(
    stage1: dict[str, Any],
    frame: Any,
) -> list[str]:
    """Validate typed PA facts without interpreting free-form prose."""
    errors: list[str] = []
    errors.extend(_validate_pa_evidence_contract(stage1, frame))
    errors.extend(_validate_structure_level_sides(stage1, frame))
    errors.extend(_validate_spike_transition_contract(stage1, frame))
    return errors


def _has_bounded_extreme_evidence(
    text: str,
    *,
    claims: set[str],
    target_seq: int | None,
    fact_catalog: Any,
    declared_fact_refs: set[str],
) -> bool:
    """Accept an extreme only when a cited program fact proves it for this bar."""
    direct_pair = _direct_pair_relations(
        text,
        target_seq=target_seq,
        fact_catalog=fact_catalog,
    )
    if direct_pair is not None and all(direct_pair[claim] for claim in claims):
        return True
    if _explicit_ranges_support_extreme_claims(
        text,
        claims=claims,
        target_seq=target_seq,
        fact_catalog=fact_catalog,
    ):
        return True
    if fact_catalog is None or target_seq is None:
        return False
    if not claims:
        return False

    for match in _PA_BOUNDED_EXTREME_FACT_RE.finditer(text):
        fact_id = match.group(0).upper()
        if fact_id not in declared_fact_refs:
            continue
        try:
            fact = fact_catalog.fact(fact_id)
        except KeyError:
            continue
        value = fact.value if isinstance(fact.value, dict) else {}
        if fact.fact_type == "bar_to_prior_window":
            if target_seq != 1:
                continue
            supported = {
                "high": value.get("high_relation") == "above",
                "low": value.get("low_relation") == "below",
            }
        elif fact.fact_type == "rolling_price_window":
            target = f"K{target_seq}"
            supported = {
                "high": value.get("high_bars") == [target],
                "low": value.get("low_bars") == [target],
            }
        elif fact.fact_type == "adjacent_bar_comparison":
            supported = _pair_fact_support(fact, target_seq=target_seq)
            if supported is None:
                continue
        else:
            continue
        if all(supported[claim] for claim in claims):
            return True
    return False


def _validate_negated_window_extreme_claims(
    text: str,
    *,
    claims: set[str],
    path: str,
    target_seq: int | None,
    fact_catalog: Any,
    declared_fact_refs: set[str],
) -> list[str]:
    if not claims:
        return []
    direct_pair = _direct_pair_relations(
        text,
        target_seq=target_seq,
        fact_catalog=fact_catalog,
    )
    if direct_pair is not None:
        contradicted = {claim for claim in claims if direct_pair[claim]}
        if not contradicted:
            return []
        return [
            f"{path} negated extreme claim conflicts with program facts for "
            + "/".join(sorted(contradicted))
        ]
    cited_fact_refs = {
        match.group(0).upper()
        for match in _PA_BOUNDED_EXTREME_FACT_RE.finditer(text)
        if match.group(0).upper() in declared_fact_refs
    }
    if not K_RANGE_RE.search(text) and not cited_fact_refs:
        return [
            f"{path} contains an unbounded negated new-high/new-low claim; cite a "
            "concrete K#-K# comparison or a matching PA.WINDOW/PA.COMPARE fact"
        ]
    if fact_catalog is None or target_seq is None:
        return []
    contradicted = {
        claim
        for claim in claims
        if _has_bounded_extreme_evidence(
            text,
            claims={claim},
            target_seq=target_seq,
            fact_catalog=fact_catalog,
            declared_fact_refs=declared_fact_refs,
        )
    }
    if not contradicted:
        return []
    return [
        f"{path} negated extreme claim conflicts with program facts for "
        + "/".join(sorted(contradicted))
    ]


def _direct_pair_relations(
    text: str,
    *,
    target_seq: int | None,
    fact_catalog: Any,
) -> dict[str, bool] | None:
    """Return exact target-vs-one-reference relations for a bounded pair claim."""
    if target_seq is None or fact_catalog is None:
        return None
    references = {
        int(match.group(1)) for match in K_REFERENCE_RE.finditer(text)
    }
    target_is_explicit = target_seq in references
    references.discard(target_seq)
    if len(references) != 1:
        return None
    if not target_is_explicit:
        return None
    reference_seq = next(iter(references))
    try:
        target = fact_catalog.fact(f"PA.BAR.K{target_seq}").value
        reference = fact_catalog.fact(f"PA.BAR.K{reference_seq}").value
    except KeyError:
        return None
    if not isinstance(target, dict) or not isinstance(reference, dict):
        return None
    return {
        "high": float(target["high"]) > float(reference["high"]),
        "low": float(target["low"]) < float(reference["low"]),
    }


def _pair_fact_support(fact: Any, *, target_seq: int) -> dict[str, bool] | None:
    value = fact.value if isinstance(fact.value, dict) else {}
    newer_seq = _parse_k_seq(value.get("newer_bar"))
    older_seq = _parse_k_seq(value.get("older_bar"))
    if newer_seq is None or older_seq is None or target_seq not in {newer_seq, older_seq}:
        return None
    high_relation = value.get("newer_high_vs_older_high")
    low_relation = value.get("newer_low_vs_older_low")
    if target_seq == newer_seq:
        return {
            "high": high_relation == "above",
            "low": low_relation == "below",
        }
    return {
        "high": high_relation == "below",
        "low": low_relation == "above",
    }


def _explicit_ranges_support_extreme_claims(
    text: str,
    *,
    claims: set[str],
    target_seq: int | None,
    fact_catalog: Any,
) -> bool:
    if fact_catalog is None or target_seq is None:
        return False
    try:
        target = fact_catalog.fact(f"PA.BAR.K{target_seq}").value
    except KeyError:
        return False
    if not isinstance(target, dict):
        return False
    for match in K_RANGE_RE.finditer(text):
        left, right = int(match.group(1)), int(match.group(2))
        seqs = range(min(left, right), max(left, right) + 1)
        reference: list[dict[str, Any]] = []
        for seq in seqs:
            if seq == target_seq:
                continue
            try:
                value = fact_catalog.fact(f"PA.BAR.K{seq}").value
            except KeyError:
                continue
            if isinstance(value, dict):
                reference.append(value)
        if not reference:
            continue
        supported = {
            "high": float(target["high"]) > max(float(item["high"]) for item in reference),
            "low": float(target["low"]) < min(float(item["low"]) for item in reference),
        }
        if all(supported[claim] for claim in claims):
            return True
    return False


def _validate_local_close_position_claims(
    text: str,
    *,
    path: str,
    target_seq: int | None,
    fact_catalog: Any,
) -> list[str]:
    claims = local_close_position_claim_kinds(text)
    if not claims or fact_catalog is None:
        return []
    if target_seq is None:
        references = {
            int(match.group(1)) for match in K_REFERENCE_RE.finditer(text)
        }
        target_seq = next(iter(references)) if len(references) == 1 else None
    if target_seq is None:
        return []
    try:
        value = fact_catalog.fact(f"PA.BAR.K{target_seq}").value
    except KeyError:
        return []
    close_position = value.get("close_position") if isinstance(value, dict) else None
    if close_position is None:
        return []
    position = float(close_position)
    close_zone = str(value.get("close_zone") or "")
    if not close_zone:
        close_zone = classify_close_position_zone(position)
    if (
        len(claims) > 1
        or ("low" in claims and close_zone != "lower_quartile")
        or ("high" in claims and close_zone != "upper_quartile")
    ):
        return [
            f"{path} local close position conflicts with program PA.BAR.K{target_seq} "
            f"close_position={position:.3f}, close_zone={close_zone}"
        ]
    return []


def _validate_structure_level_sides(stage1: dict[str, Any], frame: Any) -> list[str]:
    """Validate only numeric level geometry; the LLM still owns structural meaning."""
    if frame is None:
        return []
    latest = next(
        (
            bar
            for bar in getattr(frame, "bars", ())
            if int(getattr(bar, "seq", 0)) == 1 and bool(getattr(bar, "closed", False))
        ),
        None,
    )
    if latest is None:
        return []
    close = float(latest.close)
    errors: list[str] = []
    for field, kind in (
        ("support_levels", "support"),
        ("resistance_levels", "resistance"),
    ):
        raw_levels = stage1.get(field) or []
        if len(raw_levels) > 3:
            errors.append(f"{field} may contain at most 3 levels")
        midpoints: list[float] = []
        for index, raw in enumerate(raw_levels):
            bounds = parse_price_level_bounds(raw)
            if bounds is None:
                errors.append(
                    f"{field}[{index}] must be a positive numeric price or price range"
                )
                continue
            low, high = bounds
            midpoints.append((low + high) / 2.0)
            if kind == "support" and high >= close:
                errors.append(f"{field}[{index}] must be strictly below K1.close")
            if kind == "resistance" and low <= close:
                errors.append(f"{field}[{index}] must be strictly above K1.close")
        if kind == "support" and any(
            farther >= nearer
            for nearer, farther in zip(midpoints, midpoints[1:], strict=False)
        ):
            errors.append("support_levels must be ordered near-to-far below K1.close")
        if kind == "resistance" and any(
            farther <= nearer
            for nearer, farther in zip(midpoints, midpoints[1:], strict=False)
        ):
            errors.append("resistance_levels must be ordered near-to-far above K1.close")
    return errors


def _validate_count_restatements(
    text: str,
    *,
    path: str,
) -> list[str]:
    """Keep exact counts in PA.WINDOW facts instead of parsing prose scopes."""
    errors: list[str] = []
    if _EXACT_BAR_COUNT_RE.search(text):
        errors.append(
            f"{path} bar count must cite PA.WINDOW instead of restating "
            "an exact bar count"
        )
    for label, patterns in (
        ("bullish", _BULL_COUNT_PATTERNS),
        ("bearish", _BEAR_COUNT_PATTERNS),
    ):
        for pattern in patterns:
            count_match = next(
                (
                    match
                    for match in pattern.finditer(text)
                    if not re.search(
                        r"(?<![A-Za-z0-9_])K\s*$|\u7b2c\s*$",
                        text[: match.start(1)],
                        re.IGNORECASE,
                    )
                ),
                None,
            )
            if count_match:
                errors.append(
                    f"{path} {label} count must cite PA.WINDOW instead of restating "
                    "an exact bar count"
                )
                break
    if _EXPLICIT_CONTINUOUS_BULL_BARS_RE.search(text):
        errors.append(
            f"{path} continuous bullish claim must cite PA.WINDOW instead of "
            "restating an exact run"
        )
    if _EXPLICIT_CONTINUOUS_BEAR_BARS_RE.search(text):
        errors.append(
            f"{path} continuous bearish claim must cite PA.WINDOW instead of "
            "restating an exact run"
        )
    return errors


def _validate_bar_break_claims(
    text: str,
    *,
    path: str,
    frame: Any,
) -> list[str]:
    if frame is None:
        return []
    bars = {int(bar.seq): bar for bar in getattr(frame, "bars", ())}
    errors: list[str] = []
    for match in _BAR_BREAK_RANGE_RE.finditer(text):
        if _NEGATED_BAR_BREAK_RE.search(match.group(0)):
            continue
        target_seq = int(match.group(1))
        left = int(match.group(2))
        right = int(match.group(3))
        field = "high" if match.group(4).lower() in {"high", "\u9ad8\u70b9"} else "low"
        target = bars.get(target_seq)
        reference = [
            bars[seq]
            for seq in range(min(left, right), max(left, right) + 1)
            if seq in bars and seq != target_seq
        ]
        if target is None or not reference:
            continue
        target_value = float(getattr(target, field))
        boundary = (
            max(float(item.high) for item in reference)
            if field == "high"
            else min(float(item.low) for item in reference)
        )
        valid = target_value > boundary if field == "high" else target_value < boundary
        if not valid:
            errors.append(
                f"{path} K{target_seq} did not break the K{left}-K{right} {field}; "
                "the claim conflicts with program geometry"
            )
    return errors


def _validate_pa_evidence_contract(stage1: dict[str, Any], frame: Any) -> list[str]:
    evidence = stage1.get("pa_evidence")
    if not isinstance(evidence, dict) or frame is None:
        return []
    from pa_agent.ai.fact_catalog import build_analysis_fact_catalog
    from pa_agent.ai.pa_theory import PA_THEORY_RULE_IDS

    catalog = build_analysis_fact_catalog(frame)
    valid_ids = catalog.fact_ids(view="pa")
    errors = [
        f"pa_evidence.fact_refs contains unknown fact ID {fact_id}"
        for fact_id in evidence.get("fact_refs", [])
        if str(fact_id) not in valid_ids
    ]
    errors.extend(
        f"pa_evidence.theory_rule_ids contains unknown theory rule ID {rule_id}"
        for rule_id in evidence.get("theory_rule_ids", [])
        if str(rule_id) not in PA_THEORY_RULE_IDS
    )
    return errors


def _validate_spike_transition_contract(
    stage1: dict[str, Any],
    frame: Any = None,
) -> list[str]:
    evidence = stage1.get("pa_evidence")
    spike = evidence.get("spike_analysis") if isinstance(evidence, dict) else None
    if not isinstance(spike, dict):
        return []
    state = spike.get("post_anchor_state")
    errors: list[str] = []
    known_seqs = {
        int(getattr(bar, "seq", 0))
        for bar in (getattr(frame, "bars", ()) if frame is not None else ())
        if int(getattr(bar, "seq", 0) or 0) > 0
    }
    anchor_raw = spike.get("anchor_bar")
    anchor_seq = _parse_k_seq(anchor_raw)
    pause_raw = spike.get("first_pause_or_pullback_bar")
    pause_seq = _parse_k_seq(pause_raw)
    if anchor_seq is not None and known_seqs and anchor_seq not in known_seqs:
        errors.append(
            f"pa_evidence.spike_analysis.anchor_bar K{anchor_seq} not found "
            "in current K-line frame"
        )
    if pause_seq is not None and known_seqs and pause_seq not in known_seqs:
        errors.append(
            "pa_evidence.spike_analysis.first_pause_or_pullback_bar "
            f"K{pause_seq} not found in current K-line frame"
        )
    if (
        state == "no_closed_bar_after_anchor"
        and anchor_seq is not None
        and known_seqs
        and anchor_seq != min(known_seqs)
    ):
        errors.append(
            "post_anchor_state=no_closed_bar_after_anchor requires anchor_bar=K1"
        )
    if (
        state in {"continuation_only", "pause_or_pullback_present"}
        and anchor_seq is not None
        and known_seqs
        and anchor_seq == min(known_seqs)
    ):
        errors.append(
            f"post_anchor_state={state} requires at least one later closed bar "
            "after anchor_bar"
        )
    if (
        pause_seq is not None
        and anchor_seq is not None
        and pause_seq >= anchor_seq
    ):
        errors.append(
            "first_pause_or_pullback_bar must be more recent than anchor_bar "
            "(K sequence must be smaller)"
        )
    return errors


def _validate_ema_claim(text: str, *, path: str, frame: Any) -> list[str]:
    if frame is None or not _EMA_SLOPE_WORD_RE.search(text):
        return []
    from pa_agent.ai.market_features import compute_simple_market_features

    facts = compute_simple_market_features(frame)
    deltas = [value for value in (facts.ema_delta_5, facts.ema_delta_10) if value is not None]
    errors: list[str] = []
    if deltas and all(value > 0 for value in deltas) and _EMA_DOWN_RE.search(text):
        errors.append(f"{path} EMA slope direction conflicts with program EMA deltas")
    if deltas and all(value < 0 for value in deltas) and _EMA_UP_RE.search(text):
        errors.append(f"{path} EMA slope direction conflicts with program EMA deltas")

    scrubbed = re.sub(r"EMA\s*20|K\s*\d+", "", text, flags=re.IGNORECASE)
    stated = [float(match.group(0)) for match in _NUMBER_RE.finditer(scrubbed)]
    for number in stated:
        if not any(_approximately_equal(number, delta) for delta in deltas):
            errors.append(
                f"{path} EMA slope number is not one of the program EMA delta facts"
            )
            break
    return errors


def _validate_tick_claims(value: Any, frame: Any) -> list[str]:
    metadata = getattr(frame, "data_metadata", {}) if frame is not None else {}
    metadata = metadata if isinstance(metadata, dict) else {}
    try:
        tick_size = float(metadata.get("tick_size"))
    except (TypeError, ValueError):
        tick_size = 0.0
    verified = tick_size > 0 and bool(
        metadata.get("tick_size_verified") or metadata.get("tick_size_source")
    )
    if verified:
        return []
    errors: list[str] = []
    for path, text in _narrative_strings(value):
        if _ONE_TICK_RE.search(text) and not _TICK_DISCLOSURE_RE.search(text):
            errors.append(
                f"{path} claims one tick without verified tick size or an execution-time disclosure"
            )
    return errors


def _stage1_narratives(stage1: dict[str, Any]) -> list[tuple[str, str, int | None]]:
    values: list[tuple[str, str, int | None]] = []
    for key in ("htf_context", "entry_setup", "risk_warning"):
        values.append((key, str(stage1.get(key) or ""), None))
    for index, value in enumerate(stage1.get("key_signals") or []):
        values.append((f"key_signals[{index}]", str(value or ""), None))
    bar_analysis = stage1.get("bar_analysis")
    if isinstance(bar_analysis, dict):
        signal = bar_analysis.get("signal_bar")
        if isinstance(signal, dict):
            values.append(
                (
                    "bar_analysis.signal_bar.reason",
                    str(signal.get("reason") or ""),
                    _parse_k_seq(signal.get("bar")),
                )
            )
    for key in ("bar_by_bar_summary", "gate_trace"):
        for index, item in enumerate(stage1.get(key) or []):
            if isinstance(item, dict):
                target_seq = _parse_k_seq(item.get("bar")) if key == "bar_by_bar_summary" else None
                values.append(
                    (f"{key}[{index}].reason", str(item.get("reason") or ""), target_seq)
                )
    return values


def _narrative_strings(value: Any, path: str = "") -> list[tuple[str, str]]:
    if isinstance(value, dict):
        return [
            pair
            for key, item in value.items()
            for pair in _narrative_strings(item, f"{path}.{key}" if path else str(key))
        ]
    if isinstance(value, list):
        return [
            pair
            for index, item in enumerate(value)
            for pair in _narrative_strings(item, f"{path}[{index}]")
        ]
    return [(path, value)] if isinstance(value, str) else []


def _approximately_equal(left: float, right: float) -> bool:
    tolerance = max(1e-6, abs(right) * 1e-4)
    return abs(left - right) <= tolerance


def validate_incremental_stage1_coherence(
    stage1: dict[str, Any],
    *,
    new_bar_count: int,
    previous_stage1: dict[str, Any] | None = None,
) -> list[str]:
    if new_bar_count <= 0:
        return []
    delta = stage1.get("incremental_delta")
    if not isinstance(delta, dict):
        return ["incremental stage1 requires incremental_delta"]
    errors: list[str] = []
    if delta.get("new_closed_bars") != [f"K{i}" for i in range(1, new_bar_count + 1)]:
        errors.append("incremental_delta.new_closed_bars 与程序观测的新收盘K线不一致")
    if not str(delta.get("summary") or "").strip():
        errors.append("incremental_delta.summary 不能为空")
    if isinstance(previous_stage1, dict):
        expected = {
            key
            for key in ("cycle_position", "direction", "gate_result")
            if stage1.get(key) != previous_stage1.get(key)
        }
        actual = set(delta.get("changed_fields") or [])
        if not expected.issubset(actual):
            errors.append("incremental_delta.changed_fields 未记录实际发生的核心字段变化")
    return errors


def validate_skipped_node_consistency(
    trace: list[dict[str, Any]] | None,
    *,
    path_prefix: str,
    mandatory_nodes: tuple[str, ...] = (),
) -> list[str]:
    if not isinstance(trace, list):
        return []
    errors: list[str] = []
    for index, item in enumerate(trace):
        if not isinstance(item, dict) or not item.get("skipped"):
            continue
        if item.get("answer") != "不适用":
            errors.append(f"{path_prefix}[{index}] skipped=true 时 answer 必须为 不适用")
        if str(item.get("node_id") or "") in mandatory_nodes:
            errors.append(f"{path_prefix} 必选节点 {item.get('node_id')} 不得跳过")
    return errors


def validate_trace_bars_in_frame(
    trace: list[dict[str, Any]] | None,
    *,
    kline_frame: Any,
    path_prefix: str,
) -> list[str]:
    maximum = _max_bar_seq(kline_frame)
    if maximum is None:
        return []
    errors: list[str] = []
    for index, item in enumerate(trace or []):
        if not isinstance(item, dict):
            continue
        for seq in _bar_range_seqs(normalize_bar_range(item)):
            if not 1 <= seq <= maximum:
                errors.append(
                    f"{path_prefix}[{index}].bar_range 引用 K{seq}，当前仅有 K1-K{maximum}"
                )
    return errors


def _validate_trace(trace: list[dict[str, Any]], *, kline_frame: Any, path: str) -> list[str]:
    errors: list[str] = []
    for index, item in enumerate(trace):
        if isinstance(item, dict):
            reason = str(item.get("reason") or "").strip()
            if not item.get("skipped") and reason in {"", "-", "—"}:
                errors.append(f"{path}[{index}].reason is required for a non-skipped node")
            bar_range = normalize_bar_range(item)
            allowed = _bar_range_seqs(bar_range)
            cited = {
                int(match.group(1))
                for match in K_REFERENCE_RE.finditer(str(item.get("reason") or ""))
            }
            outside = sorted(cited - allowed) if allowed else []
            if outside:
                refs = ", ".join(f"K{number}" for number in outside)
                errors.append(
                    f"{path}[{index}].reason 引用了 bar_range={bar_range} 之外的 {refs}"
                )
    errors.extend(validate_trace_bars_in_frame(trace, kline_frame=kline_frame, path_prefix=path))
    return errors


def _trace_node_ids(trace: list[dict[str, Any]]) -> set[str]:
    return {str(item.get("node_id")) for item in trace if isinstance(item, dict)}


def _find_trace_item(trace: list[dict[str, Any]], node_id: str) -> dict[str, Any] | None:
    return next(
        (item for item in trace if isinstance(item, dict) and str(item.get("node_id")) == node_id),
        None,
    )


def _normalize_cycle_branch(value: Any) -> str | None:
    if value is None:
        return None
    key = str(value).strip().lower().replace(" ", "_")
    return _CYCLE_BRANCH_ALIASES.get(key, key) or None


def _normalize_direction_branch(value: Any) -> str | None:
    if value is None:
        return None
    key = str(value).strip().lower()
    return _DIRECTION_BRANCH_ALIASES.get(key, key) or None


def _max_bar_seq(frame: Any) -> int | None:
    bars = getattr(frame, "bars", None) if frame is not None else None
    if not bars:
        return None
    return max(int(bar.seq) for bar in bars)


def _parse_k_seq(value: Any) -> int | None:
    match = re.fullmatch(r"K\s*(\d+)", str(value or "").strip(), re.I)
    return int(match.group(1)) if match else None


def _bar_range_seqs(value: str) -> set[int]:
    numbers = [int(number) for number in re.findall(r"K\s*(\d+)", value or "", re.I)]
    if len(numbers) == 1:
        return {numbers[0]}
    if len(numbers) >= 2:
        return set(range(min(numbers[:2]), max(numbers[:2]) + 1))
    return set()
