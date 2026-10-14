"""Host-owned immutable input files and separate Worker-owned receipts."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from pa_agent.execution.demo_contracts import DemoIntent, DemoPaths, DemoReceipt, canonical

MAX_BYTES = 256 * 1024


def read_json(path: Path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise ValueError("invalid_spool_file")
    with path.open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("invalid_spool_size")
    return json.loads(raw)


def atomic_json(path: Path, value: object, *, mode: int = 0o640):
    data = canonical(value).encode()
    if len(data) > MAX_BYTES or path.is_symlink() or path.parent.is_symlink():
        raise ValueError("invalid_spool_write")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".demo-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            if os.name != "nt":
                os.fchmod(stream.fileno(), mode)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        if os.name != "nt":
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


class DemoSpool:
    def __init__(self, paths: DemoPaths, *, capacity=64):
        self.paths = paths
        self.capacity = capacity

    def publish(self, intent: DemoIntent):
        self.paths.intents.mkdir(parents=True, exist_ok=True)
        path = self.paths.intents / f"intent-{intent.content_digest}.json"
        if path.exists():
            if DemoIntent.model_validate(read_json(path)) != intent:
                raise ValueError("intent_identity_conflict")
            return
        if len(tuple(self.paths.intents.glob("intent-*.json"))) >= self.capacity:
            raise ValueError("spool_full")
        atomic_json(path, intent.model_dump(mode="json"))

    def pending(self):
        result = []
        files = tuple(self.paths.intents.glob("intent-*.json"))
        if len(files) > self.capacity:
            raise ValueError("spool_full")
        for path in files:
            try:
                intent = DemoIntent.model_validate(read_json(path))
                if path.name != f"intent-{intent.content_digest}.json":
                    raise ValueError("filename_digest")
                receipt = self.paths.receipts / path.name
                if receipt.exists():
                    ack = DemoReceipt.model_validate(read_json(receipt))
                    if (ack.intent_digest, ack.task_id, ack.cutover_id) != (
                        intent.content_digest,
                        intent.task_id,
                        intent.cutover_id,
                    ):
                        raise ValueError("receipt_binding")
                    continue
                result.append(intent)
            except (ValueError, OSError, TypeError):
                raise ValueError("invalid_spool_file") from None
        return tuple(sorted(result, key=lambda i: (i.source_bar_ms, i.generated_at_ms, i.task_id)))

    def ack(self, receipt: DemoReceipt):
        atomic_json(
            self.paths.receipts / f"intent-{receipt.intent_digest}.json",
            receipt.model_dump(mode="json"),
        )

    def collect_acknowledged(self, *, before_ms):
        count = 0
        for path in self.paths.intents.glob("intent-*.json"):
            receipt_path = self.paths.receipts / path.name
            if receipt_path.exists():
                intent = DemoIntent.model_validate(read_json(path))
                receipt = DemoReceipt.model_validate(read_json(receipt_path))
                if (
                    receipt.intent_digest == intent.content_digest
                    and receipt.accepted_at_ms < before_ms
                ):
                    path.unlink()  # Host owns input; Worker keeps durable receipt.
                    count += 1
        return count
