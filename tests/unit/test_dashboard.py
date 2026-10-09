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
from pa_agent.operations.runtime_state import (
    RuntimeControlStore,
    RuntimeStatusStore,
    control_state_to_dict,
)


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


def test_browser_password_login_works_without_basic_auth_dialog(tmp_path) -> None:
    paths = _paths(tmp_path)
    server, thread = _running_server(paths)
    try:
        connection = http.client.HTTPConnection(*server.server_address, timeout=5)
        connection.request("GET", "/")
        response = connection.getresponse()
        page = response.read().decode()
        assert response.status == 200
        assert response.getheader("WWW-Authenticate") is None
        assert 'autocomplete="current-password"' in page
        assert "a-secure-test-token" not in page

        connection.request("GET", "/api/status")
        response = connection.getresponse()
        response.read()
        assert response.status == 401
        assert response.getheader("WWW-Authenticate") is None

        connection.request(
            "POST", "/api/login", json.dumps({"password": "a-secure-test-token"}),
            headers={"Content-Type": "application/json", "X-PA-Agent-Control": "1"},
        )
        response = connection.getresponse()
        assert response.status == 200
        assert json.loads(response.read()) == {"ok": True}
        cookie = response.getheader("Set-Cookie")
        assert "HttpOnly" in cookie and "SameSite=Strict" in cookie
        assert "a-secure-test-token" not in cookie
        headers = {"Cookie": cookie.split(";", 1)[0], "X-PA-Agent-Control": "1"}

        connection.request("GET", "/", headers=headers)
        response = connection.getresponse()
        assert response.status == 200
        assert "Operations Terminal" in response.read().decode()
        connection.request("GET", "/api/status", headers=headers)
        response = connection.getresponse()
        assert response.status == 200
        response.read()
        connection.request("POST", "/api/control", json.dumps({"action": "pause"}), headers={**headers, "Origin": "http://attacker.invalid"})
        response = connection.getresponse()
        response.read()
        assert response.status == 403
        connection.request("POST", "/api/control", json.dumps({"action": "pause"}), headers={"Cookie": headers["Cookie"]})
        response = connection.getresponse()
        response.read()
        assert response.status == 403
        connection.request("POST", "/api/control", json.dumps({"action": "pause"}), headers=headers)
        response = connection.getresponse()
        assert response.status == 202
        response.read()
        assert RuntimeControlStore(paths.runtime_control_path).read().desired_state == "paused"

        connection.request("POST", "/api/logout", "{}", headers=headers)
        response = connection.getresponse()
        assert response.status == 200
        assert "Max-Age=0" in response.getheader("Set-Cookie")
        response.read()
        connection.request("GET", "/api/status", headers=headers)
        response = connection.getresponse()
        response.read()
        assert response.status == 401
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_browser_session_is_not_transferable_to_a_restarted_server(tmp_path) -> None:
    paths = _paths(tmp_path)
    cookie = ""
    for attempt in range(2):
        server, thread = _running_server(paths)
        try:
            connection = http.client.HTTPConnection(*server.server_address, timeout=5)
            if attempt == 0:
                connection.request("POST", "/api/login", json.dumps({"password": "a-secure-test-token"}), headers={"X-PA-Agent-Control": "1"})
                response = connection.getresponse()
                response.read()
                assert response.status == 200
                cookie = response.getheader("Set-Cookie").split(";", 1)[0]
            else:
                for value in (cookie, "pa_agent_dashboard_session=forged", 'pa_agent_dashboard_session="broken'):
                    connection.request("GET", "/api/status", headers={"Cookie": value})
                    response = connection.getresponse()
                    response.read()
                    assert response.status == 401
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


@pytest.mark.parametrize(
    ("body", "headers", "status"),
    [
        ({"password": "wrong"}, {}, 401),
        ({"password": "错误密码"}, {}, 401),
        ({"password": None}, {}, 400),
        ({"password": "\ud800"}, {}, 400),
        ({"password": "a-secure-test-token", "extra": True}, {}, 400),
        ({"password": "a-secure-test-token"}, {"X-PA-Agent-Control": "0"}, 403),
        ({"password": "a-secure-test-token"}, {"Origin": "http://attacker.invalid"}, 403),
        ({"password": "a-secure-test-token"}, {"Origin": "null"}, 403),
        ({"password": "a-secure-test-token"}, {"Host": "attacker.invalid"}, 403),
    ],
)
def test_browser_login_rejects_invalid_or_cross_site_requests(tmp_path, body, headers, status) -> None:
    server, thread = _running_server(_paths(tmp_path))
    try:
        connection = http.client.HTTPConnection(*server.server_address, timeout=5)
        connection.request("POST", "/api/login", json.dumps(body), headers={"X-PA-Agent-Control": "1", **headers})
        response = connection.getresponse()
        response.read()
        assert response.status == status
        assert response.getheader("Set-Cookie") is None
        assert response.getheader("WWW-Authenticate") is None
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_browser_sessions_expire_and_failed_logins_are_bounded(tmp_path, monkeypatch) -> None:
    from pa_agent import dashboard

    clock = [100.0]
    monkeypatch.setattr(dashboard.time, "monotonic", lambda: clock[0])
    server, thread = _running_server(_paths(tmp_path))
    try:
        connection = http.client.HTTPConnection(*server.server_address, timeout=5)
        headers = {"X-PA-Agent-Control": "1"}
        good = json.dumps({"password": "a-secure-test-token"})
        connection.request("POST", "/api/login", good, headers=headers)
        response = connection.getresponse()
        cookie = response.getheader("Set-Cookie").split(";", 1)[0]
        response.read()
        clock[0] += 8 * 60 * 60 + 1
        connection.request("GET", "/api/status", headers={"Cookie": cookie})
        response = connection.getresponse()
        response.read()
        assert response.status == 401
        for _ in range(10):
            connection.request("POST", "/api/login", json.dumps({"password": "wrong"}), headers=headers)
            response = connection.getresponse()
            response.read()
            assert response.status == 401
        connection.request("POST", "/api/login", good, headers=headers)
        response = connection.getresponse()
        response.read()
        assert response.status == 429
        assert response.getheader("Retry-After") == "60"
        clock[0] += 61
        connection.request("POST", "/api/login", good, headers=headers)
        response = connection.getresponse()
        response.read()
        assert response.status == 200
        assert response.getheader("Set-Cookie").split(";", 1)[0] != cookie
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


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
            "backend_experiments": {
                "available": True,
                "protocol_version": "analysis_backend_aabb_v2",
                "experiments": 2,
                "arm_coverage": 0.75,
                "raw_manifest_path": "/secret/experiment.json",
            },
            "paper": {"available": False, "accounts": []},
        }
    )

    payload = build_dashboard_payload(paths, now_ms=10_500)

    assert payload["health"]["state"] == "running"
    assert payload["runtime"]["assets"][0]["symbol"] == "XAUUSDT"
    assert payload["recent_runs"][0]["run_id"] == "run-1"
    assert payload["backend_experiments"]["experiments"] == 2
    assert "raw_manifest_path" not in payload["backend_experiments"]
    assert payload["control"]["applied"] is True
    assert "api_key" not in json.dumps(payload).lower()


def test_codex_fast_mode_control_is_versioned_and_not_applied_until_runtime_ack(tmp_path) -> None:
    paths = _paths(tmp_path)
    store = RuntimeControlStore(paths.runtime_control_path, now_ms=lambda: 9_000)
    control = store.set_codex_fast_mode(True, expected_generation=0, actor="dashboard")
    RuntimeStatusStore(paths.runtime_status_path, now_ms=lambda: 10_000).write(
        {
            "service_state": "running",
            "applied_control_generation": 0,
            "applied_codex_fast_mode": False,
            "applied_profile": AnalysisProfile().model_dump(mode="json"),
            "applied_profile_generation": 0,
            "applied_profile_fingerprint": profile_fingerprint(AnalysisProfile()),
            "mode": {"analysis_backend": "codex_only"},
            "scheduler": {"running": 0, "queued": 0},
            "assets": [],
        }
    )
    payload = build_dashboard_payload(paths, now_ms=10_500)

    assert control.codex_fast_mode is True
    assert store.read().codex_fast_mode is True
    assert payload["control"]["codex_fast_mode"] is True
    assert payload["runtime"]["applied_codex_fast_mode"] is False
    assert payload["control"]["applied"] is False


def test_fast_mode_control_migrates_old_state_but_rejects_incomplete_current_state(tmp_path) -> None:
    paths = _paths(tmp_path)
    store = RuntimeControlStore(paths.runtime_control_path)
    prior = control_state_to_dict(store.read())
    prior["schema_version"] = 3
    prior.pop("codex_fast_mode")
    paths.runtime_control_path.parent.mkdir(parents=True, exist_ok=True)
    paths.runtime_control_path.write_text(json.dumps(prior), encoding="utf-8")
    assert store.read().codex_fast_mode is False
    prior["schema_version"] = 4
    paths.runtime_control_path.write_text(json.dumps(prior), encoding="utf-8")
    assert store.read().error is not None


def test_dashboard_fast_mode_endpoint_uses_cas_and_waits_for_runtime_ack(tmp_path) -> None:
    paths = _paths(tmp_path)
    RuntimeStatusStore(paths.runtime_status_path).write(
        {
            "service_state": "paused",
            "applied_control_generation": 0,
            "applied_codex_fast_mode": False,
            "applied_profile": AnalysisProfile().model_dump(mode="json"),
            "applied_profile_generation": 0,
            "applied_profile_fingerprint": profile_fingerprint(AnalysisProfile()),
            "mode": {"analysis_backend": "codex_only"},
            "scheduler": {"running": 0, "queued": 0},
            "assets": [],
        }
    )
    server, thread = _running_server(paths)
    try:
        connection = http.client.HTTPConnection(*server.server_address, timeout=5)
        response, payload = _request(
            connection, "POST", "/api/fast-mode",
            json.dumps({"expected_generation": 0, "enabled": True}),
        )
        assert response.status == 202
        assert payload["applied"] is False
        assert RuntimeControlStore(paths.runtime_control_path).read().codex_fast_mode is True
        assert build_dashboard_payload(paths)["control"]["applied"] is False

        response, payload = _request(
            connection, "POST", "/api/fast-mode",
            json.dumps({"expected_generation": 0, "enabled": False}),
        )
        assert response.status == 409
        assert payload["error"] == "generation_conflict"
        response, payload = _request(
            connection, "POST", "/api/fast-mode",
            json.dumps({"expected_generation": 1, "enabled": "true"}),
        )
        assert response.status == 400
        assert payload["error"] == "invalid_fast_mode_request"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


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


def test_dashboard_rejects_direct_switch_when_headless_has_no_direct_key(tmp_path) -> None:
    paths = _paths(tmp_path)
    RuntimeStatusStore(paths.runtime_status_path).write(
        {
            "service_state": "paused",
            "mode": {
                "analysis_backend": "codex_only",
                "analysis_pipeline": "pa_vpa_joint",
                "direct_available": False,
            },
            "scheduler": {},
            "assets": [],
        }
    )
    control = RuntimeControlStore(paths.runtime_control_path).set_desired_backend(
        "codex_only", actor="test"
    )
    payload = build_dashboard_payload(paths)
    assert payload["runtime"]["mode"]["analysis_pipeline"] == "pa_vpa_joint"
    assert payload["runtime"]["mode"]["direct_available"] is False

    server, thread = _running_server(paths)
    try:
        connection = http.client.HTTPConnection(*server.server_address, timeout=5)
        response, body = _request(
            connection,
            "POST",
            "/api/control",
            json.dumps({"action": "set_backend", "backend": "deepseek_only"}),
        )
        assert response.status == 409
        assert body["error"] == "direct_backend_unavailable"
        assert RuntimeControlStore(paths.runtime_control_path).read().generation == control.generation
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize("concurrency", [2, 3])
def test_profile_endpoint_accepts_strict_profile_and_uses_cas(tmp_path, concurrency) -> None:
    paths = _paths(tmp_path)
    RuntimeStatusStore(paths.runtime_status_path).write(
        {"service_state": "running", "assets": [], "scheduler": {}}
    )
    server, thread = _running_server(paths)
    try:
        connection = http.client.HTTPConnection(*server.server_address, timeout=5)
        profile = AnalysisProfile(deepseek_model="deepseek-v4-pro",
            symbols=("XAUUSDT", "BTCUSDT", "ETHUSDT"), max_concurrency=concurrency,
            decision_stance="aggressive").model_dump(mode="json")
        response, payload = _request(
            connection,
            "POST",
            "/api/profile",
            json.dumps({"expected_generation": 0, "profile": profile}),
        )
        assert response.status == 202
        assert payload["control"]["analysis_profile"]["deepseek_model"] == "deepseek-v4-pro"
        assert payload["control"]["analysis_profile"]["symbols"] == ["XAUUSDT", "BTCUSDT", "ETHUSDT"]
        assert payload["control"]["analysis_profile"]["decision_stance"] == "aggressive"
        assert payload["control"]["analysis_profile"]["max_concurrency"] == concurrency

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


@pytest.mark.parametrize(("symbols", "concurrency"), [
    (("XAUUSDT",), 1),
    (("XAUUSDT",), 3),
    (("XAUUSDT", "BTCUSDT", "ETHUSDT"), 3),
    (("BTCUSDT",), 3),
])
def test_run_once_accepts_applied_catalog_scope_without_enabling_disabled_markets(
    tmp_path, symbols, concurrency,
) -> None:
    paths = _paths(tmp_path)
    store = RuntimeControlStore(paths.runtime_control_path)
    paused = store.set_desired_state("paused", actor="test")
    control = store.set_analysis_profile(
        AnalysisProfile(symbols=symbols, max_concurrency=concurrency),
        expected_generation=paused.generation, actor="test",
    )
    _publish_run_once_ready(paths, control)
    status = RuntimeStatusStore(paths.runtime_status_path).read()
    status["scheduler"]["max_concurrency"] = concurrency
    status["assets"] = [
        {"source": "binance_usdm", "venue": "BINANCE", "symbol": symbol,
         "timeframe": "1h", "in_flight": False, "analysis_enabled": symbol in symbols}
        for symbol in ("XAUUSDT", "BTCUSDT", "ETHUSDT")
    ]
    RuntimeStatusStore(paths.runtime_status_path).write(status)
    server, thread = _running_server(paths)
    try:
        connection = http.client.HTTPConnection(*server.server_address, timeout=5)
        response, payload = _request(connection, "POST", "/api/run-once",
            json.dumps({"expected_generation": control.generation}))
        assert response.status == 202
        assert payload["control"]["run_once_generation"] == 1
        assert payload["control"]["desired_state"] == "paused"
        assert payload["control"]["analysis_profile"]["symbols"] == list(symbols)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_dashboard_asset_projection_preserves_scope_and_only_safe_market_context(tmp_path) -> None:
    paths = _paths(tmp_path)
    RuntimeStatusStore(paths.runtime_status_path).write({
        "service_state": "running", "scheduler": {},
        "assets": [{"symbol": "XAUUSDT", "analysis_enabled": True,
                    "market_context": {"instrument_label": "Binance 黄金永续合约", "internal_note": "private"}},
                   {"symbol": "BTCUSDT", "analysis_enabled": False, "internal_note": "private"}],
    })
    assets = build_dashboard_payload(paths)["runtime"]["assets"]
    assert assets[0]["analysis_enabled"] is True
    assert assets[0]["market_context"] == {"instrument_label": "Binance 黄金永续合约"}
    assert assets[1]["analysis_enabled"] is False
    assert "private" not in json.dumps(assets)


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
        "PA + VPA Shadow",
        'id="analysis-pipeline"',
        'id="deepseek-model"',
        'id="deepseek-thinking"',
        'id="deepseek-effort"',
        'id="codex-model"',
        'id="codex-effort"',
        'id="profile-diff"',
        'id="apply-profile"',
        'id="run-once"',
        'id="market-symbols"',
        'id="decision-stance"',
        'id="max-concurrency"',
        "Requested / Effective Model",
        "Pipeline",
        "run.analysis_pipeline||'unknown'",
        "Host / Calls Latency",
        "Trade candidates",
        "value!=null&&value!==''",
        "first_pass_known_count",
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
