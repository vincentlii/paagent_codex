import json

import pytest

from tests.unit.test_demo_plans import intent


def test_bounded_atomic_delivery_and_receipt_restart(tmp_path):
    from pa_agent.execution.demo_contracts import DemoPaths, DemoReceipt
    from pa_agent.execution.demo_spool import DemoSpool

    paths = DemoPaths(tmp_path)
    spool = DemoSpool(paths, capacity=1)
    i = intent()
    spool.publish(i)
    spool.publish(i)
    with pytest.raises(ValueError, match="spool_full"):
        spool.publish(intent(task_id="second"))
    assert spool.pending() == (i,)
    spool.ack(
        DemoReceipt(
            intent_digest=i.content_digest,
            task_id=i.task_id,
            cutover_id=i.cutover_id,
            disposition="accepted",
            accepted_at_ms=i.generated_at_ms,
        )
    )
    assert DemoSpool(paths).pending() == ()
    assert spool.collect_acknowledged(before_ms=i.generated_at_ms + 1) == 1


def test_tampered_spool_does_not_deliver(tmp_path):
    from pa_agent.execution.demo_contracts import DemoPaths
    from pa_agent.execution.demo_spool import DemoSpool

    spool = DemoSpool(DemoPaths(tmp_path))
    i = intent()
    spool.publish(i)
    path = next(spool.paths.intents.glob("intent-*.json"))
    raw = json.loads(path.read_text())
    raw["stop"] = "48000"
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="invalid_spool"):
        spool.pending()
