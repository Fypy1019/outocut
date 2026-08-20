from __future__ import annotations

import os
import threading
from collections import Counter, deque
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from uuid import uuid4


class MediaTaskCancelled(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class MediaTaskLease:
    task_id: str
    kind: str
    cpu_threads: int


class MediaTaskScheduler:
    """Fair process-wide admission control for expensive FFmpeg work."""

    def __init__(self, max_concurrent: int = 1, logical_cpus: int | None = None):
        self._logical_cpus = max(1, logical_cpus or os.cpu_count() or 1)
        self._condition = threading.Condition()
        self._waiting: deque[tuple[str, str]] = deque()
        self._active: dict[str, str] = {}
        self._configured_limit = 1
        self.set_limit(max_concurrent)

    @property
    def effective_limit(self) -> int:
        cpu_capacity = max(1, self._logical_cpus - 2)
        return min(self._configured_limit, cpu_capacity)

    @property
    def cpu_threads_per_task(self) -> int:
        cpu_capacity = max(1, self._logical_cpus - 2)
        return max(1, cpu_capacity // self.effective_limit)

    def set_limit(self, max_concurrent: int) -> None:
        if not 1 <= max_concurrent <= 4:
            raise ValueError("媒体任务并发数必须在 1 到 4 之间")
        with self._condition:
            self._configured_limit = max_concurrent
            self._condition.notify_all()

    def status(self) -> dict[str, object]:
        with self._condition:
            return {
                "configured_limit": self._configured_limit,
                "effective_limit": self.effective_limit,
                "cpu_threads_per_task": self.cpu_threads_per_task,
                "active": len(self._active),
                "waiting": len(self._waiting),
                "active_by_kind": dict(Counter(self._active.values())),
                "waiting_by_kind": dict(Counter(kind for _ticket, kind in self._waiting)),
            }

    @contextmanager
    def acquire(
        self,
        kind: str,
        cancel: threading.Event | None = None,
    ) -> Iterator[MediaTaskLease]:
        ticket = uuid4().hex
        task_id = uuid4().hex
        acquired = False
        with self._condition:
            self._waiting.append((ticket, kind))
            try:
                while True:
                    if cancel is not None and cancel.is_set():
                        raise MediaTaskCancelled("媒体任务已取消")
                    first = bool(self._waiting and self._waiting[0][0] == ticket)
                    if first and len(self._active) < self.effective_limit:
                        self._waiting.popleft()
                        self._active[task_id] = kind
                        acquired = True
                        break
                    self._condition.wait(timeout=0.1)
            finally:
                if not acquired:
                    self._waiting = deque(
                        item for item in self._waiting if item[0] != ticket
                    )
                    self._condition.notify_all()

        lease = MediaTaskLease(task_id, kind, self.cpu_threads_per_task)
        try:
            yield lease
        finally:
            with self._condition:
                self._active.pop(task_id, None)
                self._condition.notify_all()
