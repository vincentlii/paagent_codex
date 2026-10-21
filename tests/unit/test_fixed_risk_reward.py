"""The fixed target is Host arithmetic before the existing trade decision gates."""

import copy
import json

import jsonschema
import pytest

from pa_agent.ai.fact_catalog import build_analysis_fact_catalog
from pa_agent.ai.pa_provider_contracts import materialize_stage2_draft, schema_for_provider_draft
from tests.integration.conftest import VALID_STAGE1, VALID_STAGE2, make_frame
from tests.unit.test_pa_provider_contracts import _trace_draft


def trade_draft():
    draft = copy.deepcopy(VALID_STAGE2)
    draft.update(protocol_version="pa_draft_v2", decision_intent="trade")
    draft.pop("terminal")
    draft.pop("next_cycle_prediction")
    for key in ("diagnosis_confidence", "diagnosis_confidence_reasoning",
                "take_profit_price", "take_profit_price_2"):
        draft["decision"].pop(key)
    draft["decision_trace"] = [
        item for item in _trace_draft(draft["decision_trace"]) if item["node_id"] != "10.3"
    ]
    return draft


@pytest.mark.parametrize("side,entry,stop,expected", [
    ("做多", 100, 97, 104.5), ("做空", 100, 103, 95.5),
])
def test_program_target_precedes_equation_and_does_not_change_entry_stop(side, entry, stop, expected):
    from pa_agent.ai.take_profit_policy import FixedRiskRewardPolicy

    policy = FixedRiskRewardPolicy(reward_risk_ratio="1.5", tick_size="0.1")
    draft = trade_draft()
    draft["decision"].update(order_direction=side, entry_price=entry, stop_loss_price=stop,
                             estimated_win_rate=45)
    before = copy.deepcopy(draft)
    schema = schema_for_provider_draft("stage2", take_profit_policy=policy)
    jsonschema.Draft7Validator(schema).validate(draft)
    result = materialize_stage2_draft(draft, stage1=VALID_STAGE1,
        catalog=build_analysis_fact_catalog(make_frame()), take_profit_policy=policy)
    assert draft == before
    assert result["decision"]["entry_price"] == entry
    assert result["decision"]["stop_loss_price"] == stop
    assert result["decision"]["take_profit_price"] == expected
    assert result["decision"]["take_profit_price_2"] is None
    assert result["terminal"]["outcome"] == "trade"
    draft["decision"]["estimated_win_rate"] = 35
    rejected = materialize_stage2_draft(draft, stage1=VALID_STAGE1,
        catalog=build_analysis_fact_catalog(make_frame()), take_profit_policy=policy)
    assert rejected["terminal"]["outcome"] == "reject"


@pytest.mark.parametrize("ratio,long_target,short_target", [
    ("1", "103", "97"), ("1.5", "104.5", "95.5"),
    ("2", "106", "94"), ("2.5", "107.5", "92.5"),
])
def test_four_allowed_targets_and_conservative_tick_rounding(ratio, long_target, short_target):
    from decimal import Decimal
    from pa_agent.ai.take_profit_policy import FixedRiskRewardPolicy

    policy = FixedRiskRewardPolicy(reward_risk_ratio=ratio, tick_size="0.1")
    assert policy.target(100, 97, "做多") == Decimal(long_target)
    assert policy.target(100, 103, "做空") == Decimal(short_target)
    half_tick = FixedRiskRewardPolicy(tick_size="0.1")
    assert half_tick.target("100", "99.9", "做多") == Decimal("100.1")
    assert half_tick.target("100", "100.1", "做空") == Decimal("99.9")


def test_equation_uses_rounded_reward_instead_of_requested_ratio():
    from pa_agent.ai.take_profit_policy import FixedRiskRewardPolicy

    draft = trade_draft()
    draft["decision"].update(entry_price=100, stop_loss_price=99.9, estimated_win_rate=45)
    result = materialize_stage2_draft(draft, stage1=VALID_STAGE1,
        catalog=build_analysis_fact_catalog(make_frame()),
        take_profit_policy=FixedRiskRewardPolicy(tick_size="0.1"))
    assert result["decision"]["take_profit_price"] == 100.1  # 1R after half-tick rounding
    assert result["terminal"]["outcome"] == "reject"  # 45% cannot pass a 1R target


def test_fixed_schema_forbids_model_targets_and_pa_schema_stays_unchanged():
    from pa_agent.ai.take_profit_policy import FixedRiskRewardPolicy
    from pa_agent.ai.pa_provider_contracts import schema_for_joint_stage2_draft, provider_draft_schema_digest

    policy = FixedRiskRewardPolicy(tick_size="0.1")
    for schema in (schema_for_provider_draft("stage2", take_profit_policy=policy),
                   schema_for_joint_stage2_draft(take_profit_policy=policy)):
        decision = schema["properties"]["decision"]
        assert "take_profit_price" not in decision["properties"]
        assert "take_profit_price_2" not in decision["required"]
        draft = trade_draft()
        draft["decision"]["take_profit_price"] = 9999
        assert list(jsonschema.Draft7Validator(decision).iter_errors(draft["decision"]))
    assert "take_profit_price" in schema_for_provider_draft("stage2")["properties"]["decision"]["properties"]
    assert provider_draft_schema_digest("stage1", take_profit_policy=policy) == provider_draft_schema_digest("stage1")
    assert provider_draft_schema_digest("stage2", take_profit_policy=policy) != provider_draft_schema_digest("stage2")


def test_no_trade_works_without_tick_but_trade_cannot_guess_tick():
    from pa_agent.ai.take_profit_policy import FixedRiskRewardPolicy
    from pa_agent.ai.json_validator import JsonValidator, Ok, ValidationError
    from pa_agent.ai.retry_policy import should_retry
    from pa_agent.config.settings import ValidationSettings
    from tests.fixtures.ai_payloads import VALID_STAGE2_NO_ORDER

    policy = FixedRiskRewardPolicy()
    validator = JsonValidator(take_profit_policy=policy)
    wait = copy.deepcopy(VALID_STAGE2_NO_ORDER)
    wait.update(protocol_version="pa_draft_v2", decision_intent="wait")
    wait.pop("terminal")
    wait.pop("next_cycle_prediction")
    for key in ("diagnosis_confidence", "diagnosis_confidence_reasoning",
                "take_profit_price", "take_profit_price_2"):
        wait["decision"].pop(key, None)
    wait["decision_trace"] = _trace_draft(wait["decision_trace"])
    result = validator.validate("stage2", json.dumps(wait), kline_frame=make_frame(), stage1_json=VALID_STAGE1)
    assert isinstance(result, Ok), result
    assert result.obj["decision"]["take_profit_price"] is None
    result = validator.validate("stage2", json.dumps(trade_draft()), kline_frame=make_frame(), stage1_json=VALID_STAGE1)
    assert isinstance(result, ValidationError)
    assert "fixed_rr_target_tick_unavailable" in result.message
    assert not should_retry(result.category, result.invalid_fields, result.missing_fields,
        attempt=0, settings=ValidationSettings(), error_code=result.code)


def test_profile_controls_are_strict_versioned_and_legacy_identity_is_unchanged():
    from pa_agent.operations.analysis_profile import AnalysisProfile, profile_fingerprint, profile_identity_payload
    from pa_agent.config.settings import Settings
    from pa_agent.monitoring.task_factory import create_frozen_analysis_task
    from pa_agent.monitoring.frozen_task_codec import encode_task, decode_task

    pa = AnalysisProfile()
    assert profile_fingerprint(pa) == "a7d04f3fed76d8d8ddbea9b61dd8b2c2b9fad191566bad3ff9337fa537a95f8c"
    assert "take_profit_mode" not in profile_identity_payload(pa)
    fixed = AnalysisProfile(take_profit_mode="fixed_rr")
    for values in ({"take_profit_mode": "automatic"}, {"fixed_reward_risk_ratio": "3"},
                   {"fixed_reward_risk_ratio": True}, {"fixed_reward_risk_ratio": 1.5}):
        with pytest.raises(ValueError):
            AnalysisProfile(**values)
    tasks = [create_frozen_analysis_task(make_frame(), {}, Settings(), source="test",
        analysis_fingerprint="a"*64, analysis_profile=p, take_profit_tick_size=tick)
        for p, tick in ((pa, None), (fixed, "0.1"), (fixed, "0.01"))]
    assert len({task.task_id for task in tasks}) == 3
    assert len({task.snapshot_hash for task in tasks}) == 1
    assert len({task.cohort_id for task in tasks}) == 3
    for task in tasks:
        restored = decode_task(*encode_task(task))
        assert restored.task_id == task.task_id
        assert restored.payload.take_profit_tick_size == task.payload.take_profit_tick_size


@pytest.mark.parametrize("codex", [False, True])
def test_joint_chain_delivers_the_same_fixed_target_contract_to_backend_and_validator(codex):
    from pa_agent.ai.take_profit_policy import FixedRiskRewardPolicy
    from pa_agent.ai.pa_provider_contracts import provider_draft_schema_digest
    from tests.unit.test_joint_pipeline_runtime import run_joint, joint_draft

    policy = FixedRiskRewardPolicy(tick_size="0.1")
    draft = joint_draft()
    for key in ("take_profit_price", "take_profit_price_2"):
        draft["decision"].pop(key)
    captured = []
    record, calls, writer = run_joint(codex=codex, stage2_drafts=[draft],
                                     captured_requests=captured, take_profit_policy=policy)
    assert record.exception is None, record.exception
    assert calls == ["stage1", "vpa_evidence", "stage2"]
    assert record.stage2_decision["decision"]["take_profit_price"] == 2080
    assert record.stage2_decision["decision"]["take_profit_price_2"] is None
    assert record.role_runtime["take_profit_policy"] == policy.model_dump(mode="json")
    digest = record.role_runtime["provider_contract_digests"]["stage2"]
    assert digest == provider_draft_schema_digest("stage2", joint_stage2=True, take_profit_policy=policy)
    if codex:
        request = next(r for r in captured if r.role == "stage2")
        assert "take_profit_price" not in request.schema["properties"]["decision"]["properties"]
        protocol = request.agent_context["role_protocol"]
        assert protocol["take_profit_policy"] == policy.model_dump(mode="json")
        assert protocol["provider_contract_digest"] == digest
        assert request.messages == [] or all(m["role"] == "user" for m in request.messages)
    else:
        messages = "\n".join(m["content"] for m in record.stage2_messages)
        assert "固定盈亏比" in messages
        assert digest in messages
    writer.save_full.assert_called_once()


def test_demo_preserves_frozen_policy_target_and_rejects_changed_tick(tmp_path):
    from decimal import Decimal
    from pa_agent.ai.take_profit_policy import FixedRiskRewardPolicy
    from pa_agent.execution.demo_plans import compile_demo_intent, bind_demo_limit
    from pa_agent.execution.demo_contracts import DemoIntent
    from tests.unit.test_demo_execution_flow import source_record, config, NOW
    from tests.unit.test_demo_plans import contract

    record, spec = source_record()
    policy = FixedRiskRewardPolicy(tick_size="0.1")
    record.role_runtime = {"take_profit_policy": policy.model_dump(mode="json")}
    record.stage2_decision["decision"]["take_profit_price"] = 51500
    intent = compile_demo_intent(record, spec, task_id="fixed-plan", config=config(),
        frozen_execution_epoch="epoch-1", now_ms=NOW, market_snapshot_hash="a"*64)
    restored = DemoIntent.model_validate_json(intent.model_dump_json())
    request = bind_demo_limit(restored, contract(), account_key="account", available_margin=Decimal(10000))
    assert (request.px, request.stop, request.tp1) == (Decimal(50000), Decimal(49000), Decimal(51500))
    assert restored.take_profit_policy == policy
    with pytest.raises(ValueError, match="fixed_rr_target_tick_changed"):
        bind_demo_limit(restored, contract(tick="1"), account_key="account", available_margin=Decimal(10000))
    record.stage2_decision["decision"]["take_profit_price"] = 52000
    with pytest.raises(ValueError, match="fixed_rr_target_mismatch"):
        compile_demo_intent(record, spec, task_id="bad-plan", config=config(),
            frozen_execution_epoch="epoch-1", now_ms=NOW, market_snapshot_hash="a"*64)


def test_fresh_okx_tick_is_frozen_from_projection_and_plan_view_keeps_actual_ratio(tmp_path):
    from pa_agent.execution.demo_contracts import DemoPaths
    from pa_agent.execution.demo_dispatch import OfficialExecutionDispatcher
    from pa_agent.execution.demo_spool import DemoSpool, atomic_json
    from pa_agent.execution.demo_store import DemoStore
    from pa_agent.execution.demo_worker import DemoWorker
    from tests.unit.test_demo_execution_flow import source_record, config, NOW
    from tests.unit.test_demo_worker import Exchange
    from pa_agent.execution.demo_plans import compile_demo_intent
    from pa_agent.ai.take_profit_policy import FixedRiskRewardPolicy

    paths = DemoPaths(tmp_path)
    spool, exchange = DemoSpool(paths), Exchange()
    dispatcher = OfficialExecutionDispatcher(None, spool, config(), now_ms=lambda: NOW)
    dispatcher.publish_control(running=True)
    record, spec = source_record()
    record.role_runtime = {"take_profit_policy": FixedRiskRewardPolicy(tick_size="0.1").model_dump(mode="json")}
    record.stage2_decision["decision"]["take_profit_price"] = 51500
    intent = compile_demo_intent(record, spec, task_id="fixed", config=config(),
        frozen_execution_epoch="epoch-1", now_ms=NOW, market_snapshot_hash="a"*64)
    spool.publish(intent)
    worker = DemoWorker(config(), paths, exchange, DemoStore(paths.state_db), spool, now_ms=lambda: NOW)
    try:
        projection = worker.tick()
        assert dispatcher.take_profit_tick_size("BTCUSDT") == "0.1"
        order = projection["orders"][0]
        assert order["take_profit_mode"] == "fixed_rr"
        assert order["requested_reward_risk_ratio"] == "1.5"
        assert order["actual_reward_risk_ratio"] == "1.5"
        projection["exchange_observed_at_ms"] = NOW - 30001
        atomic_json(paths.projection, projection)
        assert dispatcher.take_profit_tick_size("BTCUSDT") is None
        projection["exchange_observed_at_ms"] = NOW
        projection["price_rules"] *= 2
        atomic_json(paths.projection, projection)
        assert dispatcher.take_profit_tick_size("BTCUSDT") is None
        assert dispatcher.take_profit_tick_size("XAUUSDT") is None
    finally:
        worker.close()


@pytest.mark.parametrize("mode", ["deepseek_only", "codex_only", "auto"])
def test_factory_recovery_and_cold_fallback_use_task_policy_not_current_settings(tmp_path, monkeypatch, mode):
    from unittest.mock import MagicMock
    from pa_agent.operations.analysis_profile import AnalysisProfile
    from pa_agent.config.settings import Settings
    from pa_agent.config.paths import PROMPT_DIR
    from pa_agent.monitoring.production import ProductionOrchestratorFactory
    from pa_agent.monitoring.task_factory import create_frozen_analysis_task
    from pa_agent.monitoring.frozen_task_codec import encode_task, decode_task

    settings = Settings()
    settings.provider.api_key = "offline-test-key"
    monkeypatch.setattr("pa_agent.monitoring.production.create_ai_client", lambda *a, **k: MagicMock())
    factory = ProductionOrchestratorFactory(settings=settings, pending_writer=object(),
        prompt_dir=PROMPT_DIR, experience_dir=tmp_path, codex_backend_factory=lambda *_: MagicMock())
    fixed = AnalysisProfile(take_profit_mode="fixed_rr", fixed_reward_risk_ratio="2.5")
    task = create_frozen_analysis_task(make_frame(), {}, settings, source="test",
        analysis_fingerprint="a"*64, analysis_backend_mode=mode,
        analysis_backend_identity={"runtime_asset_digest": "b"*64},
        analysis_profile=fixed, take_profit_tick_size="0.1")
    original = factory(decode_task(*encode_task(task)))
    runners = [original] if mode != "auto" else [original._primary, original._cold_fallback]
    for runner in runners:
        assert runner.take_profit_policy.reward_risk_ratio == "2.5"
        assert runner._assembler.take_profit_policy == runner._validator.take_profit_policy == runner.take_profit_policy
    factory.apply_analysis_profile(fixed)
    legacy = create_frozen_analysis_task(make_frame(), {}, settings, source="test",
        analysis_fingerprint="a"*64)
    assert factory(legacy).take_profit_policy is None


@pytest.mark.parametrize("codex", [False, True])
def test_directed_repair_keeps_fixed_schema_and_program_target(codex):
    from pa_agent.ai.take_profit_policy import FixedRiskRewardPolicy
    from tests.unit.test_joint_pipeline_runtime import run_joint, joint_draft

    good = joint_draft()
    for key in ("take_profit_price", "take_profit_price_2"):
        good["decision"].pop(key)
    bad = copy.deepcopy(good)
    bad["decision_trace"][0]["answer"] = "unsupported-answer"
    record, calls, _ = run_joint(codex=codex, retry=True, stage2_drafts=[bad, good],
        take_profit_policy=FixedRiskRewardPolicy(tick_size="0.1"))
    assert record.exception is None, record.exception
    assert calls == ["stage1", "vpa_evidence", "stage2", "stage2"]
    assert record.stage2_decision["decision"]["take_profit_price"] == 2080


def test_profile_http_cas_and_old_host_cannot_accept_fixed_mode(tmp_path):
    import http.client
    from pa_agent.operations.runtime_state import RuntimeControlStore, RuntimeStatusStore
    from pa_agent.operations.analysis_profile import AnalysisProfile
    from pa_agent.dashboard import build_trading_dashboard_payload
    from tests.unit.test_dashboard import _paths, _request, _running_server

    paths = _paths(tmp_path)
    status = {"service_state": "paused"}
    RuntimeStatusStore(paths.runtime_status_path).write(status)
    server, thread = _running_server(paths)
    try:
        connection = http.client.HTTPConnection(*server.server_address, timeout=5)
        profile = AnalysisProfile(take_profit_mode="fixed_rr").model_dump(mode="json")
        body = json.dumps({"expected_generation": 0, "profile": profile})
        response, _ = _request(connection, "POST", "/api/profile", body)
        assert response.status == 503
        assert RuntimeControlStore(paths.runtime_control_path).read().generation == 0
        RuntimeStatusStore(paths.runtime_status_path).write(status | {"take_profit_policy_version": "fixed_rr_v1"})
        response, _ = _request(connection, "POST", "/api/profile", body)
        assert response.status == 202
        response, _ = _request(connection, "POST", "/api/profile", body)
        assert response.status == 409
        view = build_trading_dashboard_payload(paths)["profile"]
        assert view["desired"]["take_profit_mode"] == "fixed_rr"
        assert view["pending"]
        assert view["control_generation"] == 1
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize("mode", ["off", "shadow"])
def test_fixed_mode_keeps_ordinary_pa_and_shadow_qualification(mode):
    from unittest.mock import MagicMock
    from pa_agent.ai.take_profit_policy import FixedRiskRewardPolicy
    from pa_agent.ai.json_validator import JsonValidator
    from pa_agent.ai.prompt_assembler import PromptAssembler
    from pa_agent.ai.router import route_strategy_files
    from pa_agent.config.paths import PROMPT_DIR
    from pa_agent.config.settings import Settings, PromptSettings, ValidationSettings
    from pa_agent.orchestrator.two_stage import TwoStageOrchestrator
    from pa_agent.util.threading import CancelToken
    from tests.integration.conftest import make_reply
    from tests.unit.test_pa_vpa_joint_orchestrator import _vpa_report

    settings = Settings(prompt=PromptSettings(vpa_mode=mode),
                        validation=ValidationSettings(retry_enabled=False))
    policy = FixedRiskRewardPolicy(reward_risk_ratio="2", tick_size="0.1")
    client, baseline, vpa, writer = (MagicMock() for _ in range(4))
    for pa in (client, baseline):
        pa.stream_chat.side_effect = [make_reply(VALID_STAGE1), make_reply(trade_draft())]
    client.clone_with_provider.side_effect = [baseline, vpa]
    vpa.stream_chat.return_value = make_reply(_vpa_report())
    writer.save_full.return_value.ok = writer.save_full.return_value.audit_ok = True
    runner = TwoStageOrchestrator(client=client,
        assembler=PromptAssembler(PROMPT_DIR, prompt_settings=settings.prompt, take_profit_policy=policy),
        validator=JsonValidator(take_profit_policy=policy), router=route_strategy_files,
        pending_writer=writer, exp_reader=MagicMock(), settings=settings, take_profit_policy=policy)
    record = runner.submit(make_frame(), CancelToken(), lambda _: None)
    assert record.exception is None, record.exception
    assert record.stage2_decision["decision"]["take_profit_price"] == 2090
    assert record.stage2_decision["decision"]["take_profit_price_2"] is None
    assert record.role_runtime["take_profit_policy"]["reward_risk_ratio"] == "2"
    if mode == "off":
        assert record.vpa_evidence is None
        assert not vpa.stream_chat.called
    else:
        assert record.vpa_evidence is not None
        assert record.run_outcome["official_path"] == "pa_only"


def test_headless_waits_for_idle_and_freezes_target_tick_into_new_tasks(tmp_path):
    from types import SimpleNamespace
    from pa_agent.operations.analysis_profile import AnalysisProfile
    from tests.unit.test_headless_monitor import _native_monitor

    execution = SimpleNamespace(enabled=False, take_profit_tick_size=lambda symbol: "0.1")
    monitor, _, _, service, _ = _native_monitor(tmp_path, paper_execution=execution)
    monitor._profile_applier = lambda _: True
    try:
        control = monitor._control_store.set_analysis_profile(
            AnalysisProfile(take_profit_mode="fixed_rr"), expected_generation=0, actor="test")
        service.wait_idle = lambda *_: False
        assert monitor._apply_control(control) == (True, "analysis_profile_pending")
        assert monitor.runtime_snapshot(paused=True)["applied_profile"]["take_profit_mode"] == "pa"
        service.wait_idle = lambda *_: True
        assert monitor._apply_control(control, verify_latest=True) == (False, None)
        assert monitor.poll_once().accepted == 1
        task = service.submitted[-1]
        assert task.payload.take_profit_tick_size == "0.1"
        assert task.payload.analysis_profile_identity["take_profit_mode"] == "fixed_rr"
    finally:
        monitor.shutdown()
