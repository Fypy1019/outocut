from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from outocut_engine.db import Database, utc_now
from outocut_engine.media_batches import MediaBatchManager


class FakeOverlayProcessor:
    def __init__(self):
        self.calls: list[str] = []
        self.block = threading.Event()

    def render_composite(self, request):
        self.calls.append(request.video_path)
        self.block.wait(timeout=5)
        return {"output_path": request.video_path + ".mp4"}

    def cancel(self, _operation_id: str) -> bool:
        self.block.set()
        return True


class FakeResolutionProcessor:
    def __init__(self):
        self.calls: list[str] = []

    def convert(self, request):
        self.calls.append(request.video_path)
        return {"output_path": request.video_path + ".mp4"}


def wait_for_batch(manager: MediaBatchManager, batch_id: str, state: str) -> dict:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        batch = manager.get(batch_id)
        if batch["state"] == state:
            return batch
        time.sleep(0.02)
    raise AssertionError(f"batch {batch_id} did not reach {state}")


def resolution_payload(path: str) -> dict:
    return {
        "video_path": path,
        "source_root": "D:/source",
        "output_dir": "D:/output",
        "replace_original": False,
        "target_width": 720,
        "target_height": 1280,
        "relative_path": Path(path).name,
        "source_width": 360,
        "source_height": 640,
        "orientation": "portrait",
    }


def test_sqlite_resolution_batch_persists_and_finishes_in_order(tmp_path: Path) -> None:
    overlay = FakeOverlayProcessor()
    resolution = FakeResolutionProcessor()
    manager = MediaBatchManager(
        Database(tmp_path / "batch.sqlite"),
        overlay,  # type: ignore[arg-type]
        resolution,  # type: ignore[arg-type]
        worker_count=2,
    )
    batch = manager.create(
        "resolution",
        [resolution_payload("D:/source/one.mp4"), resolution_payload("D:/source/two.mp4")],
    )

    finished = wait_for_batch(manager, batch["id"], "completed")
    manager.close()

    assert resolution.calls == ["D:/source/one.mp4", "D:/source/two.mp4"]
    assert [task["state"] for task in finished["tasks"]] == ["succeeded", "succeeded"]
    assert finished["tasks"][1]["payload"]["relative_path"] == "two.mp4"


def test_interrupted_sqlite_task_is_recovered_as_pending(tmp_path: Path) -> None:
    database = Database(tmp_path / "recovery.sqlite")
    now = utc_now()
    database.execute(
        "INSERT INTO media_batches(id, kind, state, created_at, updated_at) VALUES(?,?,?,?,?)",
        ("batch", "resolution", "paused", now, now),
    )
    database.execute(
        """INSERT INTO media_tasks(
               id, batch_id, position, state, payload, created_at, updated_at
           ) VALUES(?,?,?, 'processing', ?,?,?)""",
        (
            "task",
            "batch",
            0,
            json.dumps(resolution_payload("D:/source/interrupted.mp4")),
            now,
            now,
        ),
    )
    manager = MediaBatchManager(
        database,
        FakeOverlayProcessor(),  # type: ignore[arg-type]
        FakeResolutionProcessor(),  # type: ignore[arg-type]
        worker_count=1,
    )

    recovered = manager.get("batch")
    manager.close()

    assert recovered["state"] == "paused"
    assert recovered["tasks"][0]["state"] == "pending"
    assert recovered["tasks"][0]["error"] is None
