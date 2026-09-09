"""Backend-neutral blind review packets and preregistered quality scoring."""

from __future__ import annotations

import copy
import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from pa_agent.experiments.backend_aabb import validate_backend_records

QUALITY_WEIGHTS = {
    "market_context": 0.35,
    "brooks_setup": 0.30,
    "evidence_traceability": 0.20,
    "stage_coherence": 0.15,
}
VPA_WEIGHTS = {
    "background_location": 0.25,
    "effort_result_comparison": 0.25,
    "acceptance_followthrough": 0.25,
    "counterevidence_uncertainty": 0.25,
}


def freeze_review_evidence(
    record: Any, *, knowledge: Mapping[str, str], measurements: Mapping[str, Any]
) -> dict:
    """Freeze complete domain evidence before study assignment, never future outcomes.

    Knowledge and measurements must be the complete authorized frozen resources,
    supplied by the Host, not summaries or files reread after seeing outcomes.
    """
    from pa_agent.experiments.replay import frozen_input_from_record
    from pa_agent.ai.vpa_measurements import validate_frozen_vpa_catalog
    from pa_agent.data.bar_close_wait import timeframe_to_seconds
    from pa_agent.data.datetime_ts import ts_open_to_ms
    from pa_agent.data.snapshot_identity import canonical_market_snapshot_hash
    from pa_agent.experiments.study_protocol import digest

    as_of = record.role_runtime.get("analysis_as_of_ms")
    if type(as_of) is not int or not knowledge or not measurements:
        raise ValueError("complete frozen knowledge, measurements and known as-of are required")
    if any(not isinstance(text, str) or not text.strip() for text in knowledge.values()):
        raise ValueError("knowledge resources must contain full text")
    frame, htf, catalog = frozen_input_from_record(record)
    validate_frozen_vpa_catalog(frame, catalog)
    if not htf or htf.get("status") != "available" or not record.htf_kline_data:
        raise ValueError("complete frozen HTF evidence required")
    for bars, timeframe in (
        (record.kline_data, record.meta.timeframe),
        (record.htf_kline_data, htf["timeframe"]),
    ):
        duration = timeframe_to_seconds(timeframe)
        if not duration or "M" in timeframe:
            raise ValueError("fixed-duration frozen bars required")
        if any(
            bar.get("closed") is not True
            or int(ts_open_to_ms(bar["ts_open"])) + duration * 1000 > as_of
            for bar in bars
        ):
            raise ValueError("review evidence crosses frozen as-of")
    evidence = {
        "symbol": record.meta.symbol,
        "timeframe": record.meta.timeframe,
        "as_of_ms": as_of,
        "kline_data": copy.deepcopy(record.kline_data),
        "snapshot_context": copy.deepcopy(record.snapshot_context),
        "htf_context": copy.deepcopy(record.htf_context),
        "htf_kline_data": copy.deepcopy(record.htf_kline_data),
        "vpa_catalog": copy.deepcopy(catalog),
        "knowledge": dict(knowledge),
        "measurements": copy.deepcopy(dict(measurements)),
    }
    evidence["snapshot_hash"] = canonical_market_snapshot_hash(
        symbol=record.meta.symbol,
        timeframe=record.meta.timeframe,
        kline_data=record.kline_data,
        snapshot_context=record.snapshot_context,
        htf_context=record.htf_context,
        htf_kline_data=record.htf_kline_data,
    )
    evidence["content_digest"] = digest(evidence)
    return evidence


def _dimension_scores(rating: Mapping, weights: Mapping) -> dict[str, float]:
    values = {}
    for name in weights:
        value = rating.get(name)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or not 0 <= value <= 5
        ):
            raise ValueError(f"{name} rating must be a finite number between 0 and 5")
        values[name] = float(value)
    return values


def validate_review_evidence(evidence: Mapping) -> None:
    from pa_agent.experiments.study_protocol import digest
    from pa_agent.data.snapshot_identity import canonical_market_snapshot_hash
    from pa_agent.ai.vpa_measurements import vpa_catalog_id

    required = {
        "symbol",
        "timeframe",
        "as_of_ms",
        "kline_data",
        "snapshot_context",
        "htf_context",
        "htf_kline_data",
        "vpa_catalog",
        "knowledge",
        "measurements",
        "snapshot_hash",
        "content_digest",
    }
    if set(evidence) != required or any(
        not evidence[k] for k in ("knowledge", "measurements", "kline_data", "htf_kline_data")
    ):
        raise ValueError("incomplete or non-domain review evidence")
    body = {key: value for key, value in evidence.items() if key != "content_digest"}
    if digest(body) != evidence["content_digest"]:
        raise ValueError("review evidence integrity mismatch")
    market = {
        key: evidence[key]
        for key in (
            "symbol",
            "timeframe",
            "kline_data",
            "snapshot_context",
            "htf_context",
            "htf_kline_data",
        )
    }
    if canonical_market_snapshot_hash(**market) != evidence["snapshot_hash"] or vpa_catalog_id(
        evidence["vpa_catalog"]
    ) != evidence["vpa_catalog"].get("catalog_id"):
        raise ValueError("review evidence identity mismatch")
    from pa_agent.data.bar_close_wait import timeframe_to_seconds
    from pa_agent.data.datetime_ts import ts_open_to_ms

    as_of = evidence["as_of_ms"]
    if type(as_of) is not int or as_of < 0:
        raise ValueError("known UTC as-of required")
    for bars, timeframe in (
        (evidence["kline_data"], evidence["timeframe"]),
        (evidence["htf_kline_data"], evidence["htf_context"]["timeframe"]),
    ):
        duration = timeframe_to_seconds(timeframe)
        if (
            not duration
            or "M" in timeframe
            or any(
                bar.get("closed") is not True
                or int(ts_open_to_ms(bar["ts_open"])) + duration * 1000 > as_of
                for bar in bars
            )
        ):
            raise ValueError("review evidence crosses frozen as-of")


def export_study_review(
    root, public_dir, key_path, *, blinding_salt: str, split: str = "development"
) -> dict:
    """Write reviewer-only packet and a separate private key; refuse overwrite."""
    from pathlib import Path
    from pa_agent.experiments.study_protocol import (
        _load_registration,
        digest,
        require_split_release,
        summarize_study,
        write_once,
    )

    if split not in {"development", "holdout"} or len(blinding_salt) < 16:
        raise ValueError("explicit split and at least 16-character blinding salt required")
    root, public_dir, key_path = Path(root), Path(public_dir).resolve(), Path(key_path).resolve()
    if key_path == public_dir or public_dir in key_path.parents:
        raise ValueError("unblinding key must be outside reviewer directory")
    if public_dir == root.resolve() or public_dir in root.resolve().parents:
        raise ValueError("reviewer directory must not expose private study artifacts")
    registration = _load_registration(root)
    require_split_release(root, registration, split)
    states = {row["case_id"]: row for row in summarize_study(root)["cases"]}
    samples = {sample["sample_id"]: sample for sample in registration["samples"]}
    cases, keys = [], []
    for assignment in registration["assignments"]:
        if assignment["split"] != split:
            continue
        blind_id = hashlib.sha256(
            f"{blinding_salt}:{registration['registration_digest']}:{assignment['case_id']}".encode()
        ).hexdigest()[:24]
        sample = samples[assignment["sample_id"]]
        validate_review_evidence(sample["evidence"])
        receipt_path = root / "receipts" / f"{assignment['case_id']}.json"
        receipt = (
            json.loads(receipt_path.read_text(encoding="utf-8")) if receipt_path.exists() else {}
        )
        output = receipt.get("result", {}).get("output", {})
        # Whitelist analysis fields; never include calls, provider, runtime, PnL or future bars.
        analysis = {
            name: copy.deepcopy(output.get(name))
            for name in (
                "stage1_diagnosis",
                "stage2_decision",
                "vpa_evidence",
                "pa_hypotheses",
                "strategy_files_used",
            )
        }
        if isinstance(analysis["stage2_decision"], dict):
            analysis["stage2_decision"].pop("analysis_basis", None)
        cases.append(
            {
                "blind_id": blind_id,
                "evidence_id": sample["evidence_digest"],
                "run_status": states[assignment["case_id"]]["status"],
                **analysis,
            }
        )
        keys.append(
            {
                "blind_id": blind_id,
                "group": assignment["arm_id"][0],
                "arm_id": assignment["arm_id"],
                "case_id": assignment["case_id"],
                "sample_id": sample["sample_id"],
                "episode_id": sample["episode_id"],
                "assigned_backend": assignment["condition"]["backend"],
            }
        )
    if not cases:
        raise ValueError("no preregistered cases in selected split")
    selected_evidence = {case["evidence_id"] for case in cases}
    packet = {
        "schema_version": 2,
        "rubric": {
            "scale": "0-5",
            "weights": QUALITY_WEIGHTS,
            "vpa_weights": VPA_WEIGHTS,
            "future_outcomes_withheld": True,
            "blinding_limitation": "Metadata blinded; prose and treatment content can remain inferable.",
        },
        "cases": sorted(cases, key=lambda case: case["blind_id"]),
        "evidence": {
            s["evidence_digest"]: s["evidence"]
            for s in registration["samples"]
            if s["evidence_digest"] in selected_evidence
        },
    }
    key = {
        "schema_version": 2,
        "packet_digest": digest(packet),
        "registration_digest": registration["registration_digest"],
        "contrast": registration["plan"]["contrast"],
        "split": split,
        "cases": keys,
    }
    if (public_dir / "packet.json").exists() or key_path.exists():
        raise FileExistsError("review artifacts already exist")
    write_once(key_path, key)
    write_once(public_dir / "packet.json", packet)
    return packet


@dataclass(frozen=True)
class BlindedBackendReview:
    """Public reviewer packet plus a separately held unblinding key."""

    packet: dict[str, Any]
    unblinding_key: dict[str, Any]


def build_blinded_backend_review(
    records: Sequence[Any],
    *,
    blinding_salt: str,
    frozen_evidence: Mapping[str, Any],
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
    validate_review_evidence(frozen_evidence)
    if any(
        (record.experiment_context or {}).get("market_snapshot_hash")
        != frozen_evidence["snapshot_hash"]
        or (record.vpa_catalog or {}).get("catalog_id") != frozen_evidence["vpa_catalog"].get("catalog_id")
        for record in values
    ):
        raise ValueError("review evidence does not match backend experiment")

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
                "evidence_id": frozen_evidence["content_digest"],
                "stage1_diagnosis": copy.deepcopy(getattr(record, "stage1_diagnosis", None)),
                "stage2_decision": copy.deepcopy(getattr(record, "stage2_decision", None)),
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
    packet = {
        "schema_version": 2,
        "rubric": {
            "scale": "0-5",
            "weights": dict(QUALITY_WEIGHTS),
            "vpa_scored_separately": True,
            "vpa_weights": dict(VPA_WEIGHTS),
            "future_outcomes_withheld": True,
        },
        "cases": cases,
        "evidence": {frozen_evidence["content_digest"]: copy.deepcopy(dict(frozen_evidence))},
    }
    from pa_agent.experiments.study_protocol import digest

    return BlindedBackendReview(
        packet=packet,
        unblinding_key={
            "schema_version": 2,
            "packet_digest": digest(packet),
            "contrast": "backend",
            "cases": key_rows,
        },
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
    if "" in expected_ids or len(expected_ids) != len(packet.get("cases", [])):
        raise ValueError("packet case identities must be present and unique")
    if not expected_ids or set(by_id) != expected_ids or len(by_id) != len(ratings):
        raise ValueError("ratings must cover every blind case exactly once")
    scored: list[dict[str, Any]] = []
    v2 = packet.get("schema_version") == 2
    cases_by_id = {str(case["blind_id"]): case for case in packet["cases"]}
    for blind_id in sorted(expected_ids):
        rating = by_id[blind_id]
        status = rating.get("review_status", "rated")
        if (
            v2
            and cases_by_id[blind_id].get("run_status") in {"failed", "missing"}
            and status != "unscorable"
        ):
            raise ValueError("failed or missing case must remain unscorable")
        if v2 and status == "unscorable":
            if not str(rating.get("reviewer_notes") or "").strip():
                raise ValueError("unscorable case requires a reason")
            scored.append(
                {
                    "blind_id": blind_id,
                    "review_status": status,
                    "weighted_score_0_100": None,
                    "vpa_score_0_100": None,
                    "reviewer_notes": str(rating["reviewer_notes"]),
                }
            )
            continue
        if status != "rated":
            raise ValueError("invalid review status")
        dimensions = _dimension_scores(rating, QUALITY_WEIGHTS)
        if type(rating.get("critical_error", False)) is not bool:
            raise ValueError("critical_error must be boolean")
        weighted = sum(dimensions[name] * weight for name, weight in QUALITY_WEIGHTS.items())
        scored.append(
            {
                "blind_id": blind_id,
                "dimensions": dimensions,
                "weighted_score_0_100": round(weighted * 20, 4),
                "critical_error": bool(rating.get("critical_error", False)),
                "reviewer_notes": str(rating.get("reviewer_notes") or ""),
            }
        )
        if v2:
            error_kind = rating.get("error_kind", "none")
            if error_kind not in {"none", "factual", "theoretical", "reasonable_disagreement"}:
                raise ValueError(
                    "quality review must not use future outcomes as correctness labels"
                )
            vpa = rating.get("vpa")
            vpa_scores = _dimension_scores(vpa, VPA_WEIGHTS) if isinstance(vpa, Mapping) else None
            if (
                vpa_scores is None
                and not str(rating.get("vpa_not_applicable_reason") or "").strip()
            ):
                raise ValueError("missing VPA ratings require an applicability reason")
            scored[-1].update(
                review_status="rated",
                error_kind=error_kind,
                vpa_dimensions=vpa_scores,
                vpa_score_0_100=(
                    round(sum(vpa_scores[n] * w for n, w in VPA_WEIGHTS.items()) * 20, 4)
                    if vpa_scores
                    else None
                ),
                vpa_not_applicable_reason=rating.get("vpa_not_applicable_reason"),
            )
    result = {"schema_version": 2 if v2 else 1, "scored_cases": scored}
    if v2:
        from pa_agent.experiments.study_protocol import digest

        rated = sum(row.get("review_status") == "rated" for row in scored)
        result.update(
            packet_digest=digest(packet),
            coverage={"assigned": len(scored), "rated": rated, "unscorable": len(scored) - rated},
            ready_for_promotion=False,
            calibrated_probability=False,
        )
        result["score_digest"] = digest(result)
    return result


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
    if len(keys) != len(unblinding_key.get("cases", [])):
        raise ValueError("duplicate unblinding identity")
    if scored.get("schema_version") == 2:
        from pa_agent.experiments.study_protocol import digest

        if digest({k: v for k, v in scored.items() if k != "score_digest"}) != scored.get(
            "score_digest"
        ):
            raise ValueError("sealed scores integrity mismatch")
        if not scored.get("packet_digest") or scored["packet_digest"] != unblinding_key.get(
            "packet_digest"
        ):
            raise ValueError("unblinding key does not match scored packet")
        ids = [row.get("blind_id") for row in scored.get("scored_cases", [])]
        if len(ids) != len(set(ids)) or set(ids) != set(keys):
            raise ValueError("unblinding coverage mismatch")
    rows: list[dict[str, Any]] = []
    for item in scored.get("scored_cases", []):
        blind_id = str(item.get("blind_id") or "")
        if blind_id not in keys:
            raise ValueError(f"missing unblinding identity: {blind_id}")
        if set(keys[blind_id]) - {
            "blind_id",
            "run_id",
            "arm_id",
            "group",
            "assigned_backend",
            "case_id",
            "sample_id",
            "episode_id",
        }:
            raise ValueError("unblinding key contains non-identity fields")
        rows.append({**copy.deepcopy(dict(item)), **dict(keys[blind_id])})
    groups: dict[str, dict[str, Any]] = {}
    for group in ("A", "B"):
        selected = [item for item in rows if item.get("group") == group]
        scores = [
            float(item["weighted_score_0_100"])
            for item in selected
            if item["weighted_score_0_100"] is not None
        ]
        if any(not math.isfinite(value) or not 0 <= value <= 100 for value in scores):
            raise ValueError("invalid sealed score")
        groups[group] = {
            "case_count": len(selected),
            "rated_count": len(scores),
            "mean_weighted_score_0_100": (round(sum(scores) / len(scores), 4) if scores else None),
            "critical_error_count": sum(bool(item.get("critical_error")) for item in selected),
        }
    delta = None
    a_score = groups["A"]["mean_weighted_score_0_100"]
    b_score = groups["B"]["mean_weighted_score_0_100"]
    if a_score is not None and b_score is not None:
        delta = round(
            float(b_score) - float(a_score),
            4,
        )
    result = {
        "schema_version": scored.get("schema_version", 1),
        "groups": groups,
        "codex_minus_direct_score": delta,
        "cases": rows,
        "ready_for_promotion": False,
        "calibrated_probability": False,
        "causal_limitation": (
            "The contrast is between complete backend systems and does not isolate "
            "the GPT model effect from agentic evidence investigation."
        ),
    }
    contrast = unblinding_key.get("contrast", "backend")
    result["contrast"] = contrast
    if contrast != "backend":
        result.pop("codex_minus_direct_score")
        result["b_minus_a_score"] = delta
        result["causal_limitation"] = (
            "Preregistered contrast; descriptive quality scores are not calibration or trading efficacy."
        )
    if rows and all("episode_id" in row and "sample_id" in row for row in rows):
        from pa_agent.experiments.study_protocol import paired_episode_summary

        result["paired_episodes"] = paired_episode_summary(rows, "weighted_score_0_100")
        result["paired_vpa_episodes"] = paired_episode_summary(rows, "vpa_score_0_100")
    return result
