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


def test_quantity_oco_sends_sz_without_close_fraction():
    from decimal import Decimal
    calls=[]
    c=client(lambda req:(calls.append(req) or httpx.Response(200,json={'code':'0','data':[{'sCode':'0','algoId':'1'}]})))
    try:
        c.place_oco(inst_id='BTC-USDT-SWAP',side='sell',stop=Decimal('49000'),tp1=Decimal('52000'),client_id='plan',quantity=Decimal('.4'))
        body=json.loads(calls[0].content)
        assert body['sz']=='0.4' and 'closeFraction' not in body
        assert body['reduceOnly'] is True and body['posSide']=='net'
    finally:
        c.close()


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


DAY_MS = 86_400_000


def _event(bill_id, ts, *, stream="fills", **changes):
    row = {"billId": str(bill_id), "ts": str(ts)}
    if stream == "fills":
        row.update(instId="BTC-USDT-SWAP", tradeId=str(bill_id), ordId="order-1",
                   side="buy", fillPx="50000", fillSz="0.1", fee="-0.01",
                   feeCcy="USDT", fillTime=str(ts - 1))
    else:
        row.update(ccy="USDT", balChg="-0.01", fee="-0.01", type="2", subType="1")
    return row | changes


def _event_client(events, clock=None):
    """HTTP fixture applies the documented inclusive ts range then billId pagination."""
    clock = clock or [1800003600500]
    calls = []

    def handle(request):
        path, query = request.url.path, request.url.params
        calls.append((path, dict(query)))
        if path == "/api/v5/public/time":
            data = [{"ts": str(clock[0])}]
        elif path == "/api/v5/account/config":
            data = [{"uid": "demo-user", "acctLv": "2", "posMode": "net_mode"}]
        elif path == "/api/v5/account/balance":
            data = [{"details": [{"ccy": "USDT", "eq": "10000", "availBal": "10000"}]}]
        else:
            data = list(events.get(path, []))
            lower = int(query.get("begin", "0"))
            upper = int(query.get("end", str(clock[0])))
            after = int(query.get("after", str(10**30)))
            data = [r for r in data if lower <= int(r["ts"]) <= upper
                    and int(r["billId"]) < after]
            data = sorted(data, key=lambda r: int(r["billId"]), reverse=True)[:100]
        return httpx.Response(200, json={"code": "0", "data": data})

    c = client(handle)
    c.now_ms = lambda: clock[0]
    return c, calls


@pytest.mark.parametrize("stream,age,recent,history", [
    ("fills", 4, "/api/v5/trade/fills", "/api/v5/trade/fills-history"),
    ("bills", 8, "/api/v5/account/bills", "/api/v5/account/bills-archive"),
])
def test_legacy_cursor_recovers_outside_recent_retention(stream, age, recent, history):
    now = 1800003600500
    anchor = _event(10, now - age * DAY_MS, stream=stream)
    c, calls = _event_client({history: [anchor]})
    try:
        kwargs = {"since_fill_ids": {"SWAP": "10"}} if stream == "fills" else {"since_bill_id": "10"}
        snapshot = c.snapshot(**kwargs)
        assert snapshot.coverage[stream]
        assert any(path == history for path, _ in calls)
        assert snapshot.event_sync[stream].through_ms > int(anchor["ts"])
    finally:
        c.close()


def test_empty_observation_watermark_survives_idle_beyond_both_recent_windows():
    clock = [1800003600500]
    c, calls = _event_client({}, clock)
    try:
        first = c.snapshot()
        assert first.coverage["fills"] and first.coverage["bills"]
        for elapsed in (4, 8):
            clock[0] += elapsed * DAY_MS
            current = c.snapshot(sync_state=first.event_sync)
            assert current.coverage["fills"] and current.coverage["bills"]
            assert current.event_sync["fills"].through_ms > first.event_sync["fills"].through_ms
            assert current.event_sync["fills"].last_id is None
            first = current
        assert any(path.endswith("fills-history") for path, _ in calls)
        assert any(path.endswith("bills-archive") for path, _ in calls)
    finally:
        c.close()


def test_fixed_backfill_pages_resume_then_catch_new_head_without_marking_it_complete_early():
    clock = [1800003600500]
    events = {"/api/v5/trade/fills-history": [
        _event(i, clock[0] - 4 * DAY_MS + i) for i in range(1, 452)
    ]}
    c, calls = _event_client(events, clock)
    try:
        first = c.snapshot(since_fill_ids={"SWAP": "1"})
        assert not first.coverage["fills"]
        pending = first.event_sync["fills"].pending
        assert pending is not None and pending.after == "52"
        fixed_end = pending.end_ms
        clock[0] += 20_000
        events["/api/v5/trade/fills"] = [_event(452, clock[0] - 1)]
        second = c.snapshot(sync_state=first.event_sync)
        assert second.coverage["fills"]
        resume = [q for p, q in calls if p.endswith("fills-history") and q.get("after") == "52"]
        assert resume and int(resume[0]["end"]) == fixed_end
        assert any(r["billId"] == "452" for r in second.fills)
        assert second.event_sync["fills"].last_id == "452"
        assert second.event_sync["fills"].through_ms > fixed_end
    finally:
        c.close()


def test_missing_historical_anchor_remains_incomplete_and_does_not_reset_cursor():
    c, _ = _event_client({})
    try:
        snapshot = c.snapshot(since_fill_ids={"SWAP": "17"})
        assert not snapshot.coverage["fills"]
        assert snapshot.event_sync["fills"].last_id == "17"
        assert snapshot.event_sync["fills"].through_ms == 0
        assert snapshot.event_sync["fills"].status == "anchor_not_found"
    finally:
        c.close()


def test_covered_empty_poll_does_not_recheck_expired_last_event_identity():
    clock = [1800003600500]
    events = {"/api/v5/trade/fills-history": [_event(10, clock[0] - 4 * DAY_MS)]}
    c, calls = _event_client(events, clock)
    try:
        first = c.snapshot(since_fill_ids={"SWAP": "10"})
        assert first.coverage["fills"]
        clock[0] += 30_000
        calls.clear()
        next_snapshot = c.snapshot(sync_state=first.event_sync)
        assert next_snapshot.coverage["fills"]
        assert next_snapshot.event_sync["fills"].last_id == "10"
        assert not any(path.endswith("fills-history") for path, _ in calls)
    finally:
        c.close()


def test_late_event_in_overlap_and_shared_millisecond_ids_are_all_read():
    clock = [1800003600500]
    events = {}
    c, _ = _event_client(events, clock)
    try:
        first = c.snapshot()
        events["/api/v5/trade/fills"] = [_event(i, clock[0] - 200) for i in range(1, 202)]
        clock[0] += 1000
        second = c.snapshot(sync_state=first.event_sync)
        assert second.coverage["fills"] and len(second.fills) == 201
        assert {r["billId"] for r in second.fills} == {str(i) for i in range(1, 202)}
    finally:
        c.close()


def test_unavailable_history_keeps_coverage_false_but_returns_live_position():
    c, _ = _event_client({})
    original = c._request

    def request(method, path, **kwargs):
        if path.endswith("fills-history"):
            from pa_agent.execution.okx_demo import DemoRequestRejected
            raise DemoRequestRejected("51000")
        if path.endswith("/positions"):
            return [{"instId": "BTC-USDT-SWAP", "pos": "0.1"}]
        return original(method, path, **kwargs)

    c._request = request
    try:
        snapshot = c.snapshot(since_fill_ids={"SWAP": "1"})
        assert snapshot.coverage["fills"] is False
        assert snapshot.event_sync["fills"].status == "unavailable"
        assert snapshot.positions[0]["pos"] == "0.1"
    finally:
        c.close()


def test_clock_reversal_and_expired_known_interval_cannot_advance_coverage():
    clock = [1800003600500]
    c, _ = _event_client({}, clock)
    try:
        first = c.snapshot()
        clock[0] -= 1000
        backwards = c.snapshot(sync_state=first.event_sync)
        assert backwards.coverage["fills"] is False
        assert backwards.event_sync["fills"].status == "clock_reversed"
        assert backwards.event_sync["fills"].through_ms == first.event_sync["fills"].through_ms
        clock[0] += 120 * DAY_MS
        expired = c.snapshot(sync_state=first.event_sync)
        assert expired.coverage["fills"] is False
        assert expired.event_sync["fills"].status == "history_expired"
    finally:
        c.close()


@pytest.mark.parametrize("change", [{"account_key": "another-account"}, {"stream": "bills"},
                                    {"query_filter": {"instType": "SPOT"}}])
def test_sync_state_cannot_be_rebound_to_another_account_or_filter(change):
    c, _ = _event_client({})
    try:
        first = c.snapshot()
        state = {k: v.model_dump(mode="json") for k, v in first.event_sync.items()}
        state["fills"].update(change)
        with pytest.raises(ValueError if "query_filter" in change or "stream" in change else Exception):
            c.snapshot(sync_state=state)
    finally:
        c.close()


@pytest.mark.parametrize("change", [{"ts": "0"}, {"instId": "BTC-USDT"}, {"billId": "invalid"}])
def test_out_of_scope_history_response_never_establishes_coverage(change):
    c, _ = _event_client({})
    original = c._request

    def request(method, path, **kwargs):
        if path.endswith("fills-history"):
            return [_event(10, 1800003600000) | change]
        return original(method, path, **kwargs)

    c._request = request
    try:
        snapshot = c.snapshot(since_fill_ids={"SWAP": "10"})
        assert not snapshot.coverage["fills"]
        assert snapshot.event_sync["fills"].status == "invalid_page"
    finally:
        c.close()
