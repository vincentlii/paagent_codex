"""Backend-neutral blind review packets and preregistered quality scoring."""

from __future__ import annotations

import copy
import hashlib
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from pa_agent.experiments.backend_aabb import validate_backend_records

QUALITY_WEIGHTS = {
    "market_context": 0.35,
    "brooks_setup": 0.30,
    "evidence_traceability": 0.20,
    "stage_coherence": 0.15,
}


@dataclass(frozen=True)
class BlindedBackendReview:
    """Public reviewer packet plus a separately held unblinding key."""

    packet: dict[str, Any]
    unblinding_key: dict[str, Any]


def build_blinded_backend_review(
    records: Sequence[Any],
    *,
    blinding_salt: str,
) -> BlindedBackendReview:
    """Remove backend/runtime identity while retaining professional evidence."""
    values = list(records)
    protocol = validate_backend_records(values)
    if not protocol.get("valid"):
        raise ValueError(
            "backend review requires one valid four-arm set: "
            + ", ".join(protocol.get("errors") or [])
        )
    salt = str(blinding_salt)
    if len(salt) < 16:
        raise ValueError("blinding_salt must contain at least 16 characters")

    cases: list[dict[str, Any]] = []
    key_rows: list[dict[str, Any]] = []
    for record in values:
        context = dict(getattr(record, "experiment_context", {}) or {})
        run_id = str(getattr(getattr(record, "meta", None), "run_id", "") or "")
        blind_id = hashlib.sha256(f"{salt}:{run_id}".encode("utf-8")).hexdigest()[:20]
        cases.append(
            {
                "blind_id": blind_id,
                "symbol": str(getattr(getattr(record, "meta", None), "symbol", "")),
                "timeframe": str(getattr(getattr(record, "meta", None), "timeframe", "")),
                "market_snapshot_hash": context.get("market_snapshot_hash"),
                "stage1_diagnosis": copy.deepcopy(
                    getattr(record, "stage1_diagnosis", None)
                ),
                "stage2_decision": copy.deepcopy(
                    getattr(record, "stage2_decision", None)
                ),
                "vpa_evidence": copy.deepcopy(getattr(record, "vpa_evidence", None)),
                "run_status": (getattr(record, "run_outcome", {}) or {}).get("status"),
            }
        )
        key_rows.append(
            {
                "blind_id": blind_id,
                "run_id": run_id,
                "arm_id": context.get("arm_id"),
                "group": context.get("group"),
                "assigned_backend": context.get("assigned_backend"),
            }
        )
    cases.sort(key=lambda item: item["blind_id"])
    return BlindedBackendReview(
        packet={
            "schema_version": 1,
            "rubric": {
                "scale": "0-5",
                "weights": dict(QUALITY_WEIGHTS),
                "vpa_scored_separately": True,
            },
            "cases": cases,
        },
        unblinding_key={"schema_version": 1, "cases": key_rows},
    )


def score_blinded_backend_review(
    packet: Mapping[str, Any],
    ratings: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Validate one blind rating per case and compute the fixed weighted score."""
    expected_ids = {
        str(item.get("blind_id") or "")
        for item in packet.get("cases", [])
        if isinstance(item, Mapping)
    }
    by_id = {str(item.get("blind_id") or ""): item for item in ratings}
    if not expected_ids or set(by_id) != expected_ids or len(by_id) != len(ratings):
        raise ValueError("ratings must cover every blind case exactly once")
    scored: list[dict[str, Any]] = []
    for blind_id in sorted(expected_ids):
        rating = by_id[blind_id]
        dimensions: dict[str, float] = {}
        for name, weight in QUALITY_WEIGHTS.items():
            value = float(rating[name])
            if value < 0 or value > 5:
                raise ValueError(f"{name} rating must be between 0 and 5")
            dimensions[name] = value
        weighted = sum(
            dimensions[name] * weight for name, weight in QUALITY_WEIGHTS.items()
        )
        scored.append(
            {
                "blind_id": blind_id,
                "dimensions": dimensions,
                "weighted_score_0_100": round(weighted * 20, 4),
                "critical_error": bool(rating.get("critical_error", False)),
                "reviewer_notes": str(rating.get("reviewer_notes") or ""),
            }
        )
    return {"schema_version": 1, "scored_cases": scored}


def unblind_backend_scores(
    scored: Mapping[str, Any],
    unblinding_key: Mapping[str, Any],
) -> dict[str, Any]:
    """Reveal treatment groups only after the blind ratings are frozen."""
    keys = {
        str(item.get("blind_id") or ""): item
        for item in unblinding_key.get("cases", [])
        if isinstance(item, Mapping)
    }
    rows: list[dict[str, Any]] = []
    for item in scored.get("scored_cases", []):
        blind_id = str(item.get("blind_id") or "")
        if blind_id not in keys:
            raise ValueError(f"missing unblinding identity: {blind_id}")
        rows.append({**copy.deepcopy(dict(item)), **dict(keys[blind_id])})
    groups: dict[str, dict[str, Any]] = {}
    for group in ("A", "B"):
        selected = [item for item in rows if item.get("group") == group]
        scores = [float(item["weighted_score_0_100"]) for item in selected]
        groups[group] = {
            "case_count": len(selected),
            "mean_weighted_score_0_100": (
                round(sum(scores) / len(scores), 4) if scores else None
            ),
            "critical_error_count": sum(
                bool(item.get("critical_error")) for item in selected
            ),
        }
    delta = None
    a_score = groups["A"]["mean_weighted_score_0_100"]
    b_score = groups["B"]["mean_weighted_score_0_100"]
    if a_score is not None and b_score is not None:
        delta = round(
            float(b_score) - float(a_score),
            4,
        )
    return {
        "schema_version": 1,
        "groups": groups,
        "codex_minus_direct_score": delta,
        "cases": rows,
        "causal_limitation": (
            "The contrast is between complete backend systems and does not isolate "
            "the GPT model effect from agentic evidence investigation."
        ),
    }
