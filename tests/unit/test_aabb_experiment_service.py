from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from pa_agent.ai.vpa_measurements import build_vpa_evidence_catalog
from pa_agent.config.settings import Settings
from pa_agent.data.snapshot_bundle import compact_snapshot_context
from pa_agent.experiments.continuous import PreparedAabbSource
from pa_agent.experiments.pa_vpa_aabb import AabbRunResult
from pa_agent.experiments.service import AabbExperimentService
from pa_agent.util.threading import CancelToken
from tests.integration.conftest import make_frame
from tests.unit.test_pa_vpa_aabb_protocol import _record


class _Store:
    def find_completed_experiment(self, **_kwargs):
        return {"experiment_id": "aabb-existing"}


class _Runner:
    def run(self, *_args, **_kwargs):
        raise AssertionError("a completed experiment must not call the model runner again")


class _FailingPaper:
    enabled = True

    def record_aabb(self, _result, _frozen):
        raise sqlite3.OperationalError("database is locked")


class _SuccessfulPaper:
    enabled = True

    def record_aabb(self, _result, _frozen):
        return ()


def _service(monkeypatch, paper) -> AabbExperimentService:
    result = AabbRunResult(
        experiment_id="aabb-existing",
        status="completed",
        records=(object(), object(), object(), object()),
        protocol_report={"valid": True, "errors": []},
        external_comparison={},
    )
    monkeypatch.setattr(
        AabbExperimentService,
        "_result_from_manifest",
        staticmethod(lambda _manifest: result),
    )
    service = object.__new__(AabbExperimentService)
    service._store = _Store()
    service._runner = _Runner()
    service._paper = paper
    return service


def _source() -> PreparedAabbSource:
    return PreparedAabbSource(
        source_path=Path("source.json"),
        source_run_id="source-run",
        frozen=SimpleNamespace(
            cohort_id="c" * 64,
            market_snapshot_hash="m" * 64,
        ),
    )


def test_service_reuses_completed_experiment_and_retries_only_paper(monkeypatch) -> None:
    service = _service(monkeypatch, _FailingPaper())

    result = service.run(_source(), CancelToken(), reuse_completed=True)

    assert result.experiment_id == "aabb-existing"
    assert result.reused_experiment is True
    assert result.error == "research_paper_persistence_failed:OperationalError"
    assert result.retryable is True


def test_service_marks_reused_experiment_complete_after_paper_recovers(monkeypatch) -> None:
    service = _service(monkeypatch, _SuccessfulPaper())

    result = service.run(_source(), CancelToken(), reuse_completed=True)

    assert result.status == "completed"
    assert result.error is None
    assert result.retryable is False


def test_service_prepares_exact_frozen_input_from_production_shadow_record(
    tmp_path: Path,
) -> None:
    fixture = make_frame()
    frame = replace(
        fixture,
        bars=tuple(
            replace(bar, ts_open=1_700_000_000_000 - index * 3_600_000)
            for index, bar in enumerate(fixture.bars)
        ),
        data_metadata={
            "volume_kind": "unknown",
            "volume_quality": "unknown",
            "timezone_basis": "UTC",
            "seasonality_history_bars": 0,
        },
    )
    catalog = build_vpa_evidence_catalog(frame)
    record = _record("source-shadow", mode="off", catalog=None).model_copy(
        update={
            "kline_data": [bar.__dict__ for bar in frame.bars],
            "snapshot_context": compact_snapshot_context(frame),
            "vpa_catalog": catalog,
            "run_outcome": {
                "status": "completed",
                "official_path": "pa_only",
                "requested_vpa_mode": "shadow",
                "effective_vpa_mode": "shadow",
            },
        }
    )
    source_path = tmp_path / "source.json"
    source_path.write_text(record.model_dump_json(), encoding="utf-8")
    settings = Settings()
    service = AabbExperimentService(
        settings=settings,
        prompt_dir=tmp_path / "prompts",
        experience_dir=tmp_path / "experience",
        pending_dir=tmp_path / "pending",
        db_path=tmp_path / "audit" / "analysis.sqlite3",
        paper_db_path=tmp_path / "paper.sqlite3",
    )

    prepared = service.prepare_source(source_path, production_shadow_only=True)

    assert prepared.source_run_id == "source-shadow"
    assert prepared.frozen.catalog_id == catalog["catalog_id"]
    assert prepared.frozen.snapshot_hash == catalog["snapshot_hash"]
    assert prepared.frozen.market_snapshot_hash
