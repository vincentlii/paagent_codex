"""Budgeted, role-safe access to evidence already frozen into one agent job."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from pa_agent.agent_runtime.frozen_job import FrozenAgentJob


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
    snapshot_hash: str
    reason: str | None = None


class FrozenEvidenceGateway:
    """Serve facts and knowledge without computing or interpreting new signals."""

    def __init__(self, job: FrozenAgentJob) -> None:
        self._job = job
        self._facts = {
            str(item["fact_id"]): dict(item)
            for item in job.fact_projection.get("facts", [])
        }
        self._bars = {
            int(item["seq"]): dict(item)
            for item in job.bars
        }
        self._calls_used = 0
        self._bytes_used = 0
        self._audit: list[EvidenceAuditEvent] = []

    @property
    def audit_events(self) -> tuple[EvidenceAuditEvent, ...]:
        return tuple(self._audit)

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
            return bars

        return self._query(
            "get_bars",
            {"older_seq": older, "newer_seq": newer},
            resolve,
        )

    def read_knowledge(self, knowledge_id: str) -> str:
        document_id = str(knowledge_id)

        def resolve() -> str:
            try:
                return str(self._job.knowledge_documents[document_id])
            except KeyError as exc:
                raise EvidenceAccessDenied(
                    f"knowledge is outside role allowlist: {document_id}"
                ) from exc

        return self._query(
            "read_knowledge",
            {"knowledge_id": document_id},
            resolve,
        )

    def _query(
        self,
        operation: str,
        request: dict[str, Any],
        resolver: Callable[[], Any],
    ) -> Any:
        call_index = self._calls_used + 1
        if call_index > self._job.budget.max_evidence_calls:
            self._deny(call_index, operation, request, "call_budget_exceeded")
            raise EvidenceBudgetExceeded("evidence call budget exceeded")
        self._calls_used = call_index
        try:
            response = resolver()
        except EvidenceAccessDenied:
            self._deny(call_index, operation, request, "access_denied")
            raise
        response_bytes = len(
            json.dumps(
                response,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        )
        if self._bytes_used + response_bytes > self._job.budget.max_evidence_bytes:
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
                snapshot_hash=self._job.manifest.snapshot_hash,
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
                snapshot_hash=self._job.manifest.snapshot_hash,
                reason=reason,
            )
        )
