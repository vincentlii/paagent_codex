"""Preregistered, isolated research contrasts; never a trading authority.

The injected executor owns actual backend construction. It must return observed
condition identity and an independent client token, not just echo an assignment.
Registration and receipts are append-only files, separate from production state.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
from pathlib import Path
from time import time_ns
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


class Condition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    backend: Literal["deepseek_direct", "codex_agent"]
    model: str = Field(min_length=1)
    reasoning: str = Field(min_length=1)
    pipeline: Literal["pa_only", "pa_shadow", "pa_vpa_joint"]
    investigation: Literal["fixed", "agentic"]


class StudyPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    protocol_version: Literal["analysis_study_v1"] = "analysis_study_v1"
    contrast: Literal["backend", "joint", "investigation"]
    a: Condition
    b: Condition
    domain_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    execution_version: Literal["paper_latency_v3"]
    primary_metric: Literal["blind_quality", "episode_itt_r"]
    sample_size_rationale: str = Field(min_length=1)
    holdout_start_ms: int = Field(ge=0, strict=True)

    @model_validator(mode="after")
    def independent_contrast(self):
        a, b = self.a.model_dump(), self.b.model_dump()
        differing = {key for key in a if a[key] != b[key]}
        if self.contrast == "investigation":
            valid = (
                differing == {"investigation"}
                and a["backend"] == "codex_agent"
                and a["investigation"] == "fixed"
                and b["investigation"] == "agentic"
            )
        elif self.contrast == "joint":
            valid = (
                differing == {"pipeline"}
                and a["pipeline"] == "pa_only"
                and b["pipeline"] == "pa_vpa_joint"
            )
        else:
            valid = (
                a["backend"] == "deepseek_direct"
                and b["backend"] == "codex_agent"
                and a["pipeline"] == b["pipeline"]
            )
        if not valid:
            raise ValueError(f"{self.contrast} contrast has confounded or reversed conditions")
        if any(
            c.backend == "deepseek_direct" and c.investigation != "fixed" for c in (self.a, self.b)
        ):
            raise ValueError("Direct is a fixed-context backend")
        for condition in (self.a, self.b):
            if condition.backend == "codex_agent" and (
                condition.model != "gpt-5.6-luna" or condition.reasoning != "max"
            ):
                raise ValueError(
                    "phase 7 Codex arms require gpt-5.6-luna with max reasoning"
                )
        return self


def write_once(path: Path, value: Any) -> None:
    """Refuse overwrite, including a partially written artifact after a crash."""
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())


def _load_registration(root: Path) -> dict:
    registration = json.loads((root / "registration.json").read_text(encoding="utf-8"))
    expected = registration.pop("registration_digest")
    if digest(registration) != expected:
        raise ValueError("registration integrity mismatch")
    registration["registration_digest"] = expected
    return registration


def register_study(root: Path, plan: dict, samples: list[dict]) -> dict:
    """Freeze all samples, splits and four-arm assignments before any invocation."""
    parsed = StudyPlan.model_validate(plan)
    if not samples:
        raise ValueError("study requires preregistered samples")
    frozen = copy.deepcopy(samples)
    for sample in frozen:
        from pa_agent.data.contracts import ResolvedInstrumentIdentity

        evidence = sample["evidence"]
        identity = ResolvedInstrumentIdentity.from_value(evidence.get("snapshot_context"))
        if identity is None or not identity.venue or not identity.instrument_type:
            raise ValueError("study requires resolved market identity")
        # Episode ownership comes from frozen source facts, not user-assigned labels.
        sample["market_key"] = digest([*identity.key, evidence["timeframe"]])
    if len({s["sample_id"] for s in frozen}) != len(frozen) or len(
        {s["snapshot_hash"] for s in frozen}
    ) != len(frozen):
        raise ValueError("duplicate sample or snapshot is not an independent unit")
    episodes: dict[str, tuple[int, str]] = {}
    assignments = []
    for sample in sorted(frozen, key=lambda s: (s["market_key"], s["window_start_ms"])):
        from pa_agent.experiments.backend_review import validate_review_evidence

        validate_review_evidence(sample["evidence"])
        if sample["snapshot_hash"] != sample["evidence"]["snapshot_hash"]:
            raise ValueError("sample snapshot does not match evidence")
        start, end = sample["window_start_ms"], sample["window_end_ms"]
        if type(start) is not int or type(end) is not int or not 0 <= start < end:
            raise ValueError("invalid episode window")
        if start < sample["evidence"]["as_of_ms"]:
            raise ValueError("outcome window precedes analysis as-of")
        if start < parsed.holdout_start_ms <= end:
            raise ValueError("episode crosses holdout boundary; purge it before registration")
        sample["split"] = "holdout" if start >= parsed.holdout_start_ms else "development"
        previous = episodes.get(sample["market_key"])
        episode = (
            previous[1]
            if previous and start <= previous[0]
            else digest([sample["market_key"], start])[:20]
        )
        episodes[sample["market_key"]] = (
            max(end, previous[0]) if previous and start <= previous[0] else end,
            episode,
        )
        sample["episode_id"] = episode
        sample["evidence_digest"] = digest(sample["evidence"])
        sequence = (
            ("A1", "B1", "B2", "A2")
            if int(digest(sample["snapshot_hash"])[-1], 16) % 2 == 0
            else ("B1", "A1", "A2", "B2")
        )
        for position, arm in enumerate(sequence, 1):
            assignments.append(
                {
                    "case_id": digest([sample["sample_id"], arm])[:24],
                    "sample_id": sample["sample_id"],
                    "arm_id": arm,
                    "sequence_position": position,
                    "episode_id": episode,
                    "split": sample["split"],
                    "evidence_digest": sample["evidence_digest"],
                    "condition": (parsed.a if arm[0] == "A" else parsed.b).model_dump(),
                }
            )
    body = {
        "plan": parsed.model_dump(),
        "registered_at_ms": time_ns() // 1_000_000,
        "samples": frozen,
        "assignments": assignments,
    }
    registration = {**body, "registration_digest": digest(body)}
    write_once(root / "registration.json", registration)
    return registration


def run_study(root: Path, execute_arm, *, on_completed=None) -> None:
    """One attempt per assignment. Crashes leave missing receipts, never reruns."""
    registered = _load_registration(root)
    write_once(
        root / "started.json",
        {
            "registration_digest": registered["registration_digest"],
            "started_at_ms": time_ns() // 1_000_000,
        },
    )
    samples = {s["sample_id"]: s for s in registered["samples"]}
    clients = set()
    runtimes = []
    for assignment in registered["assignments"]:
        request = copy.deepcopy(assignment)
        request["evidence"] = copy.deepcopy(samples[assignment["sample_id"]]["evidence"])
        request["registration_digest"] = registered["registration_digest"]
        request["domain_fingerprint"] = registered["plan"]["domain_fingerprint"]
        receipt = {
            "case_id": assignment["case_id"],
            "registration_digest": registered["registration_digest"],
            "started_at_ms": time_ns() // 1_000_000,
        }
        try:
            result = dict(execute_arm(request))
            runtime = result.pop("runtime_identity", None)
            if runtime is None or any(runtime is previous for previous in runtimes):
                raise ValueError("independent runtime instance required")
            runtimes.append(runtime)
            if set(result) != {
                "condition",
                "client_token",
                "output",
                "evidence_digest",
                "domain_fingerprint",
            }:
                raise ValueError("unexpected arm execution fields")
            observed = Condition.model_validate(result["condition"]).model_dump()
            token = result["client_token"]
            if not isinstance(token, str) or not token or token in clients:
                raise ValueError("independent client token required")
            clients.add(token)
            if (
                observed != assignment["condition"]
                or result["evidence_digest"] != assignment["evidence_digest"]
            ):
                raise ValueError("received treatment or frozen evidence drift")
            if result.get("domain_fingerprint") != registered["plan"]["domain_fingerprint"]:
                raise ValueError("runtime domain fingerprint drift")
            from pa_agent.records.schema import AnalysisRecord
            from pa_agent.data.snapshot_identity import canonical_market_snapshot_hash

            record = AnalysisRecord.model_validate(result["output"])
            if (
                record.exception
                or record.role_stop
                or not record.stage2_decision
                or record.run_outcome.get("status") not in {"completed", "completed_with_warnings"}
                or record.analysis_pipeline is None
                or record.analysis_pipeline.pipeline != observed["pipeline"]
            ):
                raise ValueError("incomplete or mismatched analysis pipeline")
            actual_hash = canonical_market_snapshot_hash(
                symbol=record.meta.symbol,
                timeframe=record.meta.timeframe,
                kline_data=record.kline_data,
                snapshot_context=record.snapshot_context,
                htf_context=record.htf_context,
                htf_kline_data=record.htf_kline_data,
            )
            if actual_hash != samples[assignment["sample_id"]]["snapshot_hash"]:
                raise ValueError("analysis output uses different frozen market")
            evidence = samples[assignment["sample_id"]]["evidence"]
            if record.role_runtime.get("analysis_as_of_ms") != evidence["as_of_ms"]:
                raise ValueError("analysis output uses different as-of")
            if record.vpa_catalog is not None and digest(record.vpa_catalog) != digest(evidence["vpa_catalog"]):
                raise ValueError("analysis output uses different VPA catalog")
            backend = "direct" if observed["backend"] == "deepseek_direct" else "codex_agent"
            if (
                not record.call_attempts
                or any(
                    c.get("analysis_backend") != backend or c.get("provider_fallback_used")
                    for c in record.call_attempts
                )
                or record.role_runtime.get("backend_fallback")
            ):
                raise ValueError("backend treatment was not delivered without fallback")
            if backend == "codex_agent" and any(
                c.get("investigation_policy") != observed["investigation"]
                for c in record.call_attempts
            ):
                raise ValueError("Codex investigation treatment was not delivered")
            # JSON validation rejects NaN and nonserializable provider objects.
            digest(result)
            receipt.update(status="completed", result=result)
        except Exception as exc:
            # Do not expose provider messages, credentials or identities to reviewers.
            receipt.update(status="failed", failure_type=type(exc).__name__)
        receipt["completed_at_ms"] = time_ns() // 1_000_000
        receipt["receipt_digest"] = digest(receipt)
        write_once(root / "receipts" / f"{assignment['case_id']}.json", receipt)
        if receipt["status"] == "completed" and on_completed is not None:
            try:
                on_completed(assignment["case_id"])
            except Exception as exc:
                # Paper recovery may be retried; the model arm never is.
                write_once(root / "paper_errors" / f"{assignment['case_id']}.json",
                           {"failure_type": type(exc).__name__, "case_id": assignment["case_id"]})


def summarize_study(root: Path) -> dict:
    registered = _load_registration(root)
    counts = {"assigned": len(registered["assignments"]), "completed": 0, "failed": 0, "missing": 0}
    cases = []
    for assignment in registered["assignments"]:
        path = root / "receipts" / f"{assignment['case_id']}.json"
        receipt = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
        if receipt and digest(
            {k: v for k, v in receipt.items() if k != "receipt_digest"}
        ) != receipt.get("receipt_digest"):
            raise ValueError("receipt integrity mismatch")
        if receipt and (
            receipt.get("registration_digest") != registered["registration_digest"]
            or receipt.get("case_id") != assignment["case_id"]
        ):
            raise ValueError("receipt registration mismatch")
        status = receipt["status"] if receipt else "missing"
        if status not in {"completed", "failed", "missing"}:
            raise ValueError("invalid receipt status")
        counts[status] += 1
        cases.append({**assignment, "status": status})
    return {
        "protocol_version": "analysis_study_v1",
        "contrast": registered["plan"]["contrast"],
        "registration_digest": registered["registration_digest"],
        "coverage": counts,
        "cases": cases,
        "independent_episodes": len({s["episode_id"] for s in registered["samples"]}),
        "ready_for_promotion": False,
        "calibrated_probability": False,
    }


def paired_episode_summary(rows: list[dict], value_key: str) -> dict:
    """Average replicates within snapshots, then snapshots within episodes.

    Incomplete episodes remain censored. The complete-episode contrast is a
    diagnostic, not an unbiased replacement for a missing ITT population.
    """
    samples: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        samples.setdefault((row["episode_id"], row["sample_id"]), []).append(row)
    episodes: dict[str, list[tuple | None]] = {}
    for (episode, _), selected in samples.items():
        by_arm = {row["arm_id"]: row.get(value_key) for row in selected}
        complete = (
            len(selected) == 4
            and set(by_arm) == {"A1", "A2", "B1", "B2"}
            and all(type(v) in (int, float) and math.isfinite(v) for v in by_arm.values())
        )
        value = None
        if complete:
            value = (
                (by_arm["B1"] + by_arm["B2"] - by_arm["A1"] - by_arm["A2"]) / 2,
                abs(by_arm["A1"] - by_arm["A2"]),
                abs(by_arm["B1"] - by_arm["B2"]),
            )
        episodes.setdefault(episode, []).append(value)
    complete_episodes = []
    for values in episodes.values():
        if all(v is not None for v in values):
            complete_episodes.append(
                tuple(sum(v[i] for v in values) / len(values) for i in range(3))
            )
    return {
        "assigned": len(episodes),
        "complete": len(complete_episodes),
        "censored": len(episodes) - len(complete_episodes),
        "mean_b_minus_a": (
            sum(v[0] for v in complete_episodes) / len(complete_episodes)
            if complete_episodes
            else None
        ),
        "mean_a_replicate_gap": (
            sum(v[1] for v in complete_episodes) / len(complete_episodes)
            if complete_episodes
            else None
        ),
        "mean_b_replicate_gap": (
            sum(v[2] for v in complete_episodes) / len(complete_episodes)
            if complete_episodes
            else None
        ),
        "interpretation": "complete_episode_diagnostic_not_population_ITT",
    }


def release_holdout(root: Path, *, reason: str) -> None:
    if not reason.strip():
        raise ValueError("holdout release needs an explicit final-evaluation reason")
    registration = _load_registration(root)
    write_once(
        root / "holdout_release.json",
        {
            "registration_digest": registration["registration_digest"],
            "released_at_ms": time_ns() // 1_000_000,
            "reason": reason,
        },
    )


def require_split_release(root: Path, registration: dict, split: str) -> None:
    if split not in {"development", "holdout"}:
        raise ValueError("development or holdout split required")
    if split == "holdout":
        path = root / "holdout_release.json"
        if (
            not path.exists()
            or json.loads(path.read_text(encoding="utf-8")).get("registration_digest")
            != registration["registration_digest"]
        ):
            raise ValueError("holdout is sealed until explicit final review release")


def summarize_outcomes(root: Path, outcomes: list[dict], *, split: str = "development") -> dict:
    """Join Host-supplied, cost-net Paper outcomes; never synthesize win/loss labels."""
    registration = _load_registration(root)
    require_split_release(root, registration, split)
    assigned = {a["case_id"]: a for a in registration["assignments"] if a["split"] == split}
    provided = {o["case_id"]: o for o in outcomes}
    if len(provided) != len(outcomes) or not set(provided) <= set(assigned):
        raise ValueError("duplicate, unassigned or cross-split outcome")
    samples = {s["sample_id"]: s for s in registration["samples"]}
    states = {r["case_id"]: r["status"] for r in summarize_study(root)["cases"]}
    counts = {"assigned": len(assigned), "resolved": 0, "censored": 0, "missing": 0, "failed": 0}
    rows = []
    for case_id, assignment in assigned.items():
        outcome = provided.get(case_id)
        value = None
        if states[case_id] != "completed":
            counts["failed" if states[case_id] == "failed" else "missing"] += 1
        elif outcome is None:
            counts["missing"] += 1
        else:
            if (
                outcome.get("execution_version") != registration["plan"]["execution_version"]
                or outcome.get("window_end_ms") != samples[assignment["sample_id"]]["window_end_ms"]
            ):
                raise ValueError("outcome execution version or preregistered horizon mismatch")
            status = outcome.get("status")
            if status == "closed":
                value = outcome.get("return_r")
                if type(value) not in (int, float) or not math.isfinite(value):
                    raise ValueError("closed outcome needs finite cost-net return_r")
            elif status in {"no_order", "entry_not_triggered"}:
                if outcome.get("return_r") not in (None, 0, 0.0):
                    raise ValueError("nontraded outcome cannot carry a trading return")
                value = 0.0
            elif status not in {"pending", "ambiguous"}:
                raise ValueError("unknown outcome status")
            counts["resolved" if value is not None else "censored"] += 1
        rows.append({**assignment, "return_r": value})
    return {
        "registration_digest": registration["registration_digest"],
        "contrast": registration["plan"]["contrast"],
        "split": split,
        "coverage": counts,
        "paired_episodes": paired_episode_summary(rows, "return_r"),
        "ready_for_promotion": False,
        "calibrated_probability": False,
        "limitation": "Descriptive cost-net Paper contrast; no confidence interval, calibration or efficacy claim.",
    }
