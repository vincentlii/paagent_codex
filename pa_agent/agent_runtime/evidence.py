"""Budgeted, role-safe access to evidence already frozen into one agent job."""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from dataclasses import asdict, dataclass
from typing import Any, Callable

from pa_agent.agent_runtime.frozen_job import FrozenAgentJob
from pa_agent.ai.token_counter import estimate_tokens


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


class FrozenEvidenceGateway:
    """Serve frozen evidence and bounded arithmetic, never classify signals."""

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
        self._core_context = self._extract_core_context()

    @property
    def audit_events(self) -> tuple[EvidenceAuditEvent, ...]:
        return tuple(self._audit)

    @property
    def snapshot_identity(self) -> dict[str, Any]:
        return {
            "snapshot_hash": self._job.manifest.snapshot_hash,
            "cutoff_ts_open": self._job.manifest.cutoff_ts_open,
        }

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
        }
        def resolve() -> dict[str, Any]:
            core = {key: value for key, value in self._core_context.items() if key not in excluded}
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
                int(item.get("value", {}).get("seq")): dict(item.get("value") or {})
                for item in self._facts.values()
                if item.get("fact_type") == "bar_indicator_measurements"
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
            return dict(self._job.repair_findings)

        return self._query("get_repair_findings", {}, resolve)

    def get_vpa_evidence(self, evidence_ids: tuple[str, ...] | list[str]) -> list[dict[str, Any]]:
        ids = tuple(dict.fromkeys(str(item) for item in evidence_ids))

        def resolve() -> list[dict[str, Any]]:
            role_evidence = self._job.fact_projection.get("role_evidence") or {}
            catalog = role_evidence if isinstance(role_evidence, dict) else {}
            available = {
                str(item.get("evidence_id")): dict(item)
                for item in catalog.get("evidence", [])
                if isinstance(item, dict) and item.get("evidence_id")
            }
            hidden = next((item for item in ids if item not in available), None)
            if hidden is not None:
                raise EvidenceAccessDenied(
                    f"VPA evidence is outside role snapshot: {hidden}"
                )
            return [available[item] for item in ids]

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
            return bars

        return self._query(
            "get_bars",
            {"older_seq": older, "newer_seq": newer},
            resolve,
        )

    def list_allowed_knowledge(self) -> list[dict[str, Any]]:
        def resolve() -> list[dict[str, Any]]:
            manifest = self._job.knowledge_manifest
            modules = manifest.get("modules", []) if isinstance(manifest, dict) else []
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
        if call_index > self._job.budget.max_evidence_calls:
            self._deny(call_index, operation, request, "call_budget_exceeded")
            raise EvidenceBudgetExceeded("evidence call budget exceeded")
        self._calls_used = call_index
        try:
            if self._job.repair_findings and operation not in {
                "get_repair_findings", "get_facts", "get_pa_evidence", "get_htf_evidence",
                "get_vpa_evidence", "read_allowed_knowledge",
            }:
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
        if response_bytes > self._job.budget.max_evidence_response_bytes:
            self._deny(call_index, operation, request, "response_budget_exceeded")
            raise EvidenceBudgetExceeded("single evidence response exceeds byte budget")
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
                response_tokens=estimate_tokens([{"role": "tool", "content": response_text}]),
                snapshot_hash=self._job.manifest.snapshot_hash,
                latency_ms=max(0.0, (time.perf_counter() - started) * 1000),
                evidence_ids=self._evidence_ids(response),
                measurement=(dict(response) if isinstance(response, dict) and response.get("measurement_digest") else None),
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
