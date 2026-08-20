from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Settings:
    data_root: Path
    session_token: str
    ffmpeg: str
    ffprobe: str

    @classmethod
    def from_env(cls) -> Settings:
        local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        data_root = Path(os.environ.get("OUTOCUT_DATA_ROOT", local_app_data / "OutoCut" / "workspace"))
        return cls(
            data_root=data_root.resolve(),
            session_token=os.environ.get("OUTOCUT_SESSION_TOKEN", "development-token"),
            ffmpeg=os.environ.get("OUTOCUT_FFMPEG", shutil.which("ffmpeg") or "ffmpeg"),
            ffprobe=os.environ.get("OUTOCUT_FFPROBE", shutil.which("ffprobe") or "ffprobe"),
        )

    def ensure_directories(self) -> None:
        for name in ("assets", "cache", "exports", "logs", "voices", "temp"):
            (self.data_root / name).mkdir(parents=True, exist_ok=True)
