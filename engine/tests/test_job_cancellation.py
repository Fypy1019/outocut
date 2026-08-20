from __future__ import annotations

import threading
import time
from pathlib import Path
from types import SimpleNamespace

from outocut_engine.db import Database
from outocut_engine.jobs import JobManager, ResourceCheck
from outocut_engine.models import JobCreate, JobState, MixTemplate, ShotInput


class BlockingRenderer:
    def __init__(self, output: Path):
        self.output = output
        self.started = threading.Event()
        self.release = threading.Event()
        self.global_cancel_called = False

    def cancel_active_process(self) -> None:
        self.global_cancel_called = True
        self.release.set()

    def render(self, *_args, **_kwargs):
        self.started.set()
        if not self.release.wait(timeout=5):
            raise TimeoutError("test renderer was not released")
        return SimpleNamespace(video_path=self.output)


def make_request(tmp_path: Path, name: str) -> JobCreate:
    return JobCreate(
        name=name,
        template=MixTemplate(name=name, voice_enabled=False),
        asset_paths=[str(tmp_path / "asset.mp4")],
        shots=[ShotInput(text="test")],
        output_dir=str(tmp_path / "output"),
    )


def wait_for_state(manager: JobManager, job_id: str, state: JobState) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if manager.get(job_id).state == state:
            return
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} did not reach {state}")


def test_cancelling_queued_job_does_not_terminate_active_job(tmp_path: Path) -> None:
    renderer = BlockingRenderer(tmp_path / "result.mp4")
    manager = JobManager(
        Database(tmp_path / "jobs.sqlite"),
        renderer,  # type: ignore[arg-type]
        lambda _name: None,
        resource_checker=lambda _request: ResourceCheck(True),
    )
    active = manager.create(make_request(tmp_path, "active"))
    queued = manager.create(make_request(tmp_path, "queued"))
    manager.start(active.id)
    manager.start(queued.id)

    assert renderer.started.wait(timeout=5)
    cancelled = manager.cancel(queued.id)

    assert cancelled.state == JobState.CANCELLED
    assert not renderer.global_cancel_called

    renderer.release.set()
    wait_for_state(manager, active.id, JobState.SUCCEEDED)

