from __future__ import annotations

import json
import os
import subprocess
import threading
from pathlib import Path

from .config import Settings
from .media_queue import MediaTaskCancelled, MediaTaskScheduler
from .models import (
    FullFrameOverlaySettings,
    OverlayCompositeRequest,
    OverlayRenderRequest,
    StickerOverlaySettings,
)
from .processes import WINDOWS_FFMPEG_FLAGS, WINDOWS_NO_WINDOW
from .video_pipeline import (
    encoder_args,
    hardware_decode_args,
    is_hdr_transfer,
    normalized_video_filter,
    sdr_output_args,
)


class OverlayProcessingError(RuntimeError):
    pass


class OverlayProcessingCancelled(OverlayProcessingError):
    pass


class OverlayProcessor:
    def __init__(
        self,
        settings: Settings,
        media_queue: MediaTaskScheduler | None = None,
        preferred_codec: str = "libx264",
        nvenc_available: bool = False,
    ):
        self.settings = settings
        self.media_queue = media_queue or MediaTaskScheduler()
        self.preferred_codec = preferred_codec
        self.nvenc_available = nvenc_available
        self._process_lock = threading.Lock()
        self._processes: dict[str, subprocess.Popen[str]] = {}
        self._cancelled_operations: set[str] = set()
        self._cancel_events: dict[str, threading.Event] = {}

    def _run(self, args: list[str], operation_id: str | None = None) -> None:
        with self._process_lock:
            if operation_id and operation_id in self._cancelled_operations:
                self._cancelled_operations.discard(operation_id)
                raise OverlayProcessingCancelled("转换已中止")
            cancel_event = (
                self._cancel_events.setdefault(operation_id, threading.Event())
                if operation_id
                else None
            )
        try:
            with self.media_queue.acquire("overlay", cancel_event):
                process = subprocess.Popen(
                    args,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    creationflags=WINDOWS_FFMPEG_FLAGS,
                )
                cancel_now = False
                if operation_id:
                    with self._process_lock:
                        cancel_now = operation_id in self._cancelled_operations
                        self._processes[operation_id] = process
                if cancel_now and process.poll() is None:
                    process.terminate()

                stdout, stderr = process.communicate()
                was_cancelled = False
                if operation_id:
                    with self._process_lock:
                        self._processes.pop(operation_id, None)
                        was_cancelled = operation_id in self._cancelled_operations
                if was_cancelled:
                    raise OverlayProcessingCancelled("转换已中止")
                if process.returncode:
                    message = (stderr or stdout or "FFmpeg 处理失败").strip()
                    raise OverlayProcessingError(message[-3000:])
        except MediaTaskCancelled as exc:
            raise OverlayProcessingCancelled("转换已中止") from exc
        finally:
            if operation_id:
                with self._process_lock:
                    self._processes.pop(operation_id, None)
                    self._cancel_events.pop(operation_id, None)
                    self._cancelled_operations.discard(operation_id)

    def cancel(self, operation_id: str) -> bool:
        with self._process_lock:
            self._cancelled_operations.add(operation_id)
            event = self._cancel_events.get(operation_id)
            process = self._processes.get(operation_id)
        if event is not None:
            event.set()
        if process is not None and process.poll() is None:
            process.terminate()
            return True
        return event is not None

    def _probe_media(self, video: Path) -> tuple[int, int, int | None, bool]:
        result = subprocess.run(
            [
                self.settings.ffprobe,
                "-v",
                "error",
                "-show_entries",
                "stream=index,codec_type,codec_name,width,height,color_transfer:stream_tags=rotate:"
                "stream_side_data=rotation",
                "-of",
                "json",
                str(video),
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=WINDOWS_NO_WINDOW,
            check=False,
        )
        try:
            streams = json.loads(result.stdout)["streams"]
            video_stream = next(
                stream
                for stream in streams
                if stream.get("codec_type") == "video" and stream.get("width") and stream.get("height")
            )
            audio_stream = next(
                (
                    stream
                    for stream in streams
                    if stream.get("codec_type") == "audio"
                    and stream.get("codec_name") not in {None, "none", "unknown"}
                ),
                None,
            )
            rotation = video_stream.get("tags", {}).get("rotate", 0)
            for side_data in video_stream.get("side_data_list", []):
                if side_data.get("rotation") is not None:
                    rotation = side_data["rotation"]
                    break
            width = int(video_stream["width"])
            height = int(video_stream["height"])
            if abs(int(float(rotation))) % 180 == 90:
                width, height = height, width
            audio_index = int(audio_stream["index"]) if audio_stream else None
            return width, height, audio_index, is_hdr_transfer(video_stream.get("color_transfer"))
        except (KeyError, StopIteration, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise OverlayProcessingError("无法读取视频分辨率") from exc

    def render(self, request: OverlayRenderRequest) -> dict[str, str]:
        if request.mode == "watermark":
            composite = OverlayCompositeRequest(
                video_path=request.video_path,
                output_dir=request.output_dir,
                watermark=FullFrameOverlaySettings(
                    path=request.overlay_path, opacity=request.opacity
                ),
            )
        elif request.mode == "pixel":
            composite = OverlayCompositeRequest(
                video_path=request.video_path,
                output_dir=request.output_dir,
                pixel=FullFrameOverlaySettings(path=request.overlay_path, opacity=request.opacity),
            )
        else:
            composite = OverlayCompositeRequest(
                video_path=request.video_path,
                output_dir=request.output_dir,
                sticker=StickerOverlaySettings(
                    path=request.overlay_path,
                    opacity=request.opacity,
                    scale=request.scale,
                    positions=[request.position],
                ),
            )
        return self.render_composite(composite)

    def render_composite(self, request: OverlayCompositeRequest) -> dict[str, str]:
        video = Path(request.video_path).expanduser().resolve()
        output_dir = Path(request.output_dir).expanduser().resolve()
        if not video.is_file():
            raise OverlayProcessingError(f"视频文件不存在：{video}")

        layers: list[tuple[str, Path]] = []
        if request.watermark:
            layers.append(("水印", Path(request.watermark.path).expanduser().resolve()))
        if request.sticker:
            layers.append(("贴纸", Path(request.sticker.path).expanduser().resolve()))
        if request.pixel:
            layers.append(("像素点", Path(request.pixel.path).expanduser().resolve()))
        for name, path in layers:
            if not path.is_file():
                raise OverlayProcessingError(f"{name}图像不存在：{path}")
        for name, path in layers:
            if name in {"水印", "像素点"} and path.suffix.lower() != ".gif":
                raise OverlayProcessingError(f"{name}处理需要选择 GIF 文件")
            if name == "贴纸" and path.suffix.lower() not in {
                ".png",
                ".jpg",
                ".jpeg",
                ".webp",
                ".bmp",
            }:
                raise OverlayProcessingError("批量贴纸需要选择静态图片")

        output_dir.mkdir(parents=True, exist_ok=True)
        suffix = "-".join(name for name, _path in layers)
        output = output_dir / f"{video.stem}-{suffix}.mp4"
        sequence = 2
        while output.exists():
            output = output_dir / f"{video.stem}-{suffix}-{sequence}.mp4"
            sequence += 1
        temporary = output.with_suffix(".part.mp4")
        width, height, audio_index, hdr = self._probe_media(video)
        width -= width % 2
        height -= height % 2
        codec = "h264_nvenc" if self.preferred_codec == "h264_nvenc" and self.nvenc_available else "libx264"

        input_args: list[str] = []
        filters = [
            f"[0:v]setpts=PTS-STARTPTS,{normalized_video_filter(f'scale={width}:{height}', hdr)}[v0]"
        ]
        input_index = 1
        stage = 0

        def add_full_frame(settings: FullFrameOverlaySettings) -> None:
            nonlocal input_index, stage
            input_args.extend(["-stream_loop", "-1", "-i", str(Path(settings.path).resolve())])
            filters.append(
                f"[{input_index}:v]scale={width}:{height},format=rgba,"
                f"colorchannelmixer=aa={settings.opacity:.3f},setpts=PTS-STARTPTS[layer{stage}]"
            )
            filters.append(
                f"[v{stage}][layer{stage}]overlay=0:0:shortest=1,format=yuv420p[v{stage + 1}]"
            )
            input_index += 1
            stage += 1

        if request.watermark:
            add_full_frame(request.watermark)

        if request.sticker:
            position_expressions = {
                "top_left": "24:24",
                "top_right": "W-w-24:24",
                "center": "(W-w)/2:(H-h)/2",
                "bottom_left": "24:H-h-24",
                "bottom_right": "W-w-24:H-h-24",
            }
            for position in dict.fromkeys(request.sticker.positions):
                input_args.extend(
                    ["-loop", "1", "-i", str(Path(request.sticker.path).resolve())]
                )
                filters.append(
                    f"[{input_index}:v]scale='max(1,trunc(iw*{request.sticker.scale:.3f}))':"
                    f"'max(1,trunc(ih*{request.sticker.scale:.3f}))',format=rgba,"
                    f"colorchannelmixer=aa={request.sticker.opacity:.3f},"
                    f"setpts=PTS-STARTPTS[layer{stage}]"
                )
                filters.append(
                    f"[v{stage}][layer{stage}]overlay={position_expressions[position]}:"
                    f"shortest=1,format=yuv420p[v{stage + 1}]"
                )
                input_index += 1
                stage += 1

        if request.pixel:
            add_full_frame(request.pixel)

        audio_args = (
            ["-map", f"0:{audio_index}", "-c:a", "aac", "-b:a", "192k"]
            if audio_index is not None
            else []
        )
        def command(selected_codec: str) -> list[str]:
            return [
                self.settings.ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                *hardware_decode_args(selected_codec),
                "-i",
                str(video),
                *input_args,
                "-filter_complex",
                ";".join(filters),
                "-map",
                f"[v{stage}]",
                *encoder_args(selected_codec, 20, self.media_queue.cpu_threads_per_task),
                *sdr_output_args(),
                *audio_args,
                "-shortest",
                "-movflags",
                "+faststart",
                str(temporary),
            ]
        try:
            try:
                self._run(command(codec), request.operation_id)
            except OverlayProcessingError:
                if codec != "h264_nvenc":
                    raise
                temporary.unlink(missing_ok=True)
                self._run(command("libx264"), request.operation_id)
            if not temporary.is_file() or temporary.stat().st_size < 1000:
                raise OverlayProcessingError("输出视频校验失败")
            os.replace(temporary, output)
        finally:
            if temporary.exists():
                temporary.unlink(missing_ok=True)
        return {"input_path": str(video), "output_path": str(output)}
