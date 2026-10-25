"""Maintenance handoff preserves accepted orders while stopping new submissions."""
import pytest

from pa_agent.execution.demo_contracts import DemoPaths
from pa_agent.execution.demo_spool import DemoSpool, atomic_json
from pa_agent.execution.demo_store import DemoStore
from pa_agent.execution.demo_worker import DemoWorker
from tests.unit.test_demo_execution_flow import config
from tests.unit.test_demo_worker import NOW
from tests.unit.test_independent_demo import NetExchange, plan


def test_cli_sigterm_handoff_preserves_live_order_and_leaves_new_intent_for_restart(tmp_path, monkeypatch):
    import signal
    import threading

    from pa_agent.execution import okx_demo
    from pa_agent.execution.demo_dispatch import cutover_fingerprint
    from pa_agent.execution.demo_worker import main

    paths, cfg, exchange = DemoPaths(tmp_path/'execution'), config(), NetExchange()
    spool, store = DemoSpool(paths), DemoStore(paths.state_db)
    atomic_json(paths.control, dict(cutover_id=cfg.cutover_id, generated_at_ms=NOW, running=True))
    atomic_json(paths.intents/'cutover.json', dict(config_digest=cutover_fingerprint(cfg)))
    a, b = plan('old'), plan('new')
    spool.publish(a)
    first = DemoWorker(cfg, paths, exchange, store, spool, now_ms=lambda: NOW)
    first.tick()
    first.close()
    request = next(c[1] for c in exchange.calls if c[0]=='entry')
    handlers = {}
    class StopAfterOneTick:
        stopped = False
        def is_set(self):
            return self.stopped
        def set(self):
            self.stopped = True
        def wait(self, timeout):
            spool.publish(b)
            handlers[signal.SIGTERM](signal.SIGTERM, None)
    monkeypatch.setattr(threading, 'Event', StopAfterOneTick)
    monkeypatch.setattr(signal, 'signal', lambda sig, callback: handlers.update({sig:callback}))
    monkeypatch.setattr('time.time', lambda: exchange.now/1000)
    drain_observed=[]
    def advance_drain(delay):
        if not drain_observed:
            assert exchange.orders[request.client_id]['state']=='live'
            assert not any(c[0]=='cancel' for c in exchange.calls)
            assert len([c for c in exchange.calls if c[0]=='entry'])==1
            assert any(i.content_digest==b.content_digest for i in spool.pending())
            assert not any(c[0]=='oco' for c in exchange.calls)
            # Independent exchange terminal event ends the test's draining window.
            exchange.orders[request.client_id].update(state='canceled')
        drain_observed.append(delay)
        exchange.now+=1000
    monkeypatch.setattr('time.sleep', advance_drain)
    monkeypatch.setattr(okx_demo, 'load_demo_credentials', lambda path: None)
    monkeypatch.setattr(okx_demo, 'OkxDemoClient', lambda *args, **kwargs: exchange)
    config_path = tmp_path/'config.json'
    atomic_json(config_path, cfg.model_dump(mode='json'))
    monkeypatch.setenv('PA_AGENT_DEMO_CONFIG', str(config_path))
    monkeypatch.setenv('PA_AGENT_EXECUTION_HOME', str(paths.root))
    monkeypatch.setenv('CREDENTIALS_DIRECTORY', str(tmp_path/'credentials'))
    assert main([])==0
    assert len([c for c in exchange.calls if c[0]=='entry'])==1
    assert not any(c[0]=='cancel' for c in exchange.calls)
    assert drain_observed
    assert any(i.content_digest==b.content_digest for i in spool.pending())
    restarted = DemoWorker(cfg, paths, exchange, store, spool, now_ms=lambda: exchange.now)
    try:
        restarted.tick()
        assert len([c for c in exchange.calls if c[0]=='entry'])==2
        assert not any(c[0]=='cancel' for c in exchange.calls)
    finally:
        restarted.close()


@pytest.mark.parametrize('version', ['okx_demo_intent_v1', 'okx_demo_intent_v2'])
def test_handoff_keeps_live_remainder_but_expiry_still_cancels(tmp_path, version):
    from tests.unit.test_demo_plans import intent
    paths, cfg, exchange = DemoPaths(tmp_path), config(), NetExchange()
    if version == 'okx_demo_intent_v1':
        cfg = cfg.model_copy(update={'independent_accepted_symbols': ()})
    spool, store = DemoSpool(paths), DemoStore(paths.state_db)
    atomic_json(paths.control, dict(cutover_id=cfg.cutover_id, generated_at_ms=NOW, running=True))
    a = intent(schema_version=version)
    spool.publish(a)
    worker = DemoWorker(cfg, paths, exchange, store, spool, now_ms=lambda: exchange.now)
    try:
        worker.tick()
        worker.request_shutdown()
        worker.tick()
        assert not any(c[0]=='cancel' for c in exchange.calls)
        assert store.row(a.content_digest)['entry_state']=='live'
        exchange.now=a.expires_at_ms
        worker.tick()
        assert any(c[0]=='cancel' for c in exchange.calls)
    finally:
        worker.close()


def test_handoff_waits_for_fill_after_stop_and_keeps_managing_its_protection(tmp_path):
    paths, cfg, exchange = DemoPaths(tmp_path), config(), NetExchange()
    spool, store = DemoSpool(paths), DemoStore(paths.state_db)
    atomic_json(paths.control, dict(cutover_id=cfg.cutover_id, generated_at_ms=NOW, running=True))
    a = plan()
    spool.publish(a)
    worker = DemoWorker(cfg, paths, exchange, store, spool, now_ms=lambda: exchange.now)
    try:
        worker.tick()
        worker.request_shutdown()
        worker.tick()
        assert worker.can_handoff() is False  # May not exit with a still-fillable order.
        exchange.fill(store, a)
        worker.tick()
        assert worker.can_handoff() is False
        protection=[c[1] for c in exchange.calls if c[0]=='oco']
        assert len(protection)==1 and str(protection[0]['quantity'])=='1'
        assert store.row(a.content_digest)['protection']=='protected'
        exchange.trigger(store, a)
        for _ in range(3):
            exchange.now+=1000
            worker.tick()
        assert worker.can_handoff() is True
        assert len([c for c in exchange.calls if c[0]=='entry'])==1
    finally:
        worker.close()


def test_partial_fill_at_handoff_still_gets_exact_protection(tmp_path):
    paths, cfg, exchange = DemoPaths(tmp_path), config(), NetExchange()
    spool, store = DemoSpool(paths), DemoStore(paths.state_db)
    atomic_json(paths.control, dict(cutover_id=cfg.cutover_id, generated_at_ms=NOW, running=True))
    a = plan()
    spool.publish(a)
    worker = DemoWorker(cfg, paths, exchange, store, spool, now_ms=lambda: NOW)
    try:
        worker.tick()
        exchange.fill(store, a, '0.4')
        worker.request_shutdown()
        worker.tick()
        protection=[c[1] for c in exchange.calls if c[0]=='oco']
        assert len(protection)==1 and str(protection[0]['quantity'])=='0.4'
        assert protection[0]['stop']==a.stop and protection[0]['tp1']==a.tp1
        # Filled remainders cancel under the original trading policy, not because of maintenance.
        assert any(c[0]=='cancel' for c in exchange.calls)
    finally:
        worker.close()


def test_explicit_entry_disable_still_cancels_at_handoff(tmp_path):
    paths, cfg, exchange = DemoPaths(tmp_path), config(), NetExchange()
    spool, store = DemoSpool(paths), DemoStore(paths.state_db)
    atomic_json(paths.control, dict(cutover_id=cfg.cutover_id, generated_at_ms=NOW, running=True))
    spool.publish(plan())
    worker = DemoWorker(cfg, paths, exchange, store, spool, now_ms=lambda: NOW)
    try:
        worker.tick()
        worker.config=cfg.model_copy(update={'entry_enabled':False})
        worker.request_shutdown()
        worker.tick()
        assert any(c[0]=='cancel' for c in exchange.calls)
    finally:
        worker.close()


def test_stop_arriving_during_snapshot_does_not_submit_new_entry(tmp_path):
    paths, cfg, exchange = DemoPaths(tmp_path), config(), NetExchange()
    spool, store = DemoSpool(paths), DemoStore(paths.state_db)
    atomic_json(paths.control, dict(cutover_id=cfg.cutover_id, generated_at_ms=NOW, running=True))
    spool.publish(plan())
    worker = DemoWorker(cfg, paths, exchange, store, spool, now_ms=lambda: NOW)
    original=exchange.snapshot
    def stop_during_snapshot(**kwargs):
        worker.request_shutdown()
        return original(**kwargs)
    exchange.snapshot=stop_during_snapshot
    try:
        worker.tick()
        assert not exchange.calls
        assert spool.pending()
    finally:
        worker.close()
