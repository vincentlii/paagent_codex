"""Integration test: stage 1 raises a network timeout error.

Task 11.9
"""
from __future__ import annotations

import copy
from unittest.mock import MagicMock

import openai

from pa_agent.ai.router import route_strategy_files
from pa_agent.orchestrator.two_stage import TwoStageOrchestrator
from pa_agent.util.threading import CancelToken, OrchestratorEvent
from tests.fixtures.validators import schema_test_validator
from tests.integration.conftest import VALID_STAGE1, VALID_STAGE2, make_reply


def test_httpx_read_error_stage1(frame, pending_writer, assembler, exp_reader):
    """httpx.ReadError (e.g. WinError 10054 mid-stream) → Stage1Failed + partial save."""
    try:
        import httpx
    except ImportError:
        return

    client = MagicMock()
    client.stream_chat.side_effect = httpx.ReadError(
        "[WinError 10054] 远程主机强迫关闭了一个现有的连接。"
    )

    validator = schema_test_validator()
    orchestrator = TwoStageOrchestrator(
        client=client,
        assembler=assembler,
        router=route_strategy_files,
        validator=validator,
        pending_writer=pending_writer,
        exp_reader=exp_reader,
    )

    events: list[OrchestratorEvent] = []
    record = orchestrator.submit(
        frame=frame,
        cancel_token=CancelToken(),
        on_event=events.append,
    )

    assert OrchestratorEvent.Stage1Failed in events
    assert record.run_outcome["status"] == "unavailable_provider"
    assert record.run_outcome["official_path"] == "none"
    pending_writer.save_partial.assert_called_once()
    assert pending_writer.save_partial.call_args[0][1] == "provider_error"


def test_network_timeout_stage1(frame, pending_writer, assembler, exp_reader):
    """APITimeoutError on stage1 → Stage1Failed emitted."""
    client = MagicMock()
    # openai.APITimeoutError requires a `request` parameter
    client.stream_chat.side_effect = openai.APITimeoutError(request=MagicMock())

    validator = schema_test_validator()
    orchestrator = TwoStageOrchestrator(
        client=client,
        assembler=assembler,
        router=route_strategy_files,
        validator=validator,
        pending_writer=pending_writer,
        exp_reader=exp_reader,
    )

    events: list[OrchestratorEvent] = []
    cancel_token = CancelToken()

    record = orchestrator.submit(
        frame=frame,
        cancel_token=cancel_token,
        on_event=events.append,
    )

    # Stage1Failed event must appear
    assert OrchestratorEvent.Stage1Failed in events
    assert record.run_outcome["status"] == "unavailable_provider"

    # Stage2 must never start
    assert OrchestratorEvent.Stage2Started not in events

    # Provider transport failures use the same typed persistence reason.
    pending_writer.save_partial.assert_called_once()
    call_args = pending_writer.save_partial.call_args
    reason = call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get("reason", "")
    assert reason == "provider_error"


def _status_error(status_code: int) -> openai.APIStatusError:
    return openai.APIStatusError(
        "request failed",
        response=MagicMock(status_code=status_code, headers={}),
        body=None,
    )


def test_non_retryable_stage1_request_error_is_persisted_once(
    frame,
    pending_writer,
    assembler,
    exp_reader,
) -> None:
    client = MagicMock()
    client.stream_chat.side_effect = _status_error(401)
    orchestrator = TwoStageOrchestrator(
        client=client,
        assembler=assembler,
        router=route_strategy_files,
        validator=schema_test_validator(),
        pending_writer=pending_writer,
        exp_reader=exp_reader,
    )
    events: list[OrchestratorEvent] = []

    record = orchestrator.submit(frame, CancelToken(), events.append)

    assert client.stream_chat.call_count == 1
    assert record.exception["failure_kind"] == "authentication"
    assert record.exception["status_code"] == 401
    assert record.run_outcome["status"] == "unavailable_provider"
    assert pending_writer.save_partial.call_args.args[1] == "provider_error"
    assert OrchestratorEvent.Stage1Failed in events


def test_stage1_validation_retry_provider_failure_does_not_escape(
    frame,
    pending_writer,
    assembler,
    exp_reader,
) -> None:
    invalid = copy.deepcopy(VALID_STAGE1)
    invalid.pop("cycle_position")
    client = MagicMock()
    client.stream_chat.side_effect = [make_reply(invalid), _status_error(422)]
    orchestrator = TwoStageOrchestrator(
        client=client,
        assembler=assembler,
        router=route_strategy_files,
        validator=schema_test_validator(),
        pending_writer=pending_writer,
        exp_reader=exp_reader,
    )
    events: list[OrchestratorEvent] = []

    record = orchestrator.submit(frame, CancelToken(), events.append)

    assert client.stream_chat.call_count == 2
    assert record.exception["phase"] == "validation_retry"
    assert record.exception["failure_kind"] == "invalid_request"
    assert record.run_outcome["status"] == "unavailable_provider"
    assert pending_writer.save_partial.call_args.args[1] == "provider_error"
    assert OrchestratorEvent.Stage1Failed in events


def test_stage2_validation_retry_provider_failure_does_not_escape(
    frame,
    pending_writer,
    assembler,
    exp_reader,
) -> None:
    invalid = copy.deepcopy(VALID_STAGE2)
    invalid.pop("decision")
    client = MagicMock()
    client.stream_chat.side_effect = [
        make_reply(copy.deepcopy(VALID_STAGE1)),
        make_reply(invalid),
        _status_error(400),
    ]
    orchestrator = TwoStageOrchestrator(
        client=client,
        assembler=assembler,
        router=route_strategy_files,
        validator=schema_test_validator(),
        pending_writer=pending_writer,
        exp_reader=exp_reader,
    )
    events: list[OrchestratorEvent] = []

    record = orchestrator.submit(frame, CancelToken(), events.append)

    assert client.stream_chat.call_count == 3
    assert record.exception["phase"] == "validation_retry"
    assert record.exception["failure_kind"] == "invalid_request"
    assert record.run_outcome["status"] == "unavailable_provider"
    assert pending_writer.save_partial.call_args.args[1] == "provider_error"
    assert OrchestratorEvent.Stage2Failed in events
