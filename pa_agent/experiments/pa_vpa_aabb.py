"""True A/A/B/B protocol for estimating the incremental effect of VPA.

Production Shadow and Soft runs are not experiments. This module executes four
independent analyses over one immutable market input and compares their saved
outputs in program code. A1/A2 are PA-only; B1/B2 are PA plus VPA in Soft mode.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pa_agent.ai.higher_timeframe import serialize_higher_timeframe_context
from pa_agent.ai.vpa_measurements import (
    build_vpa_evidence_catalog,
    validate_frozen_vpa_catalog,
    vpa_catalog_id,
)
from pa_agent.data.base import KlineFrame
from pa_agent.data.snapshot_bundle import compact_snapshot_context, serialize_frame_bars
from pa_agent.records.schema import AnalysisRecord

AABB_PROTOCOL_VERSION = "pa_vpa_aabb_v1"
ArmGroup = Literal["A", "B"]
ArmMode = Literal["off", "soft"]
_EXPECTED_ARM_IDS = frozenset({"A1", "A2", "B1", "B2"})
_VALID_SEQUENCES = {
    ("A1", "B1", "B2", "A2"): "ABBA",
    ("B1", "A1", "A2", "B2"): "BAAB",
}


def _stable_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        default=str,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _cohort_id(
    *,
    provider_fingerprint: str,
    settings_fingerprint: str,
    analysis_fingerprint: str,
) -> str:
    return _stable_hash(
        {
            "protocol_version": AABB_PROTOCOL_VERSION,
            "provider_fingerprint": provider_fingerprint,
            "settings_fingerprint": settings_fingerprint,
            "analysis_fingerprint": analysis_fingerprint,
        }
    )


def _provider_fingerprint(settings: Any) -> str:
    provider = getattr(settings, "provider", None)
    if provider is None:
        return _stable_hash({})
    payload = provider.model_dump() if hasattr(provider, "model_dump") else vars(provider)
    payload = dict(payload)
    payload.pop("api_key", None)
    payload.pop("api_key_encrypted", None)
    return _stable_hash(payload)


def _settings_fingerprint(settings: Any) -> str:
    prompt = getattr(settings, "prompt", None)
    prompt_payload = (
        prompt.model_dump() if hasattr(prompt, "model_dump") else dict(vars(prompt or {}))
    )
    prompt_payload = dict(prompt_payload)
    prompt_payload.pop("vpa_mode", None)
    prompt_payload.pop("vpa_enabled", None)
    return _stable_hash(
        {
            "general": (
                settings.general.model_dump()
                if hasattr(getattr(settings, "general", None), "model_dump")
                else {}
            ),
            "prompt": prompt_payload,
            "validation": (
                settings.validation.model_dump()
                if hasattr(getattr(settings, "validation", None), "model_dump")
                else {}
            ),
        }
    )


@lru_cache(maxsize=4)
def analysis_runtime_fingerprint(project_root: Path | None = None) -> str:
    """Hash analysis code and prompt assets without relying on Git metadata."""
    root = Path(project_root) if project_root is not None else Path(__file__).resolve().parents[2]
    candidates: list[Path] = []
    for directory, suffixes in (
        (root / "pa_agent", {".py"}),
        (root / "prompt_engineering", {".json", ".md", ".txt"}),
    ):
        if not directory.is_dir():
            continue
        candidates.extend(
            path
            for path in directory.rglob("*")
            if path.is_file() and path.suffix.lower() in suffixes
        )
    if not candidates:
        raise ValueError("analysis runtime files are unavailable for fingerprinting")
    digest = hashlib.sha256()
    for path in sorted(candidates, key=lambda item: item.relative_to(root).as_posix()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def balanced_arm_sequence(frozen_input_hash: str) -> tuple[str, str, str, str]:
    """Choose ABBA or BAAB deterministically from an immutable input hash."""
    text = str(frozen_input_hash or "").strip().lower()
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise ValueError("frozen_input_hash must be a SHA-256 hex digest")
    return ("A1", "B1", "B2", "A2") if int(text[-1], 16) % 2 == 0 else ("B1", "A1", "A2", "B2")


@dataclass(frozen=True)
class ArmSpec:
    arm_id: str
    group: ArmGroup
    replicate: int
    mode: ArmMode
    sequence_position: int


@dataclass(frozen=True)
class FrozenExperimentInput:
    frame: KlineFrame
    htf_context: dict[str, Any] | None
    vpa_catalog: dict[str, Any]
    frozen_input_hash: str
    market_snapshot_hash: str
    cohort_id: str
    snapshot_hash: str
    htf_hash: str
    catalog_id: str
    provider_fingerprint: str
    settings_fingerprint: str
    analysis_fingerprint: str
    snapshot_context: dict[str, Any]
    htf_snapshot_context: dict[str, Any] | None
    htf_kline_data: tuple[dict[str, Any], ...]

    def arm_context(
        self,
        *,
        experiment_id: str,
        arm_id: str,
        sequence: tuple[str, str, str, str],
        sequence_position: int,
        client_instance_token: str,
        stage1_request_hash: str,
    ) -> dict[str, Any]:
        group: ArmGroup = "A" if arm_id.startswith("A") else "B"
        return {
            "protocol_version": AABB_PROTOCOL_VERSION,
            "experiment_id": experiment_id,
            "frozen_input_hash": self.frozen_input_hash,
            "market_snapshot_hash": self.market_snapshot_hash,
            "cohort_id": self.cohort_id,
            "snapshot_hash": self.snapshot_hash,
            "htf_hash": self.htf_hash,
            "catalog_id": self.catalog_id,
            "provider_fingerprint": self.provider_fingerprint,
            "settings_fingerprint": self.settings_fingerprint,
            "analysis_fingerprint": self.analysis_fingerprint,
            "order_scheme": _VALID_SEQUENCES[sequence],
            "arm_sequence": list(sequence),
            "arm_id": arm_id,
            "group": group,
            "replicate": int(arm_id[1]),
            "sequence_position": int(sequence_position),
            "assigned_mode": "off" if group == "A" else "soft",
            "client_instance_token": client_instance_token,
            "stage1_request_hash": stage1_request_hash,
            "provider_fallback_allowed": False,
            "incremental_context_enabled": False,
        }


def freeze_experiment_input(
    frame: KlineFrame,
    htf_context: dict[str, Any] | None,
    settings: Any,
    *,
    catalog: dict[str, Any] | None = None,
    analysis_fingerprint: str | None = None,
) -> FrozenExperimentInput:
    """Validate and freeze every fact shared by all four experiment arms."""
    frozen_catalog = (
        build_vpa_evidence_catalog(frame)
        if catalog is None
        else validate_frozen_vpa_catalog(frame, catalog)
    )

    htf_snapshot = serialize_higher_timeframe_context(htf_context)
    higher_frame = None
    if isinstance(htf_context, dict) and htf_context.get("status") == "available":
        candidate = htf_context.get("frame")
        if not isinstance(candidate, KlineFrame) or not candidate.bars:
            raise ValueError("available HTF context requires a non-empty KlineFrame")
        higher_frame = candidate
    htf_bars = serialize_frame_bars(higher_frame)
    htf_hash = _stable_hash(
        {
            "context": htf_snapshot,
            "kline_data": htf_bars,
        }
    )
    snapshot_context = compact_snapshot_context(frame)
    provider_fingerprint = _provider_fingerprint(settings)
    settings_fingerprint = _settings_fingerprint(settings)
    runtime_fingerprint = str(analysis_fingerprint or analysis_runtime_fingerprint()).strip()
    if len(runtime_fingerprint) != 64 or any(
        character not in "0123456789abcdef" for character in runtime_fingerprint.lower()
    ):
        raise ValueError("analysis_fingerprint must be a SHA-256 hex digest")
    runtime_fingerprint = runtime_fingerprint.lower()
    market_snapshot_hash = _stable_hash(
        {
            "symbol": frame.symbol,
            "timeframe": frame.timeframe,
            "kline_data": serialize_frame_bars(frame),
            "snapshot_context": snapshot_context,
            "htf_context": htf_snapshot,
            "htf_kline_data": htf_bars,
        }
    )
    cohort_id = _cohort_id(
        provider_fingerprint=provider_fingerprint,
        settings_fingerprint=settings_fingerprint,
        analysis_fingerprint=runtime_fingerprint,
    )
    frozen_hash = _stable_hash(
        {
            "market_snapshot_hash": market_snapshot_hash,
            "catalog_id": frozen_catalog["catalog_id"],
            "cohort_id": cohort_id,
        }
    )
    return FrozenExperimentInput(
        frame=frame,
        htf_context=copy.deepcopy(htf_context),
        vpa_catalog=frozen_catalog,
        frozen_input_hash=frozen_hash,
        market_snapshot_hash=market_snapshot_hash,
        cohort_id=cohort_id,
        snapshot_hash=str(frozen_catalog["snapshot_hash"]),
        htf_hash=htf_hash,
        catalog_id=str(frozen_catalog["catalog_id"]),
        provider_fingerprint=provider_fingerprint,
        settings_fingerprint=settings_fingerprint,
        analysis_fingerprint=runtime_fingerprint,
        snapshot_context=copy.deepcopy(snapshot_context),
        htf_snapshot_context=copy.deepcopy(htf_snapshot),
        htf_kline_data=tuple(copy.deepcopy(htf_bars)),
    )


def _record_frozen_input_hash(record: AnalysisRecord) -> str:
    context = record.experiment_context or {}
    return _stable_hash(
        {
            "market_snapshot_hash": _record_market_snapshot_hash(record),
            "catalog_id": context.get("catalog_id"),
            "cohort_id": _record_cohort_id(record),
        }
    )


def _record_market_snapshot_hash(record: AnalysisRecord) -> str:
    return _stable_hash(
        {
            "symbol": record.meta.symbol,
            "timeframe": record.meta.timeframe,
            "kline_data": record.kline_data,
            "snapshot_context": record.snapshot_context,
            "htf_context": record.htf_context,
            "htf_kline_data": record.htf_kline_data,
        }
    )


def _record_cohort_id(record: AnalysisRecord) -> str:
    context = record.experiment_context or {}
    return _cohort_id(
        provider_fingerprint=str(context.get("provider_fingerprint") or ""),
        settings_fingerprint=str(context.get("settings_fingerprint") or ""),
        analysis_fingerprint=str(context.get("analysis_fingerprint") or ""),
    )


def _first_stage1_request_hash(record: AnalysisRecord) -> str:
    candidates = [
        item
        for item in record.call_attempts
        if isinstance(item, dict)
        and item.get("stage") == "stage1"
        and int(item.get("attempt") or 0) == 1
    ]
    if len(candidates) != 1:
        return ""
    return str(candidates[0].get("request_hash") or "")


def first_pass_clean(record: AnalysisRecord) -> bool:
    """Warnings are acceptable; retries, normalization, failures, and fallback are not."""
    outcome = record.run_outcome if isinstance(record.run_outcome, dict) else {}
    if record.exception is not None or outcome.get("status") not in {
        "completed",
        "completed_with_warnings",
    }:
        return False
    events = [item for item in record.validation_audit if isinstance(item, dict)]
    if any(str(item.get("category") or "") != "warning" for item in events):
        return False
    calls = [item for item in record.call_attempts if isinstance(item, dict)]
    if not calls:
        return False
    seen: set[tuple[str, str]] = set()
    for call in calls:
        key = (str(call.get("arm") or ""), str(call.get("stage") or ""))
        if key in seen or int(call.get("attempt") or 0) != 1:
            return False
        seen.add(key)
        if str(call.get("status") or "") != "validated":
            return False
        if call.get("normalization_applied"):
            return False
        if call.get("provider_fallback_used"):
            return False
    return True


def decision_signature(record: AnalysisRecord) -> dict[str, Any]:
    """Return externally computed decision fields; no model comparison prose is trusted."""
    stage1 = record.stage1_diagnosis if isinstance(record.stage1_diagnosis, dict) else {}
    stage2 = record.stage2_decision if isinstance(record.stage2_decision, dict) else {}
    decision = stage2.get("decision") if isinstance(stage2.get("decision"), dict) else {}
    bar_analysis = (
        stage2.get("bar_analysis") if isinstance(stage2.get("bar_analysis"), dict) else {}
    )
    entry_bar = (
        bar_analysis.get("entry_bar") if isinstance(bar_analysis.get("entry_bar"), dict) else {}
    )
    return {
        "cycle_position": stage1.get("cycle_position"),
        "alternative_cycle_position": stage1.get("alternative_cycle_position"),
        "direction": stage1.get("direction"),
        "gate_result": stage1.get("gate_result"),
        "order_type": decision.get("order_type"),
        "order_direction": decision.get("order_direction"),
        "entry_basis_bar": decision.get("entry_basis_bar"),
        "entry_basis_extreme": decision.get("entry_basis_extreme"),
        "entry_bar": entry_bar.get("bar"),
        "trade_confidence": decision.get("trade_confidence"),
        "estimated_win_rate": decision.get("estimated_win_rate"),
    }


def external_comparison(records: list[AnalysisRecord]) -> dict[str, Any]:
    by_arm = {
        str((record.experiment_context or {}).get("arm_id") or ""): record for record in records
    }
    signatures = {
        arm_id: decision_signature(record)
        for arm_id, record in by_arm.items()
        if arm_id in _EXPECTED_ARM_IDS
    }
    return {
        "signatures": signatures,
        "a_disagreement": signatures.get("A1") != signatures.get("A2"),
        "b_disagreement": signatures.get("B1") != signatures.get("B2"),
        "cross_group_differences": {
            f"{left}:{right}": [
                key
                for key in sorted(set(signatures.get(left, {})) | set(signatures.get(right, {})))
                if signatures.get(left, {}).get(key) != signatures.get(right, {}).get(key)
            ]
            for left in ("A1", "A2")
            for right in ("B1", "B2")
        },
    }


def validate_aabb_records(records: list[AnalysisRecord]) -> dict[str, Any]:
    """Validate protocol mechanics without judging PA or Wyckoff interpretation."""
    errors: set[str] = set()
    contexts = [
        record.experiment_context
        for record in records
        if isinstance(record.experiment_context, dict)
    ]
    if len(records) != 4 or len(contexts) != 4:
        errors.add("incomplete_four_arm_set")
        return {"valid": False, "errors": sorted(errors)}
    by_arm: dict[str, AnalysisRecord] = {}
    for record, context in zip(records, contexts, strict=True):
        arm_id = str(context.get("arm_id") or "")
        if arm_id in by_arm:
            errors.add("duplicate_arm_id")
        by_arm[arm_id] = record
    if set(by_arm) != _EXPECTED_ARM_IDS:
        errors.add("invalid_arm_set")

    invariant_keys = (
        "protocol_version",
        "experiment_id",
        "frozen_input_hash",
        "market_snapshot_hash",
        "cohort_id",
        "snapshot_hash",
        "htf_hash",
        "catalog_id",
        "provider_fingerprint",
        "settings_fingerprint",
        "analysis_fingerprint",
        "order_scheme",
    )
    for key in invariant_keys:
        if len({str(context.get(key)) for context in contexts}) != 1:
            errors.add(f"{key}_drift")
    for key in (
        "frozen_input_hash",
        "market_snapshot_hash",
        "cohort_id",
        "snapshot_hash",
        "htf_hash",
        "provider_fingerprint",
        "settings_fingerprint",
        "analysis_fingerprint",
    ):
        value = str(contexts[0].get(key) or "").lower()
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            errors.add(f"invalid_{key}")
    if any(context.get("protocol_version") != AABB_PROTOCOL_VERSION for context in contexts):
        errors.add("unsupported_protocol_version")

    sequences = {tuple(context.get("arm_sequence") or []) for context in contexts}
    if len(sequences) != 1 or next(iter(sequences), ()) not in _VALID_SEQUENCES:
        errors.add("invalid_arm_sequence")
    else:
        sequence = next(iter(sequences))
        if any(context.get("order_scheme") != _VALID_SEQUENCES[sequence] for context in contexts):
            errors.add("order_scheme_mismatch")
        positions = {
            int(context.get("sequence_position") or 0): str(context.get("arm_id") or "")
            for context in contexts
        }
        if tuple(positions.get(index, "") for index in range(1, 5)) != sequence:
            errors.add("arm_sequence_position_mismatch")

    tokens = [str(context.get("client_instance_token") or "") for context in contexts]
    if any(not token for token in tokens) or len(set(tokens)) != 4:
        errors.add("client_instance_reuse")
    if any(context.get("provider_fallback_allowed") is not False for context in contexts):
        errors.add("provider_fallback_allowed")
    if any(context.get("incremental_context_enabled") is not False for context in contexts):
        errors.add("incremental_context_enabled")

    hashes = {str(context.get("stage1_request_hash") or "") for context in contexts}
    if len(hashes) != 1 or "" in hashes:
        errors.add("pa_stage1_request_hash_drift")
    for record, context in zip(records, contexts, strict=True):
        if _first_stage1_request_hash(record) != context.get("stage1_request_hash"):
            errors.add("stage1_request_hash_not_in_call_ledger")
        if _record_frozen_input_hash(record) != context.get("frozen_input_hash"):
            errors.add("persisted_frozen_input_hash_mismatch")
        if _record_market_snapshot_hash(record) != context.get("market_snapshot_hash"):
            errors.add("persisted_market_snapshot_hash_mismatch")
        if _record_cohort_id(record) != context.get("cohort_id"):
            errors.add("persisted_cohort_id_mismatch")
        if bool(context.get("first_pass_clean")) != first_pass_clean(record):
            errors.add("first_pass_classification_mismatch")
        if any(
            bool(call.get("provider_fallback_used"))
            for call in record.call_attempts
            if isinstance(call, dict)
        ):
            errors.add("provider_fallback_used")

        arm_id = str(context.get("arm_id") or "")
        group = str(context.get("group") or "")
        expected_group = "A" if arm_id.startswith("A") else "B"
        if arm_id in _EXPECTED_ARM_IDS and int(context.get("replicate") or 0) != int(arm_id[1]):
            errors.add("arm_replicate_mismatch")
        if group != expected_group:
            errors.add("arm_group_mismatch")
        expected_mode = "off" if expected_group == "A" else "soft"
        if context.get("assigned_mode") != expected_mode:
            errors.add("arm_mode_mismatch")
        outcome = record.run_outcome if isinstance(record.run_outcome, dict) else {}
        if outcome.get("requested_vpa_mode") != expected_mode:
            errors.add("received_mode_mismatch")
        if expected_group == "A":
            if any(
                value is not None
                for value in (record.vpa_evidence, record.joint_decision, record.vpa_sidecar)
            ):
                errors.add("a_arm_vpa_leakage")
            if any(
                str(call.get("stage") or "") in {"vpa_evidence", "joint_arbiter"}
                for call in record.call_attempts
                if isinstance(call, dict)
            ):
                errors.add("a_arm_vpa_call_leakage")
        else:
            sidecar = record.vpa_sidecar if isinstance(record.vpa_sidecar, dict) else {}
            catalog = sidecar.get("evidence_catalog") if isinstance(sidecar, dict) else {}
            if not isinstance(catalog, dict) or catalog.get("catalog_id") != context.get(
                "catalog_id"
            ):
                errors.add("b_arm_catalog_drift")
            elif vpa_catalog_id(catalog) != context.get("catalog_id"):
                errors.add("b_arm_catalog_content_drift")
            role_execution = sidecar.get("role_execution") if isinstance(sidecar, dict) else {}
            if (
                not isinstance(role_execution, dict)
                or role_execution.get("provider_fallback_allowed") is not False
            ):
                errors.add("b_arm_role_fallback_boundary_missing")
            if record.vpa_evidence != sidecar.get(
                "vpa_report"
            ) or record.vpa_evidence != sidecar.get("stage1_context"):
                errors.add("b_arm_vpa_representation_drift")
            joint_arbitration = (
                record.joint_decision.get("joint_arbitration")
                if isinstance(record.joint_decision, dict)
                else None
            )
            if joint_arbitration != sidecar.get("joint_arbitration"):
                errors.add("b_arm_joint_representation_drift")
    return {"valid": not errors, "errors": sorted(errors)}


@dataclass(frozen=True)
class ArmExecution:
    record: AnalysisRecord
    client_identity: object
    client_instance_token: str


@dataclass(frozen=True)
class AabbRunResult:
    experiment_id: str
    status: str
    records: tuple[AnalysisRecord, ...]
    protocol_report: dict[str, Any]
    external_comparison: dict[str, Any]


class PaVpaAabbRunner:
    """Execute and persist a complete offline four-arm experiment."""

    def __init__(
        self,
        *,
        execute_arm: Callable[[ArmSpec, FrozenExperimentInput, Any], ArmExecution],
        pending_writer: Any,
        experiment_store: Any,
    ) -> None:
        self._execute_arm = execute_arm
        self._pending_writer = pending_writer
        self._experiment_store = experiment_store

    def run(
        self,
        frozen: FrozenExperimentInput,
        cancel_token: Any,
        *,
        source_run_id: str | None = None,
        source_record_path: str | None = None,
    ) -> AabbRunResult:
        sequence = balanced_arm_sequence(frozen.frozen_input_hash)
        experiment_id = f"aabb-{frozen.frozen_input_hash[:12]}-{uuid4().hex[:8]}"
        manifest = {
            "experiment_id": experiment_id,
            "protocol_version": AABB_PROTOCOL_VERSION,
            "status": "running",
            "symbol": frozen.frame.symbol,
            "timeframe": frozen.frame.timeframe,
            "frozen_input_hash": frozen.frozen_input_hash,
            "market_snapshot_hash": frozen.market_snapshot_hash,
            "cohort_id": frozen.cohort_id,
            "snapshot_hash": frozen.snapshot_hash,
            "htf_hash": frozen.htf_hash,
            "catalog_id": frozen.catalog_id,
            "provider_fingerprint": frozen.provider_fingerprint,
            "settings_fingerprint": frozen.settings_fingerprint,
            "analysis_fingerprint": frozen.analysis_fingerprint,
            "order_scheme": _VALID_SEQUENCES[sequence],
            "arm_sequence": list(sequence),
            "source_run_id": source_run_id,
            "source_record_path": source_record_path,
        }
        start = self._experiment_store.start_experiment(manifest)
        if not bool(getattr(start, "ok", False)):
            raise RuntimeError(
                f"Could not persist experiment manifest: {getattr(start, 'error', '')}"
            )

        specs: dict[str, ArmSpec] = {}
        assignment_error: str | None = None
        for position, arm_id in enumerate(sequence, start=1):
            group: ArmGroup = "A" if arm_id.startswith("A") else "B"
            spec = ArmSpec(
                arm_id=arm_id,
                group=group,
                replicate=int(arm_id[1]),
                mode="off" if group == "A" else "soft",
                sequence_position=position,
            )
            specs[arm_id] = spec
            planned = self._experiment_store.record_arm(
                experiment_id,
                {
                    "arm_id": arm_id,
                    "group_name": group,
                    "replicate": spec.replicate,
                    "sequence_position": position,
                    "assigned_mode": spec.mode,
                    "received_path": None,
                    "run_id": None,
                    "record_path": None,
                    "run_status": "assigned",
                    "first_pass_clean": False,
                    "treatment_received": False,
                    "stage1_request_hash": None,
                    "client_instance_token": None,
                    "persisted": False,
                    "error": None,
                },
            )
            if not bool(getattr(planned, "ok", False)):
                assignment_error = (
                    f"arm_assignment_write_failed:{arm_id}:" f"{getattr(planned, 'error', '')}"
                )
                break

        if assignment_error is not None:
            protocol = {"valid": False, "errors": [assignment_error]}
            finished = self._experiment_store.finish_experiment(
                experiment_id,
                status="incomplete",
                protocol_report=protocol,
                external_comparison={},
            )
            if not bool(getattr(finished, "ok", False)):
                raise RuntimeError(
                    "Could not finalize incomplete experiment manifest: "
                    f"{getattr(finished, 'error', '')}"
                )
            return AabbRunResult(
                experiment_id=experiment_id,
                status="incomplete",
                records=(),
                protocol_report=protocol,
                external_comparison={},
            )

        records: list[AnalysisRecord] = []
        client_identities: list[object] = []
        runner_errors: list[str] = []
        for position, arm_id in enumerate(sequence, start=1):
            if cancel_token.is_set():
                runner_errors.append("cancelled_before_all_arms_completed")
                break
            spec = specs[arm_id]
            group = spec.group
            try:
                execution = self._execute_arm(spec, frozen, cancel_token)
            except Exception as exc:
                error = f"arm_execution_failed:{arm_id}:{type(exc).__name__}"
                runner_errors.append(error)
                self._experiment_store.record_arm(
                    experiment_id,
                    {
                        "arm_id": arm_id,
                        "group_name": group,
                        "replicate": spec.replicate,
                        "sequence_position": position,
                        "assigned_mode": spec.mode,
                        "received_path": None,
                        "run_id": None,
                        "record_path": None,
                        "run_status": "failed",
                        "first_pass_clean": False,
                        "treatment_received": False,
                        "stage1_request_hash": None,
                        "client_instance_token": None,
                        "persisted": False,
                        "error": error,
                    },
                )
                break
            client_identities.append(execution.client_identity)
            stage1_hash = _first_stage1_request_hash(execution.record)
            context = frozen.arm_context(
                experiment_id=experiment_id,
                arm_id=arm_id,
                sequence=sequence,
                sequence_position=position,
                client_instance_token=execution.client_instance_token,
                stage1_request_hash=stage1_hash,
            )
            context["first_pass_clean"] = first_pass_clean(execution.record)
            record = execution.record.model_copy(
                update={
                    "schema_version": max(4, int(execution.record.schema_version)),
                    "snapshot_context": copy.deepcopy(frozen.snapshot_context),
                    "htf_context": copy.deepcopy(frozen.htf_snapshot_context),
                    "htf_kline_data": [copy.deepcopy(item) for item in frozen.htf_kline_data],
                    "experiment_context": context,
                }
            )
            try:
                persisted = self._pending_writer.save_full(record)
            except Exception as exc:
                error = f"record_persistence_failed:{arm_id}:{type(exc).__name__}"
                runner_errors.append(error)
                self._experiment_store.record_arm(
                    experiment_id,
                    {
                        "arm_id": arm_id,
                        "group_name": group,
                        "replicate": spec.replicate,
                        "sequence_position": position,
                        "assigned_mode": spec.mode,
                        "received_path": (record.run_outcome or {}).get("official_path"),
                        "run_id": record.meta.run_id,
                        "record_path": None,
                        "run_status": (record.run_outcome or {}).get("status"),
                        "first_pass_clean": context["first_pass_clean"],
                        "treatment_received": bool(
                            group == "B"
                            and (record.run_outcome or {}).get("official_path") == "joint"
                        ),
                        "stage1_request_hash": stage1_hash,
                        "client_instance_token": execution.client_instance_token,
                        "persisted": False,
                        "error": error,
                    },
                )
                break
            artifact_ok = bool(getattr(persisted, "ok", False))
            audit_ok = bool(getattr(persisted, "audit_ok", True))
            persisted_ok = artifact_ok and audit_ok
            persistence_error = None
            if not artifact_ok:
                persistence_error = "record_persistence_failed"
            elif not audit_ok:
                persistence_error = "audit_index_failed"
            arm_audit = {
                "arm_id": arm_id,
                "group_name": group,
                "replicate": spec.replicate,
                "sequence_position": position,
                "assigned_mode": spec.mode,
                "received_path": (record.run_outcome or {}).get("official_path"),
                "run_id": record.meta.run_id,
                "record_path": str(getattr(persisted, "path", "")),
                "run_status": (record.run_outcome or {}).get("status"),
                "first_pass_clean": context["first_pass_clean"],
                "treatment_received": bool(
                    group == "B" and (record.run_outcome or {}).get("official_path") == "joint"
                ),
                "stage1_request_hash": stage1_hash,
                "client_instance_token": execution.client_instance_token,
                "persisted": persisted_ok,
                "error": persistence_error,
            }
            arm_write = self._experiment_store.record_arm(experiment_id, arm_audit)
            records.append(record)
            if not bool(getattr(arm_write, "ok", False)):
                runner_errors.append(f"arm_ledger_write_failed:{arm_id}")
                break
            if not persisted_ok:
                runner_errors.append(f"{persistence_error}:{arm_id}")
                break

        protocol = validate_aabb_records(records)
        if len({id(value) for value in client_identities}) != len(client_identities):
            protocol = {
                "valid": False,
                "errors": sorted({*protocol["errors"], "client_object_reuse"}),
            }
        if runner_errors:
            protocol = {
                "valid": False,
                "errors": sorted({*protocol["errors"], *runner_errors}),
            }
        comparison = external_comparison(records) if len(records) == 4 else {}
        if cancel_token.is_set():
            status = "cancelled"
        elif len(records) < 4:
            status = "incomplete"
        elif protocol["valid"]:
            status = "completed"
        else:
            status = "protocol_invalid"
        finished = self._experiment_store.finish_experiment(
            experiment_id,
            status=status,
            protocol_report=protocol,
            external_comparison=comparison,
        )
        if not bool(getattr(finished, "ok", False)):
            raise RuntimeError(
                f"Could not finalize experiment manifest: {getattr(finished, 'error', '')}"
            )
        return AabbRunResult(
            experiment_id=experiment_id,
            status=status,
            records=tuple(records),
            protocol_report=protocol,
            external_comparison=comparison,
        )
