from decimal import Decimal

import pytest


def test_control_settings_survive_restart_other_controls_and_stale_save(tmp_path):
    from pa_agent.operations.runtime_state import RuntimeControlStore, RuntimeControlConflict

    path = tmp_path / "control.json"
    store = RuntimeControlStore(path, now_ms=lambda: 1000)
    state = store.set_demo_execution_settings(
        {"notional_usdt": "750", "limit_validity_hours": 6},
        expected_generation=0,
        actor="dashboard",
    )
    assert state.demo_execution_policy.notional_usdt == Decimal("750")
    assert state.demo_execution_policy.limit_validity_hours == 6
    assert state.demo_execution_policy.generation == 1

    with pytest.raises(RuntimeControlConflict):
        store.set_demo_execution_settings(
            {"notional_usdt": "900", "limit_validity_hours": 2},
            expected_generation=0,
            actor="stale",
        )
    store.set_desired_state("paused", actor="dashboard")
    store.set_desired_backend("codex_only", actor="dashboard")
    state = store.set_codex_fast_mode(True, expected_generation=3, actor="dashboard")
    state = store.set_analysis_profile(
        state.analysis_profile, expected_generation=4, actor="dashboard"
    )
    state = store.request_run_once(expected_generation=5, actor="dashboard")
    assert RuntimeControlStore(path).read().demo_execution_policy == state.demo_execution_policy
    assert state.demo_execution_policy.notional_usdt == Decimal("750")
    assert state.demo_execution_policy.generation == 1


def test_frozen_policy_sizes_and_expires_from_source_close_not_callback(tmp_path):
    from dataclasses import replace
    from pa_agent.operations.demo_settings import DemoExecutionPolicy
    from pa_agent.execution.demo_dispatch import OfficialExecutionDispatcher
    from pa_agent.execution.demo_contracts import DemoPaths
    from pa_agent.execution.demo_spool import DemoSpool, read_json
    from pa_agent.execution.demo_plans import bind_demo_limit
    from pa_agent.monitoring.frozen_task_codec import encode_task, decode_task
    from tests.unit.test_demo_execution_flow import source_record, config, NOW
    from tests.unit.test_demo_plans import contract
    from tests.unit.test_monitoring_recovery import _task

    spool = DemoSpool(DemoPaths(tmp_path))
    dispatcher = OfficialExecutionDispatcher(None, spool, config(), now_ms=lambda: NOW)
    original = _task()
    old = dispatcher.bind_task(original)
    dispatcher.apply_execution_settings(
        DemoExecutionPolicy(notional_usdt="750", limit_validity_hours=6, generation=1)
    )
    from pa_agent.monitoring.scheduler import AnalysisTask

    candidate = AnalysisTask.create(
        source=original.source,
        venue=original.venue,
        symbol=original.symbol,
        timeframe=original.timeframe,
        snapshot_hash=original.snapshot_hash,
        cohort_id="b" * 64,
        payload=original.payload,
    )
    new = dispatcher.bind_task(candidate)
    assert old.payload.demo_execution_policy["notional_usdt"] == "500"
    assert new.snapshot_hash == old.snapshot_hash
    encoded, hashed = encode_task(new)
    restored = decode_task(encoded, hashed)
    assert restored.payload.demo_execution_policy == new.payload.demo_execution_policy
    record, spec = source_record()
    for task, amount, hours in ((old, "500", 3), (new, "750", 6)):
        result = dispatcher.submit_official(
            record,
            spec,
            task_id=task.task_id,
            execution_payload=task.payload,
            execution_snapshot_hash=task.snapshot_hash,
        )
        assert result.status == "queued", result.reason
    from pa_agent.execution.demo_contracts import DemoIntent

    intents = [
        DemoIntent.model_validate(read_json(p))
        for p in (spool.paths.intents / "dispatch").glob("*.json")
    ]
    by_id = {i.task_id: i for i in intents}
    for task, amount, hours in ((old, "500", 3), (new, "750", 6)):
        intent = by_id[task.task_id]
        order = bind_demo_limit(
            intent, contract(), account_key="private-account", available_margin=Decimal("10000")
        )
        assert order.notional == Decimal(amount)
        assert intent.expires_at_ms == intent.source_bar_ms + (1 + hours) * 3_600_000
        assert order.stop == Decimal("49000") and order.tp1 == Decimal("52000")
    # A restart/retry and a new settings change cannot rebind accepted tasks.
    dispatcher.task_lookup = lambda task_id: old if task_id == old.task_id else None
    assert (
        dispatcher.bind_task(original).payload.demo_execution_policy
        == old.payload.demo_execution_policy
    )


def test_legacy_intent_digest_and_customer_identity_stay_valid():
    from pa_agent.execution.demo_contracts import DemoIntent, digest, client_id
    from tests.unit.test_demo_plans import intent

    raw = intent().model_dump(mode="json")
    raw.pop("demo_execution_policy", None)
    raw["content_digest"] = digest({k: v for k, v in raw.items() if k != "content_digest"})
    parsed = DemoIntent.model_validate(raw)
    assert parsed.content_digest == raw["content_digest"]
    assert parsed.demo_execution_policy is None
    assert client_id("account", parsed, "entry", 1) == client_id("account", intent(), "entry", 1)


def test_host_reports_settings_pending_until_idle_and_blocks_new_submission(tmp_path):
    from tests.unit.test_headless_monitor import _monitor

    monitor, _, _, service, _ = _monitor(tmp_path)
    try:
        control = monitor._control_store.set_demo_execution_settings(
            {"notional_usdt": "750", "limit_validity_hours": 6},
            expected_generation=0,
            actor="dashboard",
        )
        service.wait_idle = lambda timeout: False
        assert monitor._apply_control(control) == (True, "demo_execution_settings_pending")
        assert (
            monitor.runtime_snapshot(paused=True)["applied_demo_execution_policy"]["notional_usdt"]
            == "500"
        )
        assert not monitor._submission_matches_applied_control()
        service.wait_idle = lambda timeout: True
        assert monitor._apply_control(control, verify_latest=True) == (False, None)
        view = monitor.runtime_snapshot(paused=False)
        assert view["applied_demo_execution_policy"]["notional_usdt"] == "750"
        assert view["applied_demo_execution_policy"]["generation"] == 1
        assert monitor._submission_matches_applied_control()
    finally:
        monitor.shutdown()


@pytest.mark.parametrize(
    "settings",
    [
        {"notional_usdt": "NaN", "limit_validity_hours": 3},
        {"notional_usdt": "-1", "limit_validity_hours": 3},
        {"notional_usdt": True, "limit_validity_hours": 3},
        {"notional_usdt": "500", "limit_validity_hours": 0},
        {"notional_usdt": "500", "limit_validity_hours": 25},
        {"notional_usdt": "500", "limit_validity_hours": 1.5},
        {"notional_usdt": "500", "limit_validity_hours": True},
        {"notional_usdt": "500", "limit_validity_hours": 3, "leverage": 100},
    ],
)
def test_invalid_settings_never_write_control(tmp_path, settings):
    from pa_agent.operations.runtime_state import RuntimeControlStore

    path = tmp_path / "control.json"
    with pytest.raises(ValueError):
        RuntimeControlStore(path).set_demo_execution_settings(
            settings, expected_generation=0, actor="dashboard"
        )
    assert not path.exists()


def test_dashboard_saves_settings_with_auth_cas_and_applied_ack(tmp_path):
    import http.client
    import json
    import time
    from tests.unit.test_dashboard import _paths, _request, _running_server
    from pa_agent.operations.runtime_state import RuntimeStatusStore, RuntimeControlStore
    from pa_agent.operations.demo_settings import DemoExecutionPolicy
    from pa_agent.dashboard import build_trading_dashboard_payload

    paths = _paths(tmp_path)
    status = RuntimeStatusStore(paths.runtime_status_path)
    status.write(
        {
            "service_state": "running",
            "applied_demo_execution_policy": DemoExecutionPolicy().model_dump(mode="json"),
        }
    )
    server, thread = _running_server(paths)
    try:
        connection = http.client.HTTPConnection(*server.server_address, timeout=5)
        body = json.dumps(
            {
                "expected_generation": 0,
                "settings": {"notional_usdt": "750", "limit_validity_hours": 6},
            }
        )
        connection.request("POST", "/api/demo-execution-settings", body)
        response = connection.getresponse()
        assert response.status == 401
        response.read()
        response, _ = _request(
            connection,
            "POST",
            "/api/demo-execution-settings",
            body,
            origin="https://untrusted.example",
        )
        assert response.status == 403
        response, saved = _request(connection, "POST", "/api/demo-execution-settings", body)
        assert response.status == 202, saved
        view = build_trading_dashboard_payload(paths, now_ms=int(time.time() * 1000))[
            "demo_execution_settings"
        ]
        assert view["pending"] and view["desired"]["notional_usdt"] == "750"
        assert view["applied"]["notional_usdt"] == "500"
        response, _ = _request(connection, "POST", "/api/demo-execution-settings", body)
        assert response.status == 409
        status.write(
            {
                "service_state": "running",
                "applied_demo_execution_policy": RuntimeControlStore(paths.runtime_control_path)
                .read()
                .demo_execution_policy.model_dump(mode="json"),
            }
        )
        assert not build_trading_dashboard_payload(paths)["demo_execution_settings"]["pending"]
        # A running older Host cannot acknowledge this new protocol.
        status.write({"service_state": "running"})
        response, _ = _request(
            connection,
            "POST",
            "/api/demo-execution-settings",
            json.dumps(
                {
                    "expected_generation": 1,
                    "settings": {"notional_usdt": "900", "limit_validity_hours": 2},
                }
            ),
        )
        assert response.status == 503
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_custom_expiry_survives_worker_restart_and_cancels_only_at_deadline(tmp_path):
    from pa_agent.execution.demo_contracts import DemoPaths
    from pa_agent.execution.demo_spool import DemoSpool, atomic_json
    from pa_agent.execution.demo_store import DemoStore
    from pa_agent.execution.demo_worker import DemoWorker
    from pa_agent.execution.demo_plans import compile_demo_intent
    from pa_agent.operations.demo_settings import DemoExecutionPolicy
    from tests.unit.test_demo_execution_flow import source_record, config, NOW
    from tests.unit.test_demo_worker import Exchange

    cfg, paths, clock = config(), DemoPaths(tmp_path), [NOW]
    spool, exchange = DemoSpool(paths), Exchange()
    original_snapshot = exchange.snapshot
    exchange.snapshot = lambda **kwargs: original_snapshot(**kwargs).model_copy(
        update={"observed_at_ms": clock[0], "server_time_ms": clock[0]}
    )
    atomic_json(
        paths.control, {"cutover_id": cfg.cutover_id, "generated_at_ms": NOW, "running": True}
    )
    record, spec = source_record()
    policy = DemoExecutionPolicy(notional_usdt="750", limit_validity_hours=6, generation=1)
    intent = compile_demo_intent(
        record,
        spec,
        task_id="custom",
        config=cfg,
        frozen_execution_epoch=cfg.cutover_id,
        now_ms=NOW,
        market_snapshot_hash="a" * 64,
        demo_execution_policy=policy.model_dump(mode="json"),
    )
    spool.publish(intent)
    store = DemoStore(paths.state_db)
    worker = DemoWorker(cfg, paths, exchange, store, spool, now_ms=lambda: clock[0])
    worker.tick()
    assert exchange.calls[0][1].notional == Decimal("750")
    assert store.row(intent.content_digest)["entry_state"] == "submitted"
    worker.close()
    # Restart hours later does not refresh the absolute deadline or resubmit.
    clock[0] = intent.expires_at_ms - 1
    worker = DemoWorker(
        cfg, paths, exchange, DemoStore(paths.state_db), spool, now_ms=lambda: clock[0]
    )
    try:
        view = worker.tick()
        assert [c[0] for c in exchange.calls] == ["entry"]
        assert view["orders"][0]["expires_at_ms"] == intent.expires_at_ms
        clock[0] += 1
        worker.tick()
        assert [c[0] for c in exchange.calls] == ["entry", "cancel"]
        assert worker.store.row(intent.content_digest)["entry_state"] == "cancel_pending"
    finally:
        worker.close()
