"""JSON schemas for Stage 1 and Stage 2 AI outputs."""
from __future__ import annotations

from pa_agent.ai.pa_theory import PA_THEORY_RULE_IDS

_PRICE_LEVEL: dict = {
    "type": "string",
    "pattern": r"^\s*\d+(?:\.\d+)?(?:\s*[-~－—到至]\s*\d+(?:\.\d+)?)?\s*$",
}

# ── Shared trace item schemas (二元决策树) ─────────────────────────────────────

_TRACE_ITEM: dict = {
    "type": "object",
    "required": ["node_id", "question", "answer", "reason", "bar_range"],
    "properties": {
        "node_id": {"type": "string"},
        "question": {"type": "string"},
        "answer": {
            "type": "string",
            "enum": ["是", "否", "中性", "等待", "不适用"],
        },
        "action": {"type": "string"},
        "reason": {"type": "string"},
        "branch": {"type": ["string", "null"]},
        "next_node": {"type": ["string", "null"]},
        "skipped": {"type": "boolean"},
        "section": {"type": "string"},
        "bar_range": {
            "type": "string",
            "description": "K-line basis e.g. K50-K1 (seq1=newest closed bar)",
        },
        "evidence_refs": {
            "type": "array",
            "items": {"type": "string", "pattern": r"^PA\."},
            "uniqueItems": True,
        },
    },
    "additionalProperties": False,
}

_TERMINAL: dict = {
    "type": "object",
    "required": ["node_id", "outcome", "label"],
    "properties": {
        "node_id": {"type": "string"},
        "outcome": {
            "type": "string",
            "enum": ["wait", "reject", "trade", "proceed"],
        },
        "label": {"type": "string"},
    },
    "additionalProperties": False,
}

_SIGNAL_BAR: dict = {
    "type": "object",
    "required": ["bar", "quality", "reason"],
    "properties": {
        "bar": {"type": ["string", "null"], "pattern": r"^K[1-9]\d*$"},
        "quality": {"type": "string", "enum": ["strong", "medium", "weak", "invalid"]},
        "pattern": {"type": "string"},
        "reason": {"type": "string"},
    },
    "allOf": [
        {
            "if": {
                "properties": {"bar": {"type": "null"}},
                "required": ["bar"],
            },
            "then": {"properties": {"quality": {"const": "invalid"}}},
        }
    ],
    "additionalProperties": False,
}

_ENTRY_BAR: dict = {
    "type": "object",
    "required": ["bar", "strength", "follow_through", "still_valid", "freshness"],
    "properties": {
        "bar": {"type": ["string", "null"]},
        "strength": {"type": "string", "enum": ["strong", "weak", "not_triggered"]},
        "follow_through": {"type": ["boolean", "null"]},
        "still_valid": {"type": ["boolean", "null"]},
        "freshness": {"type": "string", "enum": ["fresh", "pending", "stale", "invalid"]},
    },
    "allOf": [
        {
            "if": {
                "properties": {"freshness": {"const": "pending"}},
                "required": ["freshness"],
            },
            "then": {
                "properties": {
                    "bar": {"type": "null"},
                    "strength": {"const": "not_triggered"},
                    "follow_through": {"type": "null"},
                    "still_valid": {"type": "null"},
                }
            },
        },
        {
            "if": {
                "properties": {"bar": {"type": "null"}},
                "required": ["bar"],
            },
            "then": {
                "properties": {
                    "strength": {"const": "not_triggered"},
                    "follow_through": {"type": "null"},
                    "still_valid": {"type": "null"},
                    "freshness": {"enum": ["pending", "invalid"]},
                }
            },
        },
    ],
    "additionalProperties": False,
}

_SECOND_ENTRY: dict = {
    "type": "object",
    "required": ["is_second_entry", "type"],
    "properties": {
        "is_second_entry": {"type": "boolean"},
        "type": {
            "type": "string",
            "enum": ["H2", "L2", "MTR", "wedge", "tr_boundary", "trendline", "none"],
        },
    },
    "additionalProperties": False,
}

_STAGE1_BAR_ANALYSIS: dict = {
    "type": "object",
    "required": [
        "always_in",
        "last_closed_bar",
        "bar_type",
        "signal_bar",
        "entry_setup_type",
        "follow_through",
    ],
    "properties": {
        "always_in": {"type": "string", "enum": ["long", "short", "neutral"]},
        "last_closed_bar": {"const": "K1"},
        "bar_type": {
            "type": "string",
            "enum": [
                "trend_bull", "trend_bear", "doji", "flat", "other",
            ],
        },
        "signal_bar": _SIGNAL_BAR,
        "entry_setup_type": {"type": "string"},
        "follow_through": {"type": ["string", "boolean", "null"]},
    },
    "additionalProperties": False,
}

_STAGE2_SIGNAL_BAR: dict = {
    "type": "object",
    "required": ["bar", "quality", "pattern", "reason"],
    "properties": _SIGNAL_BAR["properties"],
    "allOf": [
        {
            "if": {
                "properties": {"bar": {"type": "null"}},
                "required": ["bar"],
            },
            "then": {
                "properties": {
                    "quality": {"const": "invalid"},
                    "pattern": {"const": "none"},
                }
            },
        }
    ],
    "additionalProperties": False,
}

_STAGE2_BAR_ANALYSIS: dict = {
    "type": "object",
    "required": ["signal_bar", "entry_bar", "second_entry"],
    "properties": {
        "signal_bar": _STAGE2_SIGNAL_BAR,
        "entry_bar": _ENTRY_BAR,
        "second_entry": _SECOND_ENTRY,
    },
    "additionalProperties": False,
}

_BAR_BY_BAR_ITEM: dict = {
    "type": "object",
    "required": [
        "bar",
        "role",
        "bar_type",
        "context_effect",
        "follow_through",
        "trapped_side",
        "reason",
    ],
    "properties": {
        "bar": {"type": "string"},
        "role": {
            "type": "string",
            "minLength": 1,
            "maxLength": 32,
            "pattern": r"\S",
        },
        "bar_type": {
            "type": "string",
            "enum": [
                "trend_bull", "trend_bear", "doji", "flat", "other",
            ],
        },
        "context_effect": {
            "type": "string",
            "enum": [
                "strengthens_bull", "weakens_bull", "strengthens_bear",
                "weakens_bear", "neutral", "transition",
            ],
        },
        "follow_through": {"type": "string", "enum": ["yes", "no", "pending", "failed"]},
        "trapped_side": {"type": "string", "enum": ["bulls", "bears", "both", "none", "unknown"]},
        "reason": {"type": "string"},
    },
    "additionalProperties": False,
}

_PA_EVIDENCE: dict = {
    "type": "object",
    "required": ["fact_refs", "theory_rule_ids", "spike_analysis"],
    "properties": {
        "fact_refs": {
            "type": "array",
            "minItems": 1,
            "items": {"type": "string", "pattern": r"^PA\."},
            "uniqueItems": True,
        },
        "theory_rule_ids": {
            "type": "array",
            "minItems": 1,
            "items": {"type": "string", "enum": sorted(PA_THEORY_RULE_IDS)},
            "uniqueItems": True,
        },
        "spike_analysis": {
            "type": ["object", "null"],
            "required": [
                "anchor_bar",
                "post_anchor_state",
                "first_pause_or_pullback_bar",
            ],
            "properties": {
                "anchor_bar": {"type": "string", "pattern": r"^K\d+$"},
                "post_anchor_state": {
                    "type": "string",
                    "enum": [
                        "no_closed_bar_after_anchor",
                        "continuation_only",
                        "pause_or_pullback_present",
                    ],
                },
                "first_pause_or_pullback_bar": {
                    "type": ["string", "null"],
                    "pattern": r"^K\d+$",
                },
            },
            "allOf": [
                {
                    "if": {
                        "properties": {
                            "post_anchor_state": {
                                "const": "pause_or_pullback_present",
                            },
                        },
                        "required": ["post_anchor_state"],
                    },
                    "then": {
                        "properties": {
                            "first_pause_or_pullback_bar": {"type": "string"},
                        },
                    },
                },
                {
                    "if": {
                        "properties": {
                            "post_anchor_state": {
                                "enum": [
                                    "no_closed_bar_after_anchor",
                                    "continuation_only",
                                ],
                            },
                        },
                        "required": ["post_anchor_state"],
                    },
                    "then": {
                        "properties": {
                            "first_pause_or_pullback_bar": {"type": "null"},
                        },
                    },
                },
            ],
            "additionalProperties": False,
        },
    },
    "additionalProperties": False,
}

_MARKET_HYPOTHESIS: dict = {
    "type": "object",
    "required": [
        "hypothesis_id", "role", "cycle_position", "direction", "setup_hint",
        "thesis", "supporting_fact_refs", "challenging_fact_refs",
        "theory_rule_ids", "confirmation_condition", "disconfirmation_condition",
    ],
    "properties": {
        "hypothesis_id": {"type": "string", "minLength": 1},
        "role": {"type": "string", "enum": ["primary", "alternative"]},
        "cycle_position": {
            "type": "string",
            "enum": [
                "spike", "micro_channel", "tight_channel", "normal_channel",
                "broad_channel", "trending_tr", "trading_range", "extreme_tr", "unknown",
            ],
        },
        "direction": {"type": "string", "enum": ["bullish", "bearish", "neutral"]},
        "setup_hint": {"type": "string"},
        "thesis": {"type": "string", "minLength": 1},
        "supporting_fact_refs": {
            "type": "array", "items": {"type": "string", "pattern": r"^PA\."},
            "uniqueItems": True,
        },
        "challenging_fact_refs": {
            "type": "array", "items": {"type": "string", "pattern": r"^PA\."},
            "uniqueItems": True,
        },
        "theory_rule_ids": {
            "type": "array", "minItems": 1,
            "items": {"type": "string", "enum": sorted(PA_THEORY_RULE_IDS)},
            "uniqueItems": True,
        },
        "confirmation_condition": {"type": "string", "minLength": 1},
        "disconfirmation_condition": {"type": "string", "minLength": 1},
    },
    "additionalProperties": False,
}

_MARKET_HYPOTHESES: dict = {
    "type": "object",
    "required": ["primary", "alternatives"],
    "properties": {
        "primary": _MARKET_HYPOTHESIS,
        "alternatives": {"type": "array", "maxItems": 2, "items": _MARKET_HYPOTHESIS},
    },
    "additionalProperties": False,
}

# ── Stage 1 schema ────────────────────────────────────────────────────────────

STAGE1_SCHEMA: dict = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "required": [
        "cycle_position",
        "alternative_cycle_position",
        "direction",
        "diagnosis_confidence",
        "spike_stage",
        "climax_risk",
        "market_phase",
        "transition_risk",
        "detected_patterns",
        "key_signals",
        "htf_context",
        "entry_setup",
        "support_levels",
        "resistance_levels",
        "risk_warning",
        "bar_analysis",
        "bar_by_bar_summary",
        "pa_evidence",
        "gate_trace",
        "gate_result",
    ],
    "properties": {
        "cycle_position": {
            "type": "string",
            "enum": [
                "spike", "micro_channel", "tight_channel", "normal_channel",
                "broad_channel", "trending_tr", "trading_range", "extreme_tr", "unknown",
            ],
        },
        "alternative_cycle_position": {
            "type": ["string", "null"],
            "enum": [
                "spike", "micro_channel", "tight_channel", "normal_channel",
                "broad_channel", "trending_tr", "trading_range", "extreme_tr",
                "unknown", None,
            ],
        },
        "direction": {"type": "string", "enum": ["bullish", "bearish", "neutral"]},
        "diagnosis_confidence": {"type": "integer", "minimum": 0, "maximum": 100},
        "diagnosis_confidence_reasoning": {"type": "string", "minLength": 1},
        "spike_stage": {
            "type": ["string", "null"],
            "enum": ["active", "ending", "transitioning", None],
        },
        "climax_risk": {
            "type": ["string", "null"],
            "enum": ["none", "warning", "triggered", None],
        },
        "market_phase": {"type": "string", "enum": ["stable", "transitioning"]},
        "support_levels": {"type": "array", "maxItems": 3, "items": _PRICE_LEVEL},
        "resistance_levels": {"type": "array", "maxItems": 3, "items": _PRICE_LEVEL},
        "transition_risk": {
            "type": ["string", "null"],
            "enum": ["high", "medium", "low", None],
        },
        "detected_patterns": {"type": "array", "items": {"type": "string"}},
        "key_signals": {"type": "array", "items": {"type": "string"}},
        "htf_context": {"type": "string"},
        "entry_setup": {"type": "string"},
        "risk_warning": {"type": "string"},
        "bar_analysis": _STAGE1_BAR_ANALYSIS,
        "bar_by_bar_summary": {
            "type": "array",
            "minItems": 1,
            "items": _BAR_BY_BAR_ITEM,
        },
        "pa_evidence": _PA_EVIDENCE,
        "market_hypotheses": _MARKET_HYPOTHESES,
        "gate_trace": {
            "type": "array",
            "minItems": 1,
            "items": _TRACE_ITEM,
        },
        "gate_result": {
            "type": "string",
            "enum": ["proceed", "wait", "unknown"],
        },
        "incremental_delta": {
            "type": "object",
            "required": ["new_closed_bars", "changed_fields", "summary"],
            "properties": {
                "new_closed_bars": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "changed_fields": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "summary": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
    "allOf": [
        # spike only requires spike_stage (micro_channel may keep spike_stage null)
        {
            "if": {
                "properties": {"cycle_position": {"const": "spike"}},
                "required": ["cycle_position"],
            },
            "then": {
                "properties": {
                    "spike_stage": {"type": "string", "enum": ["active", "ending", "transitioning"]},
                    "pa_evidence": {
                        "properties": {
                            "spike_analysis": {"type": "object"},
                        }
                    },
                },
                "required": ["spike_stage"],
            },
        },
        {
            "if": {
                "properties": {
                    "cycle_position": {"not": {"const": "spike"}},
                },
                "required": ["cycle_position"],
            },
            "then": {
                "properties": {
                    "spike_stage": {"type": "null"},
                    "pa_evidence": {
                        "properties": {
                            "spike_analysis": {"type": "null"},
                        }
                    },
                },
            },
        },
        {
            "if": {
                "properties": {
                    "pa_evidence": {
                        "type": "object",
                        "properties": {
                            "spike_analysis": {
                                "type": "object",
                                "properties": {
                                    "post_anchor_state": {
                                        "const": "pause_or_pullback_present",
                                    },
                                },
                                "required": ["post_anchor_state"],
                            },
                        },
                        "required": ["spike_analysis"],
                    },
                },
                "required": ["pa_evidence"],
            },
            "then": {
                "properties": {
                    "spike_stage": {
                        "type": "string",
                        "enum": ["ending", "transitioning"],
                    },
                    "market_phase": {"const": "transitioning"},
                    "transition_risk": {
                        "type": "string",
                        "enum": ["high", "medium", "low"],
                    },
                },
            },
        },
        {
            "if": {
                "properties": {"market_phase": {"const": "stable"}},
                "required": ["market_phase"],
            },
            "then": {
                "properties": {
                    "transition_risk": {"type": "null"},
                },
            },
        },
        # transitioning market_phase requires transition_risk to be non-null
        {
            "if": {
                "properties": {"market_phase": {"const": "transitioning"}},
                "required": ["market_phase"],
            },
            "then": {
                "properties": {
                    "transition_risk": {"type": "string", "enum": ["high", "medium", "low"]}
                },
                "required": ["transition_risk"],
            },
        },
    ],
    "additionalProperties": False,
}


# ── Stage 2 schema ────────────────────────────────────────────────────────────

_DECISION_BASE: dict = {
    "type": "object",
    "required": [
        "order_direction",
        "order_type",
        "entry_price",
        "entry_basis_bar",
        "entry_basis_extreme",
        "entry_rule",
        "take_profit_price",
        "take_profit_price_2",
        "stop_loss_price",
        "reasoning",
        "diagnosis_confidence",
        "diagnosis_confidence_reasoning",
        "trade_confidence",
        "trade_confidence_reasoning",
        "estimated_win_rate",
        "estimated_win_rate_reasoning",
        "key_factors",
        "watch_points",
        "risk_assessment",
        "invalidation_condition",
    ],
    "properties": {
        "order_direction": {"type": ["string", "null"]},
        "order_type": {
            "type": "string",
            "enum": ["限价单", "突破单", "市价单", "不下单"],
        },
        "entry_price": {"type": ["number", "null"]},
        "entry_basis_bar": {"type": ["string", "null"]},
        "entry_basis_extreme": {"type": ["string", "null"], "enum": ["high", "low", None]},
        "entry_rule": {"type": ["string", "null"]},
        "take_profit_price": {"type": ["number", "null"]},
        "take_profit_price_2": {"type": ["number", "null"]},
        "stop_loss_price": {"type": ["number", "null"]},
        "reasoning": {"type": "string", "minLength": 1, "maxLength": 1500},
        "diagnosis_confidence": {"type": "integer", "minimum": 0, "maximum": 100},
        "diagnosis_confidence_reasoning": {"type": "string"},
        "trade_confidence": {"type": ["integer", "null"], "minimum": 0, "maximum": 100},
        "trade_confidence_reasoning": {"type": "string"},
        "estimated_win_rate": {"type": ["integer", "null"], "minimum": 0, "maximum": 100},
        "estimated_win_rate_reasoning": {"type": ["string", "null"]},
        "key_factors": {"type": "array", "items": {"type": "string"}},
        "watch_points": {"type": "array", "items": {"type": "string"}},
        "risk_assessment": {"type": "string"},
        "invalidation_condition": {"type": ["string", "null"]},
    },
    "allOf": [
        # 不下单 → all price fields and direction must be null
        {
            "if": {
                "properties": {"order_type": {"const": "不下单"}},
                "required": ["order_type"],
            },
            "then": {
                "properties": {
                    "entry_price": {"type": "null"},
                    "entry_basis_bar": {"type": "null"},
                    "entry_basis_extreme": {"type": "null"},
                    "entry_rule": {"type": "null"},
                    "take_profit_price": {"type": "null"},
                    "take_profit_price_2": {"type": "null"},
                    "stop_loss_price": {"type": "null"},
                    "order_direction": {"type": "null"},
                    "trade_confidence": {"type": "null"},
                    "estimated_win_rate": {"type": "null"},
                    "estimated_win_rate_reasoning": {"type": "null"},
                },
            },
        },
        # 有下单 → price fields must be numbers, direction must be 做多/做空
        {
            "if": {
                "properties": {
                    "order_type": {"enum": ["限价单", "突破单", "市价单"]}
                },
                "required": ["order_type"],
            },
            "then": {
                "properties": {
                    "entry_price": {"type": "number"},
                    "take_profit_price": {"type": "number"},
                    "take_profit_price_2": {"type": "number"},
                    "stop_loss_price": {"type": "number"},
                    "order_direction": {"type": "string", "enum": ["做多", "做空"]},
                    "trade_confidence": {"type": "integer", "minimum": 0, "maximum": 100},
                    "estimated_win_rate": {"type": "integer", "minimum": 0, "maximum": 100},
                    "estimated_win_rate_reasoning": {"type": "string", "minLength": 1},
                },
                "required": [
                    "entry_price",
                    "take_profit_price",
                    "take_profit_price_2",
                    "stop_loss_price",
                    "order_direction",
                    "trade_confidence",
                    "estimated_win_rate",
                ],
            },
        },
        # 突破单必须说明挂单依据，避免把 entry_price 填在 K 线中部。
        {
            "if": {
                "properties": {"order_type": {"const": "突破单"}},
                "required": ["order_type"],
            },
            "then": {
                "properties": {
                    "entry_basis_bar": {"type": "string"},
                    "entry_basis_extreme": {"type": "string", "enum": ["high", "low"]},
                    "entry_rule": {"type": "string"},
                },
                "required": ["entry_basis_bar", "entry_basis_extreme", "entry_rule"],
            },
        },
    ],
    "additionalProperties": False,
}

# ── Next bar prediction sub-schemas (R2.1, R2.2) ──────────────────────────────

_NEXT_BAR_PROBABILITIES: dict = {
    "type": ["object", "null"],
    "required": ["bullish", "bearish", "neutral"],
    "properties": {
        "bullish": {"type": "integer", "minimum": 0, "maximum": 100},
        "bearish": {"type": "integer", "minimum": 0, "maximum": 100},
        "neutral": {"type": "integer", "minimum": 0, "maximum": 100},
    },
    "additionalProperties": False,
}

_NEXT_BAR_PREDICTION: dict = {
    "type": "object",
    "required": ["direction", "probabilities", "reasoning", "unpredictable", "features_used"],
    "properties": {
        "direction": {
            "type": ["string", "null"],
            "enum": ["bullish", "bearish", "neutral", None],
        },
        "probabilities": _NEXT_BAR_PROBABILITIES,
        "reasoning": {"type": "string", "minLength": 1, "maxLength": 1500},
        "unpredictable": {"type": "boolean"},
        "features_used": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": [
                    "stage1_diagnosis",
                    "kline_features",
                    "analysis_history",
                    "experience_library",
                    "stage2_decision",
                    "previous_prediction_summary",
                ],
            },
            "uniqueItems": True,
        },
    },
    "allOf": [
        # unpredictable=false → direction must be non-null string, probabilities must be object
        {
            "if": {
                "properties": {"unpredictable": {"const": False}},
                "required": ["unpredictable"],
            },
            "then": {
                "properties": {
                    "direction": {"type": "string", "enum": ["bullish", "bearish", "neutral"]},
                    "probabilities": {"type": "object"},
                },
            },
        },
        # unpredictable=true → direction=null, probabilities=null
        {
            "if": {
                "properties": {"unpredictable": {"const": True}},
                "required": ["unpredictable"],
            },
            "then": {
                "properties": {
                    "direction": {"type": "null"},
                    "probabilities": {"type": "null"},
                },
            },
        },
    ],
    "additionalProperties": False,
}

# ── Next cycle prediction sub-schemas ────────────────────────────────────────

from pa_agent.ai.cycle_enums import CYCLE_ENUM as _CYCLE_ENUM  # noqa: E402

_NEXT_CYCLE_PROBABILITIES: dict = {
    "type": ["object", "null"],
    "required": list(_CYCLE_ENUM),
    "properties": {k: {"type": "integer", "minimum": 0, "maximum": 100} for k in _CYCLE_ENUM},
    "additionalProperties": False,
}

_NEXT_CYCLE_PREDICTION: dict = {
    "type": "object",
    "required": ["cycle", "direction", "probabilities", "reasoning", "unpredictable", "features_used"],
    "properties": {
        "cycle": {
            "type": ["string", "null"],
            "enum": list(_CYCLE_ENUM) + [None],
        },
        "direction": {
            "type": ["string", "null"],
            "enum": ["bullish", "bearish", "neutral", None],
        },
        "probabilities": _NEXT_CYCLE_PROBABILITIES,
        "reasoning": {"type": "string", "minLength": 1, "maxLength": 1500},
        "unpredictable": {"type": "boolean"},
        "features_used": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": [
                    "stage1_diagnosis",
                    "kline_features",
                    "analysis_history",
                    "experience_library",
                    "stage2_decision",
                    "previous_prediction_summary",
                ],
            },
            "uniqueItems": True,
        },
    },
    "allOf": [
        # unpredictable=false → cycle must be non-null enum string, probabilities must be object
        {
            "if": {
                "properties": {"unpredictable": {"const": False}},
                "required": ["unpredictable"],
            },
            "then": {
                "properties": {
                    "cycle": {"type": "string", "enum": list(_CYCLE_ENUM)},
                    "probabilities": {"type": "object"},
                },
            },
        },
        # unpredictable=true → cycle=null, direction=null, probabilities=null
        {
            "if": {
                "properties": {"unpredictable": {"const": True}},
                "required": ["unpredictable"],
            },
            "then": {
                "properties": {
                    "cycle": {"type": "null"},
                    "direction": {"type": "null"},
                    "probabilities": {"type": "null"},
                },
            },
        },
    ],
    "additionalProperties": False,
}

STAGE2_SCHEMA: dict = {
    "type": "object",
    "required": [
        "decision",
        "bar_analysis",
        "decision_trace",
        "terminal",
        "next_cycle_prediction",
    ],
    "properties": {
        "decision": _DECISION_BASE,
        "decision_trace": {
            "type": "array",
            "items": _TRACE_ITEM,
        },
        "terminal": _TERMINAL,
        "bar_analysis": _STAGE2_BAR_ANALYSIS,
        "gate_shortcircuited": {"type": "boolean"},
        "next_bar_prediction": _NEXT_BAR_PREDICTION,
        "next_cycle_prediction": _NEXT_CYCLE_PREDICTION,
    },
    "additionalProperties": False,
}
