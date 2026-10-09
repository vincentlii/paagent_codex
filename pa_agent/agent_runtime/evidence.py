"""Budgeted, role-safe access to evidence already frozen into one agent job."""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from dataclasses import asdict, dataclass, replace
from typing import Any, Callable, Mapping

from pa_agent.agent_runtime.frozen_job import AgentJobBudget, FrozenAgentJob
from pa_agent.agent_runtime.execution_contract import (
    ANALYSIS_EXECUTION_POLICY, INLINE_MANDATORY_KNOWLEDGE_POLICY,
    LAYERED_STAGE2_RESEARCH_POLICY,
    skill_key, stage2_host_gated_draft,
)
from pa_agent.agent_runtime.execution_contract import BOUNDED_CONTEXT_POLICY
from pa_agent.ai.token_counter import estimate_tokens

PRICE_FRAME_CHUNK_SIZE = 50

OPTIONAL_INVESTIGATION_OPERATIONS = frozenset({
    "list_fact_ids", "get_measurements", "get_pa_evidence", "get_facts", "get_bars",
    "search_allowed_knowledge", "read_allowed_knowledge", "measure_price_window", "project_price_leg",
})
REPAIR_OPERATIONS = frozenset({
    "get_repair_findings", "get_facts", "get_pa_evidence", "get_htf_evidence",
    "get_vpa_evidence", "read_allowed_knowledge",
})
CHILD_PA_OPERATIONS = frozenset({
    "get_bars", "get_pa_evidence", "get_htf_evidence", "read_allowed_knowledge",
    "measure_price_window", "project_price_leg",
})
CHILD_VPA_OPERATIONS = frozenset({"get_vpa_evidence", "read_allowed_knowledge"})


class EvidenceAccessDenied(RuntimeError):
    """A query requested evidence outside this job's frozen allowlist."""


class EvidenceBudgetExceeded(RuntimeError):
    """The role consumed its evidence call or byte budget."""


@dataclass(frozen=True)
class EvidenceAuditEvent:
    call_index: int
    operation: str
    request: dict[str, Any]
    status: str
    response_bytes: int
    response_tokens: int
    snapshot_hash: str
    latency_ms: float = 0.0
    evidence_ids: tuple[str, ...] = ()
    reason: str | None = None
    measurement: dict[str, Any] | None = None
    completed_at_epoch_ms: int = 0
    actor: str = "parent"
    baseline_assembled: bool = False


class FrozenEvidenceGateway:
    """Serve frozen evidence and bounded arithmetic, never classify signals."""

    def __init__(self, job: FrozenAgentJob, *, child_budget: AgentJobBudget | None = None) -> None:
        self._job = job
        self._budget = child_budget or job.budget
        self._actor = "child" if child_budget is not None else "parent"
        self._facts = {
            str(item["fact_id"]): dict(item)
            for item in job.fact_projection.get("facts", [])
        }
        self._bars = {
            int(item["seq"]): dict(item)
            for item in job.bars
        }
        self._calls_used = 0
        self._optional_calls_used = 0
        self._bytes_used = 0
        self._audit: list[EvidenceAuditEvent] = []
        self._core_context = self._extract_core_context()

    @property
    def audit_events(self) -> tuple[EvidenceAuditEvent, ...]:
        return tuple(self._audit)

    def mark_baseline_assembled(self) -> None:
        """Record final transport assembly without charging a second evidence read."""
        if not self._audit or self._audit[-1].status != "ok":
            raise EvidenceAccessDenied("role baseline has no completed resources")
        self._audit[-1] = replace(self._audit[-1], baseline_assembled=True)

    @property
    def frozen_job(self) -> FrozenAgentJob:
        """Expose immutable identity to the local MCP transport, not to the model."""
        return self._job

    @property
    def snapshot_identity(self) -> dict[str, Any]:
        return {
            "snapshot_hash": self._job.manifest.snapshot_hash,
            "cutoff_ts_open": self._job.manifest.cutoff_ts_open,
        }

    @property
    def analysis_execution_enabled(self) -> bool:
        return self._core_context.get("analysis_execution_policy") == ANALYSIS_EXECUTION_POLICY

    @property
    def pa_continuation(self) -> dict[str, Any]:
        value = self._core_context.get("pa_session_continuation")
        if not isinstance(value, Mapping) or value.get("protocol_version") != "pa_session_continuation_v1":
            return {}
        return {
            "protocol_version": value["protocol_version"],
            "parent_job_digest": value.get("parent_job_digest"),
            "inherited_price_frame": bool(value.get("inherited_price_frame")),
            "inherited_higher_timeframe": bool(value.get("inherited_higher_timeframe")),
            "inherited_knowledge_ids": list(value.get("inherited_knowledge_ids") or []),
        }

    def available_tool_names(self) -> list[str]:
        """Discovery mirrors existing gateway restrictions, not a second authority."""
        if self._actor == "child":
            if self._job.repair_findings:
                return []
            return sorted(CHILD_VPA_OPERATIONS if self._job.manifest.role == "vpa_evidence"
                          else CHILD_PA_OPERATIONS if self._job.manifest.role in {"stage1", "stage2"} else ())
        names = set(REPAIR_OPERATIONS) if self._job.repair_findings else {
            "get_role_baseline", "get_job_manifest", "get_market_core",
            "list_allowed_knowledge", "read_knowledge_module", "get_upstream_artifact",
            "get_experience_entries", "get_price_frame", "get_htf_evidence", "get_vpa_evidence",
            *OPTIONAL_INVESTIGATION_OPERATIONS,
        }
        if (not self._job.repair_findings
                and self._job.manifest.role == "stage1"
                and self._core_context.get("context_policy") == BOUNDED_CONTEXT_POLICY):
            names.difference_update({
                "get_role_baseline", "get_job_manifest", "get_market_core",
                "list_allowed_knowledge", "read_knowledge_module",
                "get_upstream_artifact", "get_experience_entries",
                "get_price_frame", "get_htf_evidence", "get_bars",
                "list_fact_ids", "get_measurements", "get_facts",
                "search_allowed_knowledge",
            })
            names.add("get_stage1_decision_packet")
        if self._job.manifest.role in {"stage1", "stage2"}:
            names.discard("get_vpa_evidence")
        else:
            names.discard("get_price_frame")
        identity = self._job.runtime.get("backend_runtime_identity") or {}
        if not self._job.repair_findings and identity.get("investigation_policy") == "fixed":
            names.difference_update(OPTIONAL_INVESTIGATION_OPERATIONS)
        return sorted(names)

    def deny_tool_call(self, operation: str, request: dict[str, Any]) -> None:
        """Account for a transport-level denied call without executing a resolver."""
        call_index = self._calls_used + 1
        if call_index <= self._budget.max_evidence_calls:
            self._calls_used = call_index
        self._deny(call_index, operation, request, "tool_not_available")

    def get_stage1_decision_packet(self) -> dict[str, Any]:
        """Return the publisher-frozen packet; never rebuild it at read time."""
        def resolve() -> dict[str, Any]:
            if (self._job.manifest.role != "stage1"
                    or self._core_context.get("context_policy") != BOUNDED_CONTEXT_POLICY):
                raise EvidenceAccessDenied("Stage 1 decision packet is not authorized")
            packet = self._core_context.get("stage1_decision_packet")
            if not isinstance(packet, Mapping):
                raise EvidenceAccessDenied("frozen Stage 1 decision packet is missing")
            from pa_agent.agent_runtime.decision_packet import verify_stage1_decision_packet
            verify_stage1_decision_packet(packet)
            if packet.get("identity", {}).get("snapshot_hash") != self._job.manifest.snapshot_hash:
                raise EvidenceAccessDenied("Stage 1 decision packet identity mismatch")
            return dict(packet)
        return self._query("get_stage1_decision_packet", {}, resolve)

    def baseline_delivery_index(self, resources: list[dict[str, Any]]) -> dict[str, Any]:
        """Index values actually delivered; references in prose/upstream are not reads."""
        delivered: set[str] = set()
        for resource in resources:
            payload = resource["payload"]
            if resource["operation"] == "get_price_frame":
                for row in payload["price_frame"]:
                    for record in (row, row.get("measurements") or {}):
                        if record.get("fact_id"):
                            delivered.add(str(record["fact_id"]))
            elif resource["operation"] in {"get_htf_evidence", "get_vpa_evidence"}:
                # Nested references are not the referenced record's values.
                for record in payload.get("facts", payload.get("evidence", [])):
                    identity = record.get("evidence_id") or record.get("fact_id")
                    if identity:
                        delivered.add(str(identity))
        return {
            "delivered_value_ids": sorted(delivered),
            "indexed_only_fact_ids": sorted(set(self._facts) - delivered),
            "value_delivery_note": "Delivered values may be aligned in price_frame rather than catalog-record metadata. An index or upstream reference alone is not delivery.",
            "measurement_note": "Existing bar measurements are in price_frame. measure_price_window/project_price_leg compute only explicitly requested frozen anchors; their receipts are not catalog IDs.",
        }

    def stage2_handoff_receipt(self, resources: list[dict[str, Any]]) -> dict[str, Any]:
        """Locate, and verify the presence of, the actual delivered handoff."""
        if self._job.manifest.role != "stage2":
            raise EvidenceAccessDenied("Stage 2 handoff is available only to Stage 2")
        by_operation = {
            resource["operation"]: (index, resource["payload"])
            for index, resource in enumerate(resources)
        }
        try:
            core_index, core_payload = by_operation["get_market_core"]
            upstream_index, upstream_payload = by_operation["get_upstream_artifact"]
            core = core_payload["core_context"]
            upstream = upstream_payload["upstream_artifact"]
            schema = core["provider_output_schema"]
            protocol = core["role_protocol"]
            if not isinstance(schema, dict) or not isinstance(protocol, dict):
                raise KeyError("provider_output_schema or role_protocol")
            role_skill = core.get("role_skill")
            expected_skill = self._job.runtime["skills"][skill_key("stage2", self._core_context)]
            if not isinstance(role_skill, str) or role_skill != expected_skill:
                raise KeyError("frozen Stage 2 role skill")
            if not isinstance(upstream["stage1_diagnosis"], dict):
                raise KeyError("stage1_diagnosis")
            if not isinstance(upstream["router_manifest"], (list, dict)):
                raise KeyError("router_manifest")
            schema_digest = hashlib.sha256(json.dumps(
                schema, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            ).encode("utf-8")).hexdigest()
            expected_digest = protocol.get("provider_contract_digest")
            if expected_digest and schema_digest != expected_digest:
                raise KeyError("provider contract digest mismatch")
            locations = {
                "provider_contract": f"resources[{core_index}].payload.core_context.provider_output_schema",
                "role_protocol": f"resources[{core_index}].payload.core_context.role_protocol",
                "role_skill": f"resources[{core_index}].payload.core_context.role_skill",
                "validated_stage1": f"resources[{upstream_index}].payload.upstream_artifact.stage1_diagnosis",
                "brooks_router": f"resources[{upstream_index}].payload.upstream_artifact.router_manifest",
            }
            mandatory_ids = [
                item["knowledge_module_id"]
                for item in self._job.knowledge_manifest.get("modules", [])
                if item.get("loading_policy") == "mandatory"
            ]
            inherited_ids = set((self.pa_continuation or {}).get("inherited_knowledge_ids") or [])
            delivered = {
                resource["arguments"]["knowledge_id"]: (index, resource["payload"])
                for index, resource in enumerate(resources)
                if resource["operation"] == "read_knowledge_module"
            }
            if (inherited_ids - set(mandatory_ids)
                    or set(delivered) - set(mandatory_ids)
                    or inherited_ids & set(delivered)
                    or set(mandatory_ids) != inherited_ids | set(delivered)):
                raise KeyError("mandatory knowledge delivery partition")
            knowledge_sources = []
            for module_id in mandatory_ids:
                if module_id in inherited_ids:
                    knowledge_sources.append({
                        "knowledge_module_id": module_id,
                        "source": "verified_stage1_continuation",
                    })
                    continue
                index, payload = delivered[module_id]
                expected_sections = self._knowledge_sections(
                    module_id, self._job.knowledge_documents[module_id],
                )
                if (payload.get("knowledge_module_id") != module_id
                        or payload.get("sections") != expected_sections):
                    raise KeyError(f"mandatory knowledge content: {module_id}")
                knowledge_sources.append({
                    "knowledge_module_id": module_id,
                    "source": "current_baseline",
                    "location": f"resources[{index}].payload.sections",
                })
            baseline_sections = []
            if self._core_context.get("knowledge_delivery_policy") == LAYERED_STAGE2_RESEARCH_POLICY:
                expected_spines = {
                    item["knowledge_module_id"]: list(item["baseline_section_ids"])
                    for item in self._job.knowledge_manifest.get("modules", [])
                    if item.get("loading_policy") == "on_demand"
                }
                delivered_spines = {
                    resource["arguments"]["knowledge_id"]: (index, resource)
                    for index, resource in enumerate(resources)
                    if resource["operation"] == "read_allowed_knowledge"
                }
                if set(expected_spines) != set(delivered_spines):
                    raise KeyError("layered baseline section delivery partition")
                for module_id, section_ids in expected_spines.items():
                    index, resource = delivered_spines[module_id]
                    if resource["arguments"].get("section_ids") != section_ids:
                        raise KeyError(f"layered baseline section order: {module_id}")
                    expected = {item["section_id"]: item for item in self._knowledge_sections(
                        module_id, self._job.knowledge_documents[module_id],
                    )}
                    if resource["payload"].get("sections") != [expected[key] for key in section_ids]:
                        raise KeyError(f"layered baseline section content: {module_id}")
                    baseline_sections.append({
                        "knowledge_module_id": module_id,
                        "section_ids": section_ids,
                        "location": f"resources[{index}].payload.sections",
                    })
            continuation = self.pa_continuation or {}
            price_locations = [
                f"resources[{index}].payload.price_frame"
                for index, resource in enumerate(resources)
                if resource["operation"] == "get_price_frame"
            ]
            htf_locations = [
                f"resources[{index}].payload.facts"
                for index, resource in enumerate(resources)
                if resource["operation"] == "get_htf_evidence"
            ]
            if ((continuation.get("inherited_price_frame") and price_locations)
                    or (continuation.get("inherited_higher_timeframe") and htf_locations)
                    or (not continuation.get("inherited_price_frame") and not price_locations)):
                raise KeyError("price or HTF delivery partition")
            expected_htf = (self._core_context.get("higher_timeframe") or {}).get("evidence") or []
            if expected_htf and not continuation.get("inherited_higher_timeframe") and not htf_locations:
                raise KeyError("HTF delivery partition")
            experience_locations = [
                f"resources[{index}].payload.experience_entries"
                for index, resource in enumerate(resources)
                if resource["operation"] == "get_experience_entries"
            ]
            expected_experience = self._core_context.get("experience_entries") or []
            if bool(experience_locations) != bool(expected_experience):
                raise KeyError("experience delivery partition")
            if skill_key("stage2", self._core_context) == "joint_stage2":
                from pa_agent.ai.role_contracts import VpaEvidenceReport

                report = upstream["vpa_evidence"]
                required = {
                    "catalog_id", "data_status", "background_relation",
                    "entry_timing_relation", "observations", "hypothesis_assessments",
                    "next_confirmation", "summary",
                }
                if not isinstance(report, dict) or not required.issubset(report):
                    raise KeyError("validated VPA report fields")
                validated_report = VpaEvidenceReport.model_validate(report)
                if not isinstance(upstream["pa_hypotheses"], dict):
                    raise KeyError("pa_hypotheses")
                if (stage2_host_gated_draft(self._job)
                        and (not core.get("vpa_catalog_id")
                             or validated_report.catalog_id != core["vpa_catalog_id"])):
                    raise KeyError("validated VPA report catalog identity")
                locations["validated_vpa_report"] = (
                    f"resources[{upstream_index}].payload.upstream_artifact.vpa_evidence"
                )
                locations["pa_hypotheses"] = (
                    f"resources[{upstream_index}].payload.upstream_artifact.pa_hypotheses"
                )
                lineage = core.get("pa_handoff_lineage")
                if lineage is not None and (
                    not isinstance(lineage, dict)
                    or lineage.get("verified_stage1_projection") is not True
                    or lineage.get("source_digest_scope") != "canonical_pa_hypotheses_payload"
                ):
                    raise KeyError("PA hypothesis lineage verification")
        except (KeyError, TypeError, ValueError) as exc:
            raise EvidenceAccessDenied(f"frozen Stage 2 handoff is incomplete: {exc}") from exc
        receipt = {
            "protocol_version": "stage2_handoff_receipt_v1",
            "required_handoff_fields_present": True,
            "provider_contract_digest": schema_digest,
            "locations": locations,
            "mandatory_knowledge_sources": knowledge_sources,
            "read_plan_complete": True,
            "resource_count": len(resources),
            "terminal_resource": {
                "operation": resources[-1]["operation"],
                "knowledge_id": resources[-1]["arguments"].get("knowledge_id"),
            },
            "market_context_sources": {
                "price_frame": {
                    "source": "verified_stage1_continuation"
                    if continuation.get("inherited_price_frame") else "current_baseline",
                    "locations": price_locations,
                },
                "higher_timeframe": {
                    "source": "verified_stage1_continuation"
                    if continuation.get("inherited_higher_timeframe") else "current_baseline",
                    "locations": htf_locations,
                },
            },
            "experience_entries": {
                "count": len(expected_experience),
                "locations": experience_locations,
            },
            "note": "Frozen handoff and mandatory knowledge delivery are verified; model comprehension and trade validity have separate checks.",
        }
        if skill_key("stage2", self._core_context) == "joint_stage2" and lineage is not None:
            receipt["pa_hypotheses_lineage"] = lineage
        if baseline_sections:
            receipt["layered_research_baseline_sections"] = baseline_sections
        if skill_key("stage2", self._core_context) == "joint_stage2":
            receipt["accepted_vpa_report"] = {
                "catalog_id": validated_report.catalog_id,
                "citable_evidence_ids": sorted(validated_report.evidence_ids),
                "citation_scope": "validated_report_only",
                "raw_catalog_access": False,
            }
        return receipt

    def list_fact_ids(self, *, prefix: str = "", limit: int = 100) -> list[str]:
        requested_limit = max(1, min(int(limit), 500))
        return self._query(
            "list_fact_ids",
            {"prefix": str(prefix), "limit": requested_limit},
            lambda: [
                fact_id
                for fact_id in sorted(self._facts)
                if fact_id.startswith(str(prefix))
            ][:requested_limit],
        )

    def get_job_manifest(self) -> dict[str, Any]:
        return self._query(
            "get_job_manifest",
            {},
            lambda: {**self._job.manifest.as_dict(), "budget": asdict(self._job.budget)},
        )

    def mandatory_read_plan(self) -> list[tuple[str, dict[str, Any]]]:
        """Host-selected read sequence; execution still uses each budgeted query."""
        role = self._job.manifest.role
        plan: list[tuple[str, dict[str, Any]]] = [
            ("get_job_manifest", {}), ("get_market_core", {}),
        ]
        if role in {"stage2", "vpa_evidence", "joint_arbiter"} or self._core_context.get("previous_analysis"):
            plan.append(("get_upstream_artifact", {}))
        if self._core_context.get("knowledge_delivery_policy") not in {
            INLINE_MANDATORY_KNOWLEDGE_POLICY, LAYERED_STAGE2_RESEARCH_POLICY,
        }:
            plan.append(("list_allowed_knowledge", {}))
        continuation = self.pa_continuation if role == "stage2" else {}
        inherited_knowledge = set(continuation.get("inherited_knowledge_ids") or [])
        modules = self._job.knowledge_manifest.get("modules", [])
        for module in sorted(
            (item for item in modules if item.get("loading_policy") == "mandatory"),
            key=lambda item: int(item.get("load_order") or 0),
        ):
            if module.get("knowledge_module_id") in inherited_knowledge:
                continue
            plan.append(("read_knowledge_module", {"knowledge_id": module["knowledge_module_id"]}))
        if role == "stage2" and self._core_context.get("knowledge_delivery_policy") == LAYERED_STAGE2_RESEARCH_POLICY:
            for module in sorted(
                (item for item in modules if item.get("loading_policy") == "on_demand"),
                key=lambda item: int(item.get("load_order") or 0),
            ):
                plan.append(("read_allowed_knowledge", {
                    "knowledge_id": module["knowledge_module_id"],
                    "section_ids": list(module["baseline_section_ids"]),
                }))
        if role in {"stage1", "stage2"}:
            if not continuation.get("inherited_price_frame"):
                for newer in range(1, len(self._bars) + 1, PRICE_FRAME_CHUNK_SIZE):
                    plan.append(("get_price_frame", {
                        "older_seq": min(newer + PRICE_FRAME_CHUNK_SIZE - 1, len(self._bars)),
                        "newer_seq": newer,
                    }))
            htf_ids = [item["evidence_id"] for item in
                       (self._core_context.get("higher_timeframe") or {}).get("evidence", [])]
            if not continuation.get("inherited_higher_timeframe"):
                for offset in range(0, len(htf_ids), 50):
                    plan.append(("get_htf_evidence", {"fact_ids": htf_ids[offset:offset + 50]}))
        if role == "stage2" and self._core_context.get("experience_entries"):
            plan.append(("get_experience_entries", {}))
        if role == "vpa_evidence":
            vpa_ids = [item["evidence_id"] for item in
                       (self._job.fact_projection.get("role_evidence") or {}).get("evidence", [])]
            for offset in range(0, len(vpa_ids), 25):
                plan.append(("get_vpa_evidence", {"evidence_ids": vpa_ids[offset:offset + 25]}))
        return plan

    def get_market_core(self) -> dict[str, Any]:
        excluded = {
            "higher_timeframe",
            "stage1_diagnosis",
            "router_manifest",
            "pa_baseline_stage2",
            "pa_hypotheses",
            "vpa_evidence",
            "previous_analysis",
            "experience_entries",
            "vpa_catalog_context",
            "pa_session_continuation",
            "stage1_decision_packet",
        }
        def resolve() -> dict[str, Any]:
            core = {key: value for key, value in self._core_context.items() if key not in excluded}
            if (self._job.runtime.get("backend_runtime_identity") or {}).get("provider") == "deepseek_codex_responses" or (
                self.analysis_execution_enabled and self._job.manifest.role == "stage2"
            ):
                # Stage 2's exact Draft contract is a first-class frozen field.
                # Codex also enforces its transport schema; Direct is unaffected.
                core["provider_output_schema"] = dict(self._job.output_schema)
            if self.analysis_execution_enabled or (self._job.runtime.get("backend_runtime_identity") or {}).get("provider") == "deepseek_codex_responses":
                selected = skill_key(self._job.manifest.role, self._core_context)
                core["role_skill"] = self._job.runtime["skills"][selected]
            if self._job.manifest.role in {"vpa_evidence", "joint_arbiter"}:
                catalog = self._job.fact_projection.get("role_evidence")
                if isinstance(catalog, dict):
                    # The full catalog already belongs to this digest-bound job.
                    # Preserve all metadata, including unknown/limited semantics;
                    # evidence rows remain separately budgeted tool resources.
                    core["vpa_catalog_context"] = {
                        key: value for key, value in catalog.items() if key != "evidence"
                    }
            return core
        return self._query(
            "get_market_core",
            {},
            resolve,
        )

    def get_measurements(self, *, limit: int = 100) -> list[dict[str, Any]]:
        requested_limit = max(1, min(int(limit), 500))
        return self._query(
            "get_measurements",
            {"limit": requested_limit},
            lambda: [
                dict(item)
                for item in self._facts.values()
                if str(item.get("domain")) == "indicator"
            ][:requested_limit],
        )

    def get_price_frame(
        self,
        *,
        older_seq: int,
        newer_seq: int,
    ) -> list[dict[str, Any]]:
        """Read a bounded PA-only OHLC and indicator frame in K sequence order."""
        older = max(int(older_seq), int(newer_seq))
        newer = min(int(older_seq), int(newer_seq))
        if older - newer + 1 > 100:
            raise ValueError("price frame range cannot exceed 100 bars")

        def resolve() -> list[dict[str, Any]]:
            if self._job.manifest.role not in {"stage1", "stage2"}:
                raise EvidenceAccessDenied("price frame is available only to PA roles")
            requested = range(newer, older + 1)
            if any(seq not in self._bars for seq in requested):
                raise EvidenceAccessDenied("price frame crosses frozen snapshot cutoff")
            indicators = {
                int(item.get("value", {}).get("seq")): {
                    **item["value"], "fact_id": item["fact_id"],
                }
                for item in self._facts.values()
                if item.get("fact_type") == "bar_indicator_measurements"
                and isinstance(item.get("value"), dict)
            }
            price_facts = {
                int(item["value"]["seq"]): item
                for item in self._facts.values()
                if item.get("fact_type") == "bar_ohlc_geometry"
                and isinstance(item.get("value"), dict)
            }
            rows: list[dict[str, Any]] = []
            for seq in requested:
                bar = dict(self._bars[seq])
                if any(key in bar for key in ("volume", "amount", "volume_state")):
                    raise EvidenceAccessDenied(
                        "PA price frame contains a forbidden volume field"
                    )
                rows.append(
                    {
                        **bar,
                        **({
                            "fact_id": price_facts[seq]["fact_id"],
                            "geometry": {key: value for key, value in price_facts[seq]["value"].items()
                                         if key not in {"seq", "open", "high", "low", "close", "closed"}},
                        } if seq in price_facts else {}),
                        "measurements": indicators.get(seq, {}),
                    }
                )
            return rows

        return self._query(
            "get_price_frame",
            {"older_seq": older, "newer_seq": newer},
            resolve,
        )

    def get_pa_evidence(self, fact_ids: tuple[str, ...] | list[str]) -> list[dict[str, Any]]:
        ids = tuple(str(item) for item in fact_ids)
        def resolve() -> list[dict[str, Any]]:
            if any(not item.startswith("PA.") for item in ids):
                raise EvidenceAccessDenied("PA evidence requires PA.* fact IDs")
            hidden = next((item for item in ids if item not in self._facts), None)
            if hidden is not None:
                raise EvidenceAccessDenied(f"fact is outside role snapshot: {hidden}")
            return [dict(self._facts[item]) for item in ids]

        return self._query("get_pa_evidence", {"fact_ids": list(ids)}, resolve)

    def get_htf_evidence(self, fact_ids: tuple[str, ...] | list[str]) -> list[dict[str, Any]]:
        ids = tuple(str(item) for item in fact_ids)
        def resolve() -> list[dict[str, Any]]:
            if any(not item.startswith("HTF.H") for item in ids):
                raise EvidenceAccessDenied("HTF evidence requires HTF.H* evidence IDs")
            context = self._core_context.get("higher_timeframe") or {}
            available = {
                str(item.get("evidence_id")): dict(item)
                for item in context.get("evidence", [])
                if isinstance(item, dict) and item.get("evidence_id")
            } if isinstance(context, dict) else {}
            hidden = next((item for item in ids if item not in available), None)
            if hidden is not None:
                raise EvidenceAccessDenied(f"HTF evidence is outside role snapshot: {hidden}")
            return [available[item] for item in ids]

        return self._query("get_htf_evidence", {"fact_ids": list(ids)}, resolve)

    def get_repair_findings(self) -> dict[str, Any]:
        def resolve() -> dict[str, Any]:
            if not self._job.repair_findings:
                raise EvidenceAccessDenied("this job is not an authorized repair attempt")
            findings = dict(self._job.repair_findings)
            if (self._job.runtime.get("backend_runtime_identity") or {}).get("provider") == "deepseek_codex_responses":
                findings["provider_output_schema"] = dict(self._job.output_schema)
            if self.analysis_execution_enabled or (self._job.runtime.get("backend_runtime_identity") or {}).get("provider") == "deepseek_codex_responses":
                findings["role_skill"] = self._job.runtime["skills"]["draft_pointer_repair"]
            return findings

        return self._query("get_repair_findings", {}, resolve)

    def get_vpa_evidence(self, evidence_ids: tuple[str, ...] | list[str]) -> list[dict[str, Any]]:
        ids = tuple(dict.fromkeys(str(item) for item in evidence_ids))
        return self._query("get_vpa_evidence", {"evidence_ids": list(ids)}, lambda: self._vpa_rows(ids))

    def _vpa_rows(self, ids: tuple[str, ...]) -> list[dict[str, Any]]:
        catalog = self._job.fact_projection.get("role_evidence") or {}
        available = {str(item.get("evidence_id")): dict(item) for item in catalog.get("evidence", [])
                     if isinstance(item, dict) and item.get("evidence_id")}
        hidden = next((item for item in ids if item not in available), None)
        if hidden is not None:
            raise EvidenceAccessDenied(f"VPA evidence is outside role snapshot: {hidden}")
        return [available[item] for item in ids]

    def get_vpa_evidence_payload(self, evidence_ids: tuple[str, ...]) -> dict[str, Any]:
        if self._actor != "child":
            return {"evidence": self.get_vpa_evidence(evidence_ids)}
        ids = tuple(dict.fromkeys(evidence_ids))
        def resolve() -> dict[str, Any]:
            catalog = self._job.fact_projection.get("role_evidence") or {}
            # Meaning of volume is indispensable, but the whole catalog is not.
            # Include this frozen context before byte accounting, not afterward.
            context = {key: catalog.get(key) for key in
                       ("catalog_id", "data_status", "provenance", "capabilities")}
            return {"evidence": self._vpa_rows(ids), "catalog_context": context}
        return self._query("get_vpa_evidence", {"evidence_ids": list(ids)}, resolve)

    def get_facts(self, fact_ids: tuple[str, ...] | list[str]) -> list[dict[str, Any]]:
        ids = tuple(dict.fromkeys(str(item) for item in fact_ids))

        def resolve() -> list[dict[str, Any]]:
            hidden = next((fact_id for fact_id in ids if fact_id not in self._facts), None)
            if hidden is not None:
                raise EvidenceAccessDenied(f"fact is outside role snapshot: {hidden}")
            return [dict(self._facts[fact_id]) for fact_id in ids]

        return self._query("get_facts", {"fact_ids": list(ids)}, resolve)

    def get_bars(self, *, older_seq: int, newer_seq: int) -> list[dict[str, Any]]:
        older = max(int(older_seq), int(newer_seq))
        newer = min(int(older_seq), int(newer_seq))

        def resolve() -> list[dict[str, Any]]:
            requested = range(newer, older + 1)
            if any(seq not in self._bars for seq in requested):
                raise EvidenceAccessDenied("bar range crosses frozen snapshot cutoff")
            bars = [dict(self._bars[seq]) for seq in requested]
            if any(int(bar.get("ts_open", 0)) > self._job.manifest.cutoff_ts_open for bar in bars):
                raise EvidenceAccessDenied("bar range crosses frozen snapshot cutoff")
            if self._actor == "child":
                for bar in bars:
                    fact_id = f"PA.BAR.K{bar['seq']}"
                    if fact_id in self._facts:
                        bar["fact_id"] = fact_id
            return bars

        return self._query(
            "get_bars",
            {"older_seq": older, "newer_seq": newer},
            resolve,
        )

    def list_allowed_knowledge(self) -> list[dict[str, Any]]:
        def resolve() -> list[dict[str, Any]]:
            manifest = self._job.knowledge_manifest
            modules = manifest.get("modules", []) if isinstance(manifest, Mapping) else []
            if modules:
                return [dict(item) for item in modules if isinstance(item, dict)]
            return [
                {"knowledge_module_id": key, "section_ids": [f"{key}#s001"]}
                for key in sorted(self._job.knowledge_documents)
            ]

        return self._query("list_allowed_knowledge", {}, resolve)

    def search_allowed_knowledge(
        self,
        *,
        query: str,
        knowledge_id: str | None = None,
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        terms = [item for item in str(query).casefold().split() if item]
        if not terms:
            raise ValueError("knowledge query must not be empty")
        requested_limit = max(1, min(int(limit), 20))

        def resolve() -> list[dict[str, Any]]:
            matches: list[dict[str, Any]] = []
            for module_id, document in self._job.knowledge_documents.items():
                if knowledge_id is not None and module_id != knowledge_id:
                    continue
                for section in self._knowledge_sections(module_id, document):
                    haystack = f"{section['title']}\n{section['text']}".casefold()
                    aliases = {str(value).casefold() for value in section.get("aliases", [])}
                    if " ".join(terms) in aliases or all(
                        term in aliases or (
                            re.search(r"(?<![a-z0-9_])" + re.escape(term) + r"(?![a-z0-9_])", haystack)
                            if term.isascii() else term in haystack
                        ) for term in terms
                    ):
                        matches.append(
                            {
                                "knowledge_module_id": module_id,
                                "section_id": section["section_id"],
                                "title": section["title"],
                                "snippet": section["text"][:500],
                            }
                        )
            return matches[:requested_limit]

        return self._query(
            "search_allowed_knowledge",
            {"query": query, "knowledge_id": knowledge_id, "limit": requested_limit},
            resolve,
        )

    def read_allowed_knowledge(
        self,
        *,
        knowledge_id: str,
        section_ids: tuple[str, ...] | list[str],
    ) -> dict[str, Any]:
        requested = tuple(dict.fromkeys(str(item) for item in section_ids))

        def resolve() -> dict[str, Any]:
            try:
                document = self._job.knowledge_documents[knowledge_id]
            except KeyError as exc:
                raise EvidenceAccessDenied(
                    f"knowledge is outside role allowlist: {knowledge_id}"
                ) from exc
            available = {
                item["section_id"]: item
                for item in self._knowledge_sections(knowledge_id, document)
            }
            hidden = next((item for item in requested if item not in available), None)
            if hidden is not None:
                raise EvidenceAccessDenied(
                    f"knowledge section is outside role allowlist: {hidden}"
                )
            return {
                "knowledge_module_id": knowledge_id,
                "sections": [available[item] for item in requested],
            }

        return self._query(
            "read_allowed_knowledge",
            {"knowledge_id": knowledge_id, "section_ids": list(requested)},
            resolve,
        )

    def read_knowledge_module(self, *, knowledge_id: str) -> dict[str, Any]:
        """Read one complete PA-selected module without arbitrary truncation."""

        def resolve() -> dict[str, Any]:
            try:
                document = self._job.knowledge_documents[knowledge_id]
            except KeyError as exc:
                raise EvidenceAccessDenied(
                    f"knowledge is outside role allowlist: {knowledge_id}"
                ) from exc
            return {
                "knowledge_module_id": knowledge_id,
                "sections": self._knowledge_sections(knowledge_id, document),
            }

        return self._query(
            "read_knowledge_module",
            {"knowledge_id": knowledge_id},
            resolve,
        )

    def get_upstream_artifact(self) -> dict[str, Any]:
        allowed = {
            "stage1_diagnosis",
            "router_manifest",
            "pa_baseline_stage2",
            "pa_hypotheses",
            "vpa_evidence",
            "previous_analysis",
        }
        return self._query(
            "get_upstream_artifact",
            {},
            lambda: {
                key: value for key, value in self._core_context.items() if key in allowed
            },
        )

    def _price_value(self, seq: int, field: str) -> float:
        if type(seq) is not int or seq <= 0 or field not in {"open", "high", "low", "close"}:
            raise EvidenceAccessDenied("measurement requires a frozen OHLC anchor")
        bar = self._bars.get(seq)
        if not bar or bar.get("closed") is False or int(bar.get("ts_open", 0)) > self._job.manifest.cutoff_ts_open:
            raise EvidenceAccessDenied("measurement anchor is outside frozen closed bars")
        value = bar.get(field)
        if type(value) not in {int, float} or not math.isfinite(value):
            raise EvidenceAccessDenied("measurement anchor is missing or non-finite")
        return float(value)

    def _measurement(self, operation: str, request: dict[str, Any], values: dict[str, Any], seqs: list[int]) -> dict[str, Any]:
        if any(not math.isfinite(value) for value in values.values()):
            raise EvidenceAccessDenied("measurement result is non-finite")
        result = {"protocol_version": "frozen_price_measurement_v1",
                  "snapshot_identity": self.snapshot_identity,
                  "operation": operation, "request": request, "values": values,
                  "source_bar_refs": [f"K{seq}" for seq in dict.fromkeys(seqs)]}
        if self._actor == "child":
            # Deliver anchor values, not just references, within the same hashed
            # and byte-metered receipt. The parent's measurement API is unchanged.
            result["source_bars"] = [
                {**self._bars[seq], **({"fact_id": f"PA.BAR.K{seq}"}
                 if f"PA.BAR.K{seq}" in self._facts else {})}
                for seq in dict.fromkeys(seqs)
            ]
        encoded = json.dumps(result, sort_keys=True, allow_nan=False, separators=(",", ":")).encode()
        result["measurement_digest"] = hashlib.sha256(encoded).hexdigest()
        return result

    def measure_price_window(self, *, older_seq: int, newer_seq: int) -> dict[str, Any]:
        """Explicit window geometry; no leg boundaries, trend or VPA labels inferred."""
        request = {"older_seq": older_seq, "newer_seq": newer_seq}

        def resolve() -> dict[str, Any]:
            if type(older_seq) is not int or type(newer_seq) is not int or not 1 <= newer_seq <= older_seq or older_seq - newer_seq >= 100:
                raise EvidenceAccessDenied("measurement window must contain 1 to 100 ordered bars")
            seqs = list(range(older_seq, newer_seq - 1, -1))
            highs = [self._price_value(seq, "high") for seq in seqs]
            lows = [self._price_value(seq, "low") for seq in seqs]
            if any(high < low for high, low in zip(highs, lows, strict=True)):
                raise EvidenceAccessDenied("measurement bar geometry is inconsistent")
            return self._measurement("measure_price_window", request, {
                "bar_count": len(seqs), "high": max(highs), "low": min(lows),
                "range": max(highs) - min(lows),
                "mean_bar_range": sum(high - low for high, low in zip(highs, lows, strict=True)) / len(seqs),
                "net_change": self._price_value(newer_seq, "close") - self._price_value(older_seq, "open"),
            }, seqs)

        return self._query("measure_price_window", request, resolve)

    def project_price_leg(self, *, start_seq: int, start_field: str, end_seq: int, end_field: str,
                          origin_seq: int, origin_field: str) -> dict[str, Any]:
        """origin + (end - start), preserving sign; applicability belongs to PA."""
        request = {"start_seq": start_seq, "start_field": start_field,
                   "end_seq": end_seq, "end_field": end_field,
                   "origin_seq": origin_seq, "origin_field": origin_field}

        def resolve() -> dict[str, Any]:
            start = self._price_value(start_seq, start_field)
            end = self._price_value(end_seq, end_field)
            origin = self._price_value(origin_seq, origin_field)
            if not start_seq >= end_seq >= origin_seq or start_seq - origin_seq >= 100:
                raise EvidenceAccessDenied("projection anchors require chronological order within 100 bars")
            return self._measurement("project_price_leg", request,
                                     {"signed_leg": end - start, "projected_price": origin + (end - start)},
                                     [start_seq, end_seq, origin_seq])

        return self._query("project_price_leg", request, resolve)

    def get_experience_entries(self) -> list[dict[str, Any]]:
        def resolve() -> list[dict[str, Any]]:
            entries = self._core_context.get("experience_entries") or []
            if not isinstance(entries, list):
                raise EvidenceAccessDenied("frozen experience entries are malformed")
            return [dict(item) for item in entries if isinstance(item, dict)]

        return self._query("get_experience_entries", {}, resolve)

    def _query(
        self,
        operation: str,
        request: dict[str, Any],
        resolver: Callable[[], Any],
    ) -> Any:
        call_index = self._calls_used + 1
        started = time.perf_counter()
        if call_index > self._budget.max_evidence_calls:
            self._deny(call_index, operation, request, "call_budget_exceeded")
            raise EvidenceBudgetExceeded("evidence call budget exceeded")
        self._calls_used = call_index
        try:
            if self._actor == "child" and operation not in self.available_tool_names():
                raise EvidenceAccessDenied("operation is outside delegated evidence scope")
            investigation = str(
                (self._job.runtime.get("backend_runtime_identity") or {}).get(
                    "investigation_policy", "agentic"
                )
            )
            if (
                investigation == "fixed"
                and not self._job.repair_findings
                and operation in OPTIONAL_INVESTIGATION_OPERATIONS
            ):
                raise EvidenceAccessDenied(
                    "fixed investigation condition permits mandatory semantic-parity reads only"
                )
            if (
                self._core_context.get("context_policy") == BOUNDED_CONTEXT_POLICY
                and self._job.manifest.role == "stage1"
                and self._actor == "parent"
                and operation in OPTIONAL_INVESTIGATION_OPERATIONS
            ):
                self._optional_calls_used += 1
                if self._optional_calls_used > 2:
                    raise EvidenceAccessDenied(
                        "bounded Stage 1 permits at most two optional investigation calls"
                    )
            if self._job.repair_findings and operation not in REPAIR_OPERATIONS:
                raise EvidenceAccessDenied("repair cannot start a fresh market investigation")
            if operation == "get_vpa_evidence" and self._job.manifest.role in {"stage1", "stage2"}:
                raise EvidenceAccessDenied("PA roles cannot read raw VPA evidence; joint Stage 2 uses the accepted report")
            response = resolver()
        except EvidenceAccessDenied:
            self._deny(call_index, operation, request, "access_denied")
            raise
        response_text = json.dumps(
            response,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        response_bytes = len(response_text.encode("utf-8"))
        if response_bytes > self._budget.max_evidence_response_bytes:
            self._deny(call_index, operation, request, "response_budget_exceeded")
            raise EvidenceBudgetExceeded("single evidence response exceeds byte budget")
        if self._bytes_used + response_bytes > self._budget.max_evidence_bytes:
            self._deny(call_index, operation, request, "byte_budget_exceeded")
            raise EvidenceBudgetExceeded("evidence byte budget exceeded")
        self._bytes_used += response_bytes
        self._audit.append(
            EvidenceAuditEvent(
                call_index=call_index,
                operation=operation,
                request=dict(request),
                status="ok",
                response_bytes=response_bytes,
                response_tokens=estimate_tokens([{"role": "tool", "content": response_text}]),
                snapshot_hash=self._job.manifest.snapshot_hash,
                latency_ms=max(0.0, (time.perf_counter() - started) * 1000),
                evidence_ids=self._evidence_ids(response),
                measurement=(dict(response) if isinstance(response, dict) and response.get("measurement_digest") else None),
                completed_at_epoch_ms=int(time.time() * 1000),
                actor=self._actor,
            )
        )
        return response

    def _deny(
        self,
        call_index: int,
        operation: str,
        request: dict[str, Any],
        reason: str,
    ) -> None:
        self._audit.append(
            EvidenceAuditEvent(
                call_index=call_index,
                operation=operation,
                request=dict(request),
                status="denied",
                response_bytes=0,
                response_tokens=0,
                snapshot_hash=self._job.manifest.snapshot_hash,
                reason=reason,
                completed_at_epoch_ms=int(time.time() * 1000),
                actor=self._actor,
            )
        )

    def _extract_core_context(self) -> dict[str, Any]:
        if self._job.core_context:
            return dict(self._job.core_context)
        for message in reversed(list(self._job.request.get("messages") or [])):
            if not isinstance(message, dict) or message.get("role") != "user":
                continue
            try:
                payload = json.loads(str(message.get("content") or ""))
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict) and isinstance(payload.get("core_context"), dict):
                return dict(payload["core_context"])
        return {}

    @staticmethod
    def _knowledge_sections(module_id: str, document: Any) -> list[dict[str, Any]]:
        if isinstance(document, dict) and isinstance(document.get("sections"), list):
            return [dict(item) for item in document["sections"] if isinstance(item, dict)]
        return [
            {
                "section_id": f"{module_id}#s001",
                "title": "Overview",
                "text": str(document),
            }
        ]

    @staticmethod
    def _evidence_ids(response: Any) -> tuple[str, ...]:
        found: set[str] = set()

        def visit(value: Any) -> None:
            if isinstance(value, dict):
                columns = value.get("columns")
                rows = value.get("rows")
                if isinstance(columns, list) and "fact_id" in columns and isinstance(rows, list):
                    index = columns.index("fact_id")
                    for row in rows:
                        if isinstance(row, list) and len(row) > index:
                            found.add(str(row[index]))
                for key, child in value.items():
                    if key in {"fact_id", "evidence_id", "section_id"}:
                        found.add(str(child))
                    else:
                        visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        visit(response)
        return tuple(sorted(found))
