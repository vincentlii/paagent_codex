"""The real signed REST adapter against an in-memory venue; never opens a socket."""

import hashlib
import json
from decimal import Decimal
from types import SimpleNamespace

import httpx

from tests.unit.test_demo_execution_flow import config, source_record
from tests.unit.test_demo_worker import Exchange, NOW


def test_host_to_signed_http_partial_protection_exit_and_dashboard(tmp_path):
    from pa_agent.execution.demo_contracts import DemoPaths
    from pa_agent.execution.demo_dispatch import OfficialExecutionDispatcher
    from pa_agent.execution.demo_spool import DemoSpool
    from pa_agent.execution.demo_store import DemoStore
    from pa_agent.execution.demo_worker import DemoWorker
    from pa_agent.execution.okx_demo import OkxDemoClient, OkxDemoCredentials
    from pa_agent.monitoring.analysis_runner import FrozenAnalysisPayload
    from pa_agent.operations.trading import safe_trading_projection

    exchange, calls, clock = Exchange(), [], [NOW]
    orders = exchange.orders

    def handle(request):
        assert request.url.host == "openapi.okx.com"
        assert request.headers["x-simulated-trading"] == "1"
        path, query = request.url.path, dict(request.url.params)
        payload = json.loads(request.content) if request.content else None
        calls.append((request.method, path, payload))
        if request.method == "POST":
            if path.endswith("/order"):
                orders[payload["clOrdId"]] = dict(
                    ordId="entry",
                    clOrdId=payload["clOrdId"],
                    instId=payload["instId"],
                    side=payload["side"],
                    state="live",
                    accFillSz="0",
                )
                rows = [{"sCode": "0", "ordId": "entry"}]
            elif path.endswith("/order-algo"):
                response = exchange.place_oco(
                    inst_id=payload["instId"],
                    side=payload["side"],
                    stop=Decimal(payload["slTriggerPx"]),
                    tp1=Decimal(payload["tpTriggerPx"]),
                    client_id=payload["algoClOrdId"],
                )
                exchange.algos[payload["algoClOrdId"]].update({k: str(v).lower() if isinstance(v, bool) else str(v) for k,v in payload.items()})
                exchange.algos[payload["algoClOrdId"]]["closeFraction"] = ""
                rows = [response | {"sCode": "0"}]
            elif path.endswith("/cancel-order"):
                rows = [{"sCode": "0", "ordId": "entry"}]
            else:
                raise AssertionError(path)
        elif path.endswith("/public/time"):
            rows = [{"ts": str(clock[0])}]
        elif path.endswith("/account/config"):
            rows = [{"uid": "demo-fixture", "acctLv": "2", "posMode": "net_mode"}]
        elif path.endswith("/account/balance"):
            rows = [{"details": [{"ccy": "USDT", "eq": "10000", "availBal": "9800"}]}]
        elif path.endswith("/account/positions"):
            rows = list(exchange.snapshot().positions)
        elif path.endswith("/orders-pending"):
            rows = [o for o in orders.values() if o["state"] in {"live", "partially_filled"}]
        elif path.endswith("/orders-algo-pending"):
            rows = (
                [a for a in exchange.algos.values() if a["state"] == "live"]
                if query["ordType"] == "oco"
                else []
            )
        elif path.endswith("/trade/fills") or path.endswith("/account/bills"):
            rows = []
        elif path.endswith("/account/instruments"):
            rows = [
                dict(
                    instId="BTC-USDT-SWAP",
                    state="live",
                    ctType="linear",
                    settleCcy="USDT",
                    tickSz="0.1",
                    lotSz="0.01",
                    minSz="0.01",
                    maxLmtSz="1000",
                    maxMktSz="1000",
                    ctVal="0.01",
                    ctMult="1",
                )
            ]
        elif path.endswith("/account/leverage-info"):
            rows = [{"posSide": "net", "lever": "3"}]
        elif path.endswith("/account/trade-fee"):
            rows = [{"instType": "SWAP", "makerU": "-0.0002", "takerU": "-0.0005"}]
        elif path.endswith("/market/ticker"):
            rows = [
                {"instId": "BTC-USDT-SWAP", "askPx": "50100", "bidPx": "50099", "ts": str(clock[0])}
            ]
        elif path.endswith("/trade/order"):
            order = exchange.get_order(
                query["instId"], query.get("clOrdId"), order_id=query.get("ordId")
            )
            rows = [order] if order else []
        elif path.endswith("/trade/order-algo"):
            algo = exchange.get_algo("BTC-USDT-SWAP", query["algoClOrdId"])
            # Exercise current OKX ordIdList instead of the older single ID field.
            if algo.get("state") == "effective":
                algo = algo | {"ordIdList": ["child"]}
            rows = [algo] if algo else []
        else:
            raise AssertionError(path)
        return httpx.Response(200, json={"code": "0", "data": rows})

    account = hashlib.sha256(b"okx_demo_v1:demo-fixture").hexdigest()
    cfg = config().model_copy(update={"account_key": account})
    paths = DemoPaths(tmp_path)
    spool, store = DemoSpool(paths), DemoStore(paths.state_db)
    dispatcher = OfficialExecutionDispatcher(
        SimpleNamespace(enabled=True), spool, cfg, now_ms=lambda: clock[0]
    )
    dispatcher.publish_control(running=True)
    record, spec = source_record()
    binding = FrozenAnalysisPayload(
        frame=None, execution_target="okx_demo", execution_epoch="epoch-1"
    )
    dispatcher.submit_official(
        record,
        spec,
        task_id="http-task",
        execution_payload=binding,
        execution_snapshot_hash="a" * 64,
    )
    client = OkxDemoClient(
        OkxDemoCredentials("fixture-key", "fixture-secret", "fixture-pass"),
        transport=httpx.MockTransport(handle),
        now_ms=lambda: clock[0],
    )
    worker = DemoWorker(cfg, paths, client, store, spool, now_ms=lambda: clock[0])
    try:
        worker.tick()
        cid = next(iter(orders))
        assert orders[cid]["state"] == "live"
        orders[cid].update(state="partially_filled", accFillSz="0.4")
        exchange.position = "0.4"
        clock[0] += 1
        worker.tick()
        posts = [(path, body) for method, path, body in calls if method == "POST"]
        assert [path for path, _ in posts] == [
            "/api/v5/trade/order",
            "/api/v5/trade/order-algo",
            "/api/v5/trade/cancel-order",
        ]
        protection = posts[1][1]
        assert protection["slTriggerPx"] == "49000" and protection["tpTriggerPx"] == "52000"
        assert protection["sz"] == "0.4" and "closeFraction" not in protection
        assert protection["reduceOnly"] is True
        orders[cid]["state"] = "canceled"
        clock[0] += 1
        worker.tick()
        next(iter(exchange.algos.values()))["state"] = "effective"
        orders["child"] = {
            "instId": "BTC-USDT-SWAP",
            "ordId": "child",
            "state": "filled",
            "accFillSz": "0.4",
        }
        exchange.position = "0"
        clock[0] += 1
        projection = worker.tick()
        assert projection["orders"][0]["trade_state"] == "closed"
        combined = safe_trading_projection(dispatcher.compose_projection({"schema_version": 1}))
        assert combined["selected_backend"] == "okx_demo"
        assert combined["demo"]["orders"][0]["stop"] == "49000"
        assert not (tmp_path / "paper.sqlite3").exists()
    finally:
        worker.close()
        client.close()
