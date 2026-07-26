from __future__ import annotations

import copy

import pytest

from pa_agent.ai.vpa_measurements import build_vpa_evidence_catalog
from pa_agent.experiments.replay import frozen_input_from_record
from pa_agent.records.schema import AnalysisRecord, RecordMeta
from tests.integration.conftest import VALID_STAGE1, VALID_STAGE2, make_frame


def _source_record(*, htf_available: bool, with_htf_bars: bool) -> AnalysisRecord:
    frame = make_frame()
    catalog = build_vpa_evidence_catalog(frame)
    htf_context = (
        {
            "status": "available",
            "timeframe": "4h",
            "current_timeframe": "1h",
            "reason": None,
            "metadata": {},
            "symbol": "XAUUSD",
            "bar_count": 20,
        }
        if htf_available
        else {
            "status": "disabled",
            "timeframe": None,
            "current_timeframe": "1h",
            "reason": "disabled_by_settings",
            "metadata": {},
        }
    )
    return AnalysisRecord(
        schema_version=5,
        source_schema_version=4 if with_htf_bars else 3,
        meta=RecordMeta(
            timestamp_local_iso="2026-07-20T12:00:00+08:00",
            timestamp_local_ms=frame.snapshot_ts_local_ms,
            run_id="source-run",
            symbol=frame.symbol,
            timeframe=frame.timeframe,
            bar_count=len(frame.bars),
            ai_provider={"model": "test-model"},
        ),
        kline_data=[bar.__dict__ for bar in frame.bars],
        htf_text="",
        htf_context=htf_context,
        htf_kline_data=(
            [
                {
                    **bar.__dict__,
                    "ts_open": bar.ts_open - 86_400_000,
                }
                for bar in frame.bars
            ]
            if with_htf_bars
            else []
        ),
        stage1_messages=[],
        stage1_response={},
        stage1_diagnosis=copy.deepcopy(VALID_STAGE1),
        stage2_messages=[],
        stage2_response={},
        stage2_decision=copy.deepcopy(VALID_STAGE2),
        strategy_files_used=[],
        experience_loaded=[],
        exception=None,
        usage_total={},
        vpa_catalog=catalog,
        snapshot_context={
            "source_identity": {
                "provider": "tradingview",
                "requested_symbol": "XAUUSD",
                "resolved_symbol": "XAUUSD",
                "venue": "OANDA",
                "market": "fx",
                "timezone_basis": "UTC",
                "volume_kind": "tick",
                "volume_quality": "proxy",
            },
            "source": "tradingview",
            "venue": "OANDA",
            "volume_kind": "tick",
            "volume_quality": "proxy",
            "timezone_basis": "UTC",
            "snapshot_ts_local_ms": frame.snapshot_ts_local_ms,
        },
    )


def test_replay_reconstructs_current_and_full_available_htf_frames() -> None:
    source = _source_record(htf_available=True, with_htf_bars=True)

    frame, htf_context, catalog = frozen_input_from_record(source)

    assert frame.data_metadata["source_identity"]["venue"] == "OANDA"
    assert htf_context["status"] == "available"
    assert htf_context["frame"].timeframe == "4h"
    assert len(htf_context["frame"].bars) == 20
    assert catalog["catalog_id"] == source.vpa_catalog["catalog_id"]


def test_replay_rejects_legacy_record_that_claims_htf_without_full_bars() -> None:
    source = _source_record(htf_available=True, with_htf_bars=False)

    with pytest.raises(ValueError, match="full HTF bars"):
        frozen_input_from_record(source)


def test_replay_allows_disabled_htf_without_bars() -> None:
    source = _source_record(htf_available=False, with_htf_bars=False)

    _frame, htf_context, _catalog = frozen_input_from_record(source)

    assert htf_context["status"] == "disabled"
    assert "frame" not in htf_context
