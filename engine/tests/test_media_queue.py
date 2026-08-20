from __future__ import annotations

import threading
import time

import pytest

from outocut_engine.media_queue import MediaTaskCancelled, MediaTaskScheduler


def test_global_media_queue_limits_concurrency_across_task_kinds() -> None:
    scheduler = MediaTaskScheduler(max_concurrent=2, logical_cpus=8)
    release = threading.Event()
    lock = threading.Lock()
    active = 0
    peak = 0

    def run(kind: str) -> None:
        nonlocal active, peak
        with scheduler.acquire(kind):
            with lock:
                active += 1
                peak = max(peak, active)
            release.wait(timeout=5)
            with lock:
                active -= 1

    workers = [
        threading.Thread(target=run, args=(kind,))
        for kind in ("mix", "overlay", "resolution")
    ]
    for worker in workers:
        worker.start()

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and scheduler.status()["active"] != 2:
        time.sleep(0.01)
    assert scheduler.status()["active"] == 2
    assert scheduler.status()["waiting"] == 1

    release.set()
    for worker in workers:
        worker.join(timeout=5)

    assert all(not worker.is_alive() for worker in workers)
    assert peak == 2
    assert scheduler.status()["active"] == 0
    assert scheduler.status()["waiting"] == 0


def test_waiting_media_task_can_be_cancelled_without_consuming_a_slot() -> None:
    scheduler = MediaTaskScheduler(max_concurrent=1, logical_cpus=8)
    release = threading.Event()
    active_started = threading.Event()
    cancel = threading.Event()
    errors: list[BaseException] = []

    def active_task() -> None:
        with scheduler.acquire("mix"):
            active_started.set()
            release.wait(timeout=5)

    def waiting_task() -> None:
        try:
            with scheduler.acquire("overlay", cancel):
                raise AssertionError("cancelled task should not acquire a slot")
        except BaseException as exc:
            errors.append(exc)

    active = threading.Thread(target=active_task)
    waiting = threading.Thread(target=waiting_task)
    active.start()
    assert active_started.wait(timeout=5)
    waiting.start()

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and scheduler.status()["waiting"] != 1:
        time.sleep(0.01)
    cancel.set()
    waiting.join(timeout=5)
    release.set()
    active.join(timeout=5)

    assert errors and isinstance(errors[0], MediaTaskCancelled)
    assert scheduler.status()["active"] == 0
    assert scheduler.status()["waiting"] == 0


@pytest.mark.parametrize(
    ("logical_cpus", "limit", "effective", "threads"),
    [(16, 1, 1, 14), (16, 4, 4, 3), (4, 4, 2, 1), (2, 4, 1, 1)],
)
def test_media_queue_reserves_system_cores_and_splits_cpu_threads(
    logical_cpus: int,
    limit: int,
    effective: int,
    threads: int,
) -> None:
    scheduler = MediaTaskScheduler(limit, logical_cpus)

    assert scheduler.effective_limit == effective
    assert scheduler.cpu_threads_per_task == threads
