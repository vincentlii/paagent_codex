"""Independent A/A/B/B protocol for Direct versus Codex analysis backends."""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pa_agent.ai.higher_timeframe import serialize_higher_timeframe_context
from pa_agent.ai.pa_provider_contracts import schema_for_provider_draft
from pa_agent.ai.role_contracts import JOINT_DECISION_SCHEMA, VPA_EVIDENCE_SCHEMA
from pa_agent.ai.vpa_measurements import validate_frozen_vpa_catalog
from pa_agent.data.instrument import InstrumentSpec
from pa_agent.data.snapshot_bundle import compact_snapshot_context, serialize_frame_bars
from pa_agent.data.snapshot_identity import SNAPSHOT_HASH_ALGORITHM, canonical_market_snapshot_hash
from pa_agent.experiments.pa_vpa_aabb import classify_first_pass, decision_signature
from pa_agent.records.schema import AnalysisRecord

BACKEND_AABB_PROTOCOL_VERSION = "analysis_backend_aabb_v2"
BackendGroup = Literal["A", "B"]
BackendMode = Literal["deepseek_direct", "codex_agent"]
_EXPECTED_ARMS = frozenset({"A1", "A2", "B1", "B2"})
_SEQUENCES = {
    ("A1", "B1", "B2", "A2"): "ABBA",
    ("B1", "A1", "A2", "B2"): "BAAB",
}
_SECRET_KEYS = frozenset(
    {"api_key", "api_key_encrypted", "access_token", "refresh_token", "password", "secret"}
)


def _stable_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        default=str,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def backend_configuration_fingerprint(value: Mapping[str, Any]) -> str:
    """Hash the effective, secret-free identity used to construct one backend."""
    return _stable_hash(_public_config(value))


def backend_contract_fingerprint() -> str:
    """Fingerprint all structured role boundaries compared by protocol V2."""
    return _stable_hash(
        {
            "stage1": schema_for_provider_draft("stage1"),
            "stage2": schema_for_provider_draft("stage2"),
            "vpa_evidence": VPA_EVIDENCE_SCHEMA,
            "joint_arbiter": JOINT_DECISION_SCHEMA,
            "vpa_authority": "shadow_only_pa_official",
        }
    )


def _public_config(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _public_config(item)
            for key, item in value.items()
            if not _is_secret_key(str(key))
        }
    if isinstance(value, (list, tuple)):
        return [_public_config(item) for item in value]
    return value


def _is_secret_key(key: str) -> bool:
    normalized = key.strip().lower()
    return normalized in _SECRET_KEYS or normalized.endswith(
        ("_api_key", "_password", "_secret", "_token")
    )


def _require_digest(name: str, value: str) -> str:
    digest = str(value or "").strip().lower()
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise ValueError(f"{name} must be a SHA-256 hex digest")
    return digest


def balanced_backend_sequence(frozen_input_hash: str) -> tuple[str, str, str, str]:
    digest = _require_digest("frozen_input_hash", frozen_input_hash)
    return (
        ("A1", "B1", "B2", "A2")
        if int(digest[-1], 16) % 2 == 0
        else ("B1", "A1", "A2", "B2")
    )


@dataclass(frozen=True)
class BackendArmSpec:
    arm_id: str
    group: BackendGroup
    replicate: int
    backend: BackendMode
    sequence_position: int


@dataclass(frozen=True)
class FrozenBackendExperimentInput:
    protocol_version: str
    frame: Any
    htf_context: dict[str, Any] | None
    vpa_catalog: dict[str, Any]
    frozen_input_hash: str
    market_snapshot_hash: str
    market_snapshot_hash_algorithm: str
    cohort_id: str
    snapshot_hash: str
    htf_hash: str
    instrument_spec_hash: str
    direct_backend_fingerprint: str
    codex_backend_fingerprint: str
    backend_pair_fingerprint: str
    shared_settings_fingerprint: str
    contract_fingerprint: str
    analysis_fingerprint: str
    catalog_id: str
    snapshot_context: dict[str, Any]
    htf_snapshot_context: dict[str, Any] | None
    htf_kline_data: tuple[dict[str, Any], ...]

    def arm_context(
        self,
        *,
        experiment_id: str,
        spec: BackendArmSpec,
        sequence: tuple[str, str, str, str],
        client_instance_token: str,
    ) -> dict[str, Any]:
        return {
            "protocol_version": self.protocol_version,
            "experiment_id": experiment_id,
            "frozen_input_hash": self.frozen_input_hash,
            "market_snapshot_hash": self.market_snapshot_hash,
            "market_snapshot_hash_algorithm": self.market_snapshot_hash_algorithm,
            "cohort_id": self.cohort_id,
            "snapshot_hash": self.snapshot_hash,
            "htf_hash": self.htf_hash,
            "instrument_spec_hash": self.instrument_spec_hash,
            "direct_backend_fingerprint": self.direct_backend_fingerprint,
            "codex_backend_fingerprint": self.codex_backend_fingerprint,
            "backend_pair_fingerprint": self.backend_pair_fingerprint,
            "shared_settings_fingerprint": self.shared_settings_fingerprint,
            "contract_fingerprint": self.contract_fingerprint,
            "analysis_fingerprint": self.analysis_fingerprint,
            "catalog_id": self.catalog_id,
            "vpa_mode": "shadow",
            "higher_timeframe_required": True,
            "order_scheme": _SEQUENCES[sequence],
            "arm_sequence": list(sequence),
            "arm_id": spec.arm_id,
            "group": spec.group,
            "replicate": spec.replicate,
            "sequence_position": spec.sequence_position,
            "assigned_backend": spec.backend,
            "client_instance_token": client_instance_token,
            "provider_fallback_allowed": False,
            "backend_fallback_allowed": False,
            "incremental_context_enabled": False,
            "causal_estimand": "complete_backend_system_value",
        }


def freeze_backend_experiment_input(
    frame: Any,
    htf_context: dict[str, Any] | None,
    *,
    direct_backend: Mapping[str, Any],
    codex_backend: Mapping[str, Any],
    shared_settings: Mapping[str, Any],
    contract_fingerprint: str,
    analysis_fingerprint: str,
    vpa_catalog: Mapping[str, Any],
) -> FrozenBackendExperimentInput:
    """Freeze shared facts and both backend identities before assigning any arm."""
    contract_digest = _require_digest("contract_fingerprint", contract_fingerprint)
    analysis_digest = _require_digest("analysis_fingerprint", analysis_fingerprint)
    direct_digest = backend_configuration_fingerprint(direct_backend)
    codex_digest = backend_configuration_fingerprint(codex_backend)
    pair_digest = _stable_hash({"direct": direct_digest, "codex": codex_digest})
    settings_digest = _stable_hash(_public_config(shared_settings))
    if str(shared_settings.get("vpa_mode") or "") != "shadow":
        raise ValueError("backend A/A/B/B requires standard VPA shadow mode")
    if shared_settings.get("higher_timeframe_enabled") is not True:
        raise ValueError("backend A/A/B/B requires higher-timeframe analysis")
    frozen_catalog = validate_frozen_vpa_catalog(frame, dict(vpa_catalog))
    htf_snapshot = serialize_higher_timeframe_context(htf_context)
    higher_frame = None
    if isinstance(htf_context, dict) and htf_context.get("status") == "available":
        higher_frame = htf_context.get("frame")
        if higher_frame is None or not getattr(higher_frame, "bars", None):
            raise ValueError("available HTF context requires a non-empty frame")
    else:
        raise ValueError("backend A/A/B/B requires an available frozen HTF frame")
    htf_bars = serialize_frame_bars(higher_frame)
    htf_hash = _stable_hash({"context": htf_snapshot, "kline_data": htf_bars})
    snapshot_context = compact_snapshot_context(frame)
    instrument = InstrumentSpec.from_value(snapshot_context.get("instrument_spec"))
    instrument_hash = _stable_hash(instrument.to_dict() if instrument is not None else None)
    market_hash = canonical_market_snapshot_hash(
        symbol=frame.symbol,
        timeframe=frame.timeframe,
        kline_data=serialize_frame_bars(frame),
        snapshot_context=snapshot_context,
        htf_context=htf_snapshot,
        htf_kline_data=htf_bars,
        algorithm=SNAPSHOT_HASH_ALGORITHM,
    )
    cohort_id = _stable_hash(
        {
            "protocol_version": BACKEND_AABB_PROTOCOL_VERSION,
            "backend_pair_fingerprint": pair_digest,
            "shared_settings_fingerprint": settings_digest,
            "contract_fingerprint": contract_digest,
            "analysis_fingerprint": analysis_digest,
        }
    )
    frozen_hash = _stable_hash(
        {
            "market_snapshot_hash": market_hash,
            "htf_hash": htf_hash,
            "instrument_spec_hash": instrument_hash,
            "cohort_id": cohort_id,
            "catalog_id": frozen_catalog["catalog_id"],
        }
    )
    return FrozenBackendExperimentInput(
        protocol_version=BACKEND_AABB_PROTOCOL_VERSION,
        frame=copy.deepcopy(frame),
        htf_context=copy.deepcopy(htf_context),
        vpa_catalog=copy.deepcopy(frozen_catalog),
        frozen_input_hash=frozen_hash,
        market_snapshot_hash=market_hash,
        market_snapshot_hash_algorithm=SNAPSHOT_HASH_ALGORITHM,
        cohort_id=cohort_id,
        snapshot_hash=market_hash,
        htf_hash=htf_hash,
        instrument_spec_hash=instrument_hash,
        direct_backend_fingerprint=direct_digest,
        codex_backend_fingerprint=codex_digest,
        backend_pair_fingerprint=pair_digest,
        shared_settings_fingerprint=settings_digest,
        contract_fingerprint=contract_digest,
        analysis_fingerprint=analysis_digest,
        catalog_id=str(frozen_catalog["catalog_id"]),
        snapshot_context=copy.deepcopy(snapshot_context),
        htf_snapshot_context=copy.deepcopy(htf_snapshot),
        htf_kline_data=tuple(copy.deepcopy(htf_bars)),
    )


@dataclass(frozen=True)
class BackendArmExecution:
    record: AnalysisRecord
    runtime_identity: object
    client_instance_token: str
    runtime_fingerprint: str


@dataclass(frozen=True)
class BackendAabbRunResult:
    experiment_id: str
    status: str
    records: tuple[AnalysisRecord, ...]
    protocol_report: dict[str, Any]
    external_comparison: dict[str, Any]


def _arm_metrics(record: AnalysisRecord) -> dict[str, Any]:
    calls = [item for item in record.call_attempts if isinstance(item, dict)]
    first_pass = classify_first_pass(record)

    def optional_sum(key: str) -> int | None:
        values = [item.get(key) for item in calls if item.get(key) is not None]
        return sum(int(value or 0) for value in values) if values else None

    return {
        "first_pass_clean": bool(first_pass["clean"]),
        "first_pass_status": first_pass["status"],
        "total_tokens": sum(int(item.get("total_tokens") or 0) for item in calls),
        "prompt_tokens": sum(int(item.get("prompt_tokens") or 0) for item in calls),
        "completion_tokens": sum(int(item.get("completion_tokens") or 0) for item in calls),
        "latency_ms": round(sum(float(item.get("latency_ms") or 0.0) for item in calls), 3),
        "call_count": len(calls),
        "retry_count": sum(max(0, int(item.get("attempt") or 1) - 1) for item in calls),
        "evidence_calls": sum(int(item.get("evidence_call_count") or 0) for item in calls),
        "evidence_denials": sum(int(item.get("evidence_denied_count") or 0) for item in calls),
        "evidence_latency_ms": round(
            sum(float(item.get("evidence_latency_ms") or 0.0) for item in calls), 3
        ),
        "initial_context_tokens": optional_sum("initial_context_tokens"),
        "retrieval_context_tokens": optional_sum("retrieval_context_tokens"),
    }


def backend_external_comparison(records: list[AnalysisRecord]) -> dict[str, Any]:
    by_arm = {
        str((record.experiment_context or {}).get("arm_id") or ""): record
        for record in records
    }
    signatures = {arm: decision_signature(record) for arm, record in by_arm.items()}
    metrics = {arm: _arm_metrics(record) for arm, record in by_arm.items()}
    vpa_signatures = {arm: _vpa_signature(record) for arm, record in by_arm.items()}
    return {
        "signatures": signatures,
        "vpa_signatures": vpa_signatures,
        "arm_metrics": metrics,
        "missing_arms": sorted(_EXPECTED_ARMS - set(by_arm)),
        "a_disagreement": signatures.get("A1") != signatures.get("A2"),
        "b_disagreement": signatures.get("B1") != signatures.get("B2"),
        "a_vpa_disagreement": vpa_signatures.get("A1") != vpa_signatures.get("A2"),
        "b_vpa_disagreement": vpa_signatures.get("B1") != vpa_signatures.get("B2"),
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


def _vpa_signature(record: AnalysisRecord) -> dict[str, Any]:
    report = record.vpa_evidence if isinstance(record.vpa_evidence, dict) else {}
    evidence_ids: set[str] = set()
    for collection_name in ("observations", "hypothesis_assessments"):
        for item in report.get(collection_name) or []:
            if isinstance(item, dict):
                evidence_ids.update(str(value) for value in item.get("evidence_ids") or [])
    return {
        "available": bool(report),
        "catalog_id": report.get("catalog_id"),
        "data_status": report.get("data_status"),
        "background_relation": report.get("background_relation"),
        "entry_timing_relation": report.get("entry_timing_relation"),
        "evidence_ids": sorted(evidence_ids),
    }


def validate_backend_records(records: list[AnalysisRecord]) -> dict[str, Any]:
    errors: set[str] = set()
    if len(records) != 4:
        return {"valid": False, "errors": ["incomplete_four_arm_set"]}
    contexts = [record.experiment_context for record in records]
    if any(not isinstance(context, dict) for context in contexts):
        return {"valid": False, "errors": ["missing_experiment_context"]}
    typed_contexts = [context for context in contexts if isinstance(context, dict)]
    by_arm = {str(context.get("arm_id") or ""): record for record, context in zip(records, typed_contexts, strict=True)}
    if set(by_arm) != _EXPECTED_ARMS or len(by_arm) != 4:
        errors.add("invalid_arm_set")
    invariant_keys = (
        "protocol_version", "experiment_id", "frozen_input_hash", "market_snapshot_hash",
        "market_snapshot_hash_algorithm", "cohort_id", "snapshot_hash", "htf_hash",
        "instrument_spec_hash", "direct_backend_fingerprint", "codex_backend_fingerprint",
        "backend_pair_fingerprint", "shared_settings_fingerprint", "contract_fingerprint",
        "analysis_fingerprint", "order_scheme", "causal_estimand",
        "catalog_id", "vpa_mode", "higher_timeframe_required",
    )
    for key in invariant_keys:
        if len({str(context.get(key)) for context in typed_contexts}) != 1:
            errors.add(f"{key}_drift")
    if {context.get("protocol_version") for context in typed_contexts} != {BACKEND_AABB_PROTOCOL_VERSION}:
        errors.add("unsupported_protocol_version")
    digest_keys = (
        "frozen_input_hash", "market_snapshot_hash", "cohort_id", "snapshot_hash",
        "htf_hash", "instrument_spec_hash", "direct_backend_fingerprint",
        "codex_backend_fingerprint", "backend_pair_fingerprint",
        "shared_settings_fingerprint", "contract_fingerprint", "analysis_fingerprint",
    )
    for key in digest_keys:
        try:
            _require_digest(key, str(typed_contexts[0].get(key) or ""))
        except ValueError:
            errors.add(f"invalid_{key}")
    sequences = {tuple(context.get("arm_sequence") or []) for context in typed_contexts}
    sequence = next(iter(sequences), ())
    if len(sequences) != 1 or sequence not in _SEQUENCES:
        errors.add("invalid_arm_sequence")
    else:
        positioned = {
            int(context.get("sequence_position") or 0): str(context.get("arm_id") or "")
            for context in typed_contexts
        }
        if tuple(positioned.get(index, "") for index in range(1, 5)) != sequence:
            errors.add("arm_sequence_position_mismatch")
    tokens = [str(context.get("client_instance_token") or "") for context in typed_contexts]
    if any(not token for token in tokens) or len(set(tokens)) != 4:
        errors.add("client_instance_reuse")
    for record, context in zip(records, typed_contexts, strict=True):
        arm_id = str(context.get("arm_id") or "")
        group = "A" if arm_id.startswith("A") else "B"
        expected_backend = "deepseek_direct" if group == "A" else "codex_agent"
        observed_backend = "direct" if group == "A" else "codex_agent"
        expected_fingerprint = context.get(
            "direct_backend_fingerprint" if group == "A" else "codex_backend_fingerprint"
        )
        if arm_id in _EXPECTED_ARMS and int(context.get("replicate") or 0) != int(arm_id[1]):
            errors.add("arm_replicate_mismatch")
        if context.get("group") != group or context.get("assigned_backend") != expected_backend:
            errors.add("arm_backend_assignment_mismatch")
        if context.get("observed_backend_fingerprint") != expected_fingerprint:
            errors.add("runtime_backend_fingerprint_mismatch")
        if context.get("provider_fallback_allowed") is not False or context.get("backend_fallback_allowed") is not False:
            errors.add("fallback_allowed")
        if context.get("vpa_mode") != "shadow":
            errors.add("vpa_mode_drift")
        if context.get("higher_timeframe_required") is not True:
            errors.add("higher_timeframe_contract_drift")
        outcome = record.run_outcome if isinstance(record.run_outcome, dict) else {}
        if outcome.get("requested_vpa_mode") != "shadow":
            errors.add("vpa_not_requested")
        if outcome.get("official_path") != "pa_only":
            errors.add("vpa_changed_official_authority")
        if outcome.get("status") == "completed" and not isinstance(record.vpa_evidence, dict):
            errors.add("completed_run_missing_vpa_evidence")
        calls = [item for item in record.call_attempts if isinstance(item, dict)]
        if not calls or any(str(item.get("analysis_backend") or "") != observed_backend for item in calls):
            errors.add("received_backend_mismatch")
        if any(bool(item.get("provider_fallback_used")) for item in calls):
            errors.add("provider_fallback_used")
        runtime = record.role_runtime if isinstance(record.role_runtime, dict) else {}
        if runtime.get("backend_fallback"):
            errors.add("backend_fallback_used")
        record_market_hash = canonical_market_snapshot_hash(
            symbol=record.meta.symbol,
            timeframe=record.meta.timeframe,
            kline_data=record.kline_data,
            snapshot_context=record.snapshot_context,
            htf_context=record.htf_context,
            htf_kline_data=record.htf_kline_data,
            algorithm=str(context.get("market_snapshot_hash_algorithm") or ""),
        )
        if record_market_hash != context.get("market_snapshot_hash"):
            errors.add("persisted_market_snapshot_hash_mismatch")
        reconstructed_frozen_hash = _stable_hash(
            {
                "market_snapshot_hash": record_market_hash,
                "htf_hash": context.get("htf_hash"),
                "instrument_spec_hash": context.get("instrument_spec_hash"),
                "cohort_id": context.get("cohort_id"),
                "catalog_id": context.get("catalog_id"),
            }
        )
        if reconstructed_frozen_hash != context.get("frozen_input_hash"):
            errors.add("persisted_frozen_input_hash_mismatch")
        catalog = record.vpa_catalog if isinstance(record.vpa_catalog, dict) else {}
        if catalog.get("catalog_id") != context.get("catalog_id"):
            errors.add("persisted_vpa_catalog_mismatch")
    return {"valid": not errors, "errors": sorted(errors)}


class BackendAabbRunner:
    """Run four pre-registered, independent backend analyses without fallback."""

    def __init__(
        self,
        *,
        execute_arm: Callable[[BackendArmSpec, FrozenBackendExperimentInput, Any], BackendArmExecution],
        pending_writer: Any,
        experiment_store: Any,
    ) -> None:
        self._execute_arm = execute_arm
        self._pending_writer = pending_writer
        self._experiment_store = experiment_store

    def run(
        self,
        frozen: FrozenBackendExperimentInput,
        cancel_token: Any,
        *,
        source_run_id: str | None = None,
        source_record_path: str | None = None,
    ) -> BackendAabbRunResult:
        sequence = balanced_backend_sequence(frozen.frozen_input_hash)
        experiment_id = f"backend-aabb-{frozen.frozen_input_hash[:12]}-{uuid4().hex[:8]}"
        manifest = {
            "experiment_id": experiment_id,
            "protocol_version": BACKEND_AABB_PROTOCOL_VERSION,
            "status": "running",
            "symbol": frozen.frame.symbol,
            "timeframe": frozen.frame.timeframe,
            "frozen_input_hash": frozen.frozen_input_hash,
            "market_snapshot_hash": frozen.market_snapshot_hash,
            "cohort_id": frozen.cohort_id,
            "snapshot_hash": frozen.snapshot_hash,
            "htf_hash": frozen.htf_hash,
            "catalog_id": frozen.catalog_id,
            "provider_fingerprint": frozen.backend_pair_fingerprint,
            "settings_fingerprint": frozen.shared_settings_fingerprint,
            "analysis_fingerprint": frozen.analysis_fingerprint,
            "order_scheme": _SEQUENCES[sequence],
            "arm_sequence": list(sequence),
            "source_run_id": source_run_id,
            "source_record_path": source_record_path,
        }
        started = self._experiment_store.start_experiment(manifest)
        if not bool(getattr(started, "ok", False)):
            raise RuntimeError(f"Could not persist backend experiment: {getattr(started, 'error', '')}")
        specs: dict[str, BackendArmSpec] = {}
        registration_error: str | None = None
        for position, arm_id in enumerate(sequence, start=1):
            group: BackendGroup = "A" if arm_id.startswith("A") else "B"
            spec = BackendArmSpec(
                arm_id=arm_id,
                group=group,
                replicate=int(arm_id[1]),
                backend="deepseek_direct" if group == "A" else "codex_agent",
                sequence_position=position,
            )
            specs[arm_id] = spec
            write = self._experiment_store.record_arm(experiment_id, self._arm_row(spec))
            if not bool(getattr(write, "ok", False)):
                registration_error = f"arm_registration_failed:{arm_id}"
                break
        if registration_error is not None:
            protocol = {"valid": False, "errors": [registration_error]}
            finished = self._experiment_store.finish_experiment(
                experiment_id,
                status="incomplete",
                protocol_report=protocol,
                external_comparison={"missing_arms": sorted(_EXPECTED_ARMS)},
            )
            if not bool(getattr(finished, "ok", False)):
                raise RuntimeError("Could not finalize failed backend arm registration")
            return BackendAabbRunResult(
                experiment_id=experiment_id,
                status="incomplete",
                records=(),
                protocol_report=protocol,
                external_comparison={"missing_arms": sorted(_EXPECTED_ARMS)},
            )

        records: list[AnalysisRecord] = []
        runtime_identities: list[object] = []
        runner_errors: list[str] = []
        for arm_id in sequence:
            spec = specs[arm_id]
            if cancel_token.is_set():
                runner_errors.append("cancelled_before_all_arms_completed")
                break
            try:
                execution = self._execute_arm(spec, frozen, cancel_token)
                runtime_identities.append(execution.runtime_identity)
                context = frozen.arm_context(
                    experiment_id=experiment_id,
                    spec=spec,
                    sequence=sequence,
                    client_instance_token=execution.client_instance_token,
                )
                runtime_fingerprint = _require_digest(
                    "runtime_fingerprint", execution.runtime_fingerprint
                )
                context["observed_backend_fingerprint"] = runtime_fingerprint
                record = execution.record.model_copy(
                    update={
                        "snapshot_context": copy.deepcopy(frozen.snapshot_context),
                        "htf_context": copy.deepcopy(frozen.htf_snapshot_context),
                        "htf_kline_data": [copy.deepcopy(item) for item in frozen.htf_kline_data],
                        "experiment_context": context,
                    }
                )
                persisted = self._pending_writer.save_full(record)
                persisted_ok = bool(getattr(persisted, "ok", False)) and bool(
                    getattr(persisted, "audit_ok", True)
                )
                error = None if persisted_ok else "record_persistence_failed"
                if error:
                    runner_errors.append(f"{error}:{arm_id}")
                arm_write = self._experiment_store.record_arm(
                    experiment_id,
                    self._arm_row(
                        spec,
                        record=record,
                        token=execution.client_instance_token,
                        path=str(getattr(persisted, "path", "")) or None,
                        persisted=persisted_ok,
                        error=error,
                    ),
                )
                if not bool(getattr(arm_write, "ok", False)):
                    runner_errors.append(f"arm_ledger_write_failed:{arm_id}")
                if persisted_ok:
                    records.append(record)
            except Exception as exc:
                error = f"arm_execution_failed:{arm_id}:{type(exc).__name__}"
                runner_errors.append(error)
                arm_write = self._experiment_store.record_arm(
                    experiment_id,
                    self._arm_row(spec, status="failed", error=error),
                )
                if not bool(getattr(arm_write, "ok", False)):
                    runner_errors.append(f"arm_ledger_write_failed:{arm_id}")
                continue

        protocol = validate_backend_records(records)
        if any(
            left is right
            for index, left in enumerate(runtime_identities)
            for right in runtime_identities[index + 1 :]
        ):
            protocol = {
                "valid": False,
                "errors": sorted(set(protocol["errors"]) | {"runtime_instance_reuse"}),
            }
        if runner_errors:
            protocol = {
                "valid": False,
                "errors": sorted(set(protocol["errors"]) | set(runner_errors)),
            }
        comparison = backend_external_comparison(records)
        status = "completed" if protocol["valid"] else (
            "incomplete" if len(records) != 4 else "protocol_invalid"
        )
        finished = self._experiment_store.finish_experiment(
            experiment_id,
            status=status,
            protocol_report=protocol,
            external_comparison=comparison,
        )
        if not bool(getattr(finished, "ok", False)):
            raise RuntimeError("Could not finalize backend experiment")
        return BackendAabbRunResult(
            experiment_id=experiment_id,
            status=status,
            records=tuple(records),
            protocol_report=protocol,
            external_comparison=comparison,
        )

    @staticmethod
    def _arm_row(
        spec: BackendArmSpec,
        *,
        record: AnalysisRecord | None = None,
        token: str | None = None,
        path: str | None = None,
        persisted: bool = False,
        status: str | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        metrics = classify_first_pass(record) if record is not None else {"clean": False}
        return {
            "arm_id": spec.arm_id,
            "group_name": spec.group,
            "replicate": spec.replicate,
            "sequence_position": spec.sequence_position,
            "assigned_mode": spec.backend,
            "received_path": (
                str((record.call_attempts[0] or {}).get("analysis_backend") or "")
                if record is not None and record.call_attempts
                else None
            ),
            "run_id": record.meta.run_id if record is not None else None,
            "record_path": path,
            "run_status": status or (
                str((record.run_outcome or {}).get("status") or "unknown")
                if record is not None
                else "assigned"
            ),
            "first_pass_clean": bool(metrics.get("clean")),
            "treatment_received": record is not None,
            "stage1_request_hash": None,
            "client_instance_token": token,
            "persisted": persisted,
            "error": error,
        }


def summarize_backend_experiments(
    store: Any,
    *,
    cohort_id: str | None = None,
    min_unique_snapshots: int = 20,
) -> dict[str, Any]:
    """Aggregate one deduplicated Backend cohort without claiming trading uplift."""
    rows = store.list_experiments(
        cohort_id=cohort_id,
        protocol_version=BACKEND_AABB_PROTOCOL_VERSION,
        limit=2000,
    )
    manifests = [store.get_experiment(str(row["experiment_id"])) for row in rows]
    manifests = [item for item in manifests if isinstance(item, dict)]
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for manifest in manifests:
        key = (str(manifest.get("cohort_id") or ""), str(manifest.get("market_snapshot_hash") or ""))
        grouped.setdefault(key, []).append(manifest)
    repeat_conflicts = {key for key, attempts in grouped.items() if len(attempts) != 1}
    units = [attempts[0] for attempts in grouped.values() if len(attempts) == 1]
    cohorts = sorted({cohort for cohort, _market_hash in grouped})
    complete = [
        item for item in units
        if item.get("status") == "completed"
        and bool(item.get("protocol_valid"))
        and len(item.get("arms") or []) == 4
        and all(bool(arm.get("persisted")) for arm in item.get("arms") or [])
    ]
    group_metrics: dict[str, list[dict[str, Any]]] = {"A": [], "B": []}
    a_disagreements = 0
    b_disagreements = 0
    a_vpa_disagreements = 0
    b_vpa_disagreements = 0
    for manifest in complete:
        comparison = manifest.get("external_comparison") or {}
        a_disagreements += int(bool(comparison.get("a_disagreement")))
        b_disagreements += int(bool(comparison.get("b_disagreement")))
        a_vpa_disagreements += int(bool(comparison.get("a_vpa_disagreement")))
        b_vpa_disagreements += int(bool(comparison.get("b_vpa_disagreement")))
        for arm_id, metrics in (comparison.get("arm_metrics") or {}).items():
            group_metrics["A" if str(arm_id).startswith("A") else "B"].append(metrics)

    def group_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
        count = len(items)
        def optional_mean(key: str) -> float | None:
            values = [float(item[key]) for item in items if item.get(key) is not None]
            return round(sum(values) / len(values), 3) if values else None

        return {
            "completed_arms": count,
            "first_pass_clean_rate": (
                round(sum(bool(item.get("first_pass_clean")) for item in items) / count, 4)
                if count else None
            ),
            "mean_total_tokens": (
                round(sum(float(item.get("total_tokens") or 0) for item in items) / count, 3)
                if count else None
            ),
            "mean_latency_ms": (
                round(sum(float(item.get("latency_ms") or 0) for item in items) / count, 3)
                if count else None
            ),
            "mean_initial_context_tokens": optional_mean("initial_context_tokens"),
            "mean_retrieval_context_tokens": optional_mean("retrieval_context_tokens"),
            "mean_evidence_calls": optional_mean("evidence_calls"),
            "mean_evidence_denials": optional_mean("evidence_denials"),
            "mean_evidence_latency_ms": optional_mean("evidence_latency_ms"),
        }

    unique_snapshots = len({market_hash for _cohort, market_hash in grouped})
    reviewable = (
        len(cohorts) == 1
        and len(complete) >= max(1, int(min_unique_snapshots))
        and len(complete) == len(grouped)
        and not repeat_conflicts
    )
    return {
        "status": "backend_aabb",
        "protocol_version": BACKEND_AABB_PROTOCOL_VERSION,
        "causal_scope": "complete_backend_system_value_not_agentic_runtime_alone",
        "unique_snapshots": unique_snapshots,
        "complete_four_arm_snapshots": len(complete),
        "incomplete_or_invalid_snapshots": len(grouped) - len(complete),
        "repeat_conflict_snapshots": len(repeat_conflicts),
        "attempted_experiments": len(manifests),
        "cohort_count": len(cohorts),
        "cohort_ids": cohorts,
        "minimum_repeatability_snapshots": max(1, int(min_unique_snapshots)),
        "engineering_repeatability_reviewable": reviewable,
        "a_disagreement_rate": round(a_disagreements / len(complete), 4) if complete else None,
        "b_disagreement_rate": round(b_disagreements / len(complete), 4) if complete else None,
        "a_vpa_disagreement_rate": (
            round(a_vpa_disagreements / len(complete), 4) if complete else None
        ),
        "b_vpa_disagreement_rate": (
            round(b_vpa_disagreements / len(complete), 4) if complete else None
        ),
        "groups": {group: group_summary(items) for group, items in group_metrics.items()},
        "promotion_ready": False,
        "limitations": [
            "Backend A/A/B/B compares complete systems; model family and agent runtime are confounded.",
            "Engineering repeatability does not establish trading profitability or calibration.",
            "Missing and failed assigned arms remain in coverage and block reviewability.",
        ],
    }
