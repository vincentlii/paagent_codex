import json
from decimal import Decimal as D

import pytest


def exposure(legs=(), children=(), entered="2"):
    from pa_agent.execution.demo_exposure import plan_exposure

    return plan_exposure(
        {"response": json.dumps({"accFillSz": entered, "ordId": "entry"})},
        list(legs),
        list(children),
    )


def leg(kind="oco", status="live", quantity="1", response=None, cid="oco"):
    return dict(
        leg=kind,
        status=status,
        params=json.dumps({"quantity": quantity}),
        response=json.dumps(response or {}),
        client_id=cid,
    )


def child(oid="exit", fill="1", state="filled", parent="oco"):
    return dict(
        parent_client_id=parent,
        order_id=oid,
        state=state,
        body=json.dumps({"ordId": oid, "accFillSz": fill, "state": state}),
    )


def test_distinct_exit_order_ids_count_once():
    e = exposure([leg(status="effective")], [child(), child()])
    assert (e.entered, e.exited, e.remaining, e.reserved_exit) == (D(2), D(1), D(1), D(0))


def test_unknown_oco_reserves_full_unresolved_quantity():
    e = exposure([leg(status="submission_unknown")])
    assert e.reserved_exit == 1 and e.protected == 0 and e.uncertain


def test_effective_without_children_does_not_release_budget():
    e = exposure([leg(status="effective")])
    assert e.reserved_exit == 1 and e.uncertain


def test_terminal_child_releases_confirmed_unused_remainder():
    e = exposure([leg(status="effective")], [child(fill=".4", state="canceled")])
    assert e.exited == D(".4") and e.remaining == D("1.6") and e.reserved_exit == 0


@pytest.mark.parametrize("fill", ["NaN", "-1", "3"])
def test_invalid_or_excess_exit_is_unknown_not_zero(fill):
    assert exposure([leg(status="effective")], [child(fill=fill)]).uncertain
