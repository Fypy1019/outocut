from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from .config import Settings
from .media_queue import MediaTaskScheduler
from .models import ResolutionConvertRequest
from .processes import WINDOWS_FFMPEG_FLAGS, WINDOWS_NO_WINDOW
from .video_pipeline import (
    encoder_args,
    hardware_decode_args,
    is_hdr_transfer,
    normalized_video_filter,
    sdr_output_args,
)

VIDEO_EXTENSIONS = {
    ".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".flv", ".ts",
    ".mts", ".m2ts", ".wmv", ".mpg", ".mpeg", ".3gp",
}

# These containers can safely carry the H.264/AAC streams produced by the
# resolution converter. Other scanned formats can still be exported as MP4,
# but must not be overwritten with MP4 data under their original extension.
H264_AAC_REPLACE_EXTENSIONS = {
    ".mp4", ".mov", ".m4v", ".mkv", ".flv", ".ts", ".mts", ".m2ts", ".3gp",
}
MOV_FASTSTART_EXTENSIONS = {".mp4", ".mov", ".m4v", ".3gp"}


class ResolutionProcessingError(RuntimeError):
    pass


class ResolutionProcessor:
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

    def _probe(self, video: Path) -> tuple[int, int, int | None, bool]:
        result = subprocess.run(
            [
                self.settings.ffprobe,
                "-v", "error",
                "-show_entries",
                "stream=index,codec_type,codec_name,width,height,color_transfer:stream_tags=rotate:stream_side_data=rotation",
                "-of", "json",
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
                stream for stream in streams
                if stream.get("codec_type") == "video" and stream.get("width") and stream.get("height")
            )
            audio_stream = next(
                (
                    stream for stream in streams
                    if stream.get("codec_type") == "audio"
                    and stream.get("codec_name") not in {None, "none", "unknown"}
                ),
                None,
            )
            rotation: Any = video_stream.get("tags", {}).get("rotate", 0)
            for side_data in video_stream.get("side_data_list", []):
                if side_data.get("rotation") is not None:
                    rotation = side_data["rotation"]
                    break
            width = int(video_stream["width"])
            height = int(video_stream["height"])
            if abs(int(float(rotation))) % 180 == 90:
                width, height = height, width
            return (
                width,
                height,
                int(audio_stream["index"]) if audio_stream else None,
                is_hdr_transfer(video_stream.get("color_transfer")),
            )
        except (KeyError, StopIteration, TypeError, ValueError, json.JSONDecodeError) as exc:
            message = (result.stderr or "无法读取视频分辨率").strip()
            raise ResolutionProcessingError(f"视频信息解析失败：{message[-1000:]}") from exc

    @staticmethod
    def _is_qualified(width: int, height: int) -> tuple[str, bool]:
        if height >= width:
            return "portrait", 720 <= width <= 1440 and 1280 <= height <= 2560
        return "landscape", 1280 <= width <= 2560 and 720 <= height <= 1440

    def scan(self, root_directory: str) -> dict[str, Any]:
        root = Path(root_directory).expanduser().resolve()
        if not root.is_dir():
            raise ResolutionProcessingError(f"扫描文件夹不存在：{root}")
        videos: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        for path in sorted(root.rglob("*"), key=lambda item: str(item).lower()):
            if not path.is_file() or path.suffix.lower() not in VIDEO_EXTENSIONS:
                continue
            try:
                width, height, _audio_index, _hdr = self._probe(path)
                orientation, qualified = self._is_qualified(width, height)
                videos.append({
                    "path": str(path),
                    "relative_path": str(path.relative_to(root)),
                    "name": path.name,
                    "width": width,
                    "height": height,
                    "orientation": orientation,
                    "qualified": qualified,
                })
            except ResolutionProcessingError as exc:
                errors.append({"path": str(path), "error": str(exc)})
        return {"root_directory": str(root), "videos": videos, "errors": errors}

    def convert(self, request: ResolutionConvertRequest) -> dict[str, str | int]:
        video = Path(request.video_path).expanduser().resolve()
        source_root = Path(request.source_root).expanduser().resolve()
        if not video.is_file():
            raise ResolutionProcessingError(f"视频文件不存在：{video}")
        if request.replace_original:
            extension = video.suffix.lower()
            if extension not in H264_AAC_REPLACE_EXTENSIONS:
                raise ResolutionProcessingError(
                    f"{extension or '无扩展名'} 格式不支持安全原位替换，"
                    "请选择“导出到新文件夹”（输出 MP4）"
                )
            output = video
            # Keep the original suffix so FFmpeg selects the matching muxer.
            # The previous .part.mp4 file produced an MP4 container and then
            # renamed it to .mov/.mkv, leaving the extension and contents at odds.
            temporary = video.with_name(
                f".{video.stem}.resolution.part{video.suffix}"
            )
        else:
            output_root = Path(request.output_dir).expanduser().resolve()
            try:
                relative_parent = video.parent.relative_to(source_root)
            except ValueError:
                relative_parent = Path()
            output_dir = output_root / relative_parent
            output_dir.mkdir(parents=True, exist_ok=True)
            suffix = f"-{request.target_width}x{request.target_height}"
            output = output_dir / f"{video.stem}{suffix}.mp4"
            sequence = 2
            while output.exists():
                output = output_dir / f"{video.stem}{suffix}-{sequence}.mp4"
                sequence += 1
            temporary = output.with_suffix(".part.mp4")
        _width, _height, audio_index, hdr = self._probe(video)
        audio_args = (
            ["-map", f"0:{audio_index}", "-c:a", "aac", "-b:a", "192k"]
            if audio_index is not None
            else []
        )
        filter_graph = normalized_video_filter(
            f"scale={request.target_width}:{request.target_height}:force_original_aspect_ratio=decrease,"
            f"pad={request.target_width}:{request.target_height}:(ow-iw)/2:(oh-ih)/2,setsar=1",
            hdr,
        )
        codec = "h264_nvenc" if self.preferred_codec == "h264_nvenc" and self.nvenc_available else "libx264"
        output_options = (
            ["-movflags", "+faststart"]
            if temporary.suffix.lower() in MOV_FASTSTART_EXTENSIONS
            else []
        )
        def command(selected_codec: str) -> list[str]:
            return [
                self.settings.ffmpeg,
                "-hide_banner", "-loglevel", "error", "-y",
                *hardware_decode_args(selected_codec),
                "-i", str(video),
                "-map", "0:v:0",
                "-vf", filter_graph,
                *encoder_args(selected_codec, 20, self.media_queue.cpu_threads_per_task),
                *sdr_output_args(),
                *audio_args,
                *output_options,
                str(temporary),
            ]

        def run(selected_codec: str):
            with self.media_queue.acquire("resolution"):
                return subprocess.run(
                    command(selected_codec),
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    creationflags=WINDOWS_FFMPEG_FLAGS,
                    check=False,
                )
        try:
            result = run(codec)
            if result.returncode and codec == "h264_nvenc":
                temporary.unlink(missing_ok=True)
                result = run("libx264")
            if result.returncode:
                detail = (result.stderr or "").strip()[-3000:]
                raise ResolutionProcessingError(f"分辨率转换失败：\n{detail}" if detail else "分辨率转换失败")
            if not temporary.is_file() or temporary.stat().st_size < 1000:
                raise ResolutionProcessingError("输出视频校验失败")
            os.replace(temporary, output)
        finally:
            if temporary.exists():
                temporary.unlink(missing_ok=True)
        return {
            "input_path": str(video),
            "output_path": str(output),
            "width": request.target_width,
            "height": request.target_height,
        }
