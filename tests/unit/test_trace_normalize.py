from __future__ import annotations

from pa_agent.ai.trace_normalize import (
    fix_bar_range_string,
    normalize_trace_item,
    normalize_trace_list,
)


def test_range_order_and_node_prefix_are_canonicalized() -> None:
    item = {"node_id": " §10.3 ", "bar_range": "k1-k20", "answer": "是"}

    normalize_trace_item(item)

    assert item == {"node_id": "10.3", "bar_range": "K20-K1", "answer": "是"}


def test_global_range_uses_exact_frame_limit() -> None:
    assert fix_bar_range_string("全局", default_max_seq=48) == "K48-K1"


def test_discrete_references_are_not_expanded_into_unstated_range() -> None:
    assert fix_bar_range_string("K10,K1") == "K10,K1"


def test_normalization_never_infers_answer_or_missing_bar_range() -> None:
    trace = [{"node_id": "§9.1", "branch": "bullish"}]

    normalize_trace_list(trace, default_max_seq=20)

    assert trace == [{"node_id": "9.1", "branch": "bullish"}]


def test_normalization_expands_bar_range_to_cover_valid_reason_references() -> None:
    trace = [
        {
            "node_id": "2.2",
            "reason": "长程背景为 K100-K41，近期方向参考 K40-K1区间。",
            "bar_range": "K100-K41",
        }
    ]

    normalize_trace_list(trace, default_max_seq=100)

    assert trace[0]["bar_range"] == "K100-K1"


def test_normalization_does_not_expand_to_reason_references_outside_snapshot() -> None:
    trace = [
        {
            "node_id": "2.2",
            "reason": "长程背景为 K101-K41，近期方向参考 K40-K1。",
            "bar_range": "K100-K41",
        }
    ]

    normalize_trace_list(trace, default_max_seq=100)

    assert trace[0]["bar_range"] == "K100-K41"


def test_range_dash_variants_are_canonicalized() -> None:
    for value in ("K1-K20", "K1\u2013K20", "K1\u2014K20", "K1\uff0dK20"):
        item = {"node_id": "10.3", "bar_range": value}

        normalize_trace_item(item)

        assert item["bar_range"] == "K20-K1"


def test_normalization_does_not_treat_pa_fact_ids_as_literal_k_references() -> None:
    for fact_id in ("PA.PAIR.K1.K2", "PA.COMPARE.K1.PRIOR5", "PA.WINDOW.K8"):
        item = {"bar_range": "K1", "reason": fact_id}

        normalize_trace_item(item, default_max_seq=100)

        assert item["bar_range"] == "K1"
