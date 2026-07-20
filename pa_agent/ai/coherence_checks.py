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
_CONTINUOUS_BULL_RE = re.compile(
    r"continuous(?:ly)?\s+bull(?:ish)?|consecutive\s+bull(?:ish)?|"
    r"\u8fde\u7eed.{0,8}(?:\u591a\u5934|\u4e0a\u6da8|\u9633\u7ebf)",
    re.IGNORECASE,
)
_CONTINUOUS_BEAR_RE = re.compile(
    r"continuous(?:ly)?\s+bear(?:ish)?|consecutive\s+bear(?:ish)?|"
    r"\u8fde\u7eed.{0,8}(?:\u7a7a\u5934|\u4e0b\u8dcc|\u9634\u7ebf)",
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
_BAR_BREAK_RANGE_RE = re.compile(
    r"(?<![A-Za-z0-9_])K\s*(\d+)(?![A-Za-z0-9_.]).{0,32}?"
    r"(?:broke|breaks?|exceeded|\u7a81\u7834|\u8d8a\u8fc7).{0,20}?"
    r"(?<![A-Za-z0-9_])K\s*(\d+)\s*[-\u2013\u2014\uFF0D]\s*K?\s*(\d+)"
    r"(?![A-Za-z0-9_.]).{0,12}?"
    r"(high|low|\u9ad8\u70b9|\u4f4e\u70b9)",
    re.IGNORECASE,
)
_PRICE_LEVEL_RE = re.compile(
    r"^\s*(?P<first>\d+(?:\.\d+)?)"
    r"(?:\s*[-~\uFF0D\u2014\u5230\u81F3]\s*(?P<second>\d+(?:\.\d+)?))?\s*$"
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
        expected = min(BAR_BY_BAR_TARGET_COUNT, bar_count) if bar_count else None
        if expected is not None and len(summary) != expected:
            errors.append(
                f"bar_by_bar_summary 有 {len(summary)} 条，应为 {expected} 条"
            )
        for index, item in enumerate(summary):
            if not isinstance(item, dict):
                continue
            seq = _parse_k_seq(item.get("bar"))
            if seq is None or (bar_count and not 1 <= seq <= bar_count):
                errors.append(f"bar_by_bar_summary[{index}].bar 不在当前K线窗口")
    errors.extend(_validate_stage1_bar_geometry(stage1, kline_frame))
    errors.extend(_validate_stage1_fact_language(stage1, kline_frame))
    return errors


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
    price_only_stage2 = {
        key: value for key, value in stage2.items() if key != "vpa_impact"
    }
    for path, text in _narrative_strings(price_only_stage2):
        if _PA_VOLUME_RE.search(text):
            errors.append(f"{path} violates the PA price-only evidence contract")
    errors.extend(_validate_stage2_bar_references(stage2, kline_frame))
    errors.extend(_validate_tick_claims(stage2, kline_frame))
    return errors


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
        elif label == "inside" and feature.relation_to_previous != "contained_by_previous":
            errors.append(f"{path}=inside conflicts with exact program geometry")
        elif label in {"outside_bull", "outside_bear"}:
            expected_direction = "bullish" if label == "outside_bull" else "bearish"
            if (
                feature.relation_to_previous != "contains_previous"
                or feature.candle_direction != expected_direction
            ):
                errors.append(f"{path}={label} conflicts with exact program geometry")
    return errors


def _validate_stage1_fact_language(stage1: dict[str, Any], frame: Any) -> list[str]:
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
    errors.extend(_validate_pa_evidence_contract(stage1, frame))
    errors.extend(_validate_structure_level_sides(stage1, frame))
    errors.extend(_validate_spike_transition_contract(stage1))
    errors.extend(_validate_tick_claims(stage1, frame))
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
            match = _PRICE_LEVEL_RE.fullmatch(str(raw or ""))
            if match is None:
                errors.append(
                    f"{field}[{index}] must be a positive numeric price or price range"
                )
                continue
            first = float(match.group("first"))
            second = float(match.group("second") or first)
            if first <= 0 or second <= 0:
                errors.append(
                    f"{field}[{index}] must be a positive numeric price or price range"
                )
                continue
            low, high = sorted((first, second))
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
    if _CONTINUOUS_BULL_RE.search(text):
        errors.append(
            f"{path} continuous bullish claim must cite PA.WINDOW instead of "
            "restating an exact run"
        )
    if _CONTINUOUS_BEAR_RE.search(text):
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


def _validate_spike_transition_contract(stage1: dict[str, Any]) -> list[str]:
    evidence = stage1.get("pa_evidence")
    spike = evidence.get("spike_analysis") if isinstance(evidence, dict) else None
    if not isinstance(spike, dict):
        return []
    state = spike.get("post_anchor_state")
    errors: list[str] = []
    if state == "pause_or_pullback_present":
        if stage1.get("spike_stage") == "active":
            errors.append(
                "spike_stage=active conflicts with pa_evidence pause_or_pullback_present"
            )
        if stage1.get("market_phase") != "transitioning":
            errors.append(
                "market_phase must be transitioning after the first identified spike pause/pullback"
            )
        if not spike.get("first_pause_or_pullback_bar"):
            errors.append(
                "pa_evidence.spike_analysis requires first_pause_or_pullback_bar for a transition"
            )
    if state == "continuation_only" and spike.get("first_pause_or_pullback_bar") is not None:
        errors.append(
            "first_pause_or_pullback_bar must be null when post_anchor_state=continuation_only"
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
