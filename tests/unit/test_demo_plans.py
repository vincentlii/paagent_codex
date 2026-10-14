from decimal import Decimal as D

import pytest


def intent(**changes):
    from pa_agent.execution.demo_contracts import DemoIntent

    return DemoIntent.seal(
        dict(
            cutover_id="epoch-1",
            task_id="task-1",
            run_id="run-1",
            pipeline="pa_vpa_joint",
            source_identity={
                "provider": "binance_usdm",
                "venue": "BINANCE",
                "symbol": "BTCUSDT",
                "instrument_type": "perpetual",
            },
            source_spec={},
            market_digest="a" * 64,
            record_digest="b" * 64,
            source_bar_ms=1800000000000,
            generated_at_ms=1800003600500,
            fresh_until_ms=1800007200000,
            expires_at_ms=1800014400000,
            inst_id="BTC-USDT-SWAP",
            disposition="limit",
            side="buy",
            entry="50000",
            stop="49000",
            tp1="52000",
        )
        | changes
    )


def contract(**changes):
    from pa_agent.execution.demo_contracts import DemoContract

    values = dict(
        inst_id="BTC-USDT-SWAP",
        tick="0.1",
        lot="0.01",
        minimum="0.01",
        maximum_limit="1000",
        maximum_market="1000",
        ct_val="0.01",
        ct_mult="1",
        observed_at_ms=1800003600500,
    )
    return DemoContract(**(values | changes))


def bind(i=None, c=None, balance="10000"):
    from pa_agent.execution.demo_plans import bind_demo_limit

    return bind_demo_limit(
        i or intent(), c or contract(), account_key="private-account", available_margin=D(balance)
    )


def test_fixed_500_is_not_margin_or_stop_risk():
    order = bind()
    assert order.sz == D("1")
    assert order.notional == D("500")
    assert order.stop == D("49000") and order.tp1 == D("52000")
    assert bind(intent(stop="48000"), balance="200").sz == order.sz
    assert "tp2" not in order.model_dump()


def test_floor_contract_quantity_without_exceeding_500():
    order = bind(intent(entry="50100"))
    assert order.sz == D("0.99")
    assert order.notional == D("495.99")
    assert bind(c=contract(ct_mult="2")).sz == D("0.5")


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"entry": "50000.01"}, "target_tick_mismatch"),
        ({"stop": "51000"}, "invalid_geometry"),
        ({"entry": "-1"}, "greater than 0"),
        ({"entry": "NaN"}, "finite"),
    ],
)
def test_invalid_price_is_rejected_without_repricing(changes, reason):
    with pytest.raises(ValueError, match=reason):
        bind(intent(**changes))


def test_no_increase_to_minimum_and_no_margin_resizing():
    with pytest.raises(ValueError, match="quantity_bounds"):
        bind(c=contract(minimum="2"))
    with pytest.raises(ValueError, match="insufficient_margin"):
        bind(balance="100")


def test_intent_digest_is_verified():
    from pa_agent.execution.demo_contracts import DemoIntent

    raw = intent().model_dump(mode="json")
    raw["stop"] = "48000"
    with pytest.raises(ValueError, match="digest"):
        DemoIntent.model_validate(raw)


def test_config_defaults_and_no_implicit_live_or_size_change():
    from pa_agent.execution.demo_contracts import DemoConfig

    assert not DemoConfig().enabled and not DemoConfig().entry_enabled
    assert DemoConfig().execution_target == "paper"
    for changes in ({"notional_usdt": "1500"}, {"execution_target": "live"}):
        with pytest.raises(ValueError):
            DemoConfig(**changes)
