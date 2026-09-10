"""Host-owned resource capture before registration; no model calls."""
from pathlib import Path
from types import SimpleNamespace

from pa_agent.ai.fact_catalog import build_analysis_fact_catalog
from pa_agent.experiments.backend_review import freeze_review_evidence, validate_review_evidence
from pa_agent.experiments.replay import frozen_input_from_record
from pa_agent.experiments.study_protocol import digest
from pa_agent.quality.runtime_fingerprint import analysis_runtime_fingerprint


def domain_resources(prompt_dir: Path) -> dict[str, str]:
    root = Path(prompt_dir).resolve()
    resources = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix in {".txt", ".md"}:
            if root not in path.resolve().parents:
                raise ValueError("study knowledge escapes prompt directory")
            resources[path.relative_to(root).as_posix()] = path.read_text(encoding="utf-8")
    if not resources:
        raise ValueError("study requires the full prompt resource pack")
    return resources


def study_domain_fingerprint(prompt_dir: Path, settings) -> str:
    from pa_agent.agent_runtime.runtime_assets import load_codex_runtime_assets
    shared = settings.model_dump(mode="json")
    # Credentials and treatment selections are not shared domain identity.
    provider = shared.pop("provider", {})
    shared["provider_controls"] = {k: v for k, v in provider.items() if k not in {
        "model", "thinking", "reasoning_effort", "reasoning_profiles", "api_key", "api_key_encrypted",
    }}
    shared.pop("analysis_pipeline", None)
    shared.pop("feishu", None)
    shared.pop("pushplus", None)
    shared.pop("tushare", None)
    shared["prompt"].pop("vpa_mode", None)
    shared["prompt"].pop("vpa_enabled", None)
    return digest({
        "runtime": analysis_runtime_fingerprint(prompt_dir=prompt_dir),
        "codex_assets": load_codex_runtime_assets().asset_digest,
        "knowledge": domain_resources(prompt_dir), "settings": shared,
    })


def freeze_host_evidence(record, *, prompt_dir: Path) -> dict:
    frame, _, _ = frozen_input_from_record(record)
    return freeze_review_evidence(
        record, knowledge=domain_resources(prompt_dir),
        measurements={"pa_fact_catalog": build_analysis_fact_catalog(frame).view("pa"),
                      "experience_pool": frame.data_metadata.get("experience_pool")},
    )


def input_from_evidence(evidence: dict):
    validate_review_evidence(evidence)
    # Only input fields are restored; original model conclusions are never replay input.
    record = SimpleNamespace(
        meta=SimpleNamespace(symbol=evidence["symbol"], timeframe=evidence["timeframe"],
                             timestamp_local_ms=evidence["as_of_ms"]),
        **{key: evidence[key] for key in (
            "kline_data", "snapshot_context", "htf_context", "htf_kline_data", "vpa_catalog")},
        role_runtime={"analysis_as_of_ms": evidence["as_of_ms"],
                      "experience_pool": evidence["measurements"].get("experience_pool")},
    )
    frame, htf, catalog = frozen_input_from_record(record)
    if digest(build_analysis_fact_catalog(frame).view("pa")) != digest(
        evidence["measurements"].get("pa_fact_catalog")
    ):
        raise ValueError("registered measurements differ from frozen market")
    return SimpleNamespace(frame=frame, htf_context=htf, vpa_catalog=catalog)
