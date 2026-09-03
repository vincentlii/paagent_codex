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
from pa_agent.dashboard_page import DASHBOARD_HTML
from pa_agent.operations.analysis_profile import AnalysisProfile, profile_fingerprint
from pa_agent.operations.runtime_state import RuntimeControlStore, RuntimeStatusStore


def _paths(tmp_path):
    return resolve_runtime_paths(
        project_root=tmp_path,
        environ={"PA_AGENT_HOME": str(tmp_path / "state")},
    )


def _authorization(token: str) -> str:
    encoded = base64.b64encode(f"admin:{token}".encode()).decode()
    return f"Basic {encoded}"


def _request(connection, method: str, path: str, body=None, *, origin=None):
    headers = {
        "Authorization": _authorization("a-secure-test-token"),
        "Content-Type": "application/json",
        "X-PA-Agent-Control": "1",
    }
    if origin is not None:
        headers["Origin"] = origin
    connection.request(method, path, body=body, headers=headers)
    response = connection.getresponse()
    payload = json.loads(response.read())
    return response, payload


def _running_server(paths):
    server = create_server(
        DashboardConfig(host="127.0.0.1", port=0, token="a-secure-test-token"),
        paths,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


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
            "applied_profile": AnalysisProfile().model_dump(mode="json"),
            "applied_profile_generation": 0,
            "applied_profile_fingerprint": profile_fingerprint(AnalysisProfile()),
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


def test_dashboard_payload_projects_catalog_and_truthful_profile_state(tmp_path) -> None:
    paths = _paths(tmp_path)
    control_store = RuntimeControlStore(paths.runtime_control_path, now_ms=lambda: 9_000)
    paused = control_store.set_desired_state("paused", actor="dashboard")
    desired = control_store.set_analysis_profile(
        AnalysisProfile(deepseek_model="deepseek-v4-pro"),
        expected_generation=paused.generation,
        actor="dashboard",
    )
    RuntimeStatusStore(paths.runtime_status_path, now_ms=lambda: 10_000).write(
        {
            "service_state": "paused",
            "applied_control_generation": paused.generation,
            "applied_profile": AnalysisProfile().model_dump(mode="json"),
            "applied_profile_generation": 0,
            "applied_profile_fingerprint": profile_fingerprint(AnalysisProfile()),
            "mode": {"analysis_backend": "deepseek_only", "vpa": "shadow"},
            "scheduler": {"running": 0, "queued": 0, "closed": False},
            "assets": [],
            "environment": {"HOME": "/secret/path"},
        }
    )

    payload = build_dashboard_payload(paths, now_ms=10_500)

    assert payload["capabilities"]["deepseek_models"][0]["id"] == "deepseek-v4-flash"
    assert payload["profile"]["desired"] == desired.analysis_profile.model_dump(mode="json")
    assert payload["profile"]["applied"] == AnalysisProfile().model_dump(mode="json")
    assert payload["profile"]["pending"] is True
    assert payload["profile"]["diff"]["deepseek_model"] == {
        "applied": "deepseek-v4-flash",
        "desired": "deepseek-v4-pro",
    }
    assert payload["control"]["applied"] is False
    assert "environment" not in payload["runtime"]
    encoded = json.dumps(payload).lower()
    assert "raw_prompt" not in encoded
    assert "raw_reasoning" not in encoded
    assert "/secret/path" not in encoded


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


def test_dashboard_accepts_only_bounded_backend_control(tmp_path) -> None:
    paths = _paths(tmp_path)
    RuntimeStatusStore(paths.runtime_status_path).write(
        {
            "service_state": "running",
            "applied_control_generation": 0,
            "applied_profile": AnalysisProfile().model_dump(mode="json"),
            "applied_profile_generation": 0,
            "applied_profile_fingerprint": profile_fingerprint(AnalysisProfile()),
            "mode": {"analysis_backend": "deepseek_only"},
            "assets": [],
            "scheduler": {},
        }
    )
    server = create_server(
        DashboardConfig(host="127.0.0.1", port=0, token="a-secure-test-token"),
        paths,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = http.client.HTTPConnection(*server.server_address, timeout=5)
        body = json.dumps({"action": "set_backend", "backend": "auto"})
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
        assert connection.getresponse().status == 202
        state = RuntimeControlStore(paths.runtime_control_path).read()
        assert state.desired_backend == "auto"

        invalid = json.dumps({"action": "set_backend", "backend": "custom-model"})
        connection.request(
            "POST",
            "/api/control",
            body=invalid,
            headers={
                "Authorization": _authorization("a-secure-test-token"),
                "Content-Type": "application/json",
                "X-PA-Agent-Control": "1",
            },
        )
        assert connection.getresponse().status == 400
        assert RuntimeControlStore(paths.runtime_control_path).read().desired_backend == "auto"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_profile_endpoint_accepts_strict_profile_and_uses_cas(tmp_path) -> None:
    paths = _paths(tmp_path)
    RuntimeStatusStore(paths.runtime_status_path).write(
        {"service_state": "running", "assets": [], "scheduler": {}}
    )
    server, thread = _running_server(paths)
    try:
        connection = http.client.HTTPConnection(*server.server_address, timeout=5)
        profile = AnalysisProfile(deepseek_model="deepseek-v4-pro").model_dump(mode="json")
        response, payload = _request(
            connection,
            "POST",
            "/api/profile",
            json.dumps({"expected_generation": 0, "profile": profile}),
        )
        assert response.status == 202
        assert payload["control"]["analysis_profile"]["deepseek_model"] == "deepseek-v4-pro"

        response, payload = _request(
            connection,
            "POST",
            "/api/profile",
            json.dumps({"expected_generation": 0, "profile": profile}),
        )
        assert response.status == 409
        assert payload == {"error": "generation_conflict"}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize(
    "body",
    [
        {"expected_generation": 0, "profile": {}, "extra": True},
        {"expected_generation": True, "profile": {}},
        {"expected_generation": 0, "profile": {"api_key": "secret"}},
        {"expected_generation": 0, "profile": {"codex_model": "custom-model"}},
        {
            "expected_generation": 0,
            "profile": {
                "codex_model": "gpt-5.4-mini",
                "codex_reasoning_effort": "max",
            },
        },
    ],
)
def test_profile_endpoint_rejects_unknown_or_invalid_fields(tmp_path, body) -> None:
    paths = _paths(tmp_path)
    server, thread = _running_server(paths)
    try:
        connection = http.client.HTTPConnection(*server.server_address, timeout=5)
        response, payload = _request(
            connection, "POST", "/api/profile", json.dumps(body)
        )
        assert response.status == 400
        assert payload == {"error": "invalid_profile_request"}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_profile_endpoint_reports_unavailable_control_state(tmp_path) -> None:
    paths = _paths(tmp_path)
    paths.runtime_control_path.parent.mkdir(parents=True)
    paths.runtime_control_path.write_text("{broken", encoding="utf-8")
    server, thread = _running_server(paths)
    try:
        connection = http.client.HTTPConnection(*server.server_address, timeout=5)
        response, payload = _request(
            connection,
            "POST",
            "/api/profile",
            json.dumps({"expected_generation": 0, "profile": {}}),
        )
        assert response.status == 503
        assert payload == {"error": "control_state_unavailable"}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_profile_endpoint_reports_unavailable_runtime_state(tmp_path) -> None:
    paths = _paths(tmp_path)
    server, thread = _running_server(paths)
    try:
        connection = http.client.HTTPConnection(*server.server_address, timeout=5)
        response, payload = _request(
            connection,
            "POST",
            "/api/profile",
            json.dumps({"expected_generation": 0, "profile": {}}),
        )
        assert response.status == 503
        assert payload == {"error": "runtime_state_unavailable"}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _publish_run_once_ready(paths, control) -> None:
    RuntimeStatusStore(paths.runtime_status_path).write(
        {
            "service_state": "paused",
            "heartbeat_stale_after_seconds": 90,
            "applied_control_generation": control.generation,
            "applied_profile": control.analysis_profile.model_dump(mode="json"),
            "applied_profile_generation": control.profile_generation,
            "applied_profile_fingerprint": control.profile_fingerprint,
            "consumed_run_once_generation": control.run_once_generation,
            "mode": {"analysis_backend": control.desired_backend, "vpa": "shadow"},
            "scheduler": {
                "running": 0,
                "queued": 0,
                "closed": False,
                "error": None,
                "max_concurrency": 1,
            },
            "assets": [
                {
                    "source": "binance_usdm",
                    "venue": "BINANCE",
                    "symbol": "XAUUSDT",
                    "timeframe": "1h",
                    "in_flight": False,
                }
            ],
        }
    )


def test_run_once_endpoint_accepts_only_a_fully_applied_idle_boundary(tmp_path) -> None:
    paths = _paths(tmp_path)
    control = RuntimeControlStore(paths.runtime_control_path).set_desired_state(
        "paused", actor="dashboard"
    )
    _publish_run_once_ready(paths, control)
    server, thread = _running_server(paths)
    try:
        connection = http.client.HTTPConnection(*server.server_address, timeout=5)
        response, payload = _request(
            connection,
            "POST",
            "/api/run-once",
            json.dumps({"expected_generation": control.generation}),
        )
        assert response.status == 202
        assert payload["control"]["run_once_generation"] == 1
        assert payload["control"]["desired_state"] == "paused"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize(
    ("mutation", "expected_error"),
    [
        (
            lambda status, control: status.update(service_state="running"),
            "run_once_not_ready",
        ),
        (
            lambda status, control: status["scheduler"].update(running=1),
            "run_once_not_ready",
        ),
        (
            lambda status, control: status["scheduler"].update(running="invalid"),
            "run_once_not_ready",
        ),
        (
            lambda status, control: status["assets"][0].update(in_flight=True),
            "run_once_not_ready",
        ),
        (
            lambda status, control: status["assets"].append(
                dict(status["assets"][0])
            ),
            "run_once_not_ready",
        ),
        (
            lambda status, control: status["assets"][0].update(symbol="BTCUSDT"),
            "run_once_not_ready",
        ),
        (
            lambda status, control: status["scheduler"].update(max_concurrency=2),
            "run_once_not_ready",
        ),
        (
            lambda status, control: status.update(applied_control_generation=0),
            "run_once_not_ready",
        ),
        (
            lambda status, control: status.update(consumed_run_once_generation=-1),
            "run_once_not_ready",
        ),
    ],
)
def test_run_once_endpoint_rejects_unsafe_boundaries(
    tmp_path, mutation, expected_error
) -> None:
    paths = _paths(tmp_path)
    control = RuntimeControlStore(paths.runtime_control_path).set_desired_state(
        "paused", actor="dashboard"
    )
    _publish_run_once_ready(paths, control)
    status = RuntimeStatusStore(paths.runtime_status_path).read()
    mutation(status, control)
    RuntimeStatusStore(paths.runtime_status_path).write(
        {
            key: value
            for key, value in status.items()
            if key not in {"schema_version", "updated_at_ms"}
        }
    )
    server, thread = _running_server(paths)
    try:
        connection = http.client.HTTPConnection(*server.server_address, timeout=5)
        response, payload = _request(
            connection,
            "POST",
            "/api/run-once",
            json.dumps({"expected_generation": control.generation}),
        )
        assert response.status == 409
        assert payload == {"error": expected_error}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_run_once_endpoint_reports_runtime_unavailable(tmp_path) -> None:
    paths = _paths(tmp_path)
    control = RuntimeControlStore(paths.runtime_control_path).set_desired_state(
        "paused", actor="dashboard"
    )
    server, thread = _running_server(paths)
    try:
        connection = http.client.HTTPConnection(*server.server_address, timeout=5)
        response, payload = _request(
            connection,
            "POST",
            "/api/run-once",
            json.dumps({"expected_generation": control.generation}),
        )
        assert response.status == 503
        assert payload == {"error": "runtime_state_unavailable"}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_new_writes_preserve_origin_header_and_body_size_guards(tmp_path) -> None:
    paths = _paths(tmp_path)
    server, thread = _running_server(paths)
    try:
        connection = http.client.HTTPConnection(*server.server_address, timeout=5)
        response, payload = _request(
            connection,
            "POST",
            "/api/profile",
            json.dumps({"expected_generation": 0, "profile": {}}),
            origin="http://attacker.invalid",
        )
        assert response.status == 403
        assert payload == {"error": "origin_mismatch"}

        oversized = json.dumps({"expected_generation": 0, "profile": {}}) + " " * 4096
        response, payload = _request(
            connection, "POST", "/api/profile", oversized
        )
        assert response.status == 400
        assert payload == {"error": "invalid_body_size"}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_dashboard_responses_preserve_browser_security_headers(tmp_path) -> None:
    paths = _paths(tmp_path)
    server, thread = _running_server(paths)
    try:
        connection = http.client.HTTPConnection(*server.server_address, timeout=5)
        connection.request(
            "GET",
            "/api/status",
            headers={"Authorization": _authorization("a-secure-test-token")},
        )
        response = connection.getresponse()
        response.read()
        assert response.status == 200
        assert response.getheader("Cache-Control") == "no-store"
        assert response.getheader("X-Content-Type-Options") == "nosniff"
        assert response.getheader("X-Frame-Options") == "DENY"
        assert "default-src 'self'" in response.getheader("Content-Security-Policy")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_dashboard_page_has_fixed_scope_profile_controls_and_safe_rendering() -> None:
    required = (
        "Binance USD-M",
        "XAUUSDT",
        "1h",
        "HTF 4h / 60",
        "VPA Shadow",
        'id="deepseek-model"',
        'id="deepseek-thinking"',
        'id="deepseek-effort"',
        'id="codex-model"',
        'id="codex-effort"',
        'id="profile-diff"',
        'id="apply-profile"',
        'id="run-once"',
        "Requested / Effective Model",
        "Total Latency",
        "Repairs",
        "Outcome",
    )
    for marker in required:
        assert marker in DASHBOARD_HTML
    forbidden = (
        "innerHTML",
        "raw_reasoning",
        "raw_prompt",
        "api_key",
        "base_url",
        'type="text"',
        "VPA Off",
        "HTF Off",
        "alert(",
    )
    for marker in forbidden:
        assert marker not in DASHBOARD_HTML
    assert ".textContent=" in DASHBOARD_HTML
    assert "editingGeneration" in DASHBOARD_HTML
    assert "expected_generation:editingGeneration" in DASHBOARD_HTML
    assert "Profile changed elsewhere" in DASHBOARD_HTML
    assert "not published" not in DASHBOARD_HTML
