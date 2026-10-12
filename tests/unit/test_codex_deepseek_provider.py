import hashlib
import json
from dataclasses import replace

import pytest

from pa_agent.agent_runtime.codex_worker import CodexCliConfig, CodexCliWorker, CodexWorkerOutputError
from pa_agent.agent_runtime.runtime_identity import codex_runtime_identity
from pa_agent.operations.analysis_profile import codex_model_effort_supported
from tests.unit.test_frozen_agent_job import _job


def test_provider_credentials_catalog_and_command_are_isolated(tmp_path, monkeypatch):
    catalog = tmp_path / 'models.json'
    catalog.write_text(json.dumps({'models': [{'slug': 'deepseek-flash'}]}))
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-only-not-a-key')
    monkeypatch.setenv('OPENAI_API_KEY', 'test-openai')
    config = CodexCliConfig(codex_home=tmp_path/'home', runtime_root=tmp_path/'runtime',
                            outbox_root=tmp_path/'outbox', model='deepseek-flash',
                            reasoning_effort='high', model_catalog=catalog)
    worker = CodexCliWorker(config)
    assert worker.login_health() == 'healthy'
    assert worker._environment()['DEEPSEEK_API_KEY'] == 'test-only-not-a-key'
    assert 'OPENAI_API_KEY' not in worker._environment()
    assert 'DEEPSEEK_API_KEY' not in CodexCliWorker(replace(config, model='gpt-5.6-luna'))._environment()
    command = worker._command(job=_job(), job_root=tmp_path, workspace=tmp_path,
                              schema_path=tmp_path/'schema', output_path=tmp_path/'out',
                              audit_path=tmp_path/'audit', model='deepseek-flash', reasoning_effort='high')
    assert 'model_provider="deepseek"' in command
    assert 'forced_login_method="api"' in command
    assert 'test-only-not-a-key' not in str(command)
    assert 'features.shell_tool=false' in command
    assert '--output-schema' not in command
    assert 'code-mode exec is unavailable' in command[2]
    assert '// @exec:' not in command[2]
    with pytest.raises(CodexWorkerOutputError, match='identity mismatch'):
        worker._execution_identity(_job())


def test_provider_identity_binds_catalog_without_credentials(tmp_path):
    binary = tmp_path/'codex.exe'
    binary.write_bytes(b'test-binary')
    catalog = tmp_path/'models.json'
    catalog.write_text(json.dumps({'models': [{'slug': 'deepseek-flash'}]}))
    identity = codex_runtime_identity(model='deepseek-flash', reasoning_effort='high',
                                      codex_binary=str(binary), model_catalog=catalog)
    assert identity['provider'] == 'deepseek_codex_responses'
    assert identity['model_catalog_digest'] == hashlib.sha256(catalog.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match='explicit model catalog'):
        codex_runtime_identity(model='deepseek-flash', reasoning_effort='high', codex_binary=str(binary))
    assert codex_model_effort_supported('deepseek-flash', 'high')
    assert not codex_model_effort_supported('deepseek-flash', 'ultra')


def test_schema_failure_is_not_masked_by_plugin_auth_warning():
    from pa_agent.agent_runtime.codex_worker import _classify_failed_process
    stdout = json.dumps({'type': 'turn.failed', 'error': {'message': 'invalid_request_error: Invalid json schema'}})
    failure = _classify_failed_process(1, stdout, 'plugin cache status 401 Unauthorized')
    assert type(failure) is CodexWorkerOutputError


@pytest.mark.parametrize("event, expected", [
    ({"type": "item.completed", "item": {"type": "error", "message": "rate limit exceeded"}}, "CodexWorkerRateLimited"),
    ({"type": "turn.failed", "error": {"code": "context_length_exceeded", "message": "private request details"}}, "CodexWorkerOutputError"),
    ({"type": "turn.failed", "error": {"code": "unknown_code", "message": "opaque failure"}}, "CodexWorkerOutputError"),
])
def test_structured_cli_failure_is_classified_without_startup_warning_or_raw_text(event, expected):
    from pa_agent.agent_runtime.codex_worker import _classify_failed_process

    failure = _classify_failed_process(1, json.dumps(event), "plugin cache status 401 Unauthorized private-token")
    assert type(failure).__name__ == expected
    assert "private" not in str(failure) and "opaque" not in str(failure)
    if event.get("error", {}).get("code") == "context_length_exceeded":
        assert "context window" in str(failure)


def test_agent_message_cannot_supply_cli_availability_classification():
    from pa_agent.agent_runtime.codex_worker import _classify_failed_process

    stdout = json.dumps({"type": "item.completed", "item": {
        "type": "agent_message", "code": "rate_limit_exceeded", "message": "rate limit",
    }})
    assert type(_classify_failed_process(1, stdout, "")) is CodexWorkerOutputError


def test_deepseek_schema_is_serializable_through_real_gateway():
    from pa_agent.agent_runtime.evidence import FrozenEvidenceGateway
    job = _job()
    job = replace(job, runtime={**job.runtime, 'backend_runtime_identity': {'provider': 'deepseek_codex_responses'}})
    response = FrozenEvidenceGateway(job).get_market_core()
    assert response['provider_output_schema'] == dict(job.output_schema)
    assert response['role_skill'] == job.runtime['skills']['stage1']
    json.dumps(response)
