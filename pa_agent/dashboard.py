"""Authenticated loopback dashboard for credential-free PA Agent operations."""

from __future__ import annotations

import argparse
import base64
import hmac
import json
import os
import secrets
import sys
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pa_agent.config.paths import RuntimePaths, resolve_runtime_paths
from pa_agent.dashboard_page import DASHBOARD_HTML, LOGIN_HTML
from pa_agent.operations.analysis_profile import (
    analysis_capabilities,
    validate_analysis_profile,
)
from pa_agent.operations.runtime_state import (
    RuntimeControlConflict,
    RuntimeControlStore,
    RuntimeStatusStore,
    control_state_to_dict,
)

_MAX_BODY_BYTES = 4096
_SESSION_COOKIE = "pa_agent_dashboard_session"
_SESSION_SECONDS = 8 * 60 * 60
_RUNTIME_FIELDS = (
    "service_state",
    "started_at_ms",
    "updated_at_ms",
    "heartbeat_stale_after_seconds",
    "applied_control_generation",
    "applied_codex_fast_mode",
    "applied_profile_generation",
    "applied_profile_fingerprint",
    "run_once_consumed_generation",
    "consumed_run_once_generation",
    "control_error",
)
_ASSET_FIELDS = (
    "source",
    "venue",
    "symbol",
    "timeframe",
    "analysis_enabled",
    "connected",
    "in_flight",
    "last_analysis_status",
    "fetch_failures",
    "latest_closed_bar_ts",
)
_MARKET_CONTEXT_FIELDS = ("instrument_label",)
_SCHEDULER_FIELDS = (
    "running",
    "queued",
    "completed",
    "failed",
    "audit_failures",
    "closed",
    "error",
    "max_concurrency",
)
_MODE_FIELDS = (
    "vpa", "analysis_pipeline", "direct_available", "paper_trading",
    "decision_stance", "analysis_backend",
)
_RECENT_RUN_FIELDS = (
    "run_id",
    "timestamp_local_ms",
    "symbol",
    "timeframe",
    "analysis_pipeline",
    "run_status",
    "error_type",
    "stage1_cycle_position",
    "stage1_direction",
    "stage1_gate_result",
    "order_type",
    "order_direction",
    "trade_confidence",
    "terminal_outcome",
    "vpa_data_status",
    "vpa_background_relation",
    "vpa_timing_relation",
    "vpa_effect",
    "analysis_backend",
    "backend",
    "requested_model",
    "effective_model",
    "reasoning_effort",
    "requested_reasoning_effort",
    "effective_reasoning_effort",
    "total_latency_ms",
    "total_tokens",
    "initial_context_tokens",
    "retrieval_context_tokens",
    "evidence_call_count",
    "evidence_denied_count",
    "evidence_latency_ms",
    "repair_count",
    "outcome",
)
_PAPER_ACCOUNT_FIELDS = (
    "account_id",
    "equity",
    "orders",
    "pending",
    "open",
    "closed",
    "cancelled",
    "ambiguous",
    "realized_pnl",
)
_PAPER_RESEARCH_FIELDS = ("complete_aabb_snapshots",)
_CODEX_FIELDS = (
    "failures",
    "opened_until",
    "cooldown_level",
    "probe_in_flight",
    "auth_locked",
    "state_unreadable",
    "worker_status_unreadable",
    "updated_at_epoch",
    "service_state",
    "auth_state",
    "runtime_version",
    "runtime_asset_digest",
    "codex_binary_digest",
)
_ANALYSIS_SUMMARY_FIELDS = (
    "backend",
    "analysis_pipeline",
    "run_count",
    "operational_success_rate",
    "first_pass_rate",
    "latency_p50_ms",
    "latency_p95_ms",
    "mean_total_tokens",
    "mean_initial_context_tokens",
    "mean_retrieval_context_tokens",
    "evidence_call_count",
    "evidence_denied_count",
)
_BACKEND_EXPERIMENT_FIELDS = (
    "available",
    "protocol_version",
    "experiments",
    "completed_experiments",
    "valid_experiments",
    "unique_snapshots",
    "cohort_count",
    "assigned_arms",
    "persisted_arms",
    "failed_arms",
    "arm_coverage",
)


@dataclass(frozen=True)
class DashboardConfig:
    host: str = "127.0.0.1"
    port: int = 8765
    token: str = ""

    def __post_init__(self) -> None:
        if self.host not in {"127.0.0.1", "localhost"}:
            raise ValueError("dashboard host must be loopback (127.0.0.1 or localhost)")
        if not 1 <= int(self.port) <= 65535 and int(self.port) != 0:
            raise ValueError("dashboard port must be between 1 and 65535")
        if len(self.token) < 16:
            raise ValueError("dashboard token must contain at least 16 characters")


def _health(runtime: dict[str, Any] | None, *, now_ms: int) -> dict[str, str]:
    if runtime is None:
        return {"state": "offline", "label": "Bot 离线", "message": "未找到有效运行时心跳。"}
    updated = int(runtime.get("updated_at_ms") or 0)
    stale_after = max(15, int(runtime.get("heartbeat_stale_after_seconds") or 90)) * 1000
    age = now_ms - updated
    if updated <= 0 or age < -30_000:
        return {"state": "offline", "label": "心跳异常", "message": "运行时心跳时间无效。"}
    if age > stale_after:
        return {"state": "stale", "label": "Bot 无响应", "message": "运行时心跳已经过期, 请检查 systemd。"}
    state = str(runtime.get("service_state") or "running")
    if state == "paused":
        message = "新行情分析已暂停; 进行中的任务会安全完成。"
        control_error = str(runtime.get("control_error") or "")
        if control_error == "invalid_control_state":
            message = "控制状态文件无效, Bot 已按 fail-closed 原则暂停。"
        elif control_error in {
            "backend_switch_draining",
            "backend_switch_pending",
        }:
            message = "Backend 正在等待安全边界切换, 新分析已暂停。"
        elif control_error == "backend_unavailable":
            message = "请求的 Backend 尚不可用, 新分析已按 fail-closed 原则暂停。"
        return {"state": "paused", "label": "已暂停", "message": message}
    if state != "running":
        return {"state": "offline", "label": "Bot 已停止", "message": "运行进程正在停止或尚未重新启动。"}
    return {"state": "running", "label": "运行中", "message": ""}


def _select_fields(value: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {field: value[field] for field in fields if field in value}


def _safe_runtime_projection(runtime: dict[str, Any] | None) -> dict[str, Any]:
    if runtime is None:
        return {}
    projected = _select_fields(runtime, _RUNTIME_FIELDS)
    projected["mode"] = _select_fields(runtime.get("mode"), _MODE_FIELDS)
    projected["scheduler"] = _select_fields(
        runtime.get("scheduler"), _SCHEDULER_FIELDS
    )
    projected["assets"] = []
    for item in runtime.get("assets", []):
        if not isinstance(item, dict):
            continue
        asset = _select_fields(item, _ASSET_FIELDS)
        if isinstance(item.get("market_context"), dict):
            asset["market_context"] = _select_fields(
                item["market_context"], _MARKET_CONTEXT_FIELDS
            )
        projected["assets"].append(asset)
    return projected


def _validated_applied_profile(runtime: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(runtime, dict):
        return None
    raw = runtime.get("applied_profile")
    if not isinstance(raw, dict):
        return None
    try:
        return validate_analysis_profile(raw).model_dump(mode="json")
    except (TypeError, ValueError):
        return None


def _profile_projection(control: Any, runtime: dict[str, Any] | None) -> dict[str, Any]:
    desired = control.analysis_profile.model_dump(mode="json")
    applied = _validated_applied_profile(runtime)
    applied_generation = (
        runtime.get("applied_profile_generation") if runtime is not None else None
    )
    applied_fingerprint = (
        runtime.get("applied_profile_fingerprint") if runtime is not None else None
    )
    pending = not (
        applied is not None
        and applied == desired
        and applied_generation == control.profile_generation
        and applied_fingerprint == control.profile_fingerprint
    )
    diff = {
        key: {"applied": applied.get(key) if applied is not None else None, "desired": value}
        for key, value in desired.items()
        if applied is None or applied.get(key) != value
    }
    return {
        "current": desired,
        "current_generation": control.profile_generation,
        "current_fingerprint": control.profile_fingerprint,
        "desired": desired,
        "desired_generation": control.profile_generation,
        "desired_fingerprint": control.profile_fingerprint,
        "applied": applied,
        "applied_generation": applied_generation,
        "applied_fingerprint": applied_fingerprint,
        "pending": pending,
        "pending_profile": desired if pending else None,
        "diff": diff,
    }


def _safe_paper_projection(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    accounts = value.get("accounts", [])
    projected: dict[str, Any] = {
        "accounts": [
            _select_fields(item, _PAPER_ACCOUNT_FIELDS)
            for item in accounts
            if isinstance(item, dict)
        ] if isinstance(accounts, list) else [],
        "research": _select_fields(value.get("research"), _PAPER_RESEARCH_FIELDS),
    }
    if "available" in value:
        projected["available"] = bool(value["available"])
    return projected


def build_dashboard_payload(paths: RuntimePaths, *, now_ms: int | None = None) -> dict[str, Any]:
    current_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    runtime = RuntimeStatusStore(paths.runtime_status_path).read()
    control = RuntimeControlStore(paths.runtime_control_path).read()
    runtime_view = _safe_runtime_projection(runtime)
    recent = [
        _select_fields(item, _RECENT_RUN_FIELDS)
        for item in (runtime or {}).get("recent_runs", [])
        if isinstance(item, dict)
    ]
    paper = _safe_paper_projection((runtime or {}).get("paper"))
    control_view = control_state_to_dict(control)
    profile_view = _profile_projection(control, runtime)
    applied_generation = runtime_view.get("applied_control_generation")
    expected_service_state = "paused" if control.desired_state == "paused" else "running"
    control_view["applied"] = (
        isinstance(applied_generation, int)
        and not isinstance(applied_generation, bool)
        and applied_generation == control.generation
        and control.error is None
        and runtime_view.get("service_state") == expected_service_state
        and str(
            (runtime_view.get("mode") or {}).get("analysis_backend")
            or "deepseek_only"
        )
        == control.desired_backend
        and runtime_view.get("applied_codex_fast_mode", False) == control.codex_fast_mode
        and not profile_view["pending"]
    )
    return {
        "schema_version": 1,
        "generated_at_ms": current_ms,
        "health": _health(runtime, now_ms=current_ms),
        "runtime": runtime_view,
        "control": control_view,
        "capabilities": analysis_capabilities(),
        "profile": profile_view,
        "recent_runs": recent,
        "analysis_summary": [
            _select_fields(item, _ANALYSIS_SUMMARY_FIELDS)
            for item in (runtime or {}).get("analysis_summary", [])
            if isinstance(item, dict)
        ],
        "backend_experiments": _select_fields(
            (runtime or {}).get("backend_experiments"),
            _BACKEND_EXPERIMENT_FIELDS,
        ),
        "paper": paper,
        "codex": _select_fields((runtime or {}).get("codex"), _CODEX_FIELDS),
    }


def _run_once_ready(
    control: Any,
    runtime: dict[str, Any],
    *,
    now_ms: int,
) -> bool:
    health = _health(runtime, now_ms=now_ms)
    scheduler = runtime.get("scheduler")
    assets = runtime.get("assets")
    if not isinstance(scheduler, dict) or not isinstance(assets, list):
        return False
    running = scheduler.get("running", 0)
    queued = scheduler.get("queued", 0)
    if (
        isinstance(running, bool)
        or not isinstance(running, int)
        or isinstance(queued, bool)
        or not isinstance(queued, int)
    ):
        return False
    if any(not isinstance(asset, dict) for asset in assets):
        return False
    enabled_assets = [asset for asset in assets if asset.get("analysis_enabled", True) is not False]
    enabled_symbols = [str(asset.get("symbol") or "").upper() for asset in enabled_assets]
    if sorted(enabled_symbols) != sorted(control.analysis_profile.enabled_symbols):
        return False
    if any(
        str(asset.get("source") or "").lower() != "binance_usdm"
        or str(asset.get("venue") or "").upper() != "BINANCE"
        or str(asset.get("timeframe") or "").lower() != "1h"
        for asset in enabled_assets
    ):
        return False
    max_concurrency = scheduler.get("max_concurrency")
    if (
        isinstance(max_concurrency, bool)
        or not isinstance(max_concurrency, int)
        or max_concurrency != control.analysis_profile.max_concurrency
    ):
        return False
    consumed_values = [
        runtime[key]
        for key in ("consumed_run_once_generation", "run_once_consumed_generation")
        if key in runtime
    ]
    prior_generation_consumed = bool(consumed_values) and all(
        value == control.run_once_generation for value in consumed_values
    )
    return bool(
        control.desired_state == "paused"
        and runtime.get("service_state") == "paused"
        and health["state"] == "paused"
        and runtime.get("applied_control_generation") == control.generation
        and runtime.get("applied_profile_generation") == control.profile_generation
        and runtime.get("applied_profile_fingerprint") == control.profile_fingerprint
        and _validated_applied_profile(runtime)
        == control.analysis_profile.model_dump(mode="json")
        and str((runtime.get("mode") or {}).get("analysis_backend") or "")
        == control.desired_backend
        and runtime.get("applied_codex_fast_mode", False) == control.codex_fast_mode
        and running == 0
        and queued == 0
        and not bool(scheduler.get("closed"))
        and not bool(scheduler.get("error"))
        and not any(bool(asset.get("in_flight")) for asset in assets)
        and prior_generation_consumed
    )


def _basic_password(header: str) -> str | None:
    if not header.startswith("Basic "):
        return None
    try:
        decoded = base64.b64decode(header[6:], validate=True).decode("utf-8")
        _username, password = decoded.split(":", 1)
        return password
    except (ValueError, UnicodeError):
        return None


def create_server(config: DashboardConfig, paths: RuntimePaths) -> ThreadingHTTPServer:
    control_store = RuntimeControlStore(paths.runtime_control_path)
    sessions: dict[str, float] = {}
    auth_lock = threading.Lock()
    failed_logins: list[float] = []

    class Handler(BaseHTTPRequestHandler):
        server_version = "PAAgentDashboard/1"

        def _headers(self, status: int, content_type: str, extra: dict[str, str] | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'unsafe-inline'; "
                "style-src 'unsafe-inline'; frame-ancestors 'none'; form-action 'self'",
            )
            for name, value in (extra or {}).items():
                self.send_header(name, value)
            self.end_headers()

        def _json(self, status: int, payload: dict[str, Any], extra: dict[str, str] | None = None) -> None:
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
            self._headers(status, "application/json; charset=utf-8", extra)
            self.wfile.write(body)

        def _session_id(self) -> str | None:
            raw = self.headers.get("Cookie", "")
            if len(raw) > _MAX_BODY_BYTES:
                return None
            try:
                cookie = SimpleCookie(raw)
            except CookieError:
                return None
            value = cookie.get(_SESSION_COOKIE)
            return value.value if value else None

        def _has_auth(self) -> bool:
            session_id = self._session_id()
            with auth_lock:
                now = time.monotonic()
                for key, expiry in list(sessions.items()):
                    if expiry <= now:
                        del sessions[key]
                if session_id in sessions:
                    return True
            supplied = _basic_password(self.headers.get("Authorization", ""))
            return supplied is not None and hmac.compare_digest(
                supplied.encode("utf-8"), config.token.encode("utf-8")
            )

        def _authorized(self) -> bool:
            if self._has_auth():
                return True
            self._json(401, {"error": "authentication_required"})
            return False

        def _valid_host(self) -> bool:
            if self.headers.get("Host") in {
                f"127.0.0.1:{self.server.server_port}",
                f"localhost:{self.server.server_port}",
            }:
                return True
            self._json(403, {"error": "host_mismatch"})
            return False

        def do_GET(self) -> None:
            if not self._valid_host():
                return
            path = urlsplit(self.path).path
            if path == "/healthz":
                health = _health(
                    RuntimeStatusStore(paths.runtime_status_path).read(),
                    now_ms=int(time.time() * 1000),
                )
                self._json(200 if health["state"] in {"running", "paused"} else 503, health)
                return
            if path in {"/", "/login"}:
                body = (DASHBOARD_HTML if path == "/" and self._has_auth() else LOGIN_HTML).encode("utf-8")
                self._headers(200, "text/html; charset=utf-8")
                self.wfile.write(body)
                return
            if not self._authorized():
                return
            if path == "/api/status":
                self._json(200, build_dashboard_payload(paths))
                return
            self._json(404, {"error": "not_found"})

        def do_POST(self) -> None:
            if not self._valid_host():
                return
            path = urlsplit(self.path).path
            if path != "/api/login" and not self._authorized():
                return
            if path not in {"/api/login", "/api/logout", "/api/control", "/api/profile", "/api/run-once", "/api/fast-mode"}:
                self._json(404, {"error": "not_found"})
                return
            if self.headers.get("X-PA-Agent-Control") != "1":
                self._json(403, {"error": "control_header_required"})
                return
            origin = self.headers.get("Origin")
            if origin and origin != f"http://{self.headers.get('Host')}":
                self._json(403, {"error": "origin_mismatch"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = -1
            if not 1 <= length <= _MAX_BODY_BYTES:
                self._json(400, {"error": "invalid_body_size"})
                return
            try:
                body = json.loads(self.rfile.read(length))
            except (UnicodeError, json.JSONDecodeError):
                self._json(400, {"error": "invalid_json"})
                return
            if not isinstance(body, dict):
                self._json(400, {"error": "invalid_json"})
                return
            if path == "/api/login":
                self._login(body)
                return
            if path == "/api/logout":
                if body:
                    self._json(400, {"error": "invalid_logout_request"})
                    return
                with auth_lock:
                    sessions.pop(self._session_id(), None)
                self._json(200, {"ok": True}, {"Set-Cookie": f"{_SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"})
                return
            if path == "/api/profile":
                self._profile(body)
                return
            if path == "/api/fast-mode":
                self._fast_mode(body)
                return
            if path == "/api/run-once":
                self._run_once(body)
                return
            action = body.get("action") if isinstance(body, dict) else None
            if action not in {"pause", "resume", "set_backend"}:
                self._json(400, {"error": "unsupported_action"})
                return
            if action == "set_backend":
                backend = body.get("backend")
                if backend not in {"deepseek_only", "auto", "codex_only"}:
                    self._json(400, {"error": "unsupported_backend"})
                    return
                runtime = RuntimeStatusStore(paths.runtime_status_path).read()
                if (
                    backend in {"deepseek_only", "auto"}
                    and isinstance(runtime, dict)
                    and (runtime.get("mode") or {}).get("direct_available") is False
                ):
                    self._json(409, {"error": "direct_backend_unavailable"})
                    return
                state = control_store.set_desired_backend(
                    backend,
                    actor="dashboard",
                )
            else:
                state = control_store.set_desired_state(
                    "paused" if action == "pause" else "running",
                    actor="dashboard",
                )
            self._json(
                202,
                {"ok": True, "applied": False, "control": control_state_to_dict(state)},
            )

        def _login(self, body: dict[str, Any]) -> None:
            if set(body) != {"password"} or not isinstance(body["password"], str):
                self._json(400, {"error": "invalid_login_request"})
                return
            try:
                supplied = body["password"].encode("utf-8")
            except UnicodeError:
                self._json(400, {"error": "invalid_login_request"})
                return
            with auth_lock:
                now = time.monotonic()
                failed_logins[:] = [stamp for stamp in failed_logins if stamp > now - 60]
                if len(failed_logins) >= 10:
                    self._json(429, {"error": "login_rate_limited"}, {"Retry-After": "60"})
                    return
                if not hmac.compare_digest(supplied, config.token.encode("utf-8")):
                    failed_logins.append(now)
                    self._json(401, {"error": "invalid_password"})
                    return
                sessions.pop(self._session_id(), None)
                for key, expiry in list(sessions.items()):
                    if expiry <= now:
                        del sessions[key]
                if len(sessions) >= 32:
                    del sessions[min(sessions, key=sessions.__getitem__)]
                session_id = secrets.token_urlsafe(32)
                sessions[session_id] = now + _SESSION_SECONDS
            self._json(200, {"ok": True}, {"Set-Cookie": f"{_SESSION_COOKIE}={session_id}; Path=/; HttpOnly; SameSite=Strict; Max-Age={_SESSION_SECONDS}"})

        def _profile(self, body: dict[str, Any]) -> None:
            if set(body) != {"expected_generation", "profile"}:
                self._json(400, {"error": "invalid_profile_request"})
                return
            expected = body.get("expected_generation")
            profile = body.get("profile")
            if (
                isinstance(expected, bool)
                or not isinstance(expected, int)
                or not isinstance(profile, dict)
            ):
                self._json(400, {"error": "invalid_profile_request"})
                return
            current = control_store.read()
            if current.error:
                self._json(503, {"error": "control_state_unavailable"})
                return
            try:
                validated = validate_analysis_profile(profile)
            except (TypeError, ValueError):
                self._json(400, {"error": "invalid_profile_request"})
                return
            runtime = RuntimeStatusStore(paths.runtime_status_path).read()
            if (
                runtime is None
                or _health(runtime, now_ms=int(time.time() * 1000))["state"]
                not in {"running", "paused"}
            ):
                self._json(503, {"error": "runtime_state_unavailable"})
                return
            try:
                state = control_store.set_analysis_profile(
                    validated,
                    expected_generation=expected,
                    actor="dashboard",
                )
            except RuntimeControlConflict:
                self._json(409, {"error": "generation_conflict"})
                return
            except OSError:
                self._json(503, {"error": "control_state_unavailable"})
                return
            self._json(
                202,
                {"ok": True, "applied": False, "control": control_state_to_dict(state)},
            )

        def _fast_mode(self, body: dict[str, Any]) -> None:
            if set(body) != {"expected_generation", "enabled"}:
                self._json(400, {"error": "invalid_fast_mode_request"})
                return
            expected, enabled = body["expected_generation"], body["enabled"]
            if isinstance(expected, bool) or not isinstance(expected, int) or not isinstance(enabled, bool):
                self._json(400, {"error": "invalid_fast_mode_request"})
                return
            runtime = RuntimeStatusStore(paths.runtime_status_path).read()
            if runtime is None or _health(runtime, now_ms=int(time.time() * 1000))["state"] not in {"running", "paused"}:
                self._json(503, {"error": "runtime_state_unavailable"})
                return
            try:
                state = control_store.set_codex_fast_mode(
                    enabled, expected_generation=expected, actor="dashboard"
                )
            except RuntimeControlConflict:
                self._json(409, {"error": "generation_conflict"})
                return
            except OSError:
                self._json(503, {"error": "control_state_unavailable"})
                return
            self._json(202, {"ok": True, "applied": False, "control": control_state_to_dict(state)})

        def _run_once(self, body: dict[str, Any]) -> None:
            if set(body) != {"expected_generation"}:
                self._json(400, {"error": "invalid_run_once_request"})
                return
            expected = body.get("expected_generation")
            if isinstance(expected, bool) or not isinstance(expected, int):
                self._json(400, {"error": "invalid_run_once_request"})
                return
            current = control_store.read()
            if current.error:
                self._json(503, {"error": "control_state_unavailable"})
                return
            if current.generation != expected:
                self._json(409, {"error": "generation_conflict"})
                return
            runtime = RuntimeStatusStore(paths.runtime_status_path).read()
            if runtime is None:
                self._json(503, {"error": "runtime_state_unavailable"})
                return
            if not _run_once_ready(current, runtime, now_ms=int(time.time() * 1000)):
                self._json(409, {"error": "run_once_not_ready"})
                return
            try:
                state = control_store.request_run_once(
                    expected_generation=expected,
                    actor="dashboard",
                )
            except RuntimeControlConflict:
                self._json(409, {"error": "generation_conflict"})
                return
            except OSError:
                self._json(503, {"error": "control_state_unavailable"})
                return
            self._json(
                202,
                {"ok": True, "applied": False, "control": control_state_to_dict(state)},
            )

        def log_message(self, _format: str, *_args: Any) -> None:
            return

    return ThreadingHTTPServer((config.host, config.port), Handler)


def _read_token(path: Path) -> str:
    try:
        token = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"cannot read dashboard token file {path}: {exc}") from exc
    if len(token) < 16:
        raise ValueError("dashboard token must contain at least 16 characters")
    return token


def _token_path(explicit: Path | None) -> Path | None:
    if explicit is not None:
        return explicit
    env_path = str(os.environ.get("PA_AGENT_DASHBOARD_TOKEN_FILE") or "").strip()
    if env_path:
        return Path(env_path).expanduser()
    credentials = str(os.environ.get("CREDENTIALS_DIRECTORY") or "").strip()
    if credentials:
        candidate = Path(credentials) / "pa_agent_dashboard_token"
        if candidate.is_file():
            return candidate
    return None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PA Agent authenticated VPS dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8765, type=int)
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--operation-dir", type=Path)
    parser.add_argument("--token-file", type=Path)
    parser.add_argument("--check", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(list(argv) if argv is not None else None)
    environ = dict(os.environ)
    if args.state_dir is not None:
        environ["PA_AGENT_HOME"] = str(args.state_dir)
    if args.operation_dir is not None:
        environ["PA_AGENT_OPERATION_DIR"] = str(args.operation_dir)
    paths = resolve_runtime_paths(environ=environ)
    token_path = _token_path(args.token_file)
    if token_path is None:
        print("Dashboard configuration error: token file is required", file=sys.stderr)
        return 2
    try:
        config = DashboardConfig(host=args.host, port=args.port, token=_read_token(token_path))
    except ValueError as exc:
        print(f"Dashboard configuration error: {exc}", file=sys.stderr)
        return 2
    if args.check:
        paths.runtime_dir.mkdir(parents=True, exist_ok=True)
        print(f"Dashboard configuration OK: http://{config.host}:{config.port}")
        return 0
    server = create_server(config, paths)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        return 130
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
