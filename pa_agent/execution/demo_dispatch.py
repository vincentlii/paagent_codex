"""Host-only official execution seam; never handed to the model or Dashboard."""

from __future__ import annotations

import os
from dataclasses import replace
from types import SimpleNamespace

from pa_agent.execution.demo_contracts import DemoConfig, DemoIntent, digest
from pa_agent.execution.demo_plans import compile_demo_intent
from pa_agent.execution.demo_spool import read_json, atomic_json
from pa_agent.operations.demo_settings import DemoExecutionPolicy


def load_demo_config(environ=None):
    env = os.environ if environ is None else environ
    path = env.get("PA_AGENT_DEMO_CONFIG")
    if not path:
        return DemoConfig()
    from pathlib import Path

    return DemoConfig.model_validate(read_json(Path(path)))


def bind_execution_epoch(task_payload, config):
    if getattr(task_payload, "execution_target", None) is not None:
        return task_payload  # Recovery/retry may never rebind an existing accepted task.
    if config.enabled and config.execution_target == "okx_demo":
        return replace(task_payload, execution_target="okx_demo", execution_epoch=config.cutover_id)
    return task_payload


def apply_execution_cutover(*, paused, host_idle, worker_idle, recovery_blocked, config):
    if not paused or not host_idle or not worker_idle or recovery_blocked:
        raise ValueError("cutover_requires_paused_idle_reconciled_services")
    if not config.cutover_id or config.cutover_started_at_ms <= 0:
        raise ValueError("cutover_requires_explicit_epoch_and_time")
    return config


def cutover_fingerprint(config):
    return digest(config.model_dump(mode="json", exclude={"enabled", "entry_enabled"}))


def require_cutover(paths, config):
    if config.execution_target != "okx_demo":
        return
    certificate = read_json(paths.intents / "cutover.json")
    if certificate.get("config_digest") != cutover_fingerprint(config):
        raise ValueError("execution_cutover_not_accepted")


class OfficialExecutionDispatcher:
    def __init__(self, paper, demo_spool, config, *, now_ms, task_lookup=None):
        self.paper, self.spool, self.config, self.now_ms = paper, demo_spool, config, now_ms
        self.task_lookup = task_lookup
        self.execution_policy = DemoExecutionPolicy()

    def apply_execution_settings(self, policy):
        self.execution_policy = DemoExecutionPolicy.model_validate(policy)

    @property
    def demo_selected(self):
        return self.config.execution_target == "okx_demo"

    @property
    def enabled(self):
        return self.demo_selected or self.paper.enabled

    def bind_task(self, task):
        existing = self.task_lookup(task.task_id) if self.task_lookup is not None else None
        if existing is not None:
            return replace(
                task,
                payload=replace(
                    task.payload,
                    execution_target=existing.payload.execution_target,
                    execution_epoch=existing.payload.execution_epoch,
                    demo_execution_policy=existing.payload.demo_execution_policy,
                ),
            )
        payload = bind_execution_epoch(task.payload, self.config)
        if payload is not task.payload:
            payload = replace(
                payload, demo_execution_policy=self.execution_policy.model_dump(mode="json")
            )
        return replace(task, payload=payload)

    def publish_control(self, *, running):
        if self.demo_selected:
            atomic_json(
                self.spool.paths.control,
                dict(
                    cutover_id=self.config.cutover_id,
                    generated_at_ms=self.now_ms(),
                    running=bool(running and self.config.enabled),
                ),
            )

    def compose_projection(self, paper):
        if not self.demo_selected:
            return paper
        from pa_agent.operations.demo_trading import compose_trading_projection

        try:
            demo = read_json(self.spool.paths.projection)
        except (OSError, ValueError):
            demo = None
        return compose_trading_projection(
            paper, demo, selected_backend="okx_demo", now_ms=self.now_ms()
        )

    def submit_official(
        self, record, spec, *, task_id, execution_payload=None, execution_snapshot_hash=None
    ):
        if not self.demo_selected:
            return self.paper.submit_official(record, spec, task_id=task_id)
        payload = execution_payload
        if payload is None and self.task_lookup is not None:
            task = self.task_lookup(task_id)
            payload = getattr(task, "payload", None)
            execution_snapshot_hash = getattr(task, "snapshot_hash", None)
        if (
            getattr(payload, "execution_target", None) != "okx_demo"
            or getattr(payload, "execution_epoch", None) != self.config.cutover_id
        ):
            return SimpleNamespace(status="skipped_cutover", reason="unbound_or_old_task")
        marker = (
            self.spool.paths.intents
            / "dispatch"
            / (digest([self.config.cutover_id, task_id]) + ".json")
        )
        if marker.exists():
            intent = DemoIntent.model_validate(read_json(marker))
            if intent.task_id != task_id or intent.cutover_id != self.config.cutover_id:
                raise ValueError("dispatch_binding_mismatch")
        else:
            try:
                intent = compile_demo_intent(
                    record,
                    spec,
                    task_id=task_id,
                    config=self.config,
                    frozen_execution_epoch=payload.execution_epoch,
                    now_ms=self.now_ms(),
                    market_snapshot_hash=execution_snapshot_hash,
                    demo_execution_policy=getattr(payload, "demo_execution_policy", None),
                )
            except ValueError as exc:
                return SimpleNamespace(status="rejected", reason=str(exc))
            atomic_json(marker, intent.model_dump(mode="json"))
        self.spool.collect_acknowledged(before_ms=self.now_ms() + 1)
        # Existing receipt proves delivery: never republish after queue cleanup.
        if not (self.spool.paths.receipts / f"intent-{intent.content_digest}.json").exists():
            self.spool.publish(intent)
        return SimpleNamespace(status="queued", reason="host_intent_delivered")

    def reconcile_official(self, successful_jobs, records_dir):
        if not self.demo_selected:
            return self.paper.reconcile_official(successful_jobs, records_dir)
        # Trust remains the original Host persisted-success/audit path, plus frozen epoch.
        from pa_agent.records.analysis_history import list_record_paths, load_record
        from pa_agent.execution.plans import instrument_spec_from_record

        jobs = {}
        for job in successful_jobs:
            task = self.task_lookup(job["task_id"]) if self.task_lookup else None
            if (
                getattr(getattr(task, "payload", None), "execution_epoch", None)
                == self.config.cutover_id
                and getattr(task.payload, "execution_target", None) == "okx_demo"
            ):
                jobs[job["run_id"]] = (job["task_id"], task.payload)
        actions = []
        if not jobs:
            return ()
        for path in list_record_paths(records_dir):
            record = load_record(path)
            job = jobs.get(record.meta.run_id)
            if job and (spec := instrument_spec_from_record(record)) is not None:
                actions.append(self.submit_official(record, spec, task_id=job[0]))
        return tuple(actions)

    def observe_official(self, *args, **kwargs):
        return self.paper.observe_official(*args, **kwargs)

    def expire_pending(self, *args, **kwargs):
        return self.paper.expire_pending(*args, **kwargs)

    def active_market_keys(self, *args, **kwargs):
        return self.paper.active_market_keys(*args, **kwargs)
