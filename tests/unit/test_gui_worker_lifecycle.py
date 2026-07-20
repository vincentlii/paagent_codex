from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtCore import QThread
from PyQt6.QtGui import QCloseEvent

from pa_agent.app_context import AppContext
from pa_agent.gui.ai_stream_window import _ChatWorker as StreamChatWorker
from pa_agent.gui.conversation_widget import _ChatWorker as ConversationChatWorker
from pa_agent.gui.main_window import MainWindow, _AnalysisWorker
from pa_agent.gui.snapshot_worker import SnapshotFetchWorker
from pa_agent.gui.thread_lifecycle import QThreadLifecycleManager
from pa_agent.util.threading import CancelToken


class _Reply:
    content = "ok"
    reasoning_content = "because"
    usage = None


class _Session:
    def send(self, *_args, **_kwargs):
        return _Reply()


class _Orchestrator:
    def submit(self, *_args, **_kwargs):
        return SimpleNamespace(stage2_decision={"order": {"type": "no_trade"}})


def test_analysis_result_signal_is_distinct_from_qthread_finished(qtbot) -> None:
    worker = _AnalysisWorker(
        orchestrator=_Orchestrator(),
        frame=object(),
        cancel_token=CancelToken(),
        parent=None,
    )
    decisions: list[dict] = []
    thread_finished: list[bool] = []
    worker.decision_ready.connect(decisions.append)
    worker.finished.connect(lambda: thread_finished.append(True))

    worker.start()
    qtbot.waitUntil(lambda: bool(thread_finished), timeout=2_000)

    assert decisions == [{"order": {"type": "no_trade"}}]
    assert worker.isRunning() is False


@pytest.mark.parametrize("worker_type", [StreamChatWorker, ConversationChatWorker])
def test_chat_result_signal_is_distinct_from_qthread_finished(qtbot, worker_type) -> None:
    worker = worker_type(_Session(), "question", CancelToken(), parent=None)
    replies: list[tuple] = []
    thread_finished: list[bool] = []
    worker.reply_ready.connect(lambda *args: replies.append(args))
    worker.finished.connect(lambda: thread_finished.append(True))

    worker.start()
    qtbot.waitUntil(lambda: bool(thread_finished), timeout=2_000)

    assert len(replies) == 1
    assert worker.isRunning() is False


def test_snapshot_worker_does_not_emit_after_interruption(qtbot) -> None:
    started = threading.Event()
    release = threading.Event()

    class _Source:
        def latest_snapshot(self, _count):
            started.set()
            release.wait(timeout=2.0)
            return ["stale"]

    worker = SnapshotFetchWorker(_Source(), 10, parent=None)
    received: list[list] = []
    worker.bars_ready.connect(received.append)
    worker.start()
    assert started.wait(timeout=1.0)

    worker.requestInterruption()
    release.set()
    qtbot.waitUntil(lambda: worker.isFinished(), timeout=2_000)
    qtbot.wait(10)

    assert received == []


def test_main_window_defers_destruction_until_retired_thread_exits(
    qtbot,
    monkeypatch,
) -> None:
    started = threading.Event()
    release = threading.Event()

    class _BlockingThread(QThread):
        def run(self) -> None:
            started.set()
            release.wait(timeout=2.0)

    window = MainWindow(AppContext())
    qtbot.addWidget(window)
    thread = _BlockingThread(parent=None)
    thread.start()
    assert started.wait(timeout=1.0)
    window._thread_lifecycle.retire(thread, request_interruption=False)
    monkeypatch.setattr("pa_agent.gui.main_window._WORKER_JOIN_TIMEOUT_MS", 10)

    event = QCloseEvent()
    window.closeEvent(event)

    assert event.isAccepted() is False
    assert window._shutdown_complete is False

    release.set()
    qtbot.waitUntil(lambda: window._thread_lifecycle.running_count == 0, timeout=2_000)
    window._poll_deferred_close()
    assert window._shutdown_complete is True


def test_qthread_lifecycle_survives_one_thousand_cancel_cycles(qtbot) -> None:
    manager = QThreadLifecycleManager()

    class _ShortThread(QThread):
        def run(self) -> None:
            self.msleep(1)

    for index in range(1_000):
        thread = _ShortThread(parent=None)
        thread.start()
        manager.retire(thread, wait_ms=1_000)
        assert thread.isRunning() is False
        if index % 50 == 0:
            qtbot.wait(1)

    qtbot.wait(10)
    assert manager.reap() == 0
    assert manager.retained_count == 0


def test_retire_from_qthread_finished_signal_does_not_leave_reference(qtbot) -> None:
    manager = QThreadLifecycleManager()

    class _ShortThread(QThread):
        def run(self) -> None:
            self.msleep(1)

    thread = _ShortThread(parent=None)
    finished: list[bool] = []
    thread.finished.connect(lambda: finished.append(True))
    thread.finished.connect(
        lambda: manager.retire(thread, request_interruption=False)
    )
    thread.start()

    qtbot.waitUntil(lambda: bool(finished), timeout=2_000)
    qtbot.wait(10)

    assert manager.reap() == 0
    assert manager.retained_count == 0
