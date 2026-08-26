"""Authenticated loopback dashboard for credential-free PA Agent operations."""

from __future__ import annotations

import argparse
import base64
import hmac
import json
import os
import sys
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pa_agent.config.paths import RuntimePaths, resolve_runtime_paths
from pa_agent.dashboard_page import DASHBOARD_HTML
from pa_agent.operations.runtime_state import RuntimeControlStore, RuntimeStatusStore

_MAX_BODY_BYTES = 4096


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
        if runtime.get("control_error"):
            message = "控制状态文件无效, Bot 已按 fail-closed 原则暂停。"
        return {"state": "paused", "label": "已暂停", "message": message}
    if state != "running":
        return {"state": "offline", "label": "Bot 已停止", "message": "运行进程正在停止或尚未重新启动。"}
    return {"state": "running", "label": "运行中", "message": ""}


def build_dashboard_payload(paths: RuntimePaths, *, now_ms: int | None = None) -> dict[str, Any]:
    current_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    runtime = RuntimeStatusStore(paths.runtime_status_path).read()
    control = RuntimeControlStore(paths.runtime_control_path).read()
    runtime_view = dict(runtime or {})
    recent = runtime_view.pop("recent_runs", [])
    paper = runtime_view.pop("paper", {})
    control_view = asdict(control)
    applied_generation = runtime_view.get("applied_control_generation")
    expected_service_state = "paused" if control.desired_state == "paused" else "running"
    control_view["applied"] = (
        isinstance(applied_generation, int)
        and not isinstance(applied_generation, bool)
        and applied_generation >= control.generation
        and runtime_view.get("service_state") == expected_service_state
    )
    return {
        "schema_version": 1,
        "generated_at_ms": current_ms,
        "health": _health(runtime, now_ms=current_ms),
        "runtime": runtime_view,
        "control": control_view,
        "recent_runs": recent,
        "paper": paper,
    }


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

    class Handler(BaseHTTPRequestHandler):
        server_version = "PAAgentDashboard/1"

        def _headers(self, status: int, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; frame-ancestors 'none'")
            self.end_headers()

        def _json(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
            self._headers(status, "application/json; charset=utf-8")
            self.wfile.write(body)

        def _authorized(self) -> bool:
            supplied = _basic_password(self.headers.get("Authorization", ""))
            if supplied is not None and hmac.compare_digest(supplied, config.token):
                return True
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="PA Agent"')
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return False

        def do_GET(self) -> None:
            path = urlsplit(self.path).path
            if path == "/healthz":
                health = _health(RuntimeStatusStore(paths.runtime_status_path).read(), now_ms=int(time.time() * 1000))
                self._json(200 if health["state"] in {"running", "paused"} else 503, health)
                return
            if not self._authorized():
                return
            if path == "/":
                body = DASHBOARD_HTML.encode("utf-8")
                self._headers(200, "text/html; charset=utf-8")
                self.wfile.write(body)
                return
            if path == "/api/status":
                self._json(200, build_dashboard_payload(paths))
                return
            self._json(404, {"error": "not_found"})

        def do_POST(self) -> None:
            if not self._authorized():
                return
            if urlsplit(self.path).path != "/api/control":
                self._json(404, {"error": "not_found"})
                return
            if self.headers.get("X-PA-Agent-Control") != "1":
                self._json(403, {"error": "control_header_required"})
                return
            origin = self.headers.get("Origin")
            if origin and urlsplit(origin).netloc != self.headers.get("Host"):
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
            action = body.get("action") if isinstance(body, dict) else None
            if action not in {"pause", "resume"}:
                self._json(400, {"error": "unsupported_action"})
                return
            state = control_store.set_desired_state(
                "paused" if action == "pause" else "running",
                actor="dashboard",
            )
            self._json(202, {"ok": True, "applied": False, "control": asdict(state)})

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
