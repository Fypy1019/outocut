from __future__ import annotations

import json
import logging
import math
import os
import random
import shutil
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from .config import Settings
from .fonts import font_measure
from .media_queue import MediaTaskCancelled, MediaTaskScheduler
from .models import AssetGroupSnapshot, JobCreate
from .processes import WINDOWS_FFMPEG_FLAGS, WINDOWS_NO_WINDOW
from .video_pipeline import (
    encoder_args,
    hardware_decode_args,
    is_hdr_transfer,
    normalized_video_filter,
    sdr_output_args,
)

logger = logging.getLogger("outocut_engine.renderer")


class RenderCancelled(RuntimeError):
    pass


class RenderFailed(RuntimeError):
    pass


ProgressCallback = Callable[[float, str], None]
MINIMUM_CLIP_DURATION = 3.0
MUSIC_SUFFIXES = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}


def select_shot_assets(
    request: JobCreate,
    rng: random.Random,
    required_durations: list[float] | None = None,
    asset_durations: dict[str, float] | None = None,
) -> list[str]:
    groups = [group for group in request.asset_groups if group.asset_paths]
    if required_durations is not None:
        if len(required_durations) != len(request.shots):
            raise ValueError("镜头数量与时长数量不一致")
        known_durations = asset_durations or {}
        available_groups = groups or [
            AssetGroupSnapshot(category_id="all", name="全部素材", asset_paths=request.asset_paths)
        ]
        group_by_id = {group.category_id: index for index, group in enumerate(available_groups)}
        group_order = list(range(len(available_groups)))
        rng.shuffle(group_order)
        used: dict[int, set[str]] = {}
        selected: list[str] = []
        automatic_index = 0

        for index, (shot, required) in enumerate(
            zip(request.shots, required_durations, strict=True)
        ):
            if shot.clip_path:
                candidates = [(shot.clip_path, -1, "指定素材")]
            elif shot.category_id:
                if shot.category_id not in group_by_id:
                    raise RenderFailed(f"镜头 {index + 1} 选择的素材文件夹不存在或没有可用视频")
                group_index = group_by_id[shot.category_id]
                group = available_groups[group_index]
                candidates = [(path, group_index, group.name) for path in group.asset_paths]
            else:
                candidates = []
                for offset in range(len(group_order)):
                    group_index = group_order[(automatic_index + offset) % len(group_order)]
                    group = available_groups[group_index]
                    eligible = [
                        (path, group_index, group.name)
                        for path in group.asset_paths
                        if known_durations.get(path, 0) + 0.05 >= required
                    ]
                    if eligible:
                        candidates = eligible
                        automatic_index = (automatic_index + offset + 1) % len(group_order)
                        break

            eligible = [
                item for item in candidates if known_durations.get(item[0], 0) + 0.05 >= required
            ]
            if not eligible:
                folder_name = candidates[0][2] if candidates else "可用素材文件夹"
                raise RenderFailed(
                    f"镜头 {index + 1} 需要至少 {required:.1f} 秒的视频，"
                    f"但素材文件夹“{folder_name}”中没有时长足够的素材"
                )
            unused = [item for item in eligible if item[0] not in used.setdefault(item[1], set())]
            choice = rng.choice(unused or eligible)
            used.setdefault(choice[1], set()).add(choice[0])
            selected.append(choice[0])
        return selected

    if not groups:
        assets = list(request.asset_paths)
        rng.shuffle(assets)
        return [
            shot.clip_path or assets[index % len(assets)]
            for index, shot in enumerate(request.shots)
        ]

    group_order = list(range(len(groups)))
    rng.shuffle(group_order)
    group_by_id = {group.category_id: index for index, group in enumerate(groups)}
    decks: dict[int, list[str]] = {}
    selected: list[str] = []
    automatic_index = 0
    for index, shot in enumerate(request.shots):
        if shot.clip_path:
            selected.append(shot.clip_path)
            continue
        if shot.category_id:
            if shot.category_id not in group_by_id:
                raise RenderFailed(f"镜头 {index + 1} 选择的素材文件夹不存在或没有可用视频")
            group_index = group_by_id[shot.category_id]
        else:
            group_index = group_order[automatic_index % len(group_order)]
            automatic_index += 1
        deck = decks.get(group_index, [])
        if not deck:
            deck = list(groups[group_index].asset_paths)
            rng.shuffle(deck)
            decks[group_index] = deck
        selected.append(deck.pop())
    return selected


def _ass_time(seconds: float) -> str:
    centiseconds = max(0, round(seconds * 100))
    hours, remainder = divmod(centiseconds, 360_000)
    minutes, remainder = divmod(remainder, 6_000)
    secs, cents = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{cents:02d}"


def _ass_color(hex_color: str, alpha: str = "00") -> str:
    value = hex_color.lstrip("#").upper().rjust(6, "0")[-6:]
    return f"&H{alpha}{value[4:6]}{value[2:4]}{value[0:2]}"


def _ass_text(value: str) -> str:
    return value.replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}").replace("\n", r"\N")


def _wrap_caption_text(
    text: str,
    block_width: int,
    font_size: int,
    spacing: int = 1,
    measure: Callable[[str], float] | None = None,
) -> str:
    """Hard-wrap caption text so every line fits inside ``block_width``.

    libass builds without ICU cannot break lines of CJK text that contains no
    spaces, so ``WrapStyle`` alone is not enough.  ``measure`` returns the
    rendered pixel width of a piece of text at the caption font size; when it
    is unknown (unresolvable font) a conservative ``font_size + spacing`` per
    glyph is assumed.  Measuring the real glyph advances instead of counting
    characters keeps the wrap correct regardless of the font's metrics or any
    uniform size scaling (PlayRes vs. frame size): both the text width and the
    caption block live in the same coordinate space, so the scale cancels out.
    """
    if measure is None:
        per_char = float(font_size + spacing)

        def measure(piece: str) -> float:
            return per_char * len(piece)
    wrapped: list[str] = []
    for line in text.split("\n"):
        if not line:
            wrapped.append("")
            continue
        current = ""
        current_width = 0.0
        for char in line:
            char_width = measure(char)
            if current and current_width + char_width > block_width:
                wrapped.append(current)
                current = char
                current_width = char_width
            else:
                current += char
                current_width += char_width
        wrapped.append(current)
    return "\n".join(wrapped)


def _caption_measure(
    font_name: str, font_path: str | None, font_size: int, spacing: int = 1
) -> Callable[[str], float]:
    """Return a text-width measurer for ``font_name`` at ``font_size`` px.

    The measurer uses the font's real per-glyph advances (cmap + hmtx): a
    full-width CJK glyph in Microsoft YaHei advances only ~0.76em, so a
    character-count wrap would cut lines far before the rendered output
    reaches the container edge.  The font file is resolved via ``font_path``
    (file or directory) or the system font directories; when it cannot be
    resolved, fall back to a conservative ``font_size + spacing`` per glyph.
    """
    measurer = font_measure(font_name, font_path)
    if measurer is None:
        per_char = float(font_size + spacing)
        return lambda piece: per_char * len(piece)
    return lambda piece: measurer.text_width(piece, font_size, spacing)


def _concat_path(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/").replace("'", r"'\''")


def _filter_path(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")


def cpu_encode_thread_count(logical_cpus: int | None = None) -> int:
    total = logical_cpus if logical_cpus is not None else (os.cpu_count() or 1)
    return max(1, total - 2)


def _available_artifact_paths(
    completed_dir: Path,
    subtitle_dir: Path,
    manifest_dir: Path,
    safe_name: str,
    output_index: int,
) -> tuple[Path, Path, Path]:
    for directory in (completed_dir, subtitle_dir, manifest_dir):
        directory.mkdir(parents=True, exist_ok=True)
    base_stem = f"{safe_name}-{output_index + 1:03d}"
    sequence = 1
    while True:
        stem = base_stem if sequence == 1 else f"{base_stem}-{sequence:03d}"
        video = completed_dir / f"{stem}.mp4"
        subtitle = subtitle_dir / f"{stem}.ass"
        manifest = manifest_dir / f"{stem}.json"
        if not any(path.exists() for path in (video, subtitle, manifest)):
            return video, subtitle, manifest
        sequence += 1


def _atomic_publish(source: Path, target: Path) -> None:
    """Expose the final filename once, after the complete file is ready."""
    target.parent.mkdir(parents=True, exist_ok=True)
    same_volume = (
        source.drive.casefold() == target.drive.casefold()
        if os.name == "nt"
        else source.stat().st_dev == target.parent.stat().st_dev
    )
    if same_volume:
        os.replace(source, target)
        return
    staging = target.with_name(f".{target.name}.{uuid4().hex}.part")
    try:
        shutil.copy2(source, staging)
        os.replace(staging, target)
        source.unlink(missing_ok=True)
    finally:
        staging.unlink(missing_ok=True)


def _atomic_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.with_name(f".{target.name}.{uuid4().hex}.part")
    try:
        shutil.copy2(source, staging)
        os.replace(staging, target)
    finally:
        staging.unlink(missing_ok=True)


def _atomic_write_text(target: Path, content: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.with_name(f".{target.name}.{uuid4().hex}.part")
    try:
        staging.write_text(content, encoding="utf-8")
        os.replace(staging, target)
    finally:
        staging.unlink(missing_ok=True)


@dataclass(slots=True)
class RenderArtifact:
    video_path: Path
    subtitle_path: Path
    manifest_path: Path


class FFmpegRenderer:
    def __init__(self, settings: Settings, media_queue: MediaTaskScheduler | None = None):
        self.settings = settings
        self.media_queue = media_queue or MediaTaskScheduler()
        self._process: subprocess.Popen[str] | None = None
        self._process_lock = threading.Lock()
        self._output_lock = threading.Lock()

    def _run(self, args: list[str], cancel: threading.Event) -> None:
        if cancel.is_set():
            raise RenderCancelled("任务已取消")
        try:
            with self.media_queue.acquire("mix", cancel):
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
                with self._process_lock:
                    self._process = process
                try:
                    while process.poll() is None:
                        if cancel.wait(0.1):
                            process.terminate()
                            try:
                                process.wait(timeout=3)
                            except subprocess.TimeoutExpired:
                                process.kill()
                            raise RenderCancelled("任务已取消")
                    _stdout, stderr = process.communicate()
                    if process.returncode != 0:
                        tail = "\n".join(stderr.splitlines()[-30:])
                        logger.error(
                            "FFmpeg 执行失败（返回码 %s）：\n%s",
                            process.returncode,
                            tail or "无错误输出",
                        )
                        detail = tail or "无错误输出"
                        raise RenderFailed(f"视频合成失败：FFmpeg 执行出错。\n{detail}")
                finally:
                    with self._process_lock:
                        self._process = None
        except MediaTaskCancelled as exc:
            raise RenderCancelled("任务已取消") from exc

    @property
    def cpu_encode_threads(self) -> int:
        return self.media_queue.cpu_threads_per_task

    def _probe_duration(self, path: Path) -> float:
        result = subprocess.run(
            [
                self.settings.ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            check=False,
            creationflags=WINDOWS_NO_WINDOW,
        )
        try:
            return max(0.1, float(result.stdout.strip()))
        except ValueError:
            return 0.1

    def _probe_has_audio(self, path: Path) -> bool:
        result = subprocess.run(
            [
                self.settings.ffprobe,
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=index",
                "-of",
                "csv=p=0",
                str(path),
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            check=False,
            creationflags=WINDOWS_NO_WINDOW,
        )
        return result.returncode == 0 and bool(result.stdout.strip())

    def _probe_hdr(self, path: Path) -> bool:
        result = subprocess.run(
            [
                self.settings.ffprobe,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=color_transfer",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            check=False,
            creationflags=WINDOWS_NO_WINDOW,
        )
        return result.returncode == 0 and is_hdr_transfer(result.stdout.strip())

    def _probe_audio_decodes(self, path: Path) -> bool:
        """Return True when the start of an audio file decodes cleanly.

        Guards the final mix against background-music files that FFmpeg
        cannot decode; with an unlimited stream loop such files would
        stall the render forever instead of failing fast.
        """
        result = subprocess.run(
            [
                self.settings.ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-t",
                "1.5",
                "-vn",
                "-i",
                str(path),
                "-f",
                "null",
                "-",
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            check=False,
            creationflags=WINDOWS_NO_WINDOW,
        )
        return result.returncode == 0

    def _select_background_music(self, request: JobCreate, rng: random.Random) -> Path | None:
        template = request.template
        if not template.background_music_enabled:
            return None
        configured = Path(template.background_music).expanduser() if template.background_music else None
        if configured and configured.is_file():
            resolved = configured.resolve()
            if not self._probe_audio_decodes(resolved):
                raise RenderFailed(f"背景音乐文件无法解码：{resolved}")
            return resolved
        directory = (
            Path(template.background_music_directory).expanduser()
            if template.background_music_directory
            else None
        )
        if not directory or not directory.is_dir():
            return None
        candidates = sorted(
            (
                path.resolve()
                for path in directory.iterdir()
                if path.is_file() and path.suffix.casefold() in MUSIC_SUFFIXES
            ),
            key=lambda path: str(path).casefold(),
        )
        if not candidates:
            return None
        rng.shuffle(candidates)
        for candidate in candidates:
            if self._probe_audio_decodes(candidate):
                return candidate
        raise RenderFailed("背景音乐文件夹中没有可解码的音频文件")

    def _write_ass(self, request: JobCreate, durations: list[float], path: Path) -> None:
        captions = request.template.captions
        title_settings = request.template.title_settings
        width = request.template.output.width
        height = request.template.output.height
        # Wrap only when a line would exceed 96% of the video width:
        # symmetric margins of 2% leave width*0.96 for wrapped text.
        caption_margin = max(12, round(width * 0.02))
        lines = [
            "[Script Info]",
            "ScriptType: v4.00+",
            f"PlayResX: {width}",
            f"PlayResY: {height}",
            "ScaledBorderAndShadow: yes",
            "",
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
            "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
            "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding, WrapStyle",
            f"Style: Caption,{captions.font_name},{captions.font_size},{_ass_color(captions.primary_color)},"
            f"{_ass_color(captions.primary_color)},{_ass_color(captions.outline_color)},&H80000000,"
            f"1,0,0,0,100,100,1,0,1,{captions.outline_width},1,2,"
            f"{caption_margin},{caption_margin},{captions.margin_v},1,0",
            f"Style: Title,{title_settings.font_name},{title_settings.font_size},"
            f"{_ass_color(title_settings.primary_color)},{_ass_color(title_settings.primary_color)},"
            f"{_ass_color(title_settings.outline_color)},{_ass_color(title_settings.shadow_color)},"
            f"1,0,0,0,100,100,1,0,1,{title_settings.outline_width},1,"
            f"{8 if title_settings.centered else 7},80,80,120,1,0",
            "",
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        ]
        total = sum(durations)
        if title_settings.enabled and request.template.title:
            title = _ass_text(request.template.title)
            if request.template.subtitle:
                subtitle_size = max(18, title_settings.font_size - 22)
                title += rf"\N{{\fs{subtitle_size}}}" + _ass_text(request.template.subtitle)
            title_end = min(total, title_settings.end_time or total)
            if title_end > title_settings.start_time:
                overrides = rf"{{\xshad{title_settings.shadow_x}\yshad{title_settings.shadow_y}}}"
                lines.append(
                    f"Dialogue: 1,{_ass_time(title_settings.start_time)},{_ass_time(title_end)},"
                    f"Title,,0,0,0,,{overrides}{title}"
                )
        cursor = 0.0
        if captions.enabled:
            block_width = max(1, width - 2 * caption_margin)
            measure = _caption_measure(
                captions.font_name, captions.font_path, captions.font_size
            )
            for shot, duration in zip(request.shots, durations, strict=True):
                text = shot.text.strip()
                if text:
                    effect = r"{\fad(180,180)}" if captions.animation == "fade" else ""
                    text = _wrap_caption_text(
                        text, block_width, captions.font_size, measure=measure
                    )
                    lines.append(
                        f"Dialogue: 2,{_ass_time(cursor)},{_ass_time(cursor + duration)},"
                        f"Caption,,0,0,0,,{effect}{_ass_text(text)}"
                    )
                cursor += duration
        path.write_text("\n".join(lines), encoding="utf-8-sig")

    def _create_segment(
        self,
        clip: Path,
        output: Path,
        duration: float,
        start: float,
        request: JobCreate,
        encoder: str,
        cancel: threading.Event,
    ) -> None:
        settings = request.template.output
        scale_filter = normalized_video_filter(
            f"scale={settings.width}:{settings.height}:force_original_aspect_ratio=increase,"
            f"crop={settings.width}:{settings.height},fps={settings.fps}",
            self._probe_hdr(clip),
        )
        args = [
            self.settings.ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{start:.3f}",
            *hardware_decode_args(encoder),
            "-i",
            str(clip),
            "-t",
            f"{duration:.3f}",
            "-vf",
            scale_filter,
            "-an",
            *encoder_args(encoder, settings.quality, self.cpu_encode_threads),
            *sdr_output_args(),
        ]
        args.extend(["-movflags", "+faststart", str(output)])
        self._run(args, cancel)

    def _create_audio_segment(
        self,
        voice_source: Path | None,
        clip_source: Path,
        include_clip_audio: bool,
        output: Path,
        duration: float,
        start: float,
        voice_volume: float,
        cancel: threading.Event,
    ) -> None:
        common = ["-t", f"{duration:.3f}", "-ar", "48000", "-ac", "2", "-c:a", "aac", "-b:a", "160k"]
        has_voice = bool(voice_source and voice_source.is_file())
        has_clip_audio = include_clip_audio and self._probe_has_audio(clip_source)
        if has_voice and has_clip_audio:
            args = [
                self.settings.ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(voice_source),
                "-ss",
                f"{start:.3f}",
                "-i",
                str(clip_source),
                "-filter_complex",
                (
                    f"[0:a]volume={voice_volume:.3f},apad,atrim=0:{duration:.3f}[voice];"
                    f"[1:a]apad,atrim=0:{duration:.3f}[source];"
                    f"[voice][source]amix=inputs=2:duration=longest:normalize=0,"
                    f"atrim=0:{duration:.3f}[aout]"
                ),
                "-map",
                "[aout]",
                *common,
                str(output),
            ]
        elif has_voice:
            args = [
                self.settings.ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(voice_source),
                "-af",
                f"volume={voice_volume:.3f},apad,atrim=0:{duration:.3f}",
                *common,
                str(output),
            ]
        elif has_clip_audio:
            args = [
                self.settings.ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-ss",
                f"{start:.3f}",
                "-i",
                str(clip_source),
                "-vn",
                "-af",
                f"apad,atrim=0:{duration:.3f}",
                *common,
                str(output),
            ]
        else:
            args = [
                self.settings.ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "anullsrc=r=48000:cl=stereo",
                *common,
                str(output),
            ]
        self._run(args, cancel)

    def render(
        self,
        job_id: str,
        request: JobCreate,
        output_index: int,
        cancel: threading.Event,
        progress: ProgressCallback,
    ) -> RenderArtifact:
        rng = random.Random(request.seed + output_index)
        cache_root = (
            Path(request.cache_dir).expanduser().resolve()
            if request.cache_dir
            else self.settings.data_root / "temp"
        )
        job_temp = cache_root / f"{job_id}-{output_index}"
        if job_temp.exists():
            shutil.rmtree(job_temp, ignore_errors=True)
        job_temp.mkdir(parents=True)
        output_root = Path(request.output_dir).expanduser().resolve()
        completed_dir = output_root / "Completed"
        subtitle_dir = output_root / "ass"
        manifest_dir = output_root / "json"
        safe_name = (
            "".join(char if char not in '<>:"/\\|?*' else "_" for char in request.name).strip() or "OutoCut"
        )
        temp_final = job_temp / "result.part.mp4"
        ass_path = job_temp / "captions.ass"

        narration_durations: list[float | None] = []
        for shot in request.shots:
            voice_path = Path(shot.voice_path) if shot.voice_path else None
            narration_durations.append(
                max(0.5, self._probe_duration(voice_path))
                if request.template.voice_enabled and voice_path and voice_path.is_file()
                else max(0.5, request.template.default_shot_duration)
                if request.template.voice_enabled
                else None
            )

        asset_durations: dict[str, float] = {}
        for group in request.asset_groups:
            asset_durations.update(group.asset_durations)
        candidate_paths = {
            *request.asset_paths,
            *(path for group in request.asset_groups for path in group.asset_paths),
            *(shot.clip_path for shot in request.shots if shot.clip_path),
        }
        for clip_value in candidate_paths:
            clip_path = Path(clip_value)
            if not clip_path.is_file():
                asset_durations[clip_value] = 0
            elif clip_value not in asset_durations:
                asset_durations[clip_value] = self._probe_duration(clip_path)
        required_durations = [
            max(MINIMUM_CLIP_DURATION, duration or MINIMUM_CLIP_DURATION)
            for duration in narration_durations
        ]
        selected = select_shot_assets(request, rng, required_durations, asset_durations)
        clip_durations = [self._probe_duration(Path(path)) for path in selected]
        durations = [
            narration_duration if narration_duration is not None else clip_duration
            for narration_duration, clip_duration in zip(
                narration_durations, clip_durations, strict=True
            )
        ]

        encoder = request.template.output.codec
        total_steps = len(request.shots) * 2 + 3
        completed = 0
        video_segments: list[Path] = []
        audio_segments: list[Path] = []
        try:
            for index, (shot, clip_value, duration) in enumerate(
                zip(request.shots, selected, durations, strict=True)
            ):
                clip_path = Path(clip_value)
                clip_duration = clip_durations[index]
                start = (
                    rng.uniform(0, max(0, clip_duration - duration))
                    if request.template.voice_enabled and clip_duration > duration
                    else 0
                )
                video_segment = job_temp / f"video-{index:03d}.mp4"
                try:
                    self._create_segment(clip_path, video_segment, duration, start, request, encoder, cancel)
                except RenderFailed:
                    if encoder != "h264_nvenc":
                        raise
                    encoder = "libx264"
                    self._create_segment(clip_path, video_segment, duration, start, request, encoder, cancel)
                video_segments.append(video_segment)
                completed += 1
                progress(completed / total_steps, f"正在处理镜头 {index + 1}/{len(request.shots)}")

                audio_segment = job_temp / f"audio-{index:03d}.m4a"
                self._create_audio_segment(
                    Path(shot.voice_path)
                    if request.template.voice_enabled and shot.voice_path
                    else None,
                    clip_path,
                    not shot.muted,
                    audio_segment,
                    duration,
                    start,
                    request.template.voice_volume,
                    cancel,
                )
                audio_segments.append(audio_segment)
                completed += 1
                progress(completed / total_steps, f"正在处理镜头音频 {index + 1}/{len(request.shots)}")

            video_list = job_temp / "videos.txt"
            audio_list = job_temp / "audios.txt"
            video_list.write_text(
                "\n".join(f"file '{_concat_path(path)}'" for path in video_segments), encoding="utf-8"
            )
            audio_list.write_text(
                "\n".join(f"file '{_concat_path(path)}'" for path in audio_segments), encoding="utf-8"
            )
            joined_video = job_temp / "joined-video.mp4"
            joined_audio = job_temp / "joined-audio.m4a"
            self._run(
                [
                    self.settings.ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(video_list),
                    "-c",
                    "copy",
                    str(joined_video),
                ],
                cancel,
            )
            self._run(
                [
                    self.settings.ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(audio_list),
                    "-c",
                    "copy",
                    str(joined_audio),
                ],
                cancel,
            )
            completed += 1
            progress(completed / total_steps, "正在合并镜头")

            self._write_ass(request, durations, ass_path)
            final_args = [
                self.settings.ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                *hardware_decode_args(encoder),
                "-i",
                str(joined_video),
                "-i",
                str(joined_audio),
            ]
            music = self._select_background_music(request, rng)
            duration_limit: float | None = None
            if music:
                duration_limit = sum(durations)
                bgm_duration = self._probe_duration(music)
                if bgm_duration < 0.5:
                    bgm_duration = duration_limit
                loop_count = max(0, math.ceil(duration_limit / bgm_duration) - 1)
                if loop_count > 0:
                    final_args.extend(["-stream_loop", str(loop_count), "-i", str(music)])
                else:
                    final_args.extend(["-i", str(music)])
                volume = request.template.background_music_volume
                final_args.extend(
                    [
                        "-filter_complex",
                        f"[1:a]anull[main];[2:a]volume={volume:.3f}[music];[main][music]amix=inputs=2:duration=first:dropout_transition=2[aout]",
                        "-map",
                        "0:v:0",
                        "-map",
                        "[aout]",
                    ]
                )
            else:
                final_args.extend(
                    [
                        "-filter_complex",
                        "[1:a]anull[aout]",
                        "-map",
                        "0:v:0",
                        "-map",
                        "[aout]",
                    ]
                )
            font_source = request.template.captions.font_path or request.template.title_settings.font_path
            font_directory = None
            if font_source:
                font_path = Path(font_source)
                font_directory = font_path if font_path.is_dir() else font_path.parent
            ass_filter = f"ass='{_filter_path(ass_path)}'"
            if font_directory and font_directory.is_dir():
                ass_filter += f":fontsdir='{_filter_path(font_directory)}'"
            final_start = list(final_args)
            final_args.extend(
                [
                    "-vf",
                    ass_filter,
                    *encoder_args(
                        encoder,
                        request.template.output.quality,
                        self.cpu_encode_threads,
                    ),
                    *sdr_output_args(),
                    "-c:a",
                    "aac",
                    "-b:a",
                    "192k",
                    *(["-t", f"{duration_limit:.3f}"] if duration_limit is not None else []),
                    "-shortest",
                    "-movflags",
                    "+faststart",
                    str(temp_final),
                ]
            )
            try:
                self._run(final_args, cancel)
            except RenderFailed:
                if encoder != "h264_nvenc":
                    raise
                temp_final.unlink(missing_ok=True)
                cpu_args = [
                    *final_start,
                    "-vf",
                    ass_filter,
                    *encoder_args(
                        "libx264",
                        request.template.output.quality,
                        self.cpu_encode_threads,
                    ),
                    *sdr_output_args(),
                    "-c:a",
                    "aac",
                    "-b:a",
                    "192k",
                ]
                if duration_limit is not None:
                    cpu_args.extend(["-t", f"{duration_limit:.3f}"])
                cpu_args.extend(
                    ["-shortest", "-movflags", "+faststart", str(temp_final)]
                )
                self._run(cpu_args, cancel)
            completed += 2
            progress(min(completed / total_steps, 0.99), "正在校验成片")
            if self._probe_duration(temp_final) <= 0.2:
                raise RenderFailed("生成的成片无法读取")
            with self._output_lock:
                final_path, output_ass, manifest_path = _available_artifact_paths(
                    completed_dir,
                    subtitle_dir,
                    manifest_dir,
                    safe_name,
                    output_index,
                )
                _atomic_publish(temp_final, final_path)
            manifest = {
                "job_id": job_id,
                "output_index": output_index,
                "seed": request.seed + output_index,
                "assets": selected,
                "durations": durations,
                "template": request.template.model_dump(mode="json"),
                "shots": [shot.model_dump(mode="json") for shot in request.shots],
            }
            _atomic_write_text(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2))
            _atomic_copy(ass_path, output_ass)
            progress(1.0, "合成完成")
            return RenderArtifact(final_path, output_ass, manifest_path)
        finally:
            shutil.rmtree(job_temp, ignore_errors=True)
