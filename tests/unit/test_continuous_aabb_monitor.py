from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from pa_agent.experiments.continuous import (
    ContinuousAabbMonitor,
    ContinuousAabbPolicy,
    PreparedAabbSource,
    is_continuous_source_record,
)
from pa_agent.experiments.store import AnalysisExperimentStore
from pa_agent.util.threading import CancelToken


@dataclass(frozen=True)
class _RunResult:
    experiment_id: str
    status: str
    protocol_report: dict


def _prepared(path: Path, index: int) -> PreparedAabbSource:
    return PreparedAabbSource(
        source_path=path,
        source_run_id=f"source-{index}",
        frozen=SimpleNamespace(
            cohort_id=f"{index:064x}",
            market_snapshot_hash=f"{index + 100:064x}",
            frame=SimpleNamespace(
                bars=(SimpleNamespace(ts_open=index * 1_000, closed=True),),
            ),
        ),
    )


def test_continuous_source_requires_successful_production_shadow() -> None:
    valid = SimpleNamespace(
        experiment_context=None,
        exception=None,
        stage1_diagnosis={"cycle_position": "trend"},
        stage2_decision={"order": {"type": "no_order"}},
        kline_data=[{"close": 1}],
        vpa_catalog={"catalog_id": "catalog"},
        run_outcome={
            "status": "completed",
            "official_path": "pa_only",
            "effective_vpa_mode": "shadow",
        },
    )

    assert is_continuous_source_record(valid) is True
    assert is_continuous_source_record(
        SimpleNamespace(
            **{
                **valid.__dict__,
                "run_outcome": {
                    **valid.run_outcome,
                    "status": "completed_with_warnings",
                },
            }
        )
    ) is True
    assert is_continuous_source_record(
        SimpleNamespace(**{**valid.__dict__, "experiment_context": {"arm_id": "A1"}})
    ) is False
    assert is_continuous_source_record(
        SimpleNamespace(
            **{
                **valid.__dict__,
                "run_outcome": {**valid.run_outcome, "effective_vpa_mode": "off"},
            }
        )
    ) is False
    assert is_continuous_source_record(
        SimpleNamespace(**{**valid.__dict__, "vpa_catalog": None})
    ) is False


def test_monitor_processes_oldest_eligible_sources_with_a_bounded_cycle(
    tmp_path: Path,
) -> None:
    records_dir = tmp_path / "pending"
    records_dir.mkdir()
    paths = [records_dir / f"record-{index}.json" for index in range(3)]
    for path in paths:
        path.write_text("{}", encoding="utf-8")

    prepared = {path: _prepared(path, index + 1) for index, path in enumerate(paths)}
    executed: list[str] = []

    def execute(source, _cancel):
        executed.append(source.source_run_id)
        return _RunResult(
            experiment_id=f"aabb-{source.source_run_id}",
            status="completed",
            protocol_report={"valid": True, "errors": []},
        )

    monitor = ContinuousAabbMonitor(
        records_dir=records_dir,
        prepare_source=lambda path: prepared[path],
        execute_source=execute,
        experiment_store=AnalysisExperimentStore(tmp_path / "analysis.sqlite3"),
        policy=ContinuousAabbPolicy(max_records_per_cycle=2),
        now_ms=lambda: 1_000,
    )

    outcomes = monitor.run_cycle(CancelToken())

    assert executed == ["source-1", "source-2"]
    assert [outcome.status for outcome in outcomes] == ["completed", "completed"]


def test_monitor_orders_by_frozen_market_time_not_file_metadata(tmp_path: Path) -> None:
    records_dir = tmp_path / "pending"
    records_dir.mkdir()
    newer_path = records_dir / "a-newer-file.json"
    older_path = records_dir / "z-older-file.json"
    newer_path.write_text("{}", encoding="utf-8")
    older_path.write_text("{}", encoding="utf-8")
    newer = _prepared(newer_path, 2)
    older = _prepared(older_path, 1)
    sources = {newer_path: newer, older_path: older}
    executed: list[str] = []

    def execute(source, _cancel):
        executed.append(source.source_run_id)
        return _RunResult(
            experiment_id=f"aabb-{source.source_run_id}",
            status="completed",
            protocol_report={"valid": True, "errors": []},
        )

    monitor = ContinuousAabbMonitor(
        records_dir=records_dir,
        prepare_source=lambda path: sources[path],
        execute_source=execute,
        experiment_store=AnalysisExperimentStore(tmp_path / "analysis.sqlite3"),
        policy=ContinuousAabbPolicy(max_records_per_cycle=2),
    )

    monitor.run_cycle(CancelToken())

    assert executed == ["source-1", "source-2"]


def test_monitor_continues_after_one_source_fails_and_does_not_duplicate(
    tmp_path: Path,
) -> None:
    records_dir = tmp_path / "pending"
    records_dir.mkdir()
    first_path = records_dir / "first.json"
    second_path = records_dir / "second.json"
    first_path.write_text("{}", encoding="utf-8")
    second_path.write_text("{}", encoding="utf-8")
    prepared = {
        first_path: _prepared(first_path, 1),
        second_path: _prepared(second_path, 2),
    }
    calls: list[str] = []
    prepare_calls: list[Path] = []

    def prepare(path):
        prepare_calls.append(path)
        return prepared[path]

    def execute(source, _cancel):
        calls.append(source.source_run_id)
        if source.source_run_id == "source-1":
            raise TimeoutError("provider timed out")
        return _RunResult(
            experiment_id="aabb-source-2",
            status="completed",
            protocol_report={"valid": True, "errors": []},
        )

    monitor = ContinuousAabbMonitor(
        records_dir=records_dir,
        prepare_source=prepare,
        execute_source=execute,
        experiment_store=AnalysisExperimentStore(tmp_path / "analysis.sqlite3"),
        policy=ContinuousAabbPolicy(
            max_records_per_cycle=2,
            max_attempts=2,
            retry_backoff_seconds=60,
        ),
        now_ms=lambda: 1_000,
    )

    outcomes = monitor.run_cycle(CancelToken())
    repeated = monitor.run_cycle(CancelToken())

    assert calls == ["source-1", "source-2"]
    assert prepare_calls == [first_path, second_path]
    assert [outcome.status for outcome in outcomes] == ["failed", "completed"]
    assert repeated == ()


def test_protocol_invalid_result_is_terminal_not_retried(tmp_path: Path) -> None:
    records_dir = tmp_path / "pending"
    records_dir.mkdir()
    path = records_dir / "source.json"
    path.write_text("{}", encoding="utf-8")
    source = _prepared(path, 1)
    calls = 0

    def execute(_source, _cancel):
        nonlocal calls
        calls += 1
        return _RunResult(
            experiment_id="aabb-invalid",
            status="protocol_invalid",
            protocol_report={"valid": False, "errors": ["arm_drift"]},
        )

    monitor = ContinuousAabbMonitor(
        records_dir=records_dir,
        prepare_source=lambda _path: source,
        execute_source=execute,
        experiment_store=AnalysisExperimentStore(tmp_path / "analysis.sqlite3"),
        policy=ContinuousAabbPolicy(max_attempts=3),
        now_ms=lambda: 1_000,
    )

    assert monitor.run_cycle(CancelToken())[0].status == "protocol_invalid"
    assert monitor.run_cycle(CancelToken()) == ()
    assert calls == 1


def test_monitor_surfaces_claim_store_failure_without_calling_model(tmp_path: Path) -> None:
    records_dir = tmp_path / "pending"
    records_dir.mkdir()
    path = records_dir / "source.json"
    path.write_text("{}", encoding="utf-8")
    source = _prepared(path, 1)

    class BrokenStore:
        def claim_continuous_snapshot(self, **_kwargs):
            return SimpleNamespace(
                acquired=False,
                reason="store_error",
                lease_token=None,
            )

    monitor = ContinuousAabbMonitor(
        records_dir=records_dir,
        prepare_source=lambda _path: source,
        execute_source=lambda *_args: (_ for _ in ()).throw(
            AssertionError("model must not run without a lease")
        ),
        experiment_store=BrokenStore(),
        policy=ContinuousAabbPolicy(),
    )

    outcome = monitor.run_cycle(CancelToken())

    assert len(outcome) == 1
    assert outcome[0].status == "store_error"
