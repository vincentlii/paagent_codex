from decimal import Decimal

import httpx
import pytest

from tests.unit.test_okx_demo_client import client


@pytest.mark.parametrize("payload,expected", [
    ({"feeGroup": [{"groupId": "7", "maker": "-0.0002", "taker": "-0.0005"}]}, "0.0005"),
    ({"makerU": "0.0001", "takerU": "-0.0006", "maker": "-1"}, "0.0006"),
    ({"feeGroup": [{"groupId": "8", "maker": "0", "taker": "0"}], "makerU": "0", "takerU": "0"}, None),
    ({"feeGroup": [{"groupId": "7", "maker": "NaN", "taker": "0"}]}, None),
    ({"maker": "0", "taker": "0"}, None),
])
def test_fee_identity_sign_and_read_only_cached_lookup(payload, expected):
    requests = []
    c = client(lambda req: (requests.append(req) or httpx.Response(200,
        json={"code": "0", "data": [{"instType": "SWAP", **payload}]})))
    try:
        fee = c.fee_schedule("BTC-USDT-SWAP", inst_family="BTC-USDT", group_id="7")
        assert c.fee_schedule("BTC-USDT-SWAP", inst_family="BTC-USDT", group_id="7") == fee
        assert len(requests) == 1
        req = requests[0]
        assert req.method == "GET" and req.url.path == "/api/v5/account/trade-fee"
        assert dict(req.url.params) == {"instType": "SWAP", "instFamily": "BTC-USDT"}
        assert req.headers["x-simulated-trading"] == "1"
        if expected is None:
            assert fee is None
        else:
            assert fee.commission_rate == Decimal(expected)
    finally:
        c.close()


def test_fee_timeout_is_unavailable_without_leaking_or_continuous_retry():
    calls = []
    def timeout(req):
        calls.append(req)
        raise httpx.ReadTimeout("secret", request=req)
    c = client(timeout)
    try:
        for _ in range(2):
            assert c.fee_schedule("BTC-USDT-SWAP", inst_family="BTC-USDT", group_id="7") is None
        assert len(calls) == 1
    finally:
        c.close()
