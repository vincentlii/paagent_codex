"""Exercise prior WAIT delivery through real local role assembly and frozen MCP."""
import copy
import json
from dataclasses import replace

import pytest

from pa_agent.data.contracts import ResolvedInstrumentIdentity
from pa_agent.records.schema import AnalysisRecord
from tests.integration.conftest import VALID_STAGE1, make_frame
from tests.unit.test_joint_pipeline_runtime import joint_draft, run_joint

SENTINEL = "OLD_JOINT_WAIT_SENTINEL"
WATCH = "OLD_JOINT_WATCH_SENTINEL：后续闭盘是否收回旧K1高点上方。"


def _runtime_inputs():
    frame = make_frame()
    cutoff = 1_790_000_000_000
    hour = 3_600_000
    bars = tuple(replace(bar, ts_open=cutoff - index * hour)
                 for index, bar in enumerate(frame.bars))
    identity = ResolvedInstrumentIdentity(
        provider="tradingview", requested_symbol=frame.symbol,
        resolved_symbol=frame.symbol, venue="OANDA", instrument_type="cfd",
    ).to_metadata()
    frame = replace(frame, bars=bars, snapshot_ts_local_ms=cutoff + hour,
                    data_metadata={**identity, "analysis_as_of_ms": cutoff + hour})
    old_bars = tuple(replace(bar, seq=i + 1) for i, bar in enumerate(bars[1:]))
    old_bars += (replace(bars[-1], seq=len(bars), ts_open=bars[-1].ts_open - hour),)
    old_frame = replace(frame, bars=old_bars, snapshot_ts_local_ms=cutoff,
                        data_metadata={**identity, "analysis_as_of_ms": cutoff})
    wait = _draft("wait")
    wait["decision"].update(reasoning=SENTINEL, watch_points=[WATCH])
    previous, calls, _ = run_joint(frame=old_frame, stage2_drafts=[wait])
    assert previous.exception is None, previous.exception
    assert calls == ["stage1", "vpa_evidence", "stage2"]
    assert previous.stage2_decision["terminal"]["outcome"] == "wait"
    assert previous.stage2_decision["analysis_basis"]["protocol_version"] == "pa_vpa_stage2_draft_v1"
    previous_data = previous.model_dump(mode="json")
    previous_data["analysis_timing"] = {
        "analysis_as_of_ms": cutoff, "model_completed_at_ms": cutoff + 10,
        "validated_at_ms": cutoff + 20, "result_available_at_ms": cutoff + 30,
    }
    return frame, AnalysisRecord.model_validate(previous_data)


def _draft(intent):
    draft = joint_draft()
    if intent == "wait":
        from tests.fixtures.ai_payloads import VALID_STAGE2_NO_ORDER
        from tests.unit.test_pa_provider_contracts import _trace_draft

        no_order = copy.deepcopy(VALID_STAGE2_NO_ORDER)
        draft["decision_intent"] = "wait"
        draft["decision"] = no_order["decision"]
        for field in ("diagnosis_confidence", "diagnosis_confidence_reasoning"):
            draft["decision"].pop(field)
        draft["decision_trace"] = _trace_draft(no_order["decision_trace"])
    return draft


def _incremental_stage1():
    stage1 = copy.deepcopy(VALID_STAGE1)
    stage1["incremental_delta"] = {"new_closed_bars": ["K1"], "changed_fields": [],
                                   "summary": "新增K1后复核，原背景判断仍成立。"}
    return stage1


@pytest.mark.parametrize("codex", [False, True])
@pytest.mark.parametrize("intent", ["wait", "trade"])
def test_wait_history_reaches_only_joint_stage2_and_does_not_force_intent(codex, intent):
    frame, previous = _runtime_inputs()
    requests = []
    record, calls, writer = run_joint(
        codex=codex, frame=frame, previous_record=previous, incremental_new_bar_count=1,
        captured_requests=requests, stage2_drafts=[_draft(intent)],
        stage1_override=_incremental_stage1(),
    )
    assert record.exception is None, record.exception
    assert calls == ["stage1", "vpa_evidence", "stage2"]
    assert record.stage2_decision["terminal"]["outcome"] == intent
    writer.save_full.assert_called_once()
    assert SENTINEL not in json.dumps(record.stage1_messages)
    if not codex:
        assert SENTINEL in json.dumps(record.stage2_messages)
        assert WATCH in json.dumps(record.stage2_messages, ensure_ascii=False)
    assert SENTINEL not in json.dumps(record.vpa_evidence)
    if codex:
        for request in requests:
            visible = json.dumps(request.agent_context, ensure_ascii=False)
            assert (SENTINEL in visible) is (request.role == "stage2")
            if request.role == "stage2":
                assert WATCH in visible


def test_joint_wait_history_is_digest_bound_and_delivered_via_frozen_mcp(tmp_path):
    from pa_agent.agent_runtime.evidence import FrozenEvidenceGateway
    from pa_agent.agent_runtime.evidence_mcp import EvidenceMcpServer
    from pa_agent.agent_runtime.frozen_job import FrozenAgentJobStore
    from pa_agent.agent_runtime.publisher import CodexJobPublisher
    from pa_agent.config.paths import PROMPT_DIR

    frame, previous = _runtime_inputs()
    requests = []
    record, calls, _ = run_joint(codex=True, frame=frame, previous_record=previous,
                                incremental_new_bar_count=1, captured_requests=requests,
                                stage1_override=_incremental_stage1())
    assert record.exception is None, record.exception
    assert calls == ["stage1", "vpa_evidence", "stage2"]
    publisher = CodexJobPublisher(frame=frame, prompt_dir=PROMPT_DIR, job_root=tmp_path / "jobs")
    jobs = {}
    for request in requests:
        path = publisher(request, 1)
        job = FrozenAgentJobStore(path.parent).open(path.name)
        jobs[request.role] = job
        assert job.output_schema == request.schema
        operation = "get_stage1_decision_packet" if request.role == "stage1" else "get_role_baseline"
        result = EvidenceMcpServer(FrozenEvidenceGateway(job), job_digest=job.manifest.job_digest)._call_tool(operation, {})
        assert result["isError"] is False, result
        assert (SENTINEL in json.dumps(result, ensure_ascii=False)) is (request.role == "stage2")
    no_history = []
    run_joint(codex=True, frame=frame, captured_requests=no_history)
    stage2 = next(request for request in no_history if request.role == "stage2")
    path = publisher(stage2, 1)
    baseline_job = FrozenAgentJobStore(path.parent).open(path.name)
    assert jobs["stage2"].manifest.job_digest != baseline_job.manifest.job_digest
    assert jobs["stage2"].output_schema == baseline_job.output_schema


def test_legacy_frozen_marker_does_not_upgrade_wait_history_in_joint_context():
    frame, previous = _runtime_inputs()
    requests = []
    record, calls, _ = run_joint(codex=True, frame=frame, previous_record=previous,
                                incremental_new_bar_count=1, history_context_version=None,
                                captured_requests=requests, stage1_override=_incremental_stage1())
    assert record.exception is None, record.exception
    assert calls == ["stage1", "vpa_evidence", "stage2"]
    assert SENTINEL not in json.dumps(record.stage2_messages)
    assert all(SENTINEL not in json.dumps(request.agent_context) for request in requests)


def test_real_pa_only_off_run_never_receives_old_joint_wait():
    from unittest.mock import MagicMock
    from pa_agent.ai.json_validator import JsonValidator
    from pa_agent.ai.prompt_assembler import PromptAssembler
    from pa_agent.ai.router import route_strategy_files
    from pa_agent.config.analysis_pipeline import AnalysisPipelineIdentity
    from pa_agent.config.paths import PROMPT_DIR
    from pa_agent.config.settings import Settings, ValidationSettings
    from pa_agent.orchestrator.two_stage import TwoStageOrchestrator
    from pa_agent.util.threading import CancelToken
    from tests.integration.conftest import make_reply

    frame, previous = _runtime_inputs()
    draft = _draft("trade")
    draft["protocol_version"] = "pa_draft_v2"
    draft.pop("joint_analysis")
    client = MagicMock()
    client.clone_with_provider.return_value = client
    client.stream_chat.side_effect = [make_reply(_incremental_stage1()), make_reply(draft)]
    settings = Settings(analysis_pipeline=AnalysisPipelineIdentity(pipeline="pa_only"),
                        validation=ValidationSettings(retry_enabled=False))
    writer = MagicMock()
    writer.save_full.return_value.ok = True
    writer.save_full.return_value.audit_ok = True
    record = TwoStageOrchestrator(
        client=client, assembler=PromptAssembler(PROMPT_DIR, prompt_settings=settings.prompt),
        router=route_strategy_files, validator=JsonValidator(), pending_writer=writer,
        exp_reader=MagicMock(), settings=settings,
    ).submit(frame, CancelToken(), lambda event: None,
             previous_record=previous, incremental_new_bar_count=1,
             history_context_version="decision_history_v2")
    assert record.exception is None, record.exception
    assert record.analysis_pipeline.pipeline == "pa_only"
    assert record.run_outcome["official_path"] == "pa_only"
    assert client.stream_chat.call_count == 2
    assert SENTINEL not in json.dumps(record.stage1_messages)
    assert SENTINEL not in json.dumps(record.stage2_messages)
    assert record.vpa_evidence is None
    writer.save_full.assert_called_once()
