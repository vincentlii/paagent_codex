"""Translate one frozen PA role request into a bounded Codex job bundle."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pa_agent.agent_runtime.frozen_job import (
    AgentJobBudget,
    FrozenAgentJobBuilder,
    FrozenAgentJobStore,
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
        self._budget = budget or AgentJobBudget()
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
        catalog = build_analysis_fact_catalog(self._frame)
        if catalog.snapshot_hash != str(request.snapshot_hash).lower():
            raise ValueError("agent_context frame does not match role snapshot")
        allowed_ids = tuple(
            dict.fromkeys(str(item) for item in context.pop("allowed_knowledge_ids", ()))
        )
        knowledge = self._load_knowledge(allowed_ids)
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
                        "core_context": context,
                        "available_knowledge_ids": list(allowed_ids),
                        "repair_instruction": self._repair_instruction(request, attempt),
                    },
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            },
        ]
        projection = catalog.view("pa")
        projection["relations"] = [item.as_dict() for item in catalog.relations]
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
            output_schema=dict(request.schema or {}),
            fact_projection=projection,
            bars=serialize_frame_bars(self._frame),
            knowledge_documents=knowledge,
            allowed_knowledge_ids=allowed_ids,
            budget=self._budget,
            backend_runtime_identity=self._runtime_identity,
        )
        return self._store.publish(job)

    def _load_knowledge(self, allowed_ids: tuple[str, ...]) -> dict[str, str]:
        documents: dict[str, str] = {}
        for knowledge_id in allowed_ids:
            relative = Path(knowledge_id)
            if relative.is_absolute() or relative.name != knowledge_id:
                raise ValueError(f"invalid knowledge document id: {knowledge_id}")
            path = (self._prompt_dir / relative).resolve()
            if path.parent != self._prompt_dir or not path.is_file():
                raise ValueError(f"unknown knowledge document: {knowledge_id}")
            documents[knowledge_id] = path.read_text(encoding="utf-8")
        return documents

    @staticmethod
    def _repair_instruction(request: AnalysisRoleRequest, attempt: int) -> str | None:
        if int(attempt) <= 1 or not request.messages:
            return None
        final = request.messages[-1]
        if str(final.get("role") or "") != "user":
            return None
        content = str(final.get("content") or "")
        encoded = content.encode("utf-8")
        if len(encoded) > 8_192:
            raise ValueError("repair instruction exceeds byte budget")
        return content
