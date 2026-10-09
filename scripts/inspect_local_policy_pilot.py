"""Read-only pilot progress/metrics; never subtract already-incremental usage twice."""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pa_agent.ai.analysis_metrics import summarize_call_metrics
from pa_agent.experiments.study_protocol import _load_registration, digest, summarize_study, write_once


def collect(root):
    registration = _load_registration(root)
    protocol = json.loads((root / "research_protocol.json").read_text(encoding="utf-8"))
    if protocol.get("kind") != "complementary_stage1_policy_pilot_v1":
        raise ValueError("Use the historical collector for old cumulative-usage studies")
    rows = []
    for assignment in registration["assignments"]:
        arm = root / "arms" / assignment["case_id"]
        outputs = defaultdict(list)
        for path in sorted((arm / "outbox").glob("*.json")):
            if path.name.endswith(".challenge.json"):
                continue
            worker = json.loads(path.read_text(encoding="utf-8"))
            if worker.get("job_digest") != path.stem or not isinstance(worker.get("content"), dict):
                raise ValueError("Unexpected Worker output identity")
            audit = worker.get("collaboration") or {}
            events = worker.get("evidence_events") or []
            baseline_end = max((e.get("completed_at_epoch_ms", 0) for e in events
                                if e.get("operation") in {"get_stage1_decision_packet", "get_role_baseline"}), default=0)
            started, ended, collected = [audit.get(k) or 0 for k in
                ("started_at_epoch_ms", "completed_at_epoch_ms", "collected_at_epoch_ms")]
            observation = worker.get("usage_observation") or {}
            # New runtime usage is already per-role. Session observation is raw
            # evidence for audit, not a second bill and never added to usage.
            if observation.get("scope") != "session_cumulative_v1":
                raise ValueError("Worker lacks the new usage scope")
            outputs[worker["role"]].append({
                "seconds": worker["latency_ms"] / 1000, "usage": worker["usage"],
                "usage_scope": observation["status"], "job_digest": worker["job_digest"],
                "artifact_sha256": digest(worker), "timings": worker.get("timings"),
                "evidence_calls": len(events), "evidence_bytes": sum(e["response_bytes"] for e in events),
                "evidence_service_ms": sum(e.get("latency_ms") or 0 for e in events),
                "child": ({"status": audit["status"], "collected": audit["collected"],
                    "start_rejections": audit.get("start_rejections", 0), "usage": audit.get("child_usage"),
                    "parent_usage": audit.get("parent_usage"), "collect_wait_ms": audit.get("collect_wait_ms"),
                    "seconds": (ended - started) / 1000 if started and ended else None,
                    "delegation_delay_seconds": (started - baseline_end) / 1000 if started and baseline_end else None,
                    "post_collect_seconds": (audit["parent_completed_at_epoch_ms"] - collected) / 1000 if collected else None,
                    "review_artifact": audit.get("review_artifact")} if audit else None),
            })
        receipt_path = root / "receipts" / f"{assignment['case_id']}.json"
        receipt = json.loads(receipt_path.read_text(encoding="utf-8")) if receipt_path.is_file() else None
        host_path = arm / "host-output.json"
        host = json.loads(host_path.read_text(encoding="utf-8")) if host_path.is_file() else None
        if receipt and receipt.get("result") and digest(host) != digest(receipt["result"]["output"]):
            raise ValueError("Host output differs from complete receipt")
        rows.append({"arm_id": assignment["arm_id"], "case_id": assignment["case_id"],
                     "receipt_status": receipt.get("status") if receipt else "missing",
                     "full_seconds": (receipt["completed_at_ms"] - receipt["started_at_ms"]) / 1000 if receipt else None,
                     "stages": dict(outputs), "run_outcome": host.get("run_outcome") if host else None,
                     "call_metrics": summarize_call_metrics(host["call_attempts"]) if host else None})
    return {"registration_digest": registration["registration_digest"], "coverage": summarize_study(root)["coverage"],
            "rows": rows, "independent_snapshots": 1, "ready_for_promotion": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = collect(args.root)
    if args.output:
        write_once(args.output, result)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
