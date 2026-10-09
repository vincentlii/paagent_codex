"""Preregistered local Stage 1 child-policy pilot; no trading or production state.

Default invocation is offline registration. Execution requires --execute and
exactly the registered code, CLI, assets, settings and frozen domain evidence.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
import subprocess
import time
from uuid import uuid4

from pa_agent.agent_runtime.codex_worker import CodexCliConfig, CodexCliWorker
from pa_agent.agent_runtime.frozen_job import FrozenAgentJobStore
from pa_agent.agent_runtime.publisher import CodexJobPublisher
from pa_agent.agent_runtime.runtime_identity import codex_runtime_identity
from pa_agent.ai.analysis_metrics import summarize_call_metrics
from pa_agent.ai.codex_analysis_backend import CodexAnalysisRoleBackend
from pa_agent.config.settings import Settings
from pa_agent.experiments.backend_review import export_study_review
from pa_agent.experiments.study_host import StudyHostExecutor
from pa_agent.experiments.study_protocol import (
    _load_registration, digest, register_study, run_study, summarize_study, write_once,
)
from pa_agent.experiments.study_resources import (
    domain_resources, input_from_evidence, study_domain_fingerprint,
)
from pa_agent.util.threading import CancelToken


def code_fingerprint(project: Path) -> str:
    """Freeze executable source too, not only the runtime instruction assets."""
    return digest({path.relative_to(project).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                   for path in sorted((project / "pa_agent").rglob("*.py"))})


def run(args) -> None:
    project = Path(__file__).resolve().parents[2]
    prompts, root = project / "prompt_engineering", args.root.resolve()
    evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
    input_from_evidence(evidence)
    if evidence["knowledge"] != domain_resources(prompts):
        raise ValueError("Frozen knowledge differs from current pack")
    settings = Settings.model_validate(json.loads(
        (project / "config/settings.vps.example.json").read_text(encoding="utf-8")))
    settings.paper_trading.enabled = settings.monitoring.enabled = False
    settings.prompt.experience_max_entries = 0
    settings.provider.api_key = settings.provider.api_key_encrypted = ""
    fingerprint = study_domain_fingerprint(prompts, settings)
    identity = codex_runtime_identity(model="gpt-6-luna", reasoning_effort="xhigh",
                                     codex_binary=args.codex_binary)
    version = subprocess.run([args.codex_binary, "--version"], capture_output=True, text=True,
                             check=True, timeout=10).stdout.strip()
    protocol = {"kind": "complementary_stage1_policy_pilot_v1", "cli_version": version,
                "code_fingerprint": code_fingerprint(project), "runtime_identity": identity,
                "evidence_digest": digest(evidence), "domain_fingerprint": fingerprint,
                "role_wall_seconds": 3600, "cycle_wall_seconds": 10800,
                "quality_weights": {"market_context": .35, "brooks_diagnosis": .30,
                                    "evidence_traceability": .20, "counterevidence_uncertainty": .15},
                "descriptive_gate": {"complete_chains": 4, "critical_errors": 0,
                                     "collected_children_at_least": 1, "b_stage1_latency_ratio_max": .90,
                                     "quality_b_minus_a_min_points": -5},
                "limitations": ["one frozen snapshot; no p95, profitability or promotion claim",
                                "only Stage 1 child policy is varied; VPA/Stage 2 children remain off",
                                "one attempt per assignment; failures/missing stay in ITT",
                                "historical/frozen replay; not forward Paper trading"],
                "cost_policy": "Report actual/unknown usage, including observed failure lower bounds"}
    if not args.execute:
        write_once(root / "research_protocol.json", protocol)
        write_once(root / "settings.json", settings.model_dump(mode="json"))
        condition = dict(backend="codex_agent", model="gpt-6-luna", reasoning="xhigh",
                         pipeline="pa_vpa_joint", investigation="agentic")
        registration = register_study(root, dict(
            contrast="stage_subagents", a={**condition, "stage_subagents": "off"},
            b={**condition, "stage_subagents": "stage1_challenge_v1"},
            domain_fingerprint=fingerprint, execution_version="paper_latency_v3", primary_metric="blind_quality",
            holdout_start_ms=evidence["as_of_ms"] + 30 * 86400000,
            sample_size_rationale="Four independent full chains, one new frozen market; descriptive engineering pilot"),
            [dict(sample_id="complementary-stage1-pilot", snapshot_hash=evidence["snapshot_hash"],
                  evidence=evidence, window_start_ms=evidence["as_of_ms"],
                  window_end_ms=evidence["as_of_ms"] + 20 * 3600000)])
        print(json.dumps({"preflight": "passed", "provider_calls": 0,
                          "sequence": [row["arm_id"] for row in registration["assignments"]]}), flush=True)
        return
    if json.loads((root / "research_protocol.json").read_text(encoding="utf-8")) != protocol:
        raise ValueError("Executable source or runtime differs from preregistration")
    registration = _load_registration(root)
    if registration["samples"][0]["evidence_digest"] != digest(evidence):
        raise ValueError("Registration evidence drift")

    class ResearchPublisher(CodexJobPublisher):
        def _budget_for(self, *a, **kw):
            return replace(super()._budget_for(*a, **kw), max_wall_seconds=3600)

    def execute(request):
        if code_fingerprint(project) != protocol["code_fingerprint"]:
            raise ValueError("Executable code changed during study")
        arm_root = root / "arms" / request["case_id"]
        write_once(arm_root / "assignment.json", {k: v for k, v in request.items() if k != "evidence"})
        print(json.dumps({"event": "arm_started", "arm": request["arm_id"],
                          "at_ms": time.time_ns() // 1_000_000}), flush=True)

        def factory(spec, frozen, unused_settings):
            observed = {**identity, "investigation_policy": spec.investigation, "stage_subagents": spec.stage_subagents}
            observed["profile_fingerprint"] = digest(observed)
            publisher = ResearchPublisher(frame=frozen.frame, prompt_dir=prompts,
                                          job_root=arm_root / "jobs", runtime_identity=observed)

            class CheckpointWorker(CodexCliWorker):
                def run(self, published_job, **kwargs):
                    path = Path(published_job)
                    job = FrozenAgentJobStore(path.parent).open(path.name)
                    start = time.monotonic()
                    checkpoint = {"status": "worker_incomplete", "usage": None, "usage_observation": {}}
                    write_once(arm_root / "role-starts" / f"{path.name}.json", {
                        "role": job.manifest.role, "job_digest": path.name,
                        "at_ms": time.time_ns() // 1_000_000})
                    print(json.dumps({"event": "role_started", "arm": request["arm_id"],
                                      "role": job.manifest.role, "job": path.name}), flush=True)
                    try:
                        result = super().run(published_job, **kwargs)
                        checkpoint = {"status": "worker_draft_returned", "usage": asdict(result.usage),
                                      "usage_observation": result.usage_observation,
                                      "outbox_artifact": result.outbox_path.name}
                        return result
                    except BaseException as exc:
                        checkpoint = {"status": "worker_failed", "failure_type": type(exc).__name__,
                                      "usage_observation": getattr(exc, "usage_observation", {})}
                        usage = getattr(exc, "observed_usage", None)
                        checkpoint["usage"] = asdict(usage) if usage is not None else None
                        raise
                    finally:
                        write_once(arm_root / "role-checkpoints" / f"{path.name}.json", {
                            **checkpoint, "role": job.manifest.role, "job_digest": path.name,
                            "seconds": time.monotonic() - start, "at_ms": time.time_ns() // 1_000_000})
                        print(json.dumps({"event": "role_returned", "arm": request["arm_id"],
                                          "role": job.manifest.role, "status": checkpoint["status"]}), flush=True)

            worker = CheckpointWorker(CodexCliConfig(codex_home=args.codex_home.resolve(),
                runtime_root=arm_root / "runtime", outbox_root=arm_root / "outbox", codex_binary=args.codex_binary,
                model="gpt-6-luna", reasoning_effort="xhigh", allow_experimental_subagents=True))
            if worker.login_health() != "healthy":
                raise RuntimeError("Research Codex login unavailable")
            return CodexAnalysisRoleBackend(worker=worker, publish_job=publisher,
                request_full_chain_fallback=False, runtime_identity=observed, cycle_wall_seconds=10800), observed

        host = StudyHostExecutor(settings=settings, prompt_dir=prompts, experience_dir=arm_root / "experience",
            state_dir=arm_root / "state", domain_fingerprint=fingerprint, cancel_token=CancelToken(),
            codex_backend_factory=factory)
        start = time.monotonic()
        result = host(request)
        write_once(arm_root / "host-output.json", result["output"])
        write_once(arm_root / "call-metrics.json", summarize_call_metrics(result["output"]["call_attempts"]))
        print(json.dumps({"event": "arm_returned", "arm": request["arm_id"],
                          "seconds": time.monotonic() - start, "outcome": result["output"].get("run_outcome")}), flush=True)
        return result

    run_study(root, execute)
    summary = summarize_study(root)
    write_once(root / "coverage.json", summary)
    export_study_review(root, root / "review-public", root / "review-private-key.json", blinding_salt=uuid4().hex)
    print(json.dumps({"event": "study_completed", "coverage": summary["coverage"]}), flush=True)
    if summary["coverage"]["failed"] or summary["coverage"]["missing"]:
        raise RuntimeError("registered study lacks four complete Host receipts")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--codex-home", type=Path, required=True)
    parser.add_argument("--codex-binary", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not args.execute:
        run(args)
        return
    # Logs are durable and bounded to execution metadata; no provider stream,
    # credentials or hidden reasoning is copied to these files.
    with (args.root / "execution.log").open("x", encoding="utf-8", buffering=1) as log:
        with redirect_stdout(log), redirect_stderr(log):
            try:
                run(args)
            except Exception as exc:
                write_once(args.root / "execution-error.json", {"failure_type": type(exc).__name__})
                raise


if __name__ == "__main__":
    main()
