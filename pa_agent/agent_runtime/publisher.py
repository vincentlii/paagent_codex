"""Translate one frozen PA role request into a bounded Codex job bundle."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pa_agent.agent_runtime.frozen_job import (
    AgentJobBudget,
    FrozenAgentJobBuilder,
    FrozenAgentJobStore,
)
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
        self._builder = FrozenAgentJobBuilder(
            runtime_version=assets.runtime_version,
            agents_text=assets.agents_text,
            skill_documents=assets.skill_documents,
        )

    def __call__(self, request: AnalysisRoleRequest, attempt: int) -> Path:
        if request.snapshot_hash is None or request.cutoff_ts_open is None:
            raise ValueError("Codex publishing requires frozen snapshot identity")
        context = dict(request.agent_context or {})
        if not context:
            raise ValueError("Codex publishing requires bounded agent_context")
        self._validate_role_context(request.role, context)
        # Version the evidence-read contract explicitly.  This lets the worker
        # enforce semantic parity for vNext jobs without retroactively changing
        # the meaning of already-frozen legacy jobs.
        context["context_policy"] = "semantic_parity_v1"
        catalog = build_analysis_fact_catalog(self._frame)
        if catalog.snapshot_hash != str(request.snapshot_hash).lower():
            raise ValueError("agent_context frame does not match role snapshot")
        frozen_bars = serialize_frame_bars(self._frame)
        allowed_ids = tuple(
            dict.fromkeys(str(item) for item in context.pop("allowed_knowledge_ids", ()))
        )
        role_evidence = context.pop("role_evidence", None)
        knowledge, knowledge_manifest = build_knowledge_bundle(
            prompt_dir=self._prompt_dir,
            knowledge_ids=allowed_ids,
            role=request.role,
        )
        repair_context = extract_repair_context(request.messages)
        if repair_context is not None and int(attempt) <= 1:
            raise ValueError("repair context is invalid on an initial attempt")
        projection = catalog.view("pa")
        projection["relations"] = [item.as_dict() for item in catalog.relations]
        visible_fact_ids = sorted(
            str(item.get("fact_id"))
            for item in projection.get("facts", [])
            if item.get("fact_id")
        )
        context["frozen_snapshot"] = {
            "snapshot_hash": catalog.snapshot_hash,
            "cutoff_ts_open": int(request.cutoff_ts_open),
            "catalog_id": projection.get("catalog_id"),
        }
        if "analysis_as_of_ms" in self._frame.data_metadata:
            context["frozen_snapshot"]["analysis_as_of_ms"] = self._frame.data_metadata["analysis_as_of_ms"]
        context["evidence_index"] = {
            "fact_count": len(visible_fact_ids),
            "fact_prefixes": sorted({item.split(".", 1)[0] for item in visible_fact_ids}),
            "bar_count": len(frozen_bars),
            "price_frame_chunk_size": 100,
            "newest_seq": min((int(item["seq"]) for item in frozen_bars), default=None),
            "oldest_seq": max((int(item["seq"]) for item in frozen_bars), default=None),
            "htf_evidence_ids": sorted(
                str(item.get("evidence_id"))
                for item in ((context.get("higher_timeframe") or {}).get("evidence") or [])
                if isinstance(item, dict) and item.get("evidence_id")
            ),
            "vpa_evidence_ids": sorted(
                str(item.get("evidence_id"))
                for item in ((role_evidence or {}).get("evidence") or [])
                if isinstance(item, dict) and item.get("evidence_id")
            ) if isinstance(role_evidence, dict) else [],
            "vpa_evidence_chunk_size": 25,
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
        task_id = str(context.get("task_id") or "").strip()
        if not task_id:
            raise ValueError("agent_context requires task_id")
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
            ),
            backend_runtime_identity=self._runtime_identity,
            source_request_digest=analysis_role_request_digest(request, attempt),
            knowledge_manifest=knowledge_manifest,
            repair_findings=repair_context,
            core_context=context,
        )
        return self._store.publish(job)

    @staticmethod
    def _validate_role_context(role: str, context: dict[str, Any]) -> None:
        """Reject incomplete role handoffs before they become replayable jobs."""
        expected_protocols = {
            "stage1": "pa_stage1_agent_protocol_v1",
            "stage2": "pa_stage2_agent_protocol_v1",
            "vpa_evidence": "vpa_evidence_agent_protocol_v1",
            "joint_arbiter": "joint_arbiter_agent_protocol_v1",
        }
        protocol = context.get("role_protocol")
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
    ) -> AgentJobBudget:
        if self._budget is not None:
            return self._budget
        if int(attempt) > 1:
            return AgentJobBudget(
                max_evidence_calls=2,
                max_evidence_response_bytes=32_768,
                max_evidence_bytes=65_536,
                max_wall_seconds=180,
            )
        if role == "stage2":
            frame_calls = max(1, (int(bar_count) + 99) // 100)
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
        frame_calls = max(1, (int(bar_count) + 99) // 100)
        return AgentJobBudget(
            max_evidence_calls=max(
                8,
                mandatory_module_count + frame_calls + 8,
            ),
            max_evidence_bytes=max(
                262_144,
                mandatory_knowledge_bytes + int(bar_count) * 768 + 196_608,
            ),
            max_wall_seconds=240,
        )

    @staticmethod
    def _repair_instruction(request: AnalysisRoleRequest, attempt: int) -> str | None:
        if int(attempt) <= 1 or not request.messages:
            return None
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
