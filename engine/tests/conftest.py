from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from outocut_engine.app import create_app


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OUTOCUT_DATA_ROOT", str(tmp_path / "workspace"))
    monkeypatch.setenv("OUTOCUT_SESSION_TOKEN", "test-token")
    monkeypatch.setenv("OUTOCUT_FFMPEG", os.environ.get("OUTOCUT_FFMPEG", "ffmpeg"))
    monkeypatch.setenv("OUTOCUT_FFPROBE", os.environ.get("OUTOCUT_FFPROBE", "ffprobe"))
    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture()
def auth_headers() -> dict[str, str]:
    return {"Authorization": "Bearer test-token"}
