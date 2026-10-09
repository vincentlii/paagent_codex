"""Freeze one public market/HTF input through existing PA domain builders; no LLM."""
import argparse
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pa_agent.ai.higher_timeframe import serialize_higher_timeframe_context
from pa_agent.ai.vpa_measurements import build_vpa_evidence_catalog
from pa_agent.config.settings import Settings
from pa_agent.data.factory import create_data_source, resolved_identity_for_source, volume_metadata_for_source
from pa_agent.data.higher_timeframe import fetch_higher_timeframe_context
from pa_agent.data.snapshot import INDICATOR_WARMUP_BARS, build_display_frame
from pa_agent.data.snapshot_bundle import build_snapshot_bundle, compact_snapshot_context, serialize_frame_bars
from pa_agent.data.volume_context import snapshot_fetch_count
from pa_agent.experiments.replay import frozen_input_from_record
from pa_agent.experiments.study_protocol import write_once
from pa_agent.experiments.study_resources import freeze_host_evidence, input_from_evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--settings", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Frozen input already exists")
    settings = Settings.model_validate(json.loads(args.settings.read_text(encoding="utf-8")))
    assets = [asset for asset in settings.monitoring.assets if asset.enabled]
    if len(assets) != 1 or settings.prompt.experience_max_entries:
        raise ValueError("Pilot requires one asset and experience disabled")
    asset = assets[0]
    source = create_data_source(asset.source, settings=settings, config_dir=args.settings.parent)
    try:
        source.connect()
        source.subscribe(asset.symbol, asset.timeframe)
        as_of = time.time_ns() // 1_000_000
        count = snapshot_fetch_count(settings.general.analysis_bar_count, timeframe=asset.timeframe,
                                     indicator_warmup_bars=INDICATOR_WARMUP_BARS, vpa_enabled=True)
        bars = source.latest_snapshot(count)
        identity = resolved_identity_for_source(asset.source, source, symbol=asset.symbol)
        metadata = volume_metadata_for_source(asset.source, source, identity=identity, symbol=asset.symbol)
        frame = build_display_frame(bars, settings.general.analysis_bar_count, asset.symbol, asset.timeframe,
                                    now_ms=as_of, data_metadata=metadata)
        if frame is None:
            raise ValueError("Insufficient closed bars")
        htf = fetch_higher_timeframe_context(source=source, source_kind=asset.source, symbol=asset.symbol,
            current_timeframe=asset.timeframe, bar_count=settings.general.higher_timeframe_bar_count,
            override=settings.general.higher_timeframe_override, now_ms=as_of, resolved_identity=identity)
        bundle = build_snapshot_bundle(frame, htf, expected_identity=identity, captured_at_ms=as_of)
        if not bundle.data_quality.ok or htf.get("status") != "available":
            raise ValueError("Market/HTF snapshot quality unavailable")
        record = SimpleNamespace(meta=SimpleNamespace(symbol=asset.symbol, timeframe=asset.timeframe,
                    timestamp_local_ms=as_of), kline_data=serialize_frame_bars(frame),
            snapshot_context=compact_snapshot_context(frame), htf_context=serialize_higher_timeframe_context(htf),
            htf_kline_data=serialize_frame_bars(htf["frame"]), vpa_catalog=build_vpa_evidence_catalog(frame),
            role_runtime={"analysis_as_of_ms": as_of})
        prompts = Path(__file__).resolve().parents[1] / "prompt_engineering"
        # Research replays this persisted window, not the live frame's extra
        # indicator warmup. Measure that exact restored input consistently in
        # both PA and VPA, using the established Host freeze path.
        restored_frame, _, _ = frozen_input_from_record(record)
        record.vpa_catalog = build_vpa_evidence_catalog(restored_frame)
        evidence = freeze_host_evidence(record, prompt_dir=prompts)
        input_from_evidence(evidence)
        write_once(args.output, evidence)
        print(json.dumps({"snapshot_hash": evidence["snapshot_hash"], "as_of_ms": as_of,
                          "symbol": asset.symbol, "bars": len(frame.bars), "htf_bars": len(htf["frame"].bars),
                          "model_calls": 0}))
    finally:
        source.disconnect()


if __name__ == "__main__":
    main()
