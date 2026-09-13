"""Capture a new study input using the existing market adapters; no model calls."""
import argparse
import json
import time
from pathlib import Path

from pa_agent.config.settings import Settings
from pa_agent.data.factory import create_data_source, volume_metadata_for_source
from pa_agent.data.higher_timeframe import fetch_higher_timeframe_context
from pa_agent.data.snapshot import take_snapshot_from_bars, INDICATOR_WARMUP_BARS
from pa_agent.data.snapshot_bundle import build_snapshot_bundle
from pa_agent.ai.vpa_measurements import build_vpa_evidence_catalog
from pa_agent.orchestrator.two_stage import _build_empty_record
from pa_agent.experiments.study_resources import freeze_host_evidence
from pa_agent.experiments.study_protocol import write_once


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--settings", required=True, type=Path)
    parser.add_argument("--prompt-dir", required=True, type=Path)
    parser.add_argument("--source", required=True, choices=("binance_usdm", "bitget_usdt_futures", "okx_swap"))
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--timeframe", required=True)
    args = parser.parse_args(argv)
    if args.output.exists():
        raise FileExistsError("study input is immutable")
    settings = Settings.model_validate(json.loads(args.settings.read_text(encoding="utf-8")))
    if settings.prompt.experience_max_entries:
        raise ValueError("capture-only study requires experience disabled or an independently frozen history pool")
    source = create_data_source(args.source, settings=settings)
    try:
        source.connect()
        source.subscribe(args.symbol, args.timeframe)
        as_of = time.time_ns() // 1_000_000
        raw = source.latest_snapshot(settings.general.analysis_bar_count + INDICATOR_WARMUP_BARS + 5)
        metadata = volume_metadata_for_source(args.source, source)
        metadata["analysis_as_of_ms"] = as_of
        frame = take_snapshot_from_bars(raw, settings.general.analysis_bar_count, args.symbol,
                                       args.timeframe, now_ms=as_of, data_metadata=metadata)
        htf = fetch_higher_timeframe_context(
            source=source, source_kind=args.source, symbol=args.symbol,
            current_timeframe=args.timeframe, bar_count=settings.general.higher_timeframe_bar_count,
            override=settings.general.higher_timeframe_override, now_ms=as_of,
            resolved_identity=metadata.get("source_identity"),
        )
        bundle = build_snapshot_bundle(frame, htf, captured_at_ms=as_of)
        if not bundle.data_quality.ok:
            raise ValueError("study input failed shared snapshot validation")
        record = _build_empty_record(frame, settings, htf_context=htf, prompt_dir=args.prompt_dir)
        record.vpa_catalog = build_vpa_evidence_catalog(frame)
        evidence = freeze_host_evidence(record, prompt_dir=args.prompt_dir)
        write_once(args.output, evidence)
        print(json.dumps({"snapshot_hash": evidence["snapshot_hash"], "as_of_ms": as_of,
                          "bars": len(frame.bars), "htf_status": htf["status"]}))
    finally:
        source.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
