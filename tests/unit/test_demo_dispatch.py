from dataclasses import replace

import pytest


def test_host_binding_is_optional_and_does_not_change_task_identity():
    from pa_agent.execution.demo_contracts import DemoConfig
    from pa_agent.execution.demo_dispatch import bind_execution_epoch, apply_execution_cutover
    from pa_agent.monitoring.analysis_runner import FrozenAnalysisPayload

    payload = FrozenAnalysisPayload(frame=None)
    config = DemoConfig(enabled=True, execution_target="okx_demo", cutover_id="epoch-1")
    bound = bind_execution_epoch(payload, config)
    assert bound.execution_epoch == "epoch-1" and bound.execution_target == "okx_demo"
    assert replace(bound, execution_target=None, execution_epoch=None) == payload
    with pytest.raises(ValueError, match="cutover_requires"):
        apply_execution_cutover(
            paused=False, host_idle=True, worker_idle=True, recovery_blocked=False, config=config
        )


def test_demo_dispatch_skips_old_tasks_and_still_observes_old_paper(tmp_path):
    from pa_agent.execution.demo_contracts import DemoConfig, DemoPaths
    from pa_agent.execution.demo_dispatch import OfficialExecutionDispatcher
    from pa_agent.execution.demo_spool import DemoSpool

    class Paper:
        enabled = True
        calls = []

        def submit_official(self, *args, **kwargs):
            self.calls.append("submit")

        def observe_official(self, *args, **kwargs):
            self.calls.append("observe")

    paper = Paper()
    dispatcher = OfficialExecutionDispatcher(
        paper,
        DemoSpool(DemoPaths(tmp_path)),
        DemoConfig(enabled=True, execution_target="okx_demo", cutover_id="epoch-1"),
        now_ms=lambda: 1000,
    )
    result = dispatcher.submit_official(object(), object(), task_id="old")
    assert result.status == "skipped_cutover"
    dispatcher.observe_official(None)
    assert paper.calls == ["observe"]


def test_host_codec_preserves_binding_and_legacy_bytes_without_changing_market_identity(tmp_path):
    import json
    from pa_agent.execution.demo_contracts import DemoConfig
    from pa_agent.execution.demo_dispatch import bind_execution_epoch
    from pa_agent.monitoring.audit import SchedulerAuditStore
    from pa_agent.monitoring.frozen_task_codec import encode_task, decode_task
    from pa_agent.monitoring.scheduler import SubmissionResult
    from tests.unit.test_monitoring_recovery import _task

    task = _task()
    legacy, old_digest = encode_task(task)
    assert "execution_target" not in json.loads(legacy)["payload"]
    cfg = DemoConfig(enabled=True, execution_target="okx_demo", cutover_id="epoch-1")
    bound = replace(task, payload=bind_execution_epoch(task.payload, cfg))
    encoded, hashed = encode_task(bound)
    restored = decode_task(encoded, hashed)
    assert restored.task_id == task.task_id and restored.snapshot_hash == task.snapshot_hash
    assert restored.cohort_id == task.cohort_id and restored.payload.execution_epoch == "epoch-1"
    assert encode_task(
        replace(bound, payload=replace(bound.payload, execution_target=None, execution_epoch=None))
    ) == (legacy, old_digest)
    # Existing codec normalizes InstrumentSpec numbers on decode; compare like for like.
    assert encode_task(
        replace(
            restored, payload=replace(restored.payload, execution_target=None, execution_epoch=None)
        )
    ) == encode_task(decode_task(legacy, old_digest))
    store = SchedulerAuditStore(tmp_path / "monitoring.sqlite3", require_frozen_payload=True)
    assert store.record_submission(
        bound, SubmissionResult("accepted", bound.task_id, bound.asset_key)
    )
    assert store.load_task(task.task_id).payload.execution_epoch == "epoch-1"
    assert store.load_task("missing") is None


def test_prepared_cutover_requires_fresh_paused_host_and_idle_worker(tmp_path, monkeypatch):
    from pa_agent.execution.demo_contracts import DemoPaths
    from pa_agent.execution.demo_dispatch import require_cutover
    from pa_agent.execution.demo_spool import atomic_json
    from pa_agent.execution.demo_worker import main
    from tests.unit.test_demo_execution_flow import config

    cfg, paths = config(), DemoPaths(tmp_path / "execution")
    config_path, host_path = tmp_path / "config.json", tmp_path / "host.json"
    atomic_json(config_path, cfg.model_dump(mode="json"))
    monkeypatch.setenv("PA_AGENT_DEMO_CONFIG", str(config_path))
    monkeypatch.setenv("PA_AGENT_EXECUTION_HOME", str(paths.root))
    monkeypatch.setenv("PA_AGENT_HOST_STATUS", str(host_path))
    monkeypatch.setattr("time.time", lambda: 1800003600.5)
    now = 1800003600500
    host = {
        "service_state": "running",
        "updated_at_ms": now,
        "scheduler": {"running": 0, "queued": 0, "unfinished": 0},
        "recovery_blocked_assets": [],
    }
    atomic_json(host_path, host)
    atomic_json(
        paths.projection,
        {
            "state": "ready",
            "exchange_observed_at_ms": now,
            "coverage": {"orders": True},
            "total_active_trades": 0,
            "outside_orders": 0,
            "positions": [],
        },
    )
    assert main(["--prepare-cutover"]) == 2
    assert not (paths.intents / "cutover.json").exists()
    host["service_state"] = "paused"
    atomic_json(host_path, host)
    assert main(["--prepare-cutover"]) == 0
    require_cutover(paths, cfg)
