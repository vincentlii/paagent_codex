from tests.unit.test_demo_worker import setup, NOW


def test_enabled_demo_is_default_and_never_falls_back_to_paper():
    from pa_agent.operations.demo_trading import compose_trading_projection
    from pa_agent.operations.trading import safe_trading_projection

    paper = {"schema_version": 1, "account": {"settled_balance": 99999}, "analyses": []}
    demo = {
        "schema_version": 1,
        "environment": "okx_demo_v1",
        "account_label": "OKX 模拟盘",
        "generated_at_ms": NOW,
        "exchange_observed_at_ms": NOW,
        "state": "ready",
        "account": {"equity": "10000"},
    }
    result = compose_trading_projection(paper, demo, selected_backend="okx_demo", now_ms=NOW)
    assert result["demo"]["account"]["equity"] == "10000"
    assert safe_trading_projection(result)["selected_backend"] == "okx_demo"
    missing = compose_trading_projection(paper, None, selected_backend="okx_demo", now_ms=NOW)
    assert missing["demo"]["state"] == "unavailable" and not missing["demo"].get("account")
    stale = compose_trading_projection(paper, demo, selected_backend="okx_demo", now_ms=NOW + 31000)
    assert stale["demo"]["state"] == "stale"


def test_projection_unknowns_and_secrets_are_not_exported(tmp_path):
    from pa_agent.operations.demo_trading import build_demo_projection, safe_demo_projection

    worker, exchange, store, _ = setup(tmp_path)
    snapshot = exchange.snapshot().model_copy(update={"coverage": {"bills": False}})
    projection = build_demo_projection(store, snapshot, now_ms=NOW)
    assert projection["periods"]["all"]["net_pnl"] is None
    assert "account_key" not in str(projection)
    projection["secret"] = "API KEY"
    projection["account"]["path"] = "secret path"
    projection["account"]["equity"] = float("nan")
    cleaned = safe_demo_projection(projection)
    assert "API KEY" not in str(cleaned) and "path" not in str(cleaned)
    assert cleaned["account"].get("equity") is None
    worker.close()


def test_fees_funding_and_transfers_are_separate_and_deduplicated(tmp_path):
    from pa_agent.operations.demo_trading import build_demo_projection

    worker, exchange, store, _ = setup(tmp_path)
    snapshot = exchange.snapshot().model_copy(
        update={
            "bills": (
                {"billId": "trade", "type": "2", "pnl": "10", "fee": "-1", "ts": str(NOW)},
                {"billId": "funding", "type": "8", "balChg": "-2", "ts": str(NOW)},
                {"billId": "reset", "type": "1", "balChg": "10000", "ts": str(NOW)},
            ),
            "fills": ({"instId": "BTC-USDT-SWAP", "tradeId": "f", "fee": "-1", "feeCcy": "USDT"},),
        }
    )
    store.apply_snapshot(snapshot)
    store.apply_snapshot(snapshot)
    p = build_demo_projection(store, snapshot, now_ms=NOW)
    assert p["periods"]["all"]["net_pnl"] == "7"
    assert p["periods"]["all"]["fees"] == "-1"
    assert p["periods"]["all"]["funding"] == "-2"
    assert p["periods"]["all"]["external_flows"] == "10000"
    worker.close()


def test_malformed_nested_payloads_remain_unavailable_not_crashes_or_paper_fallback():
    from pa_agent.operations.demo_trading import safe_demo_projection
    from pa_agent.operations.trading import safe_trading_projection

    for malformed in ([], "bad", 1):
        result = safe_demo_projection(
            {
                "schema_version": 1,
                "environment": "okx_demo_v1",
                "coverage": malformed,
                "periods": malformed,
                "account": {"equity": "NaN"},
            }
        )
        assert result["account"].get("equity") is None
    root = {
        "schema_version": 2,
        "selected_backend": "okx_demo",
        "generated_at_ms": "bad",
        "paper_versions": {"paper_latency_v4": {"schema_version": 2}},
        "demo": None,
    }
    assert safe_trading_projection(root)["demo"]["state"] == "unavailable"
