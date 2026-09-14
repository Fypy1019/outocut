from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

from outocut_engine.config import Settings
from outocut_engine.models import OverlayCompositeRequest
from outocut_engine.overlays import OverlayProcessingCancelled, OverlayProcessor


def create_video(path: Path, size: str, duration: float, with_audio: bool = False) -> None:
    args = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"color=c=teal:s={size}:d={duration}:r=25",
    ]
    if with_audio:
        args.extend(["-f", "lavfi", "-i", f"sine=frequency=880:duration={duration}"])
    args.extend(["-c:v", "libx264", "-pix_fmt", "yuv420p"])
    if with_audio:
        args.extend(["-c:a", "aac", "-shortest"])
    subprocess.run([*args, str(path)], check=True)


def probe(path: Path) -> dict[str, object]:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-count_frames",
            "-show_entries", "stream=codec_type,width,height,nb_read_frames:format=duration",
            "-of", "json", str(path),
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def processor(tmp_path: Path) -> OverlayProcessor:
    return OverlayProcessor(
        Settings(
            data_root=tmp_path,
            session_token="test-token",
            ffmpeg="ffmpeg",
            ffprobe="ffprobe",
        )
    )


def test_active_overlay_process_can_be_cancelled(tmp_path: Path) -> None:
    overlay_processor = processor(tmp_path)
    operation_id = "canceltest123"
    errors: list[BaseException] = []

    def run_process() -> None:
        try:
            overlay_processor._run(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                operation_id,
            )
        except BaseException as exc:  # Captured for assertion in the parent thread.
            errors.append(exc)

    worker = threading.Thread(target=run_process)
    worker.start()
    for _ in range(100):
        if overlay_processor.cancel(operation_id):
            break
        time.sleep(0.01)
    worker.join(timeout=5)

    assert not worker.is_alive()
    assert errors and isinstance(errors[0], OverlayProcessingCancelled)


def test_video_overlay_stretches_loops_and_ignores_its_audio(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    overlay = tmp_path / "overlay.mp4"
    output_dir = tmp_path / "output"
    create_video(source, "360x640", 0.9)
    create_video(overlay, "640x360", 0.25, with_audio=True)

    result = processor(tmp_path).render_composite(
        OverlayCompositeRequest.model_validate(
            {
                "video_path": str(source),
                "output_dir": str(output_dir),
                "video_overlay": {"path": str(overlay), "opacity": 0.01},
            }
        )
    )

    metadata = probe(Path(result["output_path"]))
    streams = metadata["streams"]
    assert isinstance(streams, list)
    assert [(item.get("width"), item.get("height")) for item in streams] == [(360, 640)]
    assert float(metadata["format"]["duration"]) >= 0.8
    assert [item["codec_type"] for item in streams] == ["video"]


def test_fixed_frame_drop_removes_exactly_two_frames_and_keeps_audio(tmp_path: Path) -> None:
    source = tmp_path / "source-with-audio.mp4"
    output_dir = tmp_path / "output"
    create_video(source, "360x640", 1.2, with_audio=True)
    source_metadata = probe(source)
    source_video = next(item for item in source_metadata["streams"] if item["codec_type"] == "video")

    result = processor(tmp_path).render_composite(
        OverlayCompositeRequest.model_validate(
            {
                "video_path": str(source),
                "output_dir": str(output_dir),
                "fixed_frame_drop": True,
            }
        )
    )

    metadata = probe(Path(result["output_path"]))
    output_video = next(item for item in metadata["streams"] if item["codec_type"] == "video")
    assert int(output_video["nb_read_frames"]) == int(source_video["nb_read_frames"]) - 2
    assert [item["codec_type"] for item in metadata["streams"]] == ["video", "audio"]
    assert float(metadata["format"]["duration"]) < float(source_metadata["format"]["duration"])
