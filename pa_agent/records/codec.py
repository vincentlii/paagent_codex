"""The only JSON boundary for versioned analysis records."""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pa_agent.records.projection import usage_from_call_attempts
from pa_agent.records.schema import AnalysisRecord


class UnsupportedRecordVersion(ValueError):
    pass


@dataclass(frozen=True)
class MigrationIssue:
    code: str
    severity: str
    path: str
    detail: str


@dataclass(frozen=True)
class LoadedRecord:
    record: AnalysisRecord
    migration_issues: tuple[MigrationIssue, ...]

    @property
    def replayable(self) -> bool:
        return not any(issue.severity == "error" for issue in self.migration_issues)


_SIDECAR_DUPLICATES = {
    "pa_hypotheses": "pa_hypotheses",
    "vpa_report": "vpa_evidence",
    "stage1_context": "vpa_evidence",
    "baseline_stage2": "pa_baseline_stage2",
    "joint_stage2": "joint_stage2",
}


def _choose_legacy_value(
    data: dict[str, Any],
    sidecar: dict[str, Any],
    *,
    sidecar_key: str,
    canonical_key: str,
    issues: list[MigrationIssue],
) -> None:
    legacy = sidecar.get(sidecar_key)
    canonical = data.get(canonical_key)
    if legacy is None:
        return
    if canonical is None:
        data[canonical_key] = copy.deepcopy(legacy)
        return
    if canonical != legacy:
        issues.append(
            MigrationIssue(
                code="migration_conflict",
                severity="error",
                path=canonical_key,
                detail=f"top-level value differs from vpa_sidecar.{sidecar_key}",
            )
        )


def _legacy_role_statuses(sidecar: dict[str, Any], outcome: dict[str, Any]) -> dict[str, str]:
    execution = sidecar.get("role_execution")
    execution = execution if isinstance(execution, dict) else {}
    degradation = str(outcome.get("degradation_stage") or "")
    vpa = "validated" if execution.get("vpa_first_pass") else "not_attempted"
    joint = "validated" if execution.get("joint_first_pass") else "not_attempted"
    if degradation == "vpa_evidence":
        vpa = "failed"
    if degradation == "joint_arbiter":
        joint = "failed"
    if sidecar.get("vpa_report") is not None or sidecar.get("stage1_context") is not None:
        vpa = "validated" if vpa == "not_attempted" else vpa
    if sidecar.get("joint_arbitration") is not None:
        joint = "validated" if joint == "not_attempted" else joint
    return {"vpa": vpa, "joint": joint}


def decode_record(raw: dict[str, Any]) -> LoadedRecord:
    data = copy.deepcopy(raw)
    version = int(data.get("schema_version", 4) or 4)
    if version > 5:
        raise UnsupportedRecordVersion(f"unsupported analysis record schema_version={version}")
    issues: list[MigrationIssue] = []
    if version < 5:
        sidecar = data.get("vpa_sidecar")
        sidecar = sidecar if isinstance(sidecar, dict) else {}
        for sidecar_key, canonical_key in _SIDECAR_DUPLICATES.items():
            _choose_legacy_value(
                data,
                sidecar,
                sidecar_key=sidecar_key,
                canonical_key=canonical_key,
                issues=issues,
            )
        if data.get("vpa_catalog") is None and isinstance(
            sidecar.get("evidence_catalog"), dict
        ):
            data["vpa_catalog"] = copy.deepcopy(sidecar["evidence_catalog"])
        joint = data.get("joint_decision")
        if isinstance(joint, dict) and isinstance(joint.get("stage2_decision"), dict):
            if data.get("joint_stage2") is None:
                data["joint_stage2"] = copy.deepcopy(joint["stage2_decision"])
            joint = copy.deepcopy(joint)
            joint.pop("stage2_decision", None)
            data["joint_decision"] = joint
        elif data.get("joint_decision") is None and isinstance(
            sidecar.get("joint_arbitration"), dict
        ):
            data["joint_decision"] = {
                "joint_arbitration": copy.deepcopy(sidecar["joint_arbitration"]),
                "decision_patch": {},
            }
        outcome = data.get("run_outcome")
        outcome = outcome if isinstance(outcome, dict) else {}
        data["role_statuses"] = _legacy_role_statuses(sidecar, outcome)
        execution = sidecar.get("role_execution")
        data["role_runtime"] = copy.deepcopy(execution) if isinstance(execution, dict) else {}
        data["role_responses"] = copy.deepcopy(sidecar.get("role_responses") or {})
        data["source_schema_version"] = version
        data["schema_version"] = 5
        data.pop("vpa_sidecar", None)
    persisted_usage = data.get("usage_total")
    derived_usage = usage_from_call_attempts(data.get("call_attempts") or [])
    if persisted_usage and persisted_usage != derived_usage:
        issues.append(
            MigrationIssue(
                code="usage_ledger_mismatch",
                severity="warning",
                path="usage_total",
                detail="persisted usage differs from call_attempts; ledger projection used",
            )
        )
    data["usage_total"] = derived_usage
    record = AnalysisRecord.model_validate(data)
    return LoadedRecord(record=record, migration_issues=tuple(issues))


def load_record(path: Path) -> LoadedRecord:
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw.pop("_partial_reason", None)
    return decode_record(raw)


def dump_record(record: AnalysisRecord) -> dict[str, Any]:
    data = record.model_dump(mode="json", exclude_none=True)
    data["schema_version"] = 5
    data.pop("usage_total", None)
    data.pop("vpa_sidecar", None)
    return data
