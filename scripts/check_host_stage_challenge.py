"""Explicit paid, synthetic parent/child canary; no market data or trading output.

Use the packaged Worker, independent child CLI and real MCP. This validates
transport/lifecycle, not PA/VPA quality, OS isolation or market performance.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pa_agent.agent_runtime.codex_worker import CodexCliConfig, CodexCliWorker, CodexWorkerError
from pa_agent.agent_runtime.execution_contract import ANALYSIS_EXECUTION_POLICY, INSTRUCTION_DELIVERY_POLICY, RUNTIME_CONTRACT
from pa_agent.agent_runtime.frozen_job import AgentJobBudget, FrozenAgentJobBuilder, FrozenAgentJobStore
from pa_agent.agent_runtime.runtime_assets import load_codex_runtime_assets


CANARY_INSTRUCTIONS = """This is a synthetic transport canary, not market analysis.
Only pa_evidence tools and Code Mode to invoke them are allowed. No native agents,
shell, Web or file access. First call get_role_baseline once and read it completely.
Next start_stage_challenge(question, resources), delegating exactly one question:
'Does the delivered K1 close equal 101? Read the scoped fact; do not infer other prices.'
resources must be [{"operation":"get_pa_evidence","arguments":{"fact_ids":["PA.BAR.K1"]}}].
While it runs, independently call get_bars(older_seq=1,newer_seq=1) to verify the close.
Then collect with get_stage_challenge(wait_seconds=10), repeating only while running.
Return {"result":{"ok":true}} only if both close checks agree, the child completed,
source_digest matches this Job and child evidence_refs contains PA.BAR.K1. Else ok=false.
Do not do trading analysis or speculate. Native spawn_agent is not used.
"""


def build_canary():
    assets = load_codex_runtime_assets()
    snapshot = "a" * 64
    cutoff = 1_725_000_000_000
    skill = "---\nname: pa-stage1-diagnosis\ndescription: Synthetic collaboration canary.\n---\n" + CANARY_INSTRUCTIONS
    return FrozenAgentJobBuilder(runtime_version=assets.runtime_version,
        agents_text=CANARY_INSTRUCTIONS, skill_documents={"stage1": skill},
        challenger_text=assets.challenger_text).build(
        task_id="host-challenge-canary", request_id="host-challenge-canary:stage1:1",
        role="stage1", attempt=1, snapshot_hash=snapshot, cutoff_ts_open=cutoff,
        messages=[{"role": "user", "content": "Execute the synthetic collaboration canary."}],
        output_schema={"type": "object", "properties": {"ok": {"type": "boolean"}},
                       "required": ["ok"], "additionalProperties": False},
        bars=[{"seq": 1, "ts_open": cutoff, "open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0}],
        fact_projection={"snapshot_hash": snapshot, "facts": [{"fact_id": "PA.BAR.K1",
            "domain": "price", "bar_range": "K1", "fact_type": "bar_ohlc_geometry",
            "value": {"seq": 1, "open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0}}], "relations": []},
        knowledge_documents={}, allowed_knowledge_ids=(),
        budget=AgentJobBudget(max_wall_seconds=1800, max_evidence_calls=20, max_evidence_bytes=131072),
        backend_runtime_identity={"model": "gpt-6-luna", "reasoning_effort": "xhigh",
            "profile_fingerprint": "b" * 64, "investigation_policy": "agentic",
            "stage_subagents": "stage1_challenge_v1"},
        core_context={"task_id": "host-challenge-canary", "frozen_snapshot": {
            "snapshot_hash": snapshot, "cutoff_ts_open": cutoff},
            "runtime_contract_version": RUNTIME_CONTRACT,
            "analysis_execution_policy": ANALYSIS_EXECUTION_POLICY,
            "instruction_delivery_policy": INSTRUCTION_DELIVERY_POLICY,
            "context_policy": "semantic_parity_v1", "stage_subagents": "stage1_challenge_v1"},
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="Allow two real model requests")
    parser.add_argument("--codex-home", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path, help="A new isolated report directory")
    parser.add_argument("--codex-binary", default=shutil.which("codex") or "codex")
    args = parser.parse_args()
    if not args.execute:
        print(json.dumps({"execute": False, "model": "gpt-6-luna", "effort": "xhigh",
                          "scope": "synthetic parent/child transport; no market A/B"}))
        return 0
    root = args.output_dir.resolve()
    root.mkdir(parents=True, exist_ok=False)
    worker = CodexCliWorker(CodexCliConfig(
        codex_home=args.codex_home.resolve(), runtime_root=root / "runtime", outbox_root=root / "outbox",
        model="gpt-6-luna", reasoning_effort="xhigh", codex_binary=args.codex_binary,
        allow_experimental_subagents=True))
    report = {"scope": "synthetic_transport_only", "model": "gpt-6-luna", "effort": "xhigh",
              "cli": subprocess.run([args.codex_binary, "--version"], capture_output=True,
                                    text=True, check=True, timeout=10).stdout.strip()}
    if worker.login_health() != "healthy":
        report.update({"ok": False, "failure": "research_login_unavailable"})
    else:
        job = build_canary()
        published = FrozenAgentJobStore(root / "jobs").publish(job)
        report["job_digest"] = job.manifest.job_digest
        start = time.monotonic()
        try:
            result = worker.run(published)
            audit = result.collaboration
            report.update({"ok": json.loads(result.content) == {"ok": True}
                           and audit["status"] == "completed" and audit["collected"]
                           and not audit["start_rejections"], "collaboration": audit,
                           "parent_evidence_events": list(result.evidence_events)})
        except CodexWorkerError as exc:
            report.update({"ok": False, "failure": type(exc).__name__, "detail": str(exc),
                           "collaboration": getattr(exc, "collaboration", {})})
        report["wall_seconds"] = round(time.monotonic()-start, 3)
    (root / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": report["ok"], "report": str(root / "report.json"),
                      "wall_seconds": report.get("wall_seconds")}))
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
