"""Translate one frozen PA role request into a bounded Codex job bundle."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pa_agent.agent_runtime.frozen_job import (
    AgentJobBudget,
    FrozenAgentJobBuilder,
    FrozenAgentJobStore,
    JobIntegrityError,
)
from pa_agent.agent_runtime.evidence import PRICE_FRAME_CHUNK_SIZE
from pa_agent.agent_runtime.knowledge_bundle import build_knowledge_bundle
from pa_agent.agent_runtime.repair_contract import (
    PATCH_ENVELOPE_SCHEMA,
    extract_repair_context,
    strip_repair_context,
)
from pa_agent.agent_runtime.runtime_assets import load_codex_runtime_assets
from pa_agent.ai.analysis_role_backend import AnalysisRoleRequest
from pa_agent.ai.fact_catalog import build_analysis_fact_catalog
from pa_agent.data.snapshot_bundle import serialize_frame_bars


_THREAD_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")


class CodexJobPublisher:
    """Publish only PA-selected context, facts, bars, knowledge, and schema."""

    def __init__(
        self,
        *,
        frame: Any,
        prompt_dir: Path,
        job_root: Path,
        budget: AgentJobBudget | None = None,
        runtime_identity: dict[str, Any] | None = None,
    ) -> None:
        self._frame = frame
        self._prompt_dir = Path(prompt_dir).resolve()
        self._store = FrozenAgentJobStore(job_root)
        self._budget = budget
        self._runtime_identity = dict(runtime_identity or {})
        assets = load_codex_runtime_assets()
        self._skills = assets.skill_documents
        self._builder = FrozenAgentJobBuilder(
            runtime_version=assets.runtime_version,
            agents_text=assets.agents_text,
            skill_documents=assets.skill_documents,
            challenger_text=assets.challenger_text,
        )

    def __call__(self, request: AnalysisRoleRequest, attempt: int) -> Path:
        if request.snapshot_hash is None or request.cutoff_ts_open is None:
            raise ValueError("Codex publishing requires frozen snapshot identity")
        context = dict(request.agent_context or {})
        if not context:
            raise ValueError("Codex publishing requires bounded agent_context")
        self._validate_role_context(request.role, context)
        if (request.role == "stage2" and int(attempt) == 1
                and context["role_protocol"].get("provider_contract_digest")):
            schema_digest = hashlib.sha256(json.dumps(
                request.schema, ensure_ascii=False, sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")).hexdigest()
            if schema_digest != context["role_protocol"]["provider_contract_digest"]:
                raise ValueError("Stage 2 Provider Contract does not match frozen output schema")
        raw_continuation = context.pop("pa_session_continuation", None)
        if raw_continuation is not None and (
            request.role != "stage2" or int(attempt) != 1
        ):
            raise ValueError("PA session continuation is allowed only for Stage 2 attempt 1")
        # Version the evidence-read contract explicitly.  This lets the worker
        # enforce semantic parity for vNext jobs without retroactively changing
        # the meaning of already-frozen legacy jobs.
        # New Codex jobs use the bounded packet contract. Historical jobs keep
        # their frozen semantic_parity_v1 value and remain replayable.
        from pa_agent.agent_runtime.execution_contract import (
            BOUNDED_CONTEXT_POLICY, SEMANTIC_PARITY_CONTEXT_POLICY,
        )
        context["context_policy"] = (
            BOUNDED_CONTEXT_POLICY if request.role == "stage1"
            else SEMANTIC_PARITY_CONTEXT_POLICY
        )
        context["investigation_policy"] = self._runtime_identity.get(
            "investigation_policy", "agentic"
        )
        context["stage_subagents"] = self._runtime_identity.get("stage_subagents", "off")
        from pa_agent.agent_runtime.execution_contract import (
            ANALYSIS_EXECUTION_POLICY, BASELINE_RECEIPT_POLICY, INLINE_MANDATORY_KNOWLEDGE_POLICY,
            INSTRUCTION_DELIVERY_POLICY, LAYERED_STAGE2_RESEARCH_POLICY, RUNTIME_CONTRACT,
            STAGE2_HOST_GATED_DRAFT_POLICY, skill_key,
        )

        context["runtime_contract_version"] = RUNTIME_CONTRACT
        context["analysis_execution_policy"] = ANALYSIS_EXECUTION_POLICY
        context["instruction_delivery_policy"] = INSTRUCTION_DELIVERY_POLICY
        if request.role in {"stage2", "vpa_evidence"} and int(attempt) == 1:
            context["baseline_receipt_policy"] = BASELINE_RECEIPT_POLICY
        if request.role == "stage2":
            context["result_policy"] = STAGE2_HOST_GATED_DRAFT_POLICY
        treatment = self._runtime_identity.get("knowledge_delivery_policy")
        if treatment not in {None, LAYERED_STAGE2_RESEARCH_POLICY}:
            raise ValueError("unsupported knowledge delivery treatment")
        if treatment and (self._runtime_identity.get("investigation_policy") != "agentic"
                          or self._runtime_identity.get("stage_subagents", "off") != "off"):
            raise ValueError("layered research requires agentic single-Agent analysis")
        layered = (treatment == LAYERED_STAGE2_RESEARCH_POLICY
                   and request.role == "stage2" and int(attempt) == 1)
        if request.role in {"stage2", "vpa_evidence"} and int(attempt) == 1:
            context["knowledge_delivery_policy"] = (
                LAYERED_STAGE2_RESEARCH_POLICY if layered else INLINE_MANDATORY_KNOWLEDGE_POLICY
            )
        joint = skill_key(request.role, context) == "joint_stage2"
        if layered and not joint:
            raise ValueError("layered knowledge is limited to joint Stage 2 research")
        schema_protocol = (request.schema or {}).get("properties", {}).get("protocol_version", {}).get("const")
        if joint != (schema_protocol == "pa_vpa_stage2_draft_v1"):
            raise ValueError("joint Stage 2 protocol and output schema must agree")
        catalog = build_analysis_fact_catalog(self._frame)
        if catalog.snapshot_hash != str(request.snapshot_hash).lower():
            raise ValueError("agent_context frame does not match role snapshot")
        if joint and int(attempt) == 1:
            from pa_agent.ai.pa_handoff import compile_pa_handoff
            from pa_agent.ai.role_contracts import VpaEvidenceReport

            report = VpaEvidenceReport.model_validate(context["vpa_evidence"])
            if not context.get("vpa_catalog_id") or report.catalog_id != context["vpa_catalog_id"]:
                raise ValueError("Joint Stage 2 VPA report catalog identity mismatch")

            expected_hypotheses = compile_pa_handoff(
                context["stage1_diagnosis"], None, self._frame, catalog=catalog,
            ).model_dump(mode="json")
            if context["pa_hypotheses"] != expected_hypotheses:
                raise ValueError("Joint Stage 2 PA hypotheses do not match validated Stage 1 projection")
            context["pa_handoff_lineage"] = {
                "verified_stage1_projection": True,
                "source_digest_scope": "canonical_pa_hypotheses_payload",
            }
        frozen_bars = serialize_frame_bars(self._frame)
        allowed_ids = tuple(
            dict.fromkeys(str(item) for item in context.pop("allowed_knowledge_ids", ()))
        )
        role_evidence = context.pop("role_evidence", None)
        if request.role in {"vpa_evidence", "joint_arbiter"} and isinstance(role_evidence, dict):
            from pa_agent.ai.vpa_measurements import validate_frozen_vpa_catalog

            # Reuse the canonical verifier before selecting visible rows. PA's
            # price/indicator hash and VPA's OHLCV/provenance hash have different
            # domains; bind them to the same frame rather than comparing them.
            role_evidence = validate_frozen_vpa_catalog(self._frame, role_evidence)
            context["vpa_catalog_binding"] = {
                "price_snapshot_hash": catalog.snapshot_hash,
                "ohlcv_snapshot_hash": role_evidence["snapshot_hash"],
                "catalog_id": role_evidence["catalog_id"],
                "verified_same_frame": True,
            }
        if request.role == "vpa_evidence" and isinstance(role_evidence, dict):
            from pa_agent.ai.vpa_measurements import prompt_vpa_evidence_ids

            # Match the original Direct renderer and Validator selection exactly.
            # Keep selected facts intact; never expose a window the role cannot cite.
            selected_ids = prompt_vpa_evidence_ids(role_evidence, context.get("pa_hypotheses"))
            role_evidence = {
                **role_evidence,
                "evidence": [
                    item for item in role_evidence.get("evidence", [])
                    if isinstance(item, dict) and item.get("evidence_id") in selected_ids
                ],
            }
        knowledge, knowledge_manifest = build_knowledge_bundle(
            prompt_dir=self._prompt_dir,
            knowledge_ids=allowed_ids,
            role=request.role,
            joint_stage2=joint,
            layered_stage2_research=layered,
        )
        if context.get("knowledge_delivery_policy") == INLINE_MANDATORY_KNOWLEDGE_POLICY and any(
            item.get("loading_policy") != "mandatory"
            for item in knowledge_manifest.get("modules", [])
        ):
            raise ValueError("inline mandatory knowledge requires every selected module to be mandatory")
        repair_context = extract_repair_context(request.messages, output_schema=request.schema)
        if int(attempt) > 1 and repair_context is None:
            raise ValueError("Codex repair requires Validator-authorized pointers; cannot restart full analysis")
        if repair_context is not None and int(attempt) <= 1:
            raise ValueError("repair context is invalid on an initial attempt")
        if repair_context is not None:
            repair_context["validator_feedback"] = strip_repair_context(
                str(request.messages[-1].get("content") or "")
            )
        task_id = str(context.get("task_id") or "").strip()
        if not task_id:
            raise ValueError("agent_context requires task_id")
        if raw_continuation is not None:
            context["pa_session_continuation"] = self._verify_pa_continuation(
                raw_continuation,
                task_id=task_id,
                snapshot_hash=str(request.snapshot_hash).lower(),
                cutoff_ts_open=int(request.cutoff_ts_open),
                frozen_bars=frozen_bars,
                higher_timeframe=context.get("higher_timeframe") or {},
                knowledge_manifest=knowledge_manifest,
            )
        projection = catalog.view("pa")
        projection["relations"] = [item.as_dict() for item in catalog.relations]
        visible_fact_ids = sorted(
            str(item.get("fact_id"))
            for item in projection.get("facts", [])
            if item.get("fact_id")
        )
        if request.role in {"stage1", "stage2"}:
            # Freeze navigation with the baseline, not the initial prompt. This
            # avoids a model round trip merely to discover already-visible IDs.
            context["fact_id_index"] = visible_fact_ids
        context["frozen_snapshot"] = {
            "snapshot_hash": catalog.snapshot_hash,
            "cutoff_ts_open": int(request.cutoff_ts_open),
            "catalog_id": projection.get("catalog_id"),
        }
        if "analysis_as_of_ms" in self._frame.data_metadata:
            context["frozen_snapshot"]["analysis_as_of_ms"] = self._frame.data_metadata["analysis_as_of_ms"]
        if isinstance(self._frame.data_metadata.get("market_context"), Mapping):
            context["market_context"] = dict(self._frame.data_metadata["market_context"])
        context["evidence_index"] = {
            "fact_count": len(visible_fact_ids),
            "fact_prefixes": sorted({item.split(".", 1)[0] for item in visible_fact_ids}),
            "bar_count": len(frozen_bars),
            "price_frame_chunk_size": PRICE_FRAME_CHUNK_SIZE,
            "newest_seq": min((int(item["seq"]) for item in frozen_bars), default=None),
            "oldest_seq": max((int(item["seq"]) for item in frozen_bars), default=None),
            "htf_evidence_ids": sorted(
                str(item.get("evidence_id"))
                for item in ((context.get("higher_timeframe") or {}).get("evidence") or [])
                if isinstance(item, dict) and item.get("evidence_id")
            ),
            "upstream_artifact_keys": sorted(
                key
                for key in (
                    "stage1_diagnosis",
                    "router_manifest",
                    "pa_baseline_stage2",
                    "pa_hypotheses",
                    "vpa_evidence",
                    "previous_analysis",
                )
                if context.get(key) is not None
            ),
            "experience_entry_count": len(context.get("experience_entries") or []),
        }
        if request.role in {"vpa_evidence", "joint_arbiter"}:
            context["evidence_index"]["vpa_evidence_ids"] = sorted(
                str(item.get("evidence_id"))
                for item in ((role_evidence or {}).get("evidence") or [])
                if isinstance(item, dict) and item.get("evidence_id")
            ) if isinstance(role_evidence, dict) else []
            context["evidence_index"]["vpa_evidence_chunk_size"] = 25
        initial_context = self._initial_context(context)
        messages = [
            {
                "role": "system",
                "content": (
                    "Execute only the named PA role. Runtime AGENTS, role Skill, output schema, "
                    "frozen facts and PA-selected knowledge are authoritative."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "role": request.role,
                        "core_context": initial_context,
                        "available_knowledge_ids": list(allowed_ids),
                        "repair_instruction": self._repair_instruction(request, attempt),
                        "repair_mode": repair_context is not None,
                    },
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            },
        ]
        if request.role in {"vpa_evidence", "joint_arbiter"} and role_evidence is not None:
            projection["role_evidence"] = role_evidence
        if request.role == "stage1" and repair_context is None:
            from pa_agent.agent_runtime.decision_packet import build_stage1_decision_packet
            from pa_agent.agent_runtime.investigation_policy import STAGE1_INVESTIGATION_POLICY

            context["stage1_investigation_policy"] = STAGE1_INVESTIGATION_POLICY
            context["stage1_decision_packet"] = build_stage1_decision_packet(
                snapshot_hash=str(request.snapshot_hash).lower(),
                cutoff_ts_open=int(request.cutoff_ts_open),
                bars=frozen_bars,
                facts=projection.get("facts", []),
                core_context=context,
                knowledge_documents=knowledge,
                knowledge_manifest=knowledge_manifest,
                role_skill=self._skills["stage1"],
            )
        job = self._builder.build(
            task_id=task_id,
            request_id=f"{task_id}:{request.role}:{int(attempt)}",
            role=request.role,
            attempt=int(attempt),
            snapshot_hash=str(request.snapshot_hash),
            cutoff_ts_open=int(request.cutoff_ts_open),
            messages=messages,
            output_schema=(
                PATCH_ENVELOPE_SCHEMA
                if repair_context is not None
                else dict(request.schema or {})
            ),
            fact_projection=projection,
            bars=frozen_bars,
            knowledge_documents=knowledge,
            allowed_knowledge_ids=allowed_ids,
            budget=self._budget_for(
                request.role,
                attempt,
                mandatory_module_count=len(allowed_ids),
                mandatory_knowledge_bytes=sum(
                    int(item.get("content_bytes") or 0)
                    for item in knowledge_manifest.get("modules", [])
                    if isinstance(item, dict)
                    and item.get("loading_policy") == "mandatory"
                ),
                bar_count=len(frozen_bars),
                role_evidence_count=(
                    len(role_evidence.get("evidence") or [])
                    if isinstance(role_evidence, dict)
                    else 0
                ),
                role_evidence_bytes=(
                    len(
                        json.dumps(
                            role_evidence,
                            ensure_ascii=False,
                            allow_nan=False,
                            separators=(",", ":"),
                            sort_keys=True,
                        ).encode("utf-8")
                    )
                    if isinstance(role_evidence, dict)
                    else 0
                ),
                repair_context_bytes=len(json.dumps(
                    ({**repair_context, "role_skill": self._skills["draft_pointer_repair"],
                      **({"provider_output_schema": PATCH_ENVELOPE_SCHEMA}
                         if self._runtime_identity.get("provider") == "deepseek_codex_responses" else {})}
                     if repair_context else {}), ensure_ascii=False, allow_nan=False,
                    separators=(",", ":"), sort_keys=True,
                ).encode("utf-8")),
                bounded_packet_bytes=int(
                    (context.get("stage1_decision_packet") or {}).get("packet_bytes") or 0
                ),
            ),
            backend_runtime_identity=self._runtime_identity,
            source_request_digest=analysis_role_request_digest(request, attempt),
            knowledge_manifest=knowledge_manifest,
            repair_findings=repair_context,
            core_context=context,
        )
        return self._store.publish(job)

    def _verify_pa_continuation(
        self,
        value: Any,
        *,
        task_id: str,
        snapshot_hash: str,
        cutoff_ts_open: int,
        frozen_bars: list[dict[str, Any]],
        higher_timeframe: Any,
        knowledge_manifest: dict[str, Any],
    ) -> dict[str, Any]:
        """Derive inheritance only from an integrity-checked Stage 1 parent job."""
        if not isinstance(value, dict) or set(value) != {
            "parent_job_digest",
            "thread_id",
        }:
            raise ValueError("PA parent job continuation is malformed")
        parent_digest = str(value.get("parent_job_digest") or "").strip().lower()
        thread_id = str(value.get("thread_id") or "").strip()
        if not _THREAD_ID_RE.fullmatch(thread_id):
            raise ValueError("PA parent job thread identity is invalid")
        try:
            parent = self._store.open(parent_digest, allow_expired=True)
        except (ValueError, JobIntegrityError) as exc:
            raise ValueError("PA parent job is unavailable or invalid") from exc
        manifest = parent.manifest
        if (
            manifest.role != "stage1"
            or manifest.attempt != 1
            or manifest.task_id != task_id
            or manifest.snapshot_hash != snapshot_hash
            or manifest.cutoff_ts_open != cutoff_ts_open
            or dict(parent.runtime.get("backend_runtime_identity") or {})
            != self._runtime_identity
        ):
            raise ValueError("PA parent job identity does not match Stage 2")
        price_fields = ("seq", "ts_open", "open", "high", "low", "close", "closed")
        current_price_bars = [
            {key: item[key] for key in price_fields if key in item}
            for item in frozen_bars
        ]
        inherited_price_frame = [dict(item) for item in parent.bars] == current_price_bars
        inherited_higher_timeframe = (
            parent.core_context.get("higher_timeframe") or {}
        ) == higher_timeframe
        parent_modules = {
            str(item.get("knowledge_module_id") or ""): str(item.get("sha256") or "")
            for item in parent.knowledge_manifest.get("modules", [])
            if isinstance(item, dict) and item.get("knowledge_module_id")
        }
        if parent.core_context.get("context_policy") == "bounded_context_v2":
            # Only full, byte-identical sections in the mandatory packet are
            # guaranteed delivered. An optional section index is not a read.
            delivered = {
                str(item.get("section_id")): item.get("text")
                for item in (parent.core_context.get("stage1_decision_packet") or {})
                .get("knowledge", {}).get("foundation_exact_sections", [])
            }
            parent_modules = {
                module_id: digest for module_id, digest in parent_modules.items()
                if isinstance(parent.knowledge_documents.get(module_id), dict)
                and parent.knowledge_documents[module_id].get("sections")
                and all(str(section.get("section_id")) in delivered
                        and delivered[str(section.get("section_id"))] == section.get("text")
                        for section in parent.knowledge_documents[module_id]["sections"])
            }
        inherited_knowledge_ids = [
            str(item["knowledge_module_id"])
            for item in knowledge_manifest.get("modules", [])
            if isinstance(item, dict)
            and item.get("knowledge_module_id")
            and parent_modules.get(str(item["knowledge_module_id"]))
            == str(item.get("sha256") or "")
        ]
        return {
            "protocol_version": "pa_session_continuation_v1",
            "parent_job_digest": parent_digest,
            "thread_id": thread_id,
            "inherited_price_frame": inherited_price_frame,
            "inherited_higher_timeframe": inherited_higher_timeframe,
            "inherited_knowledge_ids": inherited_knowledge_ids,
        }

    @staticmethod
    def _validate_role_context(role: str, context: dict[str, Any]) -> None:
        """Reject incomplete role handoffs before they become replayable jobs."""
        expected_protocols = {
            "stage1": "pa_stage1_agent_protocol_v1",
            "stage2": "pa_stage2_agent_protocol_v1",
            "vpa_evidence": "vpa_evidence_agent_protocol_v1",
            "joint_arbiter": "joint_arbiter_agent_protocol_v1",
        }
        from pa_agent.agent_runtime.execution_contract import JOINT_PROTOCOL

        protocol = context.get("role_protocol")
        if role == "stage2" and isinstance(protocol, dict) and protocol.get("contract_version") == JOINT_PROTOCOL:
            expected_protocols["stage2"] = JOINT_PROTOCOL
        if not isinstance(protocol, dict) or protocol.get("contract_version") != expected_protocols.get(role):
            raise ValueError(f"Codex {role} requires the mature role protocol")
        required_context = {
            "stage1": ("higher_timeframe",),
            "stage2": ("stage1_diagnosis", "router_manifest", "higher_timeframe"),
            "vpa_evidence": ("stage1_diagnosis", "pa_hypotheses"),
            "joint_arbiter": (
                "stage1_diagnosis",
                "pa_baseline_stage2",
                "pa_hypotheses",
                "vpa_evidence",
                "router_manifest",
            ),
        }
        missing = [key for key in required_context.get(role, ()) if key not in context]
        if expected_protocols.get(role) == JOINT_PROTOCOL:
            missing.extend(key for key in ("pa_hypotheses", "vpa_evidence") if key not in context)
        if missing:
            raise ValueError(
                f"Codex {role} role handoff is incomplete: {', '.join(missing)}"
            )

    @staticmethod
    def _initial_context(context: dict[str, Any]) -> dict[str, Any]:
        """Keep identity and evidence routing in-prompt; freeze detail for MCP retrieval."""
        keys = (
            "task_id",
            "symbol",
            "timeframe",
            "analysis_mode",
            "decision_stance",
            "context_policy",
            "investigation_policy",
            "stage_subagents",
            "result_policy",
            "vpa_catalog_id",
            "vpa_data_status",
            "frozen_snapshot",
            "evidence_index",
        )
        return {key: context[key] for key in keys if key in context}

    def _budget_for(
        self,
        role: str,
        attempt: int,
        *,
        mandatory_module_count: int,
        mandatory_knowledge_bytes: int,
        bar_count: int,
        role_evidence_count: int,
        role_evidence_bytes: int,
        repair_context_bytes: int = 0,
        bounded_packet_bytes: int = 0,
    ) -> AgentJobBudget:
        if self._budget is not None:
            return self._budget
        if int(attempt) > 1:
            return AgentJobBudget(
                max_evidence_calls=2,
                max_evidence_response_bytes=max(32_768, repair_context_bytes),
                max_evidence_bytes=max(65_536, repair_context_bytes + 32_768),
                max_wall_seconds=180,
            )
        if role == "stage1" and bounded_packet_bytes > 0:
            response_bytes = max(65_536, int(bounded_packet_bytes) + 8_192)
            return AgentJobBudget(
                max_evidence_calls=3,
                max_evidence_response_bytes=response_bytes,
                max_evidence_bytes=response_bytes + 2 * 65_536,
                max_wall_seconds=300,
            )
        if role == "stage2":
            frame_calls = max(1, (int(bar_count) + PRICE_FRAME_CHUNK_SIZE - 1) // PRICE_FRAME_CHUNK_SIZE)
            return AgentJobBudget(
                max_evidence_calls=max(
                    10,
                    mandatory_module_count + frame_calls + 9,
                ),
                max_evidence_bytes=max(
                    327_680,
                    mandatory_knowledge_bytes + int(bar_count) * 768 + 262_144,
                ),
                max_wall_seconds=300,
            )
        if role in {"vpa_evidence", "joint_arbiter"}:
            vpa_calls = max(1, (int(role_evidence_count) + 24) // 25)
            return AgentJobBudget(
                max_evidence_calls=max(
                    6,
                    mandatory_module_count + vpa_calls + 6,
                ),
                max_evidence_bytes=max(
                    196_608,
                    mandatory_knowledge_bytes + role_evidence_bytes + 131_072,
                ),
                max_wall_seconds=180,
            )
        frame_calls = max(1, (int(bar_count) + PRICE_FRAME_CHUNK_SIZE - 1) // PRICE_FRAME_CHUNK_SIZE)
        return AgentJobBudget(
            max_evidence_calls=max(
                8,
                mandatory_module_count + frame_calls + 8,
            ),
            max_evidence_bytes=max(
                262_144,
                mandatory_knowledge_bytes + int(bar_count) * 768 + 196_608,
            ),
            max_wall_seconds=300 if role == "stage1" else 240,
        )

    @staticmethod
    def _repair_instruction(request: AnalysisRoleRequest, attempt: int) -> str | None:
        if int(attempt) <= 1 or not request.messages:
            return None
        if extract_repair_context(request.messages) is not None:
            return (
                "Read get_repair_findings for the complete original draft, Validator "
                "feedback/contract and authorized pointers. Repair only those findings; "
                "do not restart market analysis."
            )
        final = request.messages[-1]
        if str(final.get("role") or "") != "user":
            return None
        content = strip_repair_context(str(final.get("content") or ""))
        encoded = content.encode("utf-8")
        if len(encoded) > 8_192:
            raise ValueError("repair instruction exceeds byte budget")
        return content


def analysis_role_request_digest(request: AnalysisRoleRequest, attempt: int) -> str:
    """Bind a bounded Codex job to its original PA role invocation without copying it."""
    payload = {
        "role": request.role,
        "attempt": int(attempt),
        "messages": request.messages,
        "schema": dict(request.schema or {}),
        "thinking": bool(request.thinking),
        "reasoning_effort": request.reasoning_effort,
        "structured_output": bool(request.structured_output),
        "snapshot_hash": str(request.snapshot_hash or "").lower(),
        "cutoff_ts_open": request.cutoff_ts_open,
        "agent_context": dict(request.agent_context or {}),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
