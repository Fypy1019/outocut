from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

from outocut_engine.config import Settings
from outocut_engine.overlays import OverlayProcessingCancelled, OverlayProcessor


def test_active_overlay_process_can_be_cancelled(tmp_path: Path) -> None:
    processor = OverlayProcessor(
        Settings(
            data_root=tmp_path,
            session_token="test-token",
            ffmpeg="ffmpeg",
            ffprobe="ffprobe",
        )
    )
    operation_id = "canceltest123"
    errors: list[BaseException] = []

    def run_process() -> None:
        try:
            processor._run(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                operation_id,
            )
        except BaseException as exc:  # Captured for assertion in the parent thread.
            errors.append(exc)

    worker = threading.Thread(target=run_process)
    worker.start()
    for _ in range(100):
        if processor.cancel(operation_id):
            break
        time.sleep(0.01)
    worker.join(timeout=5)

    assert not worker.is_alive()
    assert errors and isinstance(errors[0], OverlayProcessingCancelled)
