"""Strong-reference lifecycle guard for cancellable QThread instances."""

from __future__ import annotations

import time
from contextlib import suppress
from typing import Any


class QThreadLifecycleManager:
    """Never release or delete a QThread while ``isRunning()`` is true."""

    def __init__(self) -> None:
        self._retired: dict[int, Any] = {}
        self._finish_hooks: set[int] = set()

    @property
    def running_count(self) -> int:
        return sum(self._is_running(thread) for thread in self._retired.values())

    @property
    def retained_count(self) -> int:
        return len(self._retired)

    def retire(
        self,
        thread: Any,
        *,
        wait_ms: int = 0,
        request_interruption: bool = True,
    ) -> bool:
        if thread is None:
            return False
        if request_interruption:
            with suppress(AttributeError, RuntimeError):
                thread.requestInterruption()
        if self._is_running(thread) and int(wait_ms) > 0:
            thread.wait(max(0, int(wait_ms)))
        if self._is_running(thread):
            identity = id(thread)
            self._retired[identity] = thread
            self._connect_finish_hook(thread, identity)
            return True
        identity = id(thread)
        self._retired.pop(identity, None)
        self._finish_hooks.discard(identity)
        self._delete_later(thread)
        return False

    def reap(self) -> int:
        for identity, thread in tuple(self._retired.items()):
            if self._is_running(thread):
                continue
            self._retired.pop(identity, None)
            self._finish_hooks.discard(identity)
            self._delete_later(thread)
        return self.running_count

    def wait_for_all(self, *, timeout_ms: int) -> int:
        deadline = time.monotonic() + max(0, int(timeout_ms)) / 1000
        for thread in tuple(self._retired.values()):
            if not self._is_running(thread):
                continue
            remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
            if remaining_ms <= 0:
                break
            thread.wait(remaining_ms)
        return self.reap()

    def _connect_finish_hook(self, thread: Any, identity: int) -> None:
        if identity in self._finish_hooks:
            return
        signal = getattr(thread, "finished", None)
        if signal is None:
            return
        try:
            signal.connect(lambda *_, key=identity: self._on_finished(key))
        except (AttributeError, RuntimeError, TypeError):
            return
        self._finish_hooks.add(identity)

    def _on_finished(self, identity: int) -> None:
        thread = self._retired.get(identity)
        if thread is None or self._is_running(thread):
            return
        self._retired.pop(identity, None)
        self._finish_hooks.discard(identity)
        self._delete_later(thread)

    @staticmethod
    def _is_running(thread: Any) -> bool:
        try:
            return bool(thread.isRunning())
        except (AttributeError, RuntimeError):
            return False

    @staticmethod
    def _delete_later(thread: Any) -> None:
        with suppress(RuntimeError):
            thread.deleteLater()
