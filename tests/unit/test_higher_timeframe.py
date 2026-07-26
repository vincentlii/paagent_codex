from __future__ import annotations

import copy
import json

from pa_agent.ai.higher_timeframe import (
    collect_higher_timeframe_warnings,
    render_higher_timeframe_context,
    serialize_higher_timeframe_context,
    validate_higher_timeframe_output,
)
from pa_agent.ai.json_validator import JsonValidator, Ok
from pa_agent.config.settings import Settings
from pa_agent.data.base import DataSource, KlineBar
from pa_agent.data.contracts import ResolvedInstrumentIdentity
from pa_agent.data.higher_timeframe import (
    fetch_higher_timeframe_context,
    resolve_higher_timeframe,
)
from tests.integration.conftest import VALID_STAGE1, make_frame


class _FakeSource(DataSource):
    def __init__(self) -> None:
        self.fetch_calls: list[tuple[str, str, int]] = []

    def connect(self) -> None:
        return None

    def disconnect(self) -> None:
        return None

    def list_symbols(self) -> list[str]:
        return ["XAUUSD"]

    def supported_timeframes(self) -> list[str]:
        return ["15m", "1h"]

    def subscribe(self, symbol: str, timeframe: str) -> None:
        raise AssertionError("HTF fetch must not mutate the active subscription")

    def unsubscribe(self) -> None:
        return None

    def latest_snapshot(self, n: int) -> list[KlineBar]:
        raise AssertionError("HTF fetch must use fetch_snapshot_for")

    def fetch_snapshot_for(self, symbol: str, timeframe: str, n: int) -> list[KlineBar]:
        self.fetch_calls.append((symbol, timeframe, n))
        return [
            KlineBar(
                seq=index + 1,
                ts_open=1_700_000_000_000 - index * 3_600_000,
                open=100 + index,
                high=102 + index,
                low=99 + index,
                close=101 + index,
                volume=1_000,
                closed=True,
            )
            for index in range(n)
        ]


def test_default_higher_timeframe_mapping_is_single_and_configurable() -> None:
    assert resolve_higher_timeframe("15m") == "1h"
    assert resolve_higher_timeframe("1h") == "4h"
    assert resolve_higher_timeframe("1M") is None
    assert resolve_higher_timeframe("15m", "4h") == "4h"
    assert resolve_higher_timeframe("15m", "15m") is None


def test_higher_timeframe_fetch_keeps_subscription_and_renders_price_only_context() -> None:
    source = _FakeSource()
    context = fetch_higher_timeframe_context(
        source=source,
        source_kind="tradingview",
        symbol="XAUUSD",
        current_timeframe="15m",
        bar_count=20,
        now_ms=1_700_000_000_000,
    )

    assert context["status"] == "available"
    assert context["timeframe"] == "1h"
    assert source.fetch_calls[0][:2] == ("XAUUSD", "1h")
    text = render_higher_timeframe_context(context)
    assert "更高周期背景" in text
    assert "非独立硬 Gate" in text
    assert "HTF.H#" in text
    assert "成交量" not in text.split("HTF.H#", 1)[1]
    payload = serialize_higher_timeframe_context(context)
    assert payload is not None
    assert payload["bar_count"] == 20
    assert "frame" not in payload


def test_analysis_record_persists_full_htf_bars_for_exact_replay() -> None:
    from pa_agent.config.settings import Settings
    from pa_agent.orchestrator.two_stage import _build_empty_record

    context = fetch_higher_timeframe_context(
        source=_FakeSource(),
        source_kind="tradingview",
        symbol="XAUUSD",
        current_timeframe="15m",
        bar_count=20,
        now_ms=1_700_000_000_000,
    )

    record = _build_empty_record(make_frame(), Settings(), htf_context=context)

    assert record.schema_version == 5
    assert len(record.htf_kline_data) == 20
    assert record.htf_kline_data[0]["seq"] == 1


def test_higher_timeframe_uses_frozen_identity_when_provided() -> None:
    source = _FakeSource()
    identity = ResolvedInstrumentIdentity(
        provider="fake",
        requested_symbol="XAUUSD",
        resolved_symbol="XAUUSD",
        venue="TEST",
    )
    context = fetch_higher_timeframe_context(
        source=source,
        source_kind="fake",
        symbol="XAUUSD",
        current_timeframe="15m",
        bar_count=20,
        now_ms=1_700_000_000_000,
        resolved_identity=identity,
    )

    assert context["status"] == "available"
    assert context["source_identity"] == identity.to_dict()
    assert context["metadata"]["source_identity"] == identity.to_dict()


def test_higher_timeframe_prompt_keeps_full_window_as_facts_but_caps_detail_rows() -> None:
    source = _FakeSource()
    context = fetch_higher_timeframe_context(
        source=source,
        source_kind="tradingview",
        symbol="XAUUSD",
        current_timeframe="15m",
        bar_count=60,
        now_ms=1_700_000_000_000,
    )

    text = render_higher_timeframe_context(context)
    detail_rows = [
        line
        for line in text.splitlines()
        if line.startswith("HTF.H") and line[5:6].isdigit() and " | " in line
    ]
    assert "背景 K 线数：60；逐棒明细仅展示最近 24 根" in text
    assert len(detail_rows) == 24


def test_higher_timeframe_unavailable_never_blocks_current_timeframe_analysis() -> None:
    source = _FakeSource()
    context = fetch_higher_timeframe_context(
        source=source,
        source_kind="tradingview",
        symbol="XAUUSD",
        current_timeframe="1h",
        bar_count=20,
    )

    assert context["status"] == "unavailable"
    assert context["reason"] == "source_does_not_support_higher_timeframe"
    assert "禁止编造 HTF" in render_higher_timeframe_context(context)


def test_missing_higher_timeframe_citation_is_a_warning() -> None:
    context = fetch_higher_timeframe_context(
        source=_FakeSource(),
        source_kind="tradingview",
        symbol="XAUUSD",
        current_timeframe="15m",
        bar_count=20,
        now_ms=1_700_000_000_000,
    )
    stage1 = copy.deepcopy(VALID_STAGE1)
    stage1["htf_context"] = "更高周期维持空头背景，参考当前周期 K20-K1。"

    result = JsonValidator(Settings()).validate(
        "stage1",
        json.dumps(stage1, ensure_ascii=False),
        kline_frame=make_frame(),
        htf_context=context,
    )

    assert isinstance(result, Ok)
    assert any(warning.code == "htf_citation" for warning in result.warnings)


def test_higher_timeframe_citations_use_an_unambiguous_namespace() -> None:
    context = fetch_higher_timeframe_context(
        source=_FakeSource(),
        source_kind="tradingview",
        symbol="XAUUSD",
        current_timeframe="15m",
        bar_count=20,
        now_ms=1_700_000_000_000,
    )

    valid = {
        "htf_context": (
            "1\u5c0f\u65f6\u5468\u671fHTF.H1-HTF.H4\u6301\u7eed\u4e0b\u8dcc\uff0c"
            "\u4e0e\u5f53\u524d\u5468\u671f\u80cc\u666f\u4e00\u81f4\u3002"
        )
    }
    brooks_only = {
        "htf_context": "Brooks H2 setup is weak; no higher-timeframe bar is cited."
    }
    outside = {
        "htf_context": (
            "\u53c2\u8003HTF.H1-HTF.H21\u6301\u7eed\u4e0b\u8dcc\u7684\u80cc\u666f\u3002"
        )
    }
    compact_outside = {
        "htf_context": "\u53c2\u8003HTF.H1-H21\u7684\u80cc\u666f\u3002"
    }
    zero = {"htf_context": "HTF.H0 provides background."}

    assert validate_higher_timeframe_output(valid, context) == []
    assert validate_higher_timeframe_output(brooks_only, context) == []
    assert collect_higher_timeframe_warnings(brooks_only, context)
    assert any(
        "H21" in error
        for error in validate_higher_timeframe_output(outside, context)
    )
    assert any(
        "H21" in error
        for error in validate_higher_timeframe_output(compact_outside, context)
    )
    assert any(
        "H0" in error
        for error in validate_higher_timeframe_output(zero, context)
    )
