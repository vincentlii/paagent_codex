import pytest

from pa_agent.execution.demo_contracts import DemoConfig, client_id, digest
from pa_agent.execution.demo_dispatch import cutover_fingerprint
from pa_agent.execution.demo_store import DemoStore
from tests.unit.test_demo_plans import intent


def test_v2_same_bar_distinct_tasks_are_independent(tmp_path):
    store = DemoStore(tmp_path / "demo.sqlite3")
    for task in ("a", "b"):
        p = intent(schema_version="okx_demo_intent_v2", task_id=task)
        assert store.register_intent(p) is None
        assert store.row(p.content_digest)["trade_state"] == "active"
        assert store.register_intent(p).disposition == "accepted"
    with pytest.raises(ValueError, match="intent_identity_conflict"):
        store.register_intent(
            intent(schema_version="okx_demo_intent_v2", task_id="a", stop="48000")
        )


def test_old_config_cutover_digest_unchanged():
    config = DemoConfig()
    old = config.model_dump(
        mode="json", exclude={"enabled", "entry_enabled", "independent_accepted_symbols"}
    )
    assert cutover_fingerprint(config) == digest(old)


def test_v1_digest_and_client_id_unchanged():
    p = intent()
    assert p.schema_version == "okx_demo_intent_v1"
    assert p.content_digest == "b4a364a3229aafd90cab848c0a9cf81d8fb19bd2be319d08ec2f40588c9ccb01"
    assert client_id("account", p, "entry", 1) == "pad2a75919ddb842ed9bcf003cc8f551"
    assert (
        client_id("account", p, "entry", 1)
        == "pad" + digest(["okx_demo_v1", "account", p.cutover_id, p.task_id, "entry", 1])[:29]
    )
    assert "independent_accepted_symbols" not in p.digest_payload()
