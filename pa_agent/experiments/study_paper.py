"""Bind completed study assignments to isolated latency-aware Paper accounts."""

from __future__ import annotations

import json
import math
from time import time_ns
from pathlib import Path
from typing import Any

from pa_agent.execution.paper import LATENCY_PAPER_SIMULATION_VERSION, PaperBroker, RiskPolicy
from pa_agent.execution.plans import (
    closed_bars_from_record,
    instrument_spec_from_record,
    official_paper_plan,
)
from pa_agent.experiments.study_protocol import (
    _load_registration, write_once, digest, summarize_study, require_split_release,
)
from pa_agent.records.schema import AnalysisRecord


def bind_study_paper(root: Path, paper_db: Path, settings: Any, *, case_id: str | None = None) -> None:
    """Submit each completed assignment once, using its persisted delivery time."""
    registration = _load_registration(Path(root))
    summarize_study(Path(root))  # Verify receipts before any execution side effect.
    if not settings.enabled:
        raise ValueError("study Paper must be explicitly enabled")
    config_path = Path(root) / "paper_config.json"
    config = {"registration_digest": registration["registration_digest"],
              "settings": settings.model_dump(mode="json")}
    if config_path.exists():
        if json.loads(config_path.read_text(encoding="utf-8")) != config:
            raise ValueError("study Paper settings changed after binding")
    else:
        write_once(config_path, config)
    samples = {item["sample_id"]: item for item in registration["samples"]}
    policy = RiskPolicy(
        risk_fraction=float(settings.risk_fraction),
        max_portfolio_risk_fraction=float(settings.max_portfolio_risk_fraction),
        max_leverage=float(settings.max_leverage),
    )
    for assignment in registration["assignments"]:
        if case_id is not None and assignment["case_id"] != case_id:
            continue
        binding_path = Path(root) / "paper_bindings" / f"{assignment['case_id']}.json"
        if binding_path.exists():
            _read_binding(binding_path, registration, assignment)
            continue
        receipt_path = Path(root) / "receipts" / f"{assignment['case_id']}.json"
        if not receipt_path.exists():
            continue
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if receipt.get("status") != "completed":
            continue
        record = AnalysisRecord.model_validate(receipt["result"]["output"])
        spec = instrument_spec_from_record(record)
        timing = record.analysis_timing
        if spec is None or timing is None or timing.result_available_at_ms is None:
            raise ValueError("study Paper requires instrument and delivery identity")
        sample = samples[assignment["sample_id"]]
        now_ms = time_ns() // 1_000_000
        if timing.result_available_at_ms < sample["window_start_ms"]:
            raise ValueError("study delivery precedes registered forward window")
        if timing.decision_expires_at_ms is None or sample["window_end_ms"] < timing.decision_expires_at_ms:
            raise ValueError("study outcome horizon cannot precede entry expiry")
        account_id = f"study-{registration['registration_digest']}-{assignment['case_id']}"
        broker = PaperBroker(
            Path(paper_db),
            account_id=account_id,
            initial_equity=float(settings.initial_equity),
            risk_policy=policy,
            fee_bps=float(settings.fee_bps),
            slippage_bps=float(settings.slippage_bps),
        )
        # Recovery first: an order committed before the binding file must not be
        # reactivated or reclassified when the clock has advanced.
        import sqlite3
        with sqlite3.connect(paper_db) as connection:
            existing = connection.execute(
                "SELECT order_id FROM paper_orders WHERE account_id = ? AND dedupe_key = ?",
                (account_id, assignment["case_id"]),
            ).fetchone()
        order_id = existing[0] if existing else None
        status = "submitted" if existing else "pending"
        if not existing and now_ms < timing.decision_expires_at_ms:
            plan = official_paper_plan(record, spec, dedupe_key=assignment["case_id"], now_ms=now_ms)
            broker.observe_market(spec, record.meta.timeframe, closed_bars_from_record(record))
            if plan is None:
                status = "no_order"
            else:
                order = broker.submit(plan, spec, activate_now=True)
                order_id, status = order.order_id, "submitted"
        # Late, unactivated decisions remain censored; never backdate activation.
        binding = {
            "case_id": assignment["case_id"],
            "registration_digest": registration["registration_digest"],
            "receipt_digest": receipt["receipt_digest"],
            "run_id": record.meta.run_id,
            "account_id": account_id,
            "order_id": order_id,
            "status": status,
            "execution_version": LATENCY_PAPER_SIMULATION_VERSION,
            "window_end_ms": sample["window_end_ms"],
            "instrument_spec": spec.to_dict(), "timeframe": record.meta.timeframe,
            "paper_settings": settings.model_dump(mode="json"),
        }
        write_once(
            binding_path, {**binding, "binding_digest": digest(binding)},
        )


def collect_study_paper_outcomes(root: Path, paper_db: Path, *, split: str = "development") -> list[dict]:
    """Read only mechanically bound, cost-net outcomes for study summarization."""
    import sqlite3

    registration = _load_registration(Path(root))
    require_split_release(Path(root), registration, split)
    summarize_study(Path(root))
    results: list[dict] = []
    with sqlite3.connect(Path(paper_db).resolve().as_uri() + "?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        for assignment in registration["assignments"]:
            if assignment["split"] != split:
                continue
            path = Path(root) / "paper_bindings" / f"{assignment['case_id']}.json"
            if not path.exists():
                continue
            binding = _read_binding(path, registration, assignment)
            outcome = {
                "case_id": assignment["case_id"],
                "execution_version": LATENCY_PAPER_SIMULATION_VERSION,
                "window_end_ms": binding["window_end_ms"],
            }
            if binding["status"] == "no_order":
                outcome.update(status="no_order", return_r=0.0)
            elif binding["status"] == "pending":
                outcome.update(status="pending", return_r=None)
            else:
                row = connection.execute(
                    "SELECT * FROM paper_orders WHERE account_id = ? AND order_id = ?",
                    (binding["account_id"], binding["order_id"]),
                ).fetchone()
                if (row is None or row["simulation_version"] != LATENCY_PAPER_SIMULATION_VERSION
                        or row["run_id"] != binding["run_id"] or row["dedupe_key"] != assignment["case_id"]):
                    raise ValueError("bound study Paper order is missing or wrong-version")
                status = str(row["status"])
                if status == "closed":
                    from pa_agent.data.bar_close_wait import timeframe_to_seconds
                    from pa_agent.data.datetime_ts import ts_open_to_ms
                    exit_close = int(ts_open_to_ms(row["exit_bar_ts"])) + timeframe_to_seconds(row["timeframe"]) * 1000
                    if exit_close > binding["window_end_ms"]:
                        outcome.update(status="pending", return_r=None)
                        results.append(outcome)
                        continue
                    risk = (
                        abs(float(row["fill_price"]) - float(row["stop_price"]))
                        * float(row["quantity"])
                        * float(row["contract_multiplier"])
                    )
                    value = float(row["realized_pnl"]) / risk if risk > 0 else math.nan
                    if not math.isfinite(value):
                        raise ValueError("closed study Paper order has invalid R return")
                    outcome.update(status="closed", return_r=value)
                elif status == "cancelled" and row["exit_reason"] == "decision_expired":
                    outcome.update(status="entry_not_triggered", return_r=0.0)
                elif status == "ambiguous":
                    outcome.update(status="ambiguous", return_r=None)
                else:
                    outcome.update(status="pending", return_r=None)
            results.append(outcome)
    return results


def _read_binding(path: Path, registration: dict, assignment: dict) -> dict:
    binding = json.loads(path.read_text(encoding="utf-8"))
    sample = next(s for s in registration["samples"] if s["sample_id"] == assignment["sample_id"])
    receipt = json.loads((path.parent.parent / "receipts" / f"{assignment['case_id']}.json").read_text(encoding="utf-8"))
    if (digest({k: v for k, v in binding.items() if k != "binding_digest"}) != binding.get("binding_digest")
            or binding.get("registration_digest") != registration["registration_digest"]
            or binding.get("case_id") != assignment["case_id"]
            or binding.get("receipt_digest") != receipt["receipt_digest"]
            or binding.get("window_end_ms") != sample["window_end_ms"]
            or binding.get("execution_version") != LATENCY_PAPER_SIMULATION_VERSION):
        raise ValueError("study Paper binding identity mismatch")
    return binding


def observe_study_paper(root: Path, paper_db: Path, *, spec, timeframe: str, bars) -> None:
    """Advance isolated accounts from Host-supplied closed bars within each horizon."""
    from pa_agent.data.bar_close_wait import timeframe_to_seconds
    from pa_agent.data.datetime_ts import ts_open_to_ms

    registration = _load_registration(root)
    summarize_study(root)
    duration = timeframe_to_seconds(timeframe)
    if not duration or "M" in timeframe:
        raise ValueError("study Paper requires fixed-duration bars")
    now_ms = time_ns() // 1_000_000
    for assignment in registration["assignments"]:
        path = root / "paper_bindings" / f"{assignment['case_id']}.json"
        if not path.exists():
            continue
        binding = _read_binding(path, registration, assignment)
        if binding["status"] != "submitted" or binding["timeframe"] != timeframe:
            continue
        if binding["instrument_spec"] != spec.to_dict():
            continue
        config = binding["paper_settings"]
        broker = PaperBroker(
            paper_db, account_id=binding["account_id"], initial_equity=config["initial_equity"],
            risk_policy=RiskPolicy(**{k: config[k] for k in (
                "risk_fraction", "max_portfolio_risk_fraction", "max_leverage")}),
            fee_bps=config["fee_bps"], slippage_bps=config["slippage_bps"],
        )
        selected = [bar for bar in bars if bar.closed and
                    int(ts_open_to_ms(bar.ts_open)) + duration * 1000 <= min(now_ms, binding["window_end_ms"])]
        broker.observe_market(spec, timeframe, selected)
        broker.expire_pending(spec, timeframe, now_ms=min(now_ms, binding["window_end_ms"]))


__all__ = ["bind_study_paper", "collect_study_paper_outcomes", "observe_study_paper"]
