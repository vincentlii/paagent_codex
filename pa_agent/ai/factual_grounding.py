"""Guards for narrative fields that are designated as inference-only.

These checks do not decide market meaning. They keep raw numeric market facts in
program-owned tables and require the model to express only contextual inference
plus explicit K-line references in selected narrative fields.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from pa_agent.ai.kline_references import (
    K_REFERENCE_RE,
    REFERENCE_TOKEN_RE,
    parse_k_bar_range_bounds,
)
from pa_agent.ai.vpa_measurements import net_close_progress_direction_relation

_NUMERIC_LITERAL_RE = re.compile(
    r"(?<![A-Za-z0-9_])[-+]?(?:\d+(?:\.\d+)?|\.\d+)(?:\s*%|\s*x)?"
    r"(?![A-Za-z0-9_])",
    re.IGNORECASE,
)
_PA_DECIMAL_MARKET_LITERAL_RE = re.compile(
    r"(?<![A-Za-z0-9_])[-+]?(?:\d+\.\d+|\.\d+)(?![A-Za-z0-9_])"
)
_PA_SCALED_MARKET_LITERAL_RE = re.compile(
    r"(?<![A-Za-z0-9_])[-+]?(?:\d+(?:\.\d+)?|\.\d+)\s*(?:%|x)"
    r"(?![A-Za-z0-9_])",
    re.IGNORECASE,
)
_PA_FRACTION_MARKET_LITERAL_RE = re.compile(
    r"(?<![A-Za-z0-9_])\d+\s*/\s*\d+(?![A-Za-z0-9_])"
)
_PA_MARKET_LABEL_VALUE_RE = re.compile(
    r"(?:\b(?:open|high|low|close|price|volume|atr|ema\d*|spread|range|"
    r"body(?:_ratio)?|close_position|relative_volume)\b|"
    r"\u5f00\u76d8|\u6536\u76d8|\u6700\u9ad8\u70b9|\u6700\u4f4e\u70b9|"
    r"\u9ad8\u70b9|\u4f4e\u70b9|\u6210\u4ea4\u91cf|\u632f\u5e45|\u5b9e\u4f53)"
    r"(?:\s*(?:=|:|was|is|at|\u4e3a|\u662f)\s*|\s+)"
    r"[-+]?(?:\d+(?:\.\d+)?|\.\d+)(?![A-Za-z0-9_])",
    re.IGNORECASE,
)
_PA_THEORY_REFERENCE_RE = re.compile(
    r"(?:\b(?:node|section)\s*|\u8282\u70b9\s*|\u00a7\s*)"
    r"\d+(?:\.\d+)+",
    re.IGNORECASE,
)
_HTF_TIMEFRAME_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_])\d+\s*(?:seconds?|secs?|minutes?|mins?|hours?|hrs?|"
    r"days?|weeks?|months?|[smhdw]|秒|分钟|小时|日线?|周线?|月线?)"
    r"(?![A-Za-z0-9_])",
    re.IGNORECASE,
)
_HTF_INDICATOR_PERIOD_RE = re.compile(
    r"\b(?:EMA|SMA|ATR|RSI)\s*\d+(?![A-Za-z0-9_])",
    re.IGNORECASE,
)
_PA_CANONICAL_KEY_VALUE_RE = re.compile(
    r"\b(?:candle_direction|relation_to_previous|range_atr_ratio|body_ratio|"
    r"close_position|close_zone|direction_counts|max_consecutive|"
    r"(?:high|low|close)_relation|"
    r"newer_(?:high|low|close)_vs_older_(?:high|low|close))\s*(?:=|:)\s*",
    re.IGNORECASE,
)
_WINDOW_HIGH_EXTREME_RE = re.compile(
    r"\bnew\s+high\b|\bhighest\b|"
    r"\b(?:window|range)\s+(?:price\s+)?high\b|"
    r"\bhigh\s+of\s+(?:the\s+)?(?:K\s*\d+\s*[-\u2013\u2014\uFF0D]\s*K?\s*\d+\s+)?(?:window|range)\b|"
    r"\u65b0\u9ad8|\u521b\u9ad8|\u6700\u9ad8|\u5237\u65b0\u9ad8\u70b9|"
    r"(?:\u7a97\u53e3|\u533a\u95f4|\u524d\u7a97)(?:\u5185|\u7684)?(?:\u6700\u9ad8\u70b9|\u9ad8\u70b9)",
    re.IGNORECASE,
)
_WINDOW_LOW_EXTREME_RE = re.compile(
    r"\bnew\s+low\b|\blowest\b|"
    r"\b(?:window|range)\s+(?:price\s+)?low\b|"
    r"\blow\s+of\s+(?:the\s+)?(?:K\s*\d+\s*[-\u2013\u2014\uFF0D]\s*K?\s*\d+\s+)?(?:window|range)\b|"
    r"\u65b0\u4f4e|\u521b\u4f4e|\u6700\u4f4e|\u5237\u65b0\u4f4e\u70b9|"
    r"(?:\u7a97\u53e3|\u533a\u95f4|\u524d\u7a97)(?:\u5185|\u7684)?(?:\u6700\u4f4e\u70b9|\u4f4e\u70b9)",
    re.IGNORECASE,
)
_LOCAL_CLOSE_POSITION_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "high": (
        re.compile(
            r"(?:收盘(?:价|位置)?)(?:位于|处于|在|于)\s*"
            r"(?:本(?:根|棒|K线)?|该(?:根|棒|K线)?|自身)?(?:最高点|高点)",
            re.IGNORECASE,
        ),
        re.compile(
            r"(?:\u6536\u76d8(?:\u4ef7|\u4f4d\u7f6e)?|\u6536\u4e8e|\u6536\u5728).{0,28}?"
            r"(?:(?:\u63a5\u8fd1|\u9760\u8fd1|\u8d34\u8fd1|\u90bb\u8fd1)"
            r"(?:(?!\u7a97\u53e3|\u533a\u95f4|\u524d\u7a97|K\s*\d).){0,10}?"
            r"(?:\u672c(?:\u6839|\u68d2|K\u7ebf)?|\u8be5(?:\u6839|\u68d2|K\u7ebf)?|\u81ea\u8eab)?"
            r"(?:\u6700\u9ad8\u70b9|\u9ad8\u70b9)|"
            r"(?:\u672c(?:\u6839|\u68d2|K\u7ebf)?|\u8be5(?:\u6839|\u68d2|K\u7ebf)?|\u81ea\u8eab)"
            r"(?:\u6700\u9ad8\u70b9|\u9ad8\u70b9).{0,6}?(?:\u9644\u8fd1|\u4e00\u5e26))",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(?:close(?:d|s)?|closing)\b.{0,24}?"
            r"\b(?:near|close\s+to|at|toward(?:s)?)\b\s*"
            r"(?:(?:its|the|this)\s+)?(?:own\s+)?"
            r"\bhigh\b(?!\s+(?:of|in)\s+(?:the\s+)?K\s*\d)"
            r"(?:\s+(?:point|of\s+(?:the|this)\s+bar))?",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(?:close(?:d|s)?|closing)\b.{0,24}?"
            r"\b(?:near|close\s+to|at|toward(?:s)?)\b\s*"
            r"(?:(?:its|this\s+bar(?:'s)?|the\s+bar(?:'s)?)\s+(?:own\s+)?highest"
            r"(?:\s+point)?|the\s+highest\s+point\s+of\s+(?:the|this)\s+bar)",
            re.IGNORECASE,
        ),
    ),
    "low": (
        re.compile(
            r"(?:收盘(?:价|位置)?)(?:位于|处于|在|于)\s*"
            r"(?:本(?:根|棒|K线)?|该(?:根|棒|K线)?|自身)?(?:最低点|低点)",
            re.IGNORECASE,
        ),
        re.compile(
            r"(?:\u6536\u76d8(?:\u4ef7|\u4f4d\u7f6e)?|\u6536\u4e8e|\u6536\u5728).{0,28}?"
            r"(?:(?:\u63a5\u8fd1|\u9760\u8fd1|\u8d34\u8fd1|\u90bb\u8fd1)"
            r"(?:(?!\u7a97\u53e3|\u533a\u95f4|\u524d\u7a97|K\s*\d).){0,10}?"
            r"(?:\u672c(?:\u6839|\u68d2|K\u7ebf)?|\u8be5(?:\u6839|\u68d2|K\u7ebf)?|\u81ea\u8eab)?"
            r"(?:\u6700\u4f4e\u70b9|\u4f4e\u70b9)|"
            r"(?:\u672c(?:\u6839|\u68d2|K\u7ebf)?|\u8be5(?:\u6839|\u68d2|K\u7ebf)?|\u81ea\u8eab)"
            r"(?:\u6700\u4f4e\u70b9|\u4f4e\u70b9).{0,6}?(?:\u9644\u8fd1|\u4e00\u5e26))",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(?:close(?:d|s)?|closing)\b.{0,24}?"
            r"\b(?:near|close\s+to|at|toward(?:s)?)\b\s*"
            r"(?:(?:its|the|this)\s+)?(?:own\s+)?"
            r"\blow\b(?!\s+(?:of|in)\s+(?:the\s+)?K\s*\d)"
            r"(?:\s+(?:point|of\s+(?:the|this)\s+bar))?",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(?:close(?:d|s)?|closing)\b.{0,24}?"
            r"\b(?:near|close\s+to|at|toward(?:s)?)\b\s*"
            r"(?:(?:its|this\s+bar(?:'s)?|the\s+bar(?:'s)?)\s+(?:own\s+)?lowest"
            r"(?:\s+point)?|the\s+lowest\s+point\s+of\s+(?:the|this)\s+bar)",
            re.IGNORECASE,
        ),
    ),
}
_NEGATED_WINDOW_EXTREME_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "high": (
        re.compile(
            r"(?:\u672a\u80fd?|\u6ca1\u6709|\u5e76\u672a|\u5c1a\u672a|\u65e0\u6cd5|\u4e0d\u80fd|\u4e0d\u518d)"
            r".{0,8}?(?:\u521b(?:\u51fa)?\u65b0\u9ad8|\u5237\u65b0\u9ad8\u70b9|\u65b0\u9ad8)",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(?:did|does|do|has|have|had|was|were|is|are)?\s*not\b.{0,16}?"
            r"\bnew\s+high\b|\b(?:no|without)\s+(?:a\s+)?new\s+high\b|"
            r"\bfailed\s+to\s+(?:make|create|set)\s+(?:a\s+)?new\s+high\b",
            re.IGNORECASE,
        ),
    ),
    "low": (
        re.compile(
            r"(?:\u672a\u80fd?|\u6ca1\u6709|\u5e76\u672a|\u5c1a\u672a|\u65e0\u6cd5|\u4e0d\u80fd|\u4e0d\u518d)"
            r".{0,8}?(?:\u521b(?:\u51fa)?\u65b0\u4f4e|\u5237\u65b0\u4f4e\u70b9|\u65b0\u4f4e)",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(?:did|does|do|has|have|had|was|were|is|are)?\s*not\b.{0,16}?"
            r"\bnew\s+low\b|\b(?:no|without)\s+(?:a\s+)?new\s+low\b|"
            r"\bfailed\s+to\s+(?:make|create|set)\s+(?:a\s+)?new\s+low\b",
            re.IGNORECASE,
        ),
    ),
}
_STRONG_REPAIR_DELIBERATION_RE = re.compile(
    r"(?:核对(?:数据|事实)|上述(?:说法|描述)|前述(?:说法|描述)).{0,16}?(?:错误|不正确)|"
    r"(?:应改为|直接描述|最终改为)\s*[:：]|"
    r"\b(?:the\s+above|previous\s+(?:claim|statement)).{0,20}?"
    r"(?:wrong|incorrect)|\b(?:should\s+be\s+revised\s+to|final\s+rewrite)\s*:",
    re.IGNORECASE,
)
_REPAIR_DELIBERATION_MARKER_RE = re.compile(
    r"(?:修正|更正|核对(?:数据|事实)|应改为|直接描述)\s*[:：]|"
    r"\b(?:correction|recheck|revised\s+wording|rewrite)\s*:",
    re.IGNORECASE,
)
_VPA_HARD_CLAUSE_SPLIT_RE = re.compile(
    r"[\u3002\uFF01\uFF1F!?;\uFF1B\n]+|\.\s+"
)
_VPA_COMMA_SPLIT_RE = re.compile(r"[,\uFF0C]+")
_AMBIGUOUS_VOLUME_RE = re.compile(
    r"\b(?:low|high)[ -]?volume\b|\bvolume\s+(?:contract(?:ed|ion)?|expand(?:ed|sion)?)\b|"
    r"\bvolume\s+(?:hold(?:s|ing)?|maintain(?:s|ed|ing)?|increase[sd]?|decrease[sd]?)\b|"
    r"低量|高量|缩量|放量|(?:成交量|量能).{0,8}?(?:维持|萎缩|放大|收缩|扩张|增加|减少)",
    re.IGNORECASE,
)
_EXPLICIT_VOLUME_COMPARATOR_RE = re.compile(
    r"causal\s+baseline|rolling\s+baseline|previous\s+bar|prior\s+bar|"
    r"seasonal(?:ity)?|same[- ]slot|previous\s+window|prior\s+window|"
    r"因果基线|滚动基线|相对基线|前(?:一)?(?:根|棒)|季节|同时段|前窗",
    re.IGNORECASE,
)
_FUTURE_CLOSED_BAR_RE = re.compile(
    r"\b(?:next|future)\s+(?:fully\s+)?closed\s+(?:bar|candle)\b|"
    r"(?:下一根|未来).{0,8}?(?:已收盘|收盘后|闭合)(?:的)?"
    r"(?:\s*K\s*线|棒|bar)?",
    re.IGNORECASE,
)
_FUTURE_STRUCTURE_ANCHOR_RE = re.compile(
    r"(?:K\s*\d+).{0,8}?(?:high|low|高点|低点)|"
    r"(?:high|low|高点|低点).{0,8}?(?:of\s+)?K\s*\d+|"
    r"\b(?:support|resistance|range\s+(?:upper|lower)?\s*boundary|"
    r"breakout\s+level|retest\s+level|structure\s+level)\b|"
    r"支撑(?:位|带)?|阻力(?:位|带)?|区间(?:上沿|下沿|边界)|突破位|回测位|结构位",
    re.IGNORECASE,
)

_VPA_UNCLASSIFIED_SESSION_RE = re.compile(
    r"\b(?:asia|asian|london|new\s+york|tokyo|sydney)\b|"
    r"\u4e9a\u6d32|\u4f26\u6566|\u7ebd\u7ea6|\u4e1c\u4eac|\u6089\u5c3c",
    re.IGNORECASE,
)
_VPA_ACTOR_ATTRIBUTION_RE = re.compile(
    r"\binstitution(?:al)?\b|\bsmart\s+money\b|\bactive\s+(?:buyer|seller)s?\b|"
    r"\baggress(?:ive|or)\s+(?:buying|selling|side)\b|\bbuyer[- ]initiated\b|"
    r"\bseller[- ]initiated\b|\u673a\u6784|\u4e3b\u529b|\u806a\u660e\u94b1|"
    r"\u4e3b\u52a8\u4e70\u76d8|\u4e3b\u52a8\u5356\u76d8|\u4e70\u65b9\u4e3b\u52a8|"
    r"\u5356\u65b9\u4e3b\u52a8",
    re.IGNORECASE,
)
_VPA_PROXY_CERTAINTY_RE = re.compile(
    r"\bactive\s+demand\b|\bactive\s+supply\b|\bdemand\s+participation\b|"
    r"\bsupply\s+participation\b|"
    r"\b(?:bulls?|bears?)\s+(?:fully|aggressively|actively)\s+"
    r"(?:push(?:ed|ing)?|buy(?:ing)?|sell(?:ing)?|press(?:ed|ing)?)\b|"
    r"\u9700\u6c42\u6d3b\u8dc3|\u4f9b\u7ed9\u6d3b\u8dc3|"
    r"\u4e70\u65b9\u79ef\u6781\u53c2\u4e0e|\u5356\u65b9\u79ef\u6781\u53c2\u4e0e|"
    r"(?:\u591a\u5934|\u7a7a\u5934).{0,4}?"
    r"(?:\u5168\u529b|\u4e3b\u52a8|\u79ef\u6781).{0,4}?"
    r"(?:\u63a8\u52a8|\u8fdb\u653b|\u6253\u538b|\u629b\u552e|\u4e70\u5165|\u5356\u51fa)",
    re.IGNORECASE,
)
_VPA_FORMING_BAR_RE = re.compile(
    r"\bforming\s+bar\b|\bunclosed\s+bar\b|"
    r"(?<![A-Za-z0-9_])K\s*0(?![A-Za-z0-9_.])|"
    r"\u672a\u6536\u76d8|\u672a\u95ed\u5408",
    re.IGNORECASE,
)
_VPA_CAPABILITY_NEGATION_BEFORE_RE = re.compile(
    r"(?:\b(?:no\s+(?:evidence\s+(?:of|for)|basis\s+to)|"
    r"not(?!\s+only\b)|cannot|can't|could\s+not|does\s+not|do\s+not|"
    r"without|unable\s+to|insufficient\s+to)\b.{0,40}|"
    r"(?:\u65e0\u6cd5|\u4e0d\u80fd|\u4e0d\u53ef|\u4e0d\u5e94|\u5e76\u975e|"
    r"\u6ca1\u6709|\u672a\u80fd|\u4e0d\u8db3\u4ee5|\u4e0d\u652f\u6301|"
    r"\u4e0d\u5f97).{0,20})$",
    re.IGNORECASE,
)
_VPA_CAPABILITY_NEGATION_AFTER_RE = re.compile(
    r"^.{0,28}\b(?:cannot|can't|could\s+not|is\s+not|are\s+not|"
    r"cannot\s+be|can't\s+be|is\s+unknown|are\s+unknown|"
    r"is\s+unobservable|are\s+unobservable)\b|"
    r"^.{0,16}(?:\u65e0\u6cd5|\u4e0d\u80fd|\u4e0d\u53ef|\u5e76\u975e|"
    r"\u672a\u77e5|\u65e0\u6cd5\u786e\u8ba4|\u4e0d\u53ef\u786e\u8ba4)",
    re.IGNORECASE,
)
_VPA_CLAUSE_SPLIT_RE = re.compile(r"[\u3002\uFF01\uFF1F!?;\uFF1B\n]+|\.\s+")

_RELATION_CLAIMS: tuple[tuple[str, str, str, re.Pattern[str]], ...] = (
    (
        "net close progress direction",
        "net_close_progress_direction_relation",
        "opposite",
        re.compile(
            r"(?:two|the\s+two|both)\s+(?:comparison\s+)?windows?.{0,32}?"
            r"(?:opposite|different)\s+(?:net\s+)?(?:close\s+)?progress\s+directions?|"
            r"(?:两(?:个)?|这两(?:个)?)窗口.{0,24}?(?:净收盘|净)?(?:进展)?方向(?:相反|不同)",
            re.IGNORECASE,
        ),
    ),
    (
        "net close progress direction",
        "net_close_progress_direction_relation",
        "same",
        re.compile(
            r"(?:two|the\s+two|both)\s+(?:comparison\s+)?windows?.{0,32}?"
            r"(?:same|matching)\s+(?:net\s+)?(?:close\s+)?progress\s+directions?|"
            r"(?:两(?:个)?|这两(?:个)?)窗口.{0,24}?(?:净收盘|净)?(?:进展)?方向(?:相同|同向)",
            re.IGNORECASE,
        ),
    ),
    (
        "causal baseline",
        "volume_vs_baseline_relation",
        "below",
        re.compile(
            r"(?:below|lower\s+than).*?(?:causal|rolling)?\s*baseline|"
            r"(?:causal|rolling)?\s*baseline.*?(?:below|lower)|"
            r"(?:低于|弱于|少于).*?(?:因果|滚动)?基线|(?:因果|滚动)?基线.*?(?:更低|偏低)",
            re.IGNORECASE,
        ),
    ),
    (
        "causal baseline",
        "volume_vs_baseline_relation",
        "above",
        re.compile(
            r"(?:above|higher\s+than).*?(?:causal|rolling)?\s*baseline|"
            r"(?:causal|rolling)?\s*baseline.*?(?:above|higher)|"
            r"(?:高于|强于|多于).*?(?:因果|滚动)?基线|(?:因果|滚动)?基线.*?(?:更高|偏高)",
            re.IGNORECASE,
        ),
    ),
    (
        "previous bar",
        "volume_vs_previous_relation",
        "below",
        re.compile(
            r"(?:below|lower\s+than).*?(?:previous|prior)\s+bar|"
            r"(?:previous|prior)\s+bar.*?(?:below|lower)|"
            r"(?:低于|弱于|少于).*?前(?:一)?(?:根|棒)|前(?:一)?(?:根|棒).*?(?:更低|偏低)",
            re.IGNORECASE,
        ),
    ),
    (
        "previous bar",
        "volume_vs_previous_relation",
        "above",
        re.compile(
            r"(?:above|higher\s+than).*?(?:previous|prior)\s+bar|"
            r"(?:previous|prior)\s+bar.*?(?:above|higher)|"
            r"(?:高于|强于|多于).*?前(?:一)?(?:根|棒)|前(?:一)?(?:根|棒).*?(?:更高|偏高)",
            re.IGNORECASE,
        ),
    ),
)


def _mentioned_vpa_evidence_ids(
    text: str,
    evidence_ids: Iterable[str],
) -> list[str]:
    return [
        evidence_id
        for evidence_id in evidence_ids
        if re.search(
            rf"(?<![A-Za-z0-9_-]){re.escape(evidence_id)}(?![A-Za-z0-9_-])",
            text,
            re.IGNORECASE,
        )
    ]


def _has_vpa_selector(text: str, evidence_ids: Iterable[str]) -> bool:
    return bool(
        K_REFERENCE_RE.search(text)
        or _mentioned_vpa_evidence_ids(text, evidence_ids)
    )


def _has_vpa_relation_claim(text: str) -> bool:
    return any(pattern.search(text) for _, _, _, pattern in _RELATION_CLAIMS)


def _vpa_claim_units(value: str, evidence_ids: Iterable[str]) -> list[str]:
    """Split only where both sides are independently grounded comparator claims."""
    units: list[str] = []
    evidence_ids = tuple(evidence_ids)
    for sentence in _VPA_HARD_CLAUSE_SPLIT_RE.split(value):
        current = ""
        for part in _VPA_COMMA_SPLIT_RE.split(sentence):
            part = part.strip()
            if not part:
                continue
            current_complete = bool(
                current
                and _has_vpa_selector(current, evidence_ids)
                and _has_vpa_relation_claim(current)
            )
            part_complete = bool(
                _has_vpa_selector(part, evidence_ids)
                and _has_vpa_relation_claim(part)
            )
            if current_complete and part_complete:
                units.append(current)
                current = part
            else:
                current = f"{current}, {part}" if current else part
        if current:
            units.append(current)
    return units


def validate_inference_only_narrative(text: object, *, path: str) -> list[str]:
    """Reject unverifiable fact restatements from an inference-only field."""
    value = str(text or "").strip()
    if not value:
        return []
    without_references = REFERENCE_TOKEN_RE.sub("", value)
    errors: list[str] = []
    if _NUMERIC_LITERAL_RE.search(without_references):
        errors.append(
            f"{path} must cite program evidence instead of restating numeric market facts"
        )
    if window_extreme_claim_kinds(value):
        errors.append(
            f"{path} contains an unbounded new-high/new-low claim; use a concrete K-line comparison"
        )
    return errors


def validate_future_confirmation_narrative(text: object, *, path: str) -> list[str]:
    """Validate a future closed-bar criterion without judging its market meaning."""
    value = str(text or "").strip()
    if not value:
        return []
    without_references = REFERENCE_TOKEN_RE.sub("", value)
    errors: list[str] = []
    if _NUMERIC_LITERAL_RE.search(without_references):
        errors.append(
            f"{path} must cite program evidence instead of restating numeric market facts"
        )
    if not _FUTURE_CLOSED_BAR_RE.search(value):
        errors.append(f"{path} must describe a future closed bar")
    if _VPA_FORMING_BAR_RE.search(value):
        errors.append(f"{path} must not use K0, a forming bar, or an unclosed bar")
    extreme_claims = window_extreme_claim_kinds(value)
    extreme_claims.update(negated_window_extreme_claim_kinds(value))
    if extreme_claims and not _FUTURE_STRUCTURE_ANCHOR_RE.search(value):
        errors.append(
            f"{path} future new-high/new-low condition needs an explicit K-line "
            "or structure anchor"
        )
    if (
        _AMBIGUOUS_VOLUME_RE.search(value)
        and not _EXPLICIT_VOLUME_COMPARATOR_RE.search(value)
    ):
        errors.append(
            f"{path} uses relative-volume language without an explicit comparator"
        )
    return errors


def validate_vpa_capability_narrative(
    text: object,
    *,
    path: str,
    capabilities: dict[str, Any],
    market_session: str,
) -> list[str]:
    """Reject positive claims that exceed the catalog's data capabilities.

    A clause that explicitly says an attribution cannot be made is a valid
    limitation statement, not an attribution. This keeps the guard focused on
    claims rather than vocabulary alone.
    """
    value = str(text or "").strip()
    if not value:
        return []

    def has_positive_claim(clause: str, pattern: re.Pattern[str]) -> bool:
        for match in pattern.finditer(clause):
            before = clause[max(0, match.start() - 60) : match.start()]
            after = clause[match.end() : match.end() + 60]
            if _VPA_CAPABILITY_NEGATION_BEFORE_RE.search(before):
                continue
            if _VPA_CAPABILITY_NEGATION_AFTER_RE.search(after):
                continue
            return True
        return False

    errors: list[str] = []
    for clause in _VPA_CLAUSE_SPLIT_RE.split(value):
        clause = clause.strip()
        if not clause:
            continue
        if (
            market_session.strip().lower() == "unclassified"
            and has_positive_claim(clause, _VPA_UNCLASSIFIED_SESSION_RE)
        ):
            errors.append(f"{path} contains an unsupported named-session claim")
        if (
            not capabilities.get("actor_attribution", False)
            and has_positive_claim(clause, _VPA_ACTOR_ATTRIBUTION_RE)
        ):
            errors.append(
                f"{path} volume provenance cannot support actor attribution"
            )
        if (
            capabilities.get("interpretation_ceiling")
            == "relative_activity_only"
            and has_positive_claim(clause, _VPA_PROXY_CERTAINTY_RE)
        ):
            errors.append(
                f"{path} proxy/unknown volume supports relative activity only, "
                "not active demand/supply"
            )
    return errors


def validate_vpa_k_references(
    text: object,
    *,
    evidence_items: Iterable[dict[str, Any]],
    path: str,
) -> list[str]:
    """Require every explicit K reference to be covered by cited evidence."""
    value = str(text or "").strip()
    if not value:
        return []
    allowed: set[int] = set()
    for item in evidence_items:
        if not isinstance(item, dict):
            continue
        bounds = parse_k_bar_range_bounds(item.get("bar_range"))
        if bounds is not None:
            allowed.update(range(bounds[0], bounds[1] + 1))
    return [
        f"{path} references K{seq} outside its cited evidence ranges"
        for seq in sorted(
            {
                int(match.group(1))
                for match in K_REFERENCE_RE.finditer(value)
                if int(match.group(1)) not in allowed
            }
        )
    ]


def validate_k_reference_bounds(
    text: object,
    *,
    maximum: int | None,
    path: str,
) -> list[str]:
    """Reject explicit K references outside a frozen frame's closed bars."""
    if maximum is None:
        return []
    value = str(text or "")
    return [
        f"{path} references K{seq}, but the frozen frame contains K1-K{maximum}"
        for seq in sorted(
            {
                int(match.group(1))
                for match in K_REFERENCE_RE.finditer(value)
                if not 1 <= int(match.group(1)) <= maximum
            }
        )
    ]


def validate_pa_inference_only_narrative(text: object, *, path: str) -> list[str]:
    """Reject hand-copied PA measurements while allowing theory/reference numbers."""
    value = str(text or "").strip()
    if not value:
        return []
    without_references = REFERENCE_TOKEN_RE.sub("", value)
    without_references = _PA_THEORY_REFERENCE_RE.sub("", without_references)
    has_raw_measurement = any(
        pattern.search(without_references)
        for pattern in (
            _PA_DECIMAL_MARKET_LITERAL_RE,
            _PA_SCALED_MARKET_LITERAL_RE,
            _PA_FRACTION_MARKET_LITERAL_RE,
            _PA_MARKET_LABEL_VALUE_RE,
        )
    )
    errors: list[str] = []
    if has_raw_measurement:
        errors.append(
            f"{path} must cite program evidence instead of restating numeric market facts"
        )
    if _PA_CANONICAL_KEY_VALUE_RE.search(without_references):
        errors.append(
            f"{path} must cite the fact ID without copying canonical key=value payloads"
        )
    return errors


def validate_htf_inference_only_narrative(text: object, *, path: str) -> list[str]:
    """Reject copied HTF values while allowing bar, timeframe, and indicator IDs."""
    value = str(text or "").strip()
    if not value:
        return []
    errors = validate_pa_inference_only_narrative(value, path=path)
    without_references = REFERENCE_TOKEN_RE.sub("", value)
    without_references = _PA_THEORY_REFERENCE_RE.sub("", without_references)
    without_references = _HTF_TIMEFRAME_TOKEN_RE.sub("", without_references)
    without_references = _HTF_INDICATOR_PERIOD_RE.sub("", without_references)
    if _NUMERIC_LITERAL_RE.search(without_references) and not any(
        "numeric market facts" in error for error in errors
    ):
        errors.append(
            f"{path} must cite program evidence instead of restating numeric market facts"
        )
    return errors


def local_close_position_claim_kinds(text: object) -> set[str]:
    """Return bar-internal close-location claims, not cross-window extrema."""
    value = str(text or "")
    return {
        kind
        for kind, patterns in _LOCAL_CLOSE_POSITION_PATTERNS.items()
        if any(pattern.search(value) for pattern in patterns)
    }


def negated_window_extreme_claim_kinds(text: object) -> set[str]:
    """Return explicit claims that a cross-bar high/low was not made."""
    value = str(text or "")
    return {
        kind
        for kind, patterns in _NEGATED_WINDOW_EXTREME_PATTERNS.items()
        if any(pattern.search(value) for pattern in patterns)
    }


def window_extreme_claim_kinds(text: object) -> set[str]:
    """Return cross-bar extreme claims after removing local close-location phrases."""
    value = str(text or "")
    for patterns in (
        *_LOCAL_CLOSE_POSITION_PATTERNS.values(),
        *_NEGATED_WINDOW_EXTREME_PATTERNS.values(),
    ):
        for pattern in patterns:
            value = pattern.sub("", value)
    claims: set[str] = set()
    if _WINDOW_HIGH_EXTREME_RE.search(value):
        claims.add("high")
    if _WINDOW_LOW_EXTREME_RE.search(value):
        claims.add("low")
    return claims


def contains_retry_deliberation(text: object) -> bool:
    """Detect correction scratch-work leaking into a formal narrative field."""
    value = str(text or "")
    if _STRONG_REPAIR_DELIBERATION_RE.search(value):
        return True
    return len(_REPAIR_DELIBERATION_MARKER_RE.findall(value)) >= 2


def classify_vpa_comparator_narrative(
    text: object,
    *,
    evidence_items: Iterable[dict[str, Any]],
    path: str,
) -> tuple[list[str], list[str]]:
    """Ground relative-volume wording in the exact program comparator used.

    This validates only reproducible relation language. It deliberately does
    not decide whether the evidence means absorption, exhaustion, a test, or a
    trade-quality change.
    """
    value = str(text or "").strip()
    if not value:
        return [], []
    items = [item for item in evidence_items if isinstance(item, dict)]
    errors: list[str] = []
    warnings: list[str] = []
    if _AMBIGUOUS_VOLUME_RE.search(value) and not _EXPLICIT_VOLUME_COMPARATOR_RE.search(value):
        warnings.append(
            f"{path} uses relative-volume language without an explicit comparator"
        )

    by_bar: dict[int, list[dict[str, Any]]] = {}
    by_id: dict[str, dict[str, Any]] = {}
    for item in items:
        evidence_id = str(item.get("evidence_id") or "").strip()
        if evidence_id:
            by_id[evidence_id.lower()] = item
        bounds = parse_k_bar_range_bounds(item.get("bar_range"))
        if bounds is not None and bounds[0] == bounds[1]:
            by_bar.setdefault(bounds[0], []).append(item)

    direction_labels = {
        "反弹段": "positive",
        "上涨段": "positive",
        "上行段": "positive",
        "下跌段": "negative",
        "下行段": "negative",
        "回落段": "negative",
        "rebound segment": "positive",
        "rising segment": "positive",
        "falling segment": "negative",
        "declining segment": "negative",
    }
    for item in items:
        evidence_id = str(item.get("evidence_id") or "").strip()
        facts = item.get("facts") if isinstance(item.get("facts"), dict) else {}
        if not evidence_id.lower().startswith(
            ("vpa-c", "vpa-w")
        ) or not _mentioned_vpa_evidence_ids(value, (evidence_id,)):
            continue
        for prefix in ("current", "prior"):
            window = str(facts.get(f"{prefix}_window") or "").strip()
            actual = str(facts.get(f"{prefix}_progress_direction") or "")
            if not window or actual not in {"positive", "negative"}:
                continue
            label_match = re.search(
                rf"{re.escape(window)}\s*[（(]\s*([^）)]+?)\s*[）)]",
                value,
                re.IGNORECASE,
            )
            if label_match is None:
                continue
            label = label_match.group(1).strip().lower()
            expected = direction_labels.get(label)
            if expected is not None and expected != actual:
                errors.append(
                    f"{path} {prefix} window direction claim for {evidence_id} "
                    "conflicts with program relation"
                )

    for clause in _vpa_claim_units(value, by_id):
        clause = clause.strip()
        if not clause:
            continue
        claims = [claim for claim in _RELATION_CLAIMS if claim[3].search(clause)]
        if not claims:
            continue

        mentioned_ids = _mentioned_vpa_evidence_ids(clause, by_id)
        selectors: dict[str, list[dict[str, Any]]]
        if mentioned_ids:
            selectors = {
                evidence_id: [by_id[evidence_id]] for evidence_id in mentioned_ids
            }
        else:
            bars = sorted(
                {int(match.group(1)) for match in K_REFERENCE_RE.finditer(clause)}
            )
            selectors = {f"K{bar}": by_bar.get(bar, []) for bar in bars}
        if not selectors:
            continue

        if len(selectors) > 1:
            warnings.append(
                f"{path} binds comparator language to multiple evidence selectors; "
                "separate each claim into its own clause"
            )
            continue

        for label, fact_key, expected, _ in claims:
            for selector, candidates in selectors.items():
                relations = {
                    (
                        net_close_progress_direction_relation(
                            candidate.get("facts") or {}
                        )
                        if fact_key == "net_close_progress_direction_relation"
                        else str((candidate.get("facts") or {}).get(fact_key) or "")
                    )
                    for candidate in candidates
                }
                if expected not in relations:
                    errors.append(
                        f"{path} {label} claim for {selector} conflicts with "
                        "program relation"
                    )
    return errors, warnings


def validate_vpa_comparator_narrative(
    text: object,
    *,
    evidence_items: Iterable[dict[str, Any]],
    path: str,
) -> list[str]:
    """Compatibility wrapper returning all comparator findings."""
    errors, warnings = classify_vpa_comparator_narrative(
        text,
        evidence_items=evidence_items,
        path=path,
    )
    return [*errors, *warnings]
