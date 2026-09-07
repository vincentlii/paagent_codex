import json

from pa_agent.records.experience_reader import ExperienceReader


def test_causal_pool_excludes_unknown_future_revision_and_other_market(tmp_path):
    directory = tmp_path / "trend" / "success_cases"
    directory.mkdir(parents=True)
    base = {"source_identity": {"provider": "test", "requested_symbol": "A", "resolved_symbol": "A", "venue": "V", "instrument_type": "spot"},
            "timeframe": "1h", "event_at_ms": 100, "outcome_known_at_ms": 200, "revision_at_ms": 300}
    for index, patch in enumerate(({}, {"revision_at_ms": 600}, {"timeframe": "4h"}, {"outcome_known_at_ms": None})):
        (directory / f"2026-01-01_00-00-0{index}.json").write_text(json.dumps({"causal_context": {**base, **patch}, "summary": str(index)}), encoding="utf-8")
    reader = ExperienceReader(tmp_path)
    pool = reader.freeze_pool()
    rows = reader.read_for_stage2("trend", as_of_ms=500, source_identity=base["source_identity"], timeframe="1h", frozen_pool=pool)
    assert [row.content["summary"] for row in rows] == ["0"]
    (directory / "2026-01-01_00-00-00.json").write_text("{}", encoding="utf-8")
    assert reader.read_for_stage2("trend", as_of_ms=500, source_identity=base["source_identity"], timeframe="1h", frozen_pool=pool) == rows


def test_stage2_without_frozen_causal_boundary_loads_no_legacy_experience(tmp_path):
    reader = ExperienceReader(tmp_path)
    assert reader.read_for_stage2("trend") == []


def test_experiment_binds_pool_without_counting_it_as_a_new_market(tmp_path):
    from dataclasses import replace
    from pa_agent.config.settings import Settings
    from pa_agent.experiments.pa_vpa_aabb import freeze_experiment_input
    from pa_agent.orchestrator.two_stage import _build_empty_record
    from pa_agent.experiments.replay import frozen_input_from_record
    from tests.integration.conftest import make_frame

    reader = ExperienceReader(tmp_path)
    pool = reader.freeze_pool()
    frame = make_frame()
    frame = replace(frame, data_metadata={"volume_kind": "unknown", "volume_quality": "unknown", "timezone_basis": "UTC"}, bars=tuple(replace(bar, ts_open=1_700_000_000_000 - index * 3_600_000) for index, bar in enumerate(frame.bars)))
    first = freeze_experiment_input(frame, None, Settings(), analysis_fingerprint="a" * 64)
    bound = replace(frame, data_metadata={**frame.data_metadata,
                    "experience_pool": pool, "analysis_as_of_ms": 500})
    second = freeze_experiment_input(bound, None, Settings(), analysis_fingerprint="a" * 64)
    assert first.market_snapshot_hash == second.market_snapshot_hash
    assert first.frozen_input_hash != second.frozen_input_hash
    record = _build_empty_record(bound, Settings())
    record.vpa_catalog = second.vpa_catalog
    restored, _, _ = frozen_input_from_record(record)
    assert restored.data_metadata["experience_pool"] == pool
    replay = freeze_experiment_input(restored, None, Settings(), catalog=record.vpa_catalog, analysis_fingerprint="a" * 64)
    assert replay.frozen_input_hash == second.frozen_input_hash
