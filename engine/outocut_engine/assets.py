from __future__ import annotations

import json
import subprocess
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from .config import Settings
from .db import Database, utc_now
from .models import AssetCategory, AssetClip, AssetFolderView, AssetIssue, AssetRootOverview
from .processes import WINDOWS_NO_WINDOW

SUPPORTED_VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}
MINIMUM_CLIP_DURATION = 3.0


def _fraction(value: str | None) -> float:
    if not value or value == "0/0":
        return 0
    numerator, _, denominator = value.partition("/")
    try:
        return float(numerator) / float(denominator or 1)
    except (TypeError, ValueError, ZeroDivisionError):
        return 0


class AssetScanner:
    def __init__(self, settings: Settings, db: Database):
        self.settings = settings
        self.db = db

    def probe(self, category_id: str, path: Path) -> AssetClip:
        clip = AssetClip(category_id=category_id, path=str(path.resolve()), name=path.name)
        try:
            result = subprocess.run(
                [
                    self.settings.ffprobe,
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration:stream=index,codec_type,codec_name,width,height,r_frame_rate,color_transfer:stream_tags=rotate",
                    "-of",
                    "json",
                    str(path),
                ],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
                creationflags=WINDOWS_NO_WINDOW,
            )
            if result.returncode != 0:
                clip.issue = AssetIssue.CORRUPT
                clip.issue_message = (result.stderr or "ffprobe 无法读取文件")[-500:]
                return clip
            payload = json.loads(result.stdout)
            video = next(
                (stream for stream in payload.get("streams", []) if stream.get("codec_type") == "video"), None
            )
            if not video:
                clip.issue = AssetIssue.UNSUPPORTED
                clip.issue_message = "文件中没有视频轨道"
                return clip
            clip.duration = float(payload.get("format", {}).get("duration") or 0)
            clip.width = int(video.get("width") or 0)
            clip.height = int(video.get("height") or 0)
            clip.fps = round(_fraction(video.get("r_frame_rate")), 3)
            clip.codec = str(video.get("codec_name") or "")
            clip.rotation = int(video.get("tags", {}).get("rotate") or 0)
            clip.has_audio = any(stream.get("codec_type") == "audio" for stream in payload.get("streams", []))
            clip.hdr = video.get("color_transfer") in {"smpte2084", "arib-std-b67"}
            if clip.hdr:
                clip.issue = AssetIssue.HDR
                clip.issue_message = "HDR素材将在合成时转换为SDR"
            if clip.duration <= 0 or clip.width <= 0 or clip.height <= 0:
                clip.issue = AssetIssue.CORRUPT
                clip.issue_message = "视频元数据不完整"
            elif clip.duration + 0.001 < MINIMUM_CLIP_DURATION:
                clip.issue = AssetIssue.TOO_SHORT
                clip.issue_message = "不可用：视频时长不足3秒"
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError, ValueError) as exc:
            clip.issue = AssetIssue.CORRUPT
            clip.issue_message = str(exc)[:500]
        return clip

    def scan(self, category: AssetCategory) -> list[AssetClip]:
        root = Path(category.directory)
        if not root.is_dir():
            raise FileNotFoundError(f"素材目录不存在：{root}")
        clips: list[AssetClip] = []
        for path in sorted(root.rglob("*"), key=lambda item: str(item).casefold()):
            if not path.is_file() or path.suffix.casefold() not in SUPPORTED_VIDEO_SUFFIXES:
                continue
            clips.append(self.probe(category.id, path))
        now = utc_now()
        with self.db.connection() as connection:
            connection.execute("DELETE FROM asset_clips WHERE category_id=?", (category.id,))
            connection.executemany(
                "INSERT INTO asset_clips(id, category_id, path, payload, updated_at) VALUES(?, ?, ?, ?, ?)",
                [(clip.id, category.id, clip.path, clip.model_dump_json(), now) for clip in clips],
            )
            connection.execute(
                """UPDATE asset_categories SET scanned_at=?, clip_count=?, issue_count=? WHERE id=?""",
                (now, len(clips), sum(clip.issue != AssetIssue.NONE for clip in clips), category.id),
            )
            connection.commit()
        return clips

    def scan_root(self, root: Path) -> AssetRootOverview:
        root = root.expanduser().resolve()
        if not root.is_dir():
            raise FileNotFoundError(f"素材根目录不存在：{root}")
        directories = sorted(
            (path for path in root.iterdir() if path.is_dir() and not path.is_symlink()),
            key=lambda path: path.name.casefold(),
        )
        categories = [
            AssetCategory(
                id=uuid5(NAMESPACE_URL, str(path).casefold()).hex,
                name=path.name,
                directory=str(path),
            )
            for path in directories
        ]
        ids = [category.id for category in categories]
        with self.db.connection() as connection:
            if ids:
                placeholders = ",".join("?" for _ in ids)
                connection.execute(
                    f"DELETE FROM asset_categories WHERE id NOT IN ({placeholders})",
                    ids,
                )
            else:
                connection.execute("DELETE FROM asset_categories")
            for category in categories:
                connection.execute(
                    """INSERT INTO asset_categories(id, name, directory, scanned_at, clip_count, issue_count)
                       VALUES(?, ?, ?, NULL, 0, 0)
                       ON CONFLICT(id) DO UPDATE SET name=excluded.name, directory=excluded.directory""",
                    (category.id, category.name, category.directory),
                )
            connection.commit()
        for category in categories:
            self.scan(category)
        return self.root_overview(root)

    def folder_view(self, category: AssetCategory) -> AssetFolderView:
        clips = self.list_clips(category.id)
        usable = [clip for clip in clips if clip.issue in {AssetIssue.NONE, AssetIssue.HDR}]
        directory = Path(category.directory)
        unsupported_count = 0
        if directory.is_dir():
            unsupported_count = sum(
                1
                for path in directory.rglob("*")
                if path.is_file() and path.suffix.casefold() not in SUPPORTED_VIDEO_SUFFIXES
            )
        return AssetFolderView(
            **category.model_dump(),
            usable_count=len(usable),
            unsupported_file_count=unsupported_count,
            hdr_count=sum(clip.hdr for clip in clips),
            preview_paths=[clip.path for clip in usable[:4]],
        )

    def root_overview(self, root: Path | None) -> AssetRootOverview:
        if root is None:
            return AssetRootOverview()
        root = root.expanduser().resolve()
        rows = self.db.fetch_all("SELECT * FROM asset_categories ORDER BY name")
        folders = [self.folder_view(AssetCategory.model_validate(dict(row))) for row in rows]
        scanned_values = [folder.scanned_at for folder in folders if folder.scanned_at]
        return AssetRootOverview(
            root_directory=str(root),
            folders=folders,
            folder_count=len(folders),
            usable_clip_count=sum(folder.usable_count for folder in folders),
            unsupported_file_count=sum(folder.unsupported_file_count for folder in folders),
            insufficient_folder_count=sum(folder.usable_count < 3 for folder in folders),
            scanned_at=max(scanned_values) if scanned_values else None,
        )

    def list_clips(self, category_id: str | None = None) -> list[AssetClip]:
        if category_id:
            rows = self.db.fetch_all(
                "SELECT payload FROM asset_clips WHERE category_id=? ORDER BY path", (category_id,)
            )
        else:
            rows = self.db.fetch_all("SELECT payload FROM asset_clips ORDER BY path")
        clips: list[AssetClip] = []
        for row in rows:
            clip = AssetClip.model_validate_json(row["payload"])
            if not Path(clip.path).exists():
                clip.issue = AssetIssue.MISSING
                clip.issue_message = "素材文件已被移动或删除"
            elif clip.issue in {AssetIssue.NONE, AssetIssue.HDR, AssetIssue.TOO_SHORT}:
                if clip.duration + 0.001 < MINIMUM_CLIP_DURATION:
                    clip.issue = AssetIssue.TOO_SHORT
                    clip.issue_message = "不可用：视频时长不足3秒"
                elif clip.hdr:
                    clip.issue = AssetIssue.HDR
                    clip.issue_message = "HDR素材将在合成时转换为SDR"
                else:
                    clip.issue = AssetIssue.NONE
                    clip.issue_message = ""
            clips.append(clip)
        return clips
