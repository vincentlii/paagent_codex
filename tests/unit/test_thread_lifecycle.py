from __future__ import annotations

from pa_agent.gui.thread_lifecycle import QThreadLifecycleManager


class _FakeThread:
    def __init__(self, *, running: bool, finishes_on_wait: bool = False) -> None:
        self.running = running
        self.finishes_on_wait = finishes_on_wait
        self.interrupted = False
        self.deleted = False
        self.wait_calls: list[int] = []

    def isRunning(self) -> bool:
        return self.running

    def requestInterruption(self) -> None:
        self.interrupted = True

    def wait(self, timeout_ms: int) -> bool:
        self.wait_calls.append(timeout_ms)
        if self.finishes_on_wait:
            self.running = False
        return not self.running

    def deleteLater(self) -> None:
        self.deleted = True


def test_running_thread_is_retained_until_it_finishes() -> None:
    manager = QThreadLifecycleManager()
    thread = _FakeThread(running=True)

    still_running = manager.retire(thread, wait_ms=0)

    assert still_running is True
    assert thread.interrupted is True
    assert thread.deleted is False
    assert manager.running_count == 1

    thread.running = False
    assert manager.reap() == 0
    assert thread.deleted is True


def test_wait_for_all_never_deletes_a_running_thread() -> None:
    manager = QThreadLifecycleManager()
    finishes = _FakeThread(running=True, finishes_on_wait=True)
    stuck = _FakeThread(running=True)
    manager.retire(finishes)
    manager.retire(stuck)

    remaining = manager.wait_for_all(timeout_ms=20)

    assert remaining == 1
    assert finishes.deleted is True
    assert stuck.deleted is False


def test_retiring_same_thread_twice_keeps_one_reference() -> None:
    manager = QThreadLifecycleManager()
    thread = _FakeThread(running=True)

    manager.retire(thread)
    manager.retire(thread)

    assert manager.running_count == 1


def test_repeated_retire_and_reap_cycles_do_not_leak_threads() -> None:
    manager = QThreadLifecycleManager()

    for _ in range(1_000):
        thread = _FakeThread(running=True)
        assert manager.retire(thread) is True
        thread.running = False
        assert manager.reap() == 0
        assert thread.deleted is True

    assert manager.running_count == 0
