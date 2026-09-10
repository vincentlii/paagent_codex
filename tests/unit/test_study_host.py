import pytest

from pa_agent.config.settings import Settings
from pa_agent.experiments.study_host import StudyHostExecutor
from tests.unit.test_backend_aabb_protocol import _record
from tests.unit.test_backend_review import review_evidence
from pa_agent.experiments.study_protocol import digest
from pa_agent.experiments.study_resources import domain_resources, study_domain_fingerprint


class OrchestratorStub:
    settings = None
    backend = None

    def __init__(self, **kwargs):
        type(self).settings = kwargs["settings"]
        type(self).backend = kwargs["analysis_backend"]

    def submit(self, *args, **kwargs):
        del args, kwargs
        record = _record("study-host", "codex_agent")
        for call in record.call_attempts:
            call.update(effective_model="gpt-5.6-luna", effective_reasoning_effort="max")
        return record


@pytest.mark.parametrize("failure", [None, "persistence", "experience"])
def test_host_delivers_registered_codex_pipeline_and_investigation(monkeypatch, tmp_path, failure):
    from pa_agent.experiments import study_host

    monkeypatch.setattr(study_host, "TwoStageOrchestrator", OrchestratorStub)
    monkeypatch.setattr(study_host, "PromptAssembler", lambda **kwargs: object())
    monkeypatch.setattr(study_host, "ExperienceReader", lambda **kwargs: object())
    monkeypatch.setattr(study_host, "JsonValidator", lambda *args, **kwargs: object())
    if failure == "persistence":
        original_submit = OrchestratorStub.submit
        def failed_submit(self, *args, **kwargs):
            args[2](study_host.OrchestratorEvent.RecordSaveFailed)
            return original_submit(self, *args, **kwargs)
        monkeypatch.setattr(OrchestratorStub, "submit", failed_submit)
    backend = object()
    observed = {}

    def factory(spec, frozen, settings):
        observed.update(spec=spec, frozen=frozen, settings=settings)
        return backend, {
            "model": spec.model,
            "reasoning_effort": spec.reasoning,
            "investigation_policy": spec.investigation,
        }

    (tmp_path / "core.txt").write_text("full knowledge", encoding="utf-8")
    evidence = review_evidence()
    evidence["knowledge"] = domain_resources(tmp_path)
    from pa_agent.experiments.replay import frozen_input_from_record
    from pa_agent.ai.fact_catalog import build_analysis_fact_catalog
    source = _record("source", "direct")
    for key in ("kline_data", "snapshot_context", "htf_context", "htf_kline_data", "vpa_catalog"):
        setattr(source, key, evidence[key])
    source.role_runtime["analysis_as_of_ms"] = evidence["as_of_ms"]
    frame, _, _ = frozen_input_from_record(source)
    evidence["measurements"] = {"pa_fact_catalog": build_analysis_fact_catalog(frame).view("pa"), "experience_pool": None}
    evidence["content_digest"] = digest({k: v for k, v in evidence.items() if k != "content_digest"})
    settings = Settings()
    settings.prompt.experience_max_entries = 1 if failure == "experience" else 0
    fingerprint = study_domain_fingerprint(tmp_path, settings)
    execute = StudyHostExecutor(
        settings=settings,
        prompt_dir=tmp_path,
        experience_dir=tmp_path,
        state_dir=tmp_path / "state",
        domain_fingerprint=fingerprint,
        cancel_token=object(),
        codex_backend_factory=factory,
    )
    request = {
            "sample_id": "s1",
            "case_id": "a" * 24,
            "condition": {
                "backend": "codex_agent",
                "model": "gpt-5.6-luna",
                "reasoning": "max",
                "pipeline": "pa_vpa_joint",
                "investigation": "fixed",
            },
            "domain_fingerprint": fingerprint,
            "evidence": evidence,
            "evidence_digest": digest(evidence),
        }
    if failure:
        with pytest.raises(ValueError, match="persistence failed" if failure == "persistence" else "pre-frozen pool"):
            execute(request)
        return
    result = execute(request)

    assert observed["spec"].investigation == "fixed"
    assert OrchestratorStub.settings.analysis_pipeline.pipeline == "pa_vpa_joint"
    assert OrchestratorStub.backend is backend
    assert result["condition"]["model"] == "gpt-5.6-luna"
    assert result["evidence_digest"] == digest(evidence)
