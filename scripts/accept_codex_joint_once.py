"""Run one isolated real frozen PA+VPA Codex acceptance, with no Paper writes."""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
import time
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import asdict, replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pa_agent.agent_runtime.codex_worker import CodexCliConfig, CodexCliWorker
from pa_agent.agent_runtime.frozen_job import FrozenAgentJobStore
from pa_agent.agent_runtime.publisher import CodexJobPublisher
from pa_agent.agent_runtime.runtime_identity import codex_runtime_identity
from pa_agent.ai.analysis_metrics import summarize_call_metrics
from pa_agent.ai.codex_analysis_backend import CodexAnalysisRoleBackend
from pa_agent.config.settings import Settings
from pa_agent.experiments.local_policy_pilot import code_fingerprint
from pa_agent.experiments.study_host import StudyHostExecutor
from pa_agent.experiments.study_protocol import Condition, digest, write_once
from pa_agent.experiments.study_resources import (
    domain_resources,
    input_from_evidence,
    study_domain_fingerprint,
)
from pa_agent.util.threading import CancelToken


class AcceptancePublisher(CodexJobPublisher):
    def _budget_for(self, *args, **kwargs):
        return replace(super()._budget_for(*args, **kwargs), max_wall_seconds=3600)


def run(args: argparse.Namespace) -> None:
    if importlib.util.find_spec("mcp") is None:
        raise RuntimeError("acceptance interpreter lacks the project's MCP dependency")
    project = Path(__file__).resolve().parents[1]
    prompts = project / "prompt_engineering"
    root = args.root.resolve()
    evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
    frozen = input_from_evidence(evidence)
    if evidence["knowledge"] != domain_resources(prompts):
        raise ValueError("frozen knowledge differs from current domain pack")
    settings = Settings.model_validate(json.loads(args.settings.read_text(encoding="utf-8")))
    settings.paper_trading.enabled = False
    settings.monitoring.enabled = False
    settings.prompt.experience_max_entries = 0
    settings.provider.api_key = settings.provider.api_key_encrypted = ""
    domain = study_domain_fingerprint(prompts, settings)
    identity = codex_runtime_identity(
        model="gpt-6-luna", reasoning_effort="xhigh", codex_binary=args.codex_binary
    )
    condition = Condition(
        backend="codex_agent", model="gpt-6-luna", reasoning="xhigh",
        pipeline="pa_vpa_joint", investigation="agentic", stage_subagents="off",
    )
    case_id = digest({"evidence": evidence["content_digest"], "runtime": identity})[:24]
    request = {
        "case_id": case_id,
        "condition": condition.model_dump(),
        "domain_fingerprint": domain,
        "evidence_digest": digest(evidence),
        "evidence": evidence,
    }
    if args.preflight:
        print(json.dumps({"preflight": "passed", "model_calls": 0,
                          "case_id": case_id, "snapshot_hash": evidence["snapshot_hash"],
                          "bars": len(frozen.frame.bars)}), flush=True)
        return
    if CodexCliWorker(CodexCliConfig(
        codex_home=args.codex_home.resolve(), runtime_root=root / "runtime",
        outbox_root=root / "outbox", codex_binary=args.codex_binary,
        model="gpt-6-luna", reasoning_effort="xhigh",
    )).login_health() != "healthy":
        raise RuntimeError("isolated Codex login is unavailable")
    version = subprocess.run(
        [args.codex_binary, "--version"], capture_output=True, text=True,
        check=True, timeout=10,
    ).stdout.strip()
    write_once(root / "start.json", {
        "case_id": case_id, "snapshot_hash": evidence["snapshot_hash"],
        "as_of_ms": evidence["as_of_ms"], "evidence_digest": digest(evidence),
        "domain_fingerprint": domain, "code_fingerprint": code_fingerprint(project),
        "runtime_identity": identity, "condition": condition.model_dump(),
        "cli_version": version, "started_at_ms": time.time_ns() // 1_000_000,
        "role_wall_seconds": 3600, "cycle_wall_seconds": 10800,
    })

    def factory(spec, frozen_input, unused_settings):
        observed = {
            **identity, "investigation_policy": spec.investigation,
            "stage_subagents": spec.stage_subagents,
        }
        observed["profile_fingerprint"] = digest(observed)
        publisher = AcceptancePublisher(
            frame=frozen_input.frame, prompt_dir=prompts,
            job_root=root / "jobs", runtime_identity=observed,
        )

        class CheckpointWorker(CodexCliWorker):
            def run(self, published_job, **kwargs):
                path = Path(published_job)
                job = FrozenAgentJobStore(path.parent).open(path.name)
                role = job.manifest.role
                started = time.monotonic()
                write_once(root / "role-starts" / f"{path.name}.json", {
                    "role": role, "job_digest": path.name,
                    "started_at_ms": time.time_ns() // 1_000_000,
                })
                print(json.dumps({"event": "role_started", "role": role}), flush=True)
                checkpoint = {"status": "worker_incomplete", "usage": None}
                try:
                    result = super().run(published_job, **kwargs)
                    checkpoint = {
                        "status": "worker_draft_returned", "usage": asdict(result.usage),
                        "usage_observation": result.usage_observation,
                    }
                    return result
                except BaseException as exc:
                    checkpoint = {"status": "worker_failed", "failure_type": type(exc).__name__,
                                  "usage_observation": getattr(exc, "usage_observation", {})}
                    raise
                finally:
                    write_once(root / "role-checkpoints" / f"{path.name}.json", {
                        **checkpoint, "role": role, "job_digest": path.name,
                        "seconds": time.monotonic() - started,
                        "completed_at_ms": time.time_ns() // 1_000_000,
                    })
                    print(json.dumps({"event": "role_returned", "role": role,
                                      "status": checkpoint["status"]}), flush=True)

        worker = CheckpointWorker(CodexCliConfig(
            codex_home=args.codex_home.resolve(), runtime_root=root / "runtime",
            outbox_root=root / "outbox", codex_binary=args.codex_binary,
            model="gpt-6-luna", reasoning_effort="xhigh",
        ))
        return CodexAnalysisRoleBackend(
            worker=worker, publish_job=publisher, request_full_chain_fallback=False,
            runtime_identity=observed, cycle_wall_seconds=10800,
        ), observed

    host = StudyHostExecutor(
        settings=settings, prompt_dir=prompts, experience_dir=root / "experience",
        state_dir=root / "state", domain_fingerprint=domain,
        cancel_token=CancelToken(), codex_backend_factory=factory,
    )
    started = time.monotonic()
    try:
        result = host(request)
        write_once(root / "host-output.json", result["output"])
        write_once(root / "call-metrics.json",
                   summarize_call_metrics(result["output"]["call_attempts"]))
        print(json.dumps({"event": "host_returned", "seconds": time.monotonic() - started,
                          "run_outcome": result["output"].get("run_outcome")}), flush=True)
    except BaseException as exc:
        write_once(root / "failure.json", {
            "failure_type": type(exc).__name__, "seconds": time.monotonic() - started,
        })
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--settings", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--codex-home", type=Path, required=True)
    parser.add_argument("--codex-binary", required=True)
    parser.add_argument("--preflight", action="store_true")
    args = parser.parse_args()
    if args.preflight:
        run(args)
        return
    args.root.mkdir(parents=True, exist_ok=True)
    with (args.root / "execution.log").open("x", encoding="utf-8", buffering=1) as log:
        with redirect_stdout(log), redirect_stderr(log):
            run(args)


if __name__ == "__main__":
    main()
