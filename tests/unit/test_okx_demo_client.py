import base64
import hashlib
import hmac
import json

import httpx
import pytest

from tests.unit.test_demo_plans import bind


def client(handler):
    from pa_agent.execution.okx_demo import OkxDemoClient, OkxDemoCredentials

    return OkxDemoClient(
        OkxDemoCredentials("secret-key", "secret-sign", "secret-pass"),
        transport=httpx.MockTransport(handler),
        now_ms=lambda: 1800003600500,
    )


def test_signed_body_demo_only_and_credentials_not_in_repr():
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json={"code": "0", "data": [{"sCode": "0", "ordId": "1"}]})

    c = client(handle)
    assert "secret" not in repr(c.credentials)
    assert c.place_limit(bind())["ordId"] == "1"
    req = calls[0]
    assert str(req.url) == "https://openapi.okx.com/api/v5/trade/order"
    assert req.headers["x-simulated-trading"] == "1"
    text = req.headers["OK-ACCESS-TIMESTAMP"] + "POST/api/v5/trade/order" + req.content.decode()
    assert (
        req.headers["OK-ACCESS-SIGN"]
        == base64.b64encode(
            hmac.new(b"secret-sign", text.encode(), hashlib.sha256).digest()
        ).decode()
    )
    body = json.loads(req.content)
    assert body["px"] == "50000" and body["sz"] == "1"
    assert body["tdMode"] == "isolated" and body["posSide"] == "net"


@pytest.mark.parametrize(
    "status,payload,error",
    [
        (
            200,
            {"code": "0", "data": [{"sCode": "51000", "sMsg": "secret-pass"}]},
            "DemoRequestRejected",
        ),
        (200, {"code": "0", "data": [{}]}, "DemoRequestUnknown"),
        (503, {"secret": "secret-key"}, "DemoRequestUnknown"),
        (302, {}, "DemoRequestUnknown"),
    ],
)
def test_http_success_is_not_order_acceptance_and_errors_are_redacted(status, payload, error):
    c = client(lambda req: httpx.Response(status, json=payload))
    with pytest.raises(Exception) as caught:
        c.place_limit(bind())
    assert type(caught.value).__name__ == error
    assert "secret" not in str(caught.value)


def test_timeout_is_unknown_and_never_retried():
    calls = []

    def handle(req):
        calls.append(req)
        raise httpx.ReadTimeout("secret-sign", request=req)

    with pytest.raises(Exception, match="unknown") as caught:
        client(handle).place_limit(bind())
    assert type(caught.value).__name__ == "DemoRequestUnknown"
    assert len(calls) == 1 and caught.value.__cause__ is None


def test_oco_uses_both_original_triggers_reduce_only_and_close_all():
    calls = []
    c = client(
        lambda req: (
            calls.append(req)
            or httpx.Response(200, json={"code": "0", "data": [{"sCode": "0", "algoId": "a"}]})
        )
    )
    c.place_oco(
        inst_id="BTC-USDT-SWAP",
        side="sell",
        stop=bind().stop,
        tp1=bind().tp1,
        client_id="pad" + "a" * 29,
    )
    body = json.loads(calls[0].content)
    assert body["ordType"] == "oco" and body["closeFraction"] == "1"
    assert body["reduceOnly"] is True and body["posSide"] == "net"
    assert body["slTriggerPx"] == "49000" and body["tpTriggerPx"] == "52000"
    assert body["slOrdPx"] == body["tpOrdPx"] == "-1"
    assert body["slTriggerPxType"] == body["tpTriggerPxType"] == "last"
    assert "sz" not in body


def test_fill_identity_is_per_instrument_not_trade_id_alone():
    c = client(
        lambda req: httpx.Response(
            200,
            json={
                "code": "0",
                "data": [
                    {"instId": "BTC-USDT-SWAP", "tradeId": "7", "billId": "100"},
                    {"instId": "ETH-USDT-SWAP", "tradeId": "7", "billId": "99"},
                ],
            },
        )
    )
    rows, complete = c._pages("/api/v5/trade/fills", {}, "tradeId")
    assert len(rows) == 2 and complete


@pytest.mark.parametrize(
    "payload",
    [
        {"code": "50004", "data": []},
        {"code": "0", "data": [{"sCode": "50004"}]},
    ],
)
def test_business_timeout_is_unknown_not_definite_rejection(payload):
    from pa_agent.execution.okx_demo import DemoRequestUnknown

    with pytest.raises(DemoRequestUnknown):
        client(lambda req: httpx.Response(200, json=payload)).place_limit(bind())


def test_trigger_child_id_list_is_retained_for_terminal_reconciliation():
    c = client(
        lambda req: httpx.Response(
            200,
            json={
                "code": "0",
                "data": [
                    {
                        "instId": "BTC-USDT-SWAP",
                        "algoId": "123",
                        "state": "effective",
                        "ordIdList": ["456", "457"],
                    }
                ],
            },
        )
    )
    row = c.get_algo("BTC-USDT-SWAP", "pad" + "a" * 29)
    assert json.loads(row["child_order_ids"]) == ["456", "457"]


@pytest.mark.parametrize(
    "case",
    [
        "valid",
        "wrong_user",
        "group_read",
        "other_read",
        "extra_user",
        "invalid_acl",
        "wrong_directory",
        "writable_directory",
        "wrong_owner",
        "symlink",
        "bad_version",
        "truncated",
        "plain_0600",
        "plain_0400",
    ],
)
def test_credential_loader_understands_private_systemd_acl(tmp_path, monkeypatch, case):
    import os
    import stat
    import struct
    from pathlib import Path
    from types import SimpleNamespace
    import pa_agent.execution.okx_demo as module

    directory = tmp_path / "credentials"
    directory.mkdir()
    for name in ["key", "secret", "passphrase"]:
        (directory / ("okx-demo-" + name)).write_text("test-" + name)
    original_stat = Path.stat

    def metadata(path, *args, **kwargs):
        value = original_stat(path, *args, **kwargs)
        if path == directory or path.parent == directory:
            fields = list(value)
            fields[0] = (
                (stat.S_IFDIR | (0o770 if case == "writable_directory" else 0o550))
                if path == directory
                else stat.S_IFREG | 0o440
            )
            fields[4] = 995 if case == "wrong_owner" else 0
            if path != directory and case in {"plain_0600", "plain_0400"}:
                fields[0] = stat.S_IFREG | (0o600 if case == "plain_0600" else 0o400)
            return os.stat_result(fields)
        return value

    def acl(path, name):
        assert name == "system.posix_acl_access"
        permission = 5 if path == directory else 4
        entries = [
            (1, permission, 0xFFFFFFFF),
            (2, permission, 995 if case == "wrong_user" else 994),
            (4, 4 if case == "group_read" else 0, 0xFFFFFFFF),
            (16, permission, 0xFFFFFFFF),
            (32, 4 if case == "other_read" else 0, 0xFFFFFFFF),
        ]
        if case == "extra_user":
            entries.append((2, 4, 996))
        raw = struct.pack("<I", 99 if case == "bad_version" else 2) + b"".join(
            struct.pack("<HHI", *entry) for entry in entries
        )
        return b"invalid" if case == "invalid_acl" else raw[:-1] if case == "truncated" else raw

    monkeypatch.setattr(Path, "stat", metadata)
    if case == "symlink":
        monkeypatch.setattr(Path, "is_symlink", lambda path: path.parent == directory)
    monkeypatch.setattr(
        module,
        "os",
        SimpleNamespace(
            name="posix",
            geteuid=lambda: 994,
            getxattr=acl,
            environ={
                "CREDENTIALS_DIRECTORY": str(
                    tmp_path / "other" if case == "wrong_directory" else directory
                )
            },
        ),
    )
    if case in {"valid", "plain_0600", "plain_0400"}:
        credentials = module.load_demo_credentials(directory)
        assert credentials.key == "test-key" and credentials.secret == "test-secret"
    else:
        with pytest.raises(
            module.DemoUnavailable,
            match="invalid_credential_file" if case == "symlink" else "credential_permissions",
        ):
            module.load_demo_credentials(directory)
