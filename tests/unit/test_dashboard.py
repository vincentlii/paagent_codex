from __future__ import annotations

import base64
import http.client
import json
import subprocess
import sys
import threading

import pytest

from pa_agent.config.paths import resolve_runtime_paths
from pa_agent.dashboard import DashboardConfig, build_dashboard_payload, create_server
from pa_agent.operations.runtime_state import RuntimeControlStore, RuntimeStatusStore


def _paths(tmp_path):
    return resolve_runtime_paths(
        project_root=tmp_path,
        environ={"PA_AGENT_HOME": str(tmp_path / "state")},
    )


def _authorization(token: str) -> str:
    encoded = base64.b64encode(f"admin:{token}".encode()).decode()
    return f"Basic {encoded}"


def test_dashboard_import_does_not_load_qt() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            (
                "import sys; import pa_agent.dashboard; "
                "print(any(name.startswith('PyQt6') for name in sys.modules))"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.stdout.strip() == "False"


def test_dashboard_rejects_non_loopback_bind() -> None:
    with pytest.raises(ValueError, match="loopback"):
        DashboardConfig(host="0.0.0.0", token="a-secure-test-token")


def test_dashboard_payload_is_credential_free_and_reports_health(tmp_path) -> None:
    paths = _paths(tmp_path)
    RuntimeStatusStore(paths.runtime_status_path, now_ms=lambda: 10_000).write(
        {
            "service_state": "running",
            "started_at_ms": 1_000,
            "applied_control_generation": 0,
            "heartbeat_stale_after_seconds": 90,
            "mode": {"vpa": "shadow", "paper_trading": True},
            "scheduler": {"running": 1, "queued": 0},
            "assets": [{"symbol": "XAUUSDT", "timeframe": "1h", "in_flight": True}],
            "recent_runs": [{"run_id": "run-1", "symbol": "XAUUSDT"}],
            "paper": {"available": False, "accounts": []},
        }
    )

    payload = build_dashboard_payload(paths, now_ms=10_500)

    assert payload["health"]["state"] == "running"
    assert payload["runtime"]["assets"][0]["symbol"] == "XAUUSDT"
    assert payload["recent_runs"][0]["run_id"] == "run-1"
    assert payload["control"]["applied"] is True
    assert "api_key" not in json.dumps(payload).lower()


def test_dashboard_requires_auth_and_accepts_audited_pause(tmp_path) -> None:
    paths = _paths(tmp_path)
    RuntimeStatusStore(paths.runtime_status_path).write(
        {"service_state": "running", "assets": [], "scheduler": {}}
    )
    server = create_server(
        DashboardConfig(host="127.0.0.1", port=0, token="a-secure-test-token"),
        paths,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = http.client.HTTPConnection(*server.server_address, timeout=5)
        connection.request("GET", "/api/status")
        assert connection.getresponse().status == 401

        body = json.dumps({"action": "pause"})
        connection.request(
            "POST",
            "/api/control",
            body=body,
            headers={
                "Authorization": _authorization("a-secure-test-token"),
                "Content-Type": "application/json",
                "X-PA-Agent-Control": "1",
            },
        )
        response = connection.getresponse()
        assert response.status == 202
        assert RuntimeControlStore(paths.runtime_control_path).read().desired_state == "paused"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
