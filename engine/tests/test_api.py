from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path

from fastapi.testclient import TestClient

from outocut_engine.providers import MiniMaxClient


def create_video(path: Path, color: str = "purple", duration: float = 1.2) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s=360x640:d={duration}:r=25",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
    )


def create_video_with_audio(path: Path, duration: float = 3.4) -> None:
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"color=c=teal:s=360x640:d={duration}:r=25",
            "-f", "lavfi", "-i", f"sine=frequency=880:duration={duration}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path),
        ],
        check=True,
    )


def probe_duration(path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, check=True,
    )
    return float(result.stdout.strip())


def probe_mean_volume(path: Path) -> float:
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(path), "-af", "volumedetect", "-f", "null", "NUL"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    match = re.search(r"mean_volume:\s*(-?[\d.]+) dB", result.stderr)
    return float(match.group(1)) if match else -100.0


def create_sized_video(path: Path, size: str, duration: float = 0.3) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"color=c=navy:s={size}:d={duration}:r=25",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path),
        ],
        check=True,
    )


def create_hdr_video(path: Path, size: str = "360x640", duration: float = 0.3) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"testsrc2=s={size}:d={duration}:r=25",
            "-vf", "format=yuv420p10le",
            "-c:v", "libx265", "-preset", "ultrafast",
            "-x265-params", "colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc",
            str(path),
        ],
        check=True,
    )


def create_sticker(path: Path) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=yellow:s=80x80",
            "-frames:v",
            "1",
            str(path),
        ],
        check=True,
    )


def create_gif(path: Path) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=s=90x160:r=5:d=0.4",
            str(path),
        ],
        check=True,
    )


def create_rotated_video(path: Path) -> None:
    encoded = path.with_name("coded-landscape.mp4")
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=640x360:r=25:d=0.8",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(encoded),
        ],
        check=True,
    )
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-display_rotation:v:0",
            "90",
            "-i",
            str(encoded),
            "-c",
            "copy",
            str(path),
        ],
        check=True,
    )


def probe_video_size(path: Path) -> tuple[int, int]:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "json",
            str(path),
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=True,
    )
    stream = json.loads(result.stdout)["streams"][0]
    return int(stream["width"]), int(stream["height"])


def probe_container_brand(path: Path) -> str:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format_tags=major_brand",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def probe_color_transfer(path: Path) -> str:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=color_transfer",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def test_authentication_and_health(client: TestClient, auth_headers: dict[str, str]) -> None:
    assert client.get("/health").status_code == 401
    response = client.get("/health", headers=auth_headers)
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["ffmpeg"] is True


def test_job_queue_can_pause_and_resume(client: TestClient, auth_headers: dict[str, str]) -> None:
    initial = client.get("/jobs-queue", headers=auth_headers)
    assert initial.status_code == 200
    assert initial.json()["paused"] is False

    paused = client.post("/jobs-queue/pause", headers=auth_headers)
    assert paused.status_code == 200
    assert paused.json()["paused"] is True
    assert paused.json()["automatic"] is False

    resumed = client.post("/jobs-queue/resume", headers=auth_headers)
    assert resumed.status_code == 200
    assert resumed.json()["paused"] is False


def test_minimax_voice_list_clone_preview_and_local_profile_persistence(
    client: TestClient,
    auth_headers: dict[str, str],
    tmp_path: Path,
    monkeypatch,
) -> None:
    client.post(
        "/settings/secrets",
        headers=auth_headers,
        json={"minimax_api_key": "test-minimax-key"},
    )

    async def fake_list(_self):
        return [
            {
                "type": "system_voice",
                "voice_id": "Chinese_Test_Voice",
                "voice_name": "测试系统音色",
                "description": ["用于自动测试"],
                "created_time": "1970-01-01",
            }
        ]

    async def fake_clone(_self, _request):
        return {"base_resp": {"status_code": 0, "status_msg": "success"}}

    async def fake_tts(_self, _request, output_path: Path):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"test-audio")
        return output_path

    monkeypatch.setattr(MiniMaxClient, "list_voices", fake_list)
    monkeypatch.setattr(MiniMaxClient, "clone", fake_clone)
    monkeypatch.setattr(MiniMaxClient, "tts", fake_tts)

    voices = client.get("/voices", headers=auth_headers)
    assert voices.status_code == 200
    assert voices.json()[0]["voice_id"] == "Chinese_Test_Voice"

    source = tmp_path / "clone.wav"
    source.write_bytes(b"source")
    cloned = client.post(
        "/voices/clone",
        headers=auth_headers,
        json={
            "source_path": str(source),
            "voice_name": "品牌主理人",
            "voice_id": "BrandOwner2026",
            "preview_text": "声音复刻测试",
            "model": "speech-2.8-hd",
        },
    )
    assert cloned.status_code == 200
    assert cloned.json()["voice"]["voice_name"] == "品牌主理人"
    assert Path(cloned.json()["voice"]["preview_path"]).is_file()

    refreshed = client.get("/voices", headers=auth_headers).json()
    saved = next(voice for voice in refreshed if voice["voice_id"] == "BrandOwner2026")
    assert saved["saved_locally"] is True
    assert saved["voice_name"] == "品牌主理人"


def test_static_sticker_overlay(
    client: TestClient, auth_headers: dict[str, str], tmp_path: Path
) -> None:
    source = tmp_path / "overlay-source.mp4"
    sticker = tmp_path / "sticker.bmp"
    output = tmp_path / "overlay-output"
    create_video(source, duration=0.6)
    create_sticker(sticker)

    response = client.post(
        "/overlays/render",
        headers=auth_headers,
        json={
            "video_path": str(source),
            "overlay_path": str(sticker),
            "output_dir": str(output),
            "mode": "sticker",
            "opacity": 0.6,
            "scale": 0.5,
            "position": "bottom_right",
        },
    )

    assert response.status_code == 200, response.text
    result = Path(response.json()["output_path"])
    assert result.is_file()
    assert result.stat().st_size > 1000


def test_full_frame_gif_watermark_loops_until_video_ends(
    client: TestClient, auth_headers: dict[str, str], tmp_path: Path
) -> None:
    source = tmp_path / "watermark-source.mp4"
    watermark = tmp_path / "watermark.gif"
    output = tmp_path / "watermark-output"
    create_video(source, duration=0.9)
    create_gif(watermark)

    response = client.post(
        "/overlays/render",
        headers=auth_headers,
        json={
            "video_path": str(source),
            "overlay_path": str(watermark),
            "output_dir": str(output),
            "mode": "watermark",
            "opacity": 0.35,
        },
    )

    assert response.status_code == 200, response.text
    result = Path(response.json()["output_path"])
    assert result.is_file()
    assert result.stat().st_size > 1000


def test_gif_watermark_preserves_rotated_mov_display_ratio(
    client: TestClient, auth_headers: dict[str, str], tmp_path: Path
) -> None:
    source = tmp_path / "rotated.mov"
    watermark = tmp_path / "rotated-watermark.gif"
    output = tmp_path / "rotated-output"
    create_rotated_video(source)
    create_gif(watermark)

    response = client.post(
        "/overlays/render",
        headers=auth_headers,
        json={
            "video_path": str(source),
            "overlay_path": str(watermark),
            "output_dir": str(output),
            "mode": "watermark",
            "opacity": 0.35,
        },
    )

    assert response.status_code == 200, response.text
    result = Path(response.json()["output_path"])
    assert probe_video_size(result) == (360, 640)


def test_composite_overlay_supports_all_layers_and_multiple_sticker_positions(
    client: TestClient, auth_headers: dict[str, str], tmp_path: Path
) -> None:
    source = tmp_path / "composite-source.mp4"
    watermark = tmp_path / "composite-watermark.gif"
    pixel = tmp_path / "composite-pixel.gif"
    sticker = tmp_path / "composite-sticker.bmp"
    output = tmp_path / "composite-output"
    create_video(source, duration=0.7)
    create_gif(watermark)
    create_gif(pixel)
    create_sticker(sticker)

    response = client.post(
        "/overlays/composite",
        headers=auth_headers,
        json={
            "video_path": str(source),
            "output_dir": str(output),
            "watermark": {"path": str(watermark), "opacity": 0.25},
            "sticker": {
                "path": str(sticker),
                "opacity": 0.8,
                "scale": 0.01,
                "positions": ["top_left", "center", "bottom_right"],
            },
            "pixel": {"path": str(pixel), "opacity": 0.2},
        },
    )

    assert response.status_code == 200, response.text
    result = Path(response.json()["output_path"])
    assert result.is_file()
    assert result.stat().st_size > 1000


def test_resolution_scan_recurses_and_conversion_outputs_exact_target(
    client: TestClient, auth_headers: dict[str, str], tmp_path: Path
) -> None:
    source_root = tmp_path / "resolution-source"
    portrait = source_root / "nested" / "portrait.mp4"
    landscape = source_root / "landscape.mp4"
    create_sized_video(portrait, "360x640")
    create_sized_video(landscape, "640x360")

    scan = client.post(
        "/resolutions/scan",
        headers=auth_headers,
        json={"root_directory": str(source_root)},
    )
    assert scan.status_code == 200, scan.text
    videos = scan.json()["videos"]
    assert len(videos) == 2
    assert {item["orientation"] for item in videos} == {"portrait", "landscape"}
    assert all(not item["qualified"] for item in videos)

    output_root = tmp_path / "resolution-output"
    converted = client.post(
        "/resolutions/convert",
        headers=auth_headers,
        json={
            "video_path": str(portrait),
            "source_root": str(source_root),
            "output_dir": str(output_root),
            "target_width": 720,
            "target_height": 1280,
        },
    )
    assert converted.status_code == 200, converted.text
    output = Path(converted.json()["output_path"])
    assert output.parent == output_root / "nested"
    assert probe_video_size(output) == (720, 1280)

    replace_source = source_root / "replace-original.mp4"
    create_sized_video(replace_source, "360x640")
    replaced = client.post(
        "/resolutions/convert",
        headers=auth_headers,
        json={
            "video_path": str(replace_source),
            "source_root": str(source_root),
            "replace_original": True,
            "target_width": 720,
            "target_height": 1280,
        },
    )
    assert replaced.status_code == 200, replaced.text
    assert Path(replaced.json()["output_path"]) == replace_source
    assert probe_video_size(replace_source) == (720, 1280)


def test_resolution_conversion_tonemaps_hdr_to_bt709(
    client: TestClient, auth_headers: dict[str, str], tmp_path: Path
) -> None:
    source_root = tmp_path / "resolution-hdr-source"
    source = source_root / "portrait-hdr.mp4"
    create_hdr_video(source)
    assert probe_color_transfer(source) == "smpte2084"

    response = client.post(
        "/resolutions/convert",
        headers=auth_headers,
        json={
            "video_path": str(source),
            "source_root": str(source_root),
            "output_dir": str(tmp_path / "resolution-hdr-output"),
            "target_width": 720,
            "target_height": 1280,
        },
    )

    assert response.status_code == 200, response.text
    output = Path(response.json()["output_path"])
    assert probe_video_size(output) == (720, 1280)
    assert probe_color_transfer(output) == "bt709"


def test_resolution_replace_preserves_mov_container(
    client: TestClient, auth_headers: dict[str, str], tmp_path: Path
) -> None:
    source_root = tmp_path / "resolution-mov-source"
    source = source_root / "portrait.mov"
    create_sized_video(source, "360x640")

    response = client.post(
        "/resolutions/convert",
        headers=auth_headers,
        json={
            "video_path": str(source),
            "source_root": str(source_root),
            "replace_original": True,
            "target_width": 720,
            "target_height": 1280,
        },
    )

    assert response.status_code == 200, response.text
    assert Path(response.json()["output_path"]) == source
    assert probe_video_size(source) == (720, 1280)
    assert probe_container_brand(source) == "qt"


def test_resolution_replace_rejects_incompatible_original_container(
    client: TestClient, auth_headers: dict[str, str], tmp_path: Path
) -> None:
    source_root = tmp_path / "resolution-webm-source"
    source_root.mkdir()
    source = source_root / "portrait.webm"
    source.write_bytes(b"not probed because the container is rejected first")

    response = client.post(
        "/resolutions/convert",
        headers=auth_headers,
        json={
            "video_path": str(source),
            "source_root": str(source_root),
            "replace_original": True,
            "target_width": 720,
            "target_height": 1280,
        },
    )

    assert response.status_code == 400
    assert "不支持安全原位替换" in response.json()["detail"]


def test_editing_and_storage_settings_are_persisted(
    client: TestClient, auth_headers: dict[str, str], tmp_path: Path
) -> None:
    current = client.get("/settings", headers=auth_headers).json()
    current.update(
        {
            "cache_directory": str(tmp_path / "cache"),
            "output_directory": str(tmp_path / "exports"),
            "default_width": 1920,
            "default_height": 1080,
            "default_fps": 25,
            "default_codec": "libx264",
            "default_quality": 28,
            "asr_model": "paraformer-realtime-v1",
            "rewrite_instructions": "Use a concise and natural tone.",
        }
    )
    saved = client.put("/settings", headers=auth_headers, json=current)
    assert saved.status_code == 200, saved.text
    payload = client.get("/settings", headers=auth_headers).json()
    assert payload["cache_directory"] == str(tmp_path / "cache")
    assert payload["default_width"] == 1920
    assert payload["default_fps"] == 25
    assert payload["default_quality"] == 28
    assert payload["asr_model"] == "paraformer-realtime-v1"
    assert payload["rewrite_instructions"] == "Use a concise and natural tone."


def test_media_queue_limit_updates_with_settings(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    current = client.get("/settings", headers=auth_headers).json()
    current["max_concurrent_jobs"] = 2
    saved = client.put("/settings", headers=auth_headers, json=current)

    assert saved.status_code == 200, saved.text
    status = client.get("/media-queue", headers=auth_headers)
    assert status.status_code == 200, status.text
    assert status.json()["configured_limit"] == 2
    assert status.json()["effective_limit"] <= 2
    assert status.json()["cpu_threads_per_task"] >= 1


def test_asset_scan_and_crud(client: TestClient, auth_headers: dict[str, str], tmp_path: Path) -> None:
    source = tmp_path / "中文素材"
    source.mkdir()
    create_video(source / "样片.mp4", duration=4.2)
    category = {
        "id": "category-1",
        "name": "产品展示",
        "directory": str(source),
        "scanned_at": None,
        "clip_count": 0,
        "issue_count": 0,
    }
    assert client.post("/assets/categories", headers=auth_headers, json=category).status_code == 200
    response = client.post("/assets/scan?category_id=category-1", headers=auth_headers)
    assert response.status_code == 200
    clips = response.json()
    assert len(clips) == 1
    assert clips[0]["width"] == 360
    assert clips[0]["height"] == 640
    assert clips[0]["issue"] == "none"

    persona = {
        "id": "persona-1",
        "name": "门店主理人",
        "industry": "餐饮",
        "audience": "附近居民",
        "selling_points": "信息真实",
        "experience": "经营五年",
        "tone": "自然",
        "prohibited_claims": ["全网第一"],
    }
    assert client.post("/personas", headers=auth_headers, json=persona).status_code == 200
    assert client.get("/personas", headers=auth_headers).json()[0]["name"] == "门店主理人"


def test_root_scan_creates_one_view_per_child_folder(
    client: TestClient, auth_headers: dict[str, str], tmp_path: Path
) -> None:
    root = tmp_path / "asset-root"
    action = root / "action"
    product = root / "product"
    action.mkdir(parents=True)
    product.mkdir()
    create_video(action / "action-01.mp4", "red", 4.2)
    create_video(product / "product-01.mp4", "blue", 4.2)
    (product / "notes.txt").write_text("not a video", encoding="utf-8")
    (root / "ignored-at-root.mp4").write_bytes(b"not scanned")

    response = client.post(
        "/assets/root/scan",
        headers=auth_headers,
        json={"root_directory": str(root)},
    )
    assert response.status_code == 200, response.text
    overview = response.json()
    assert overview["root_directory"] == str(root.resolve())
    assert overview["folder_count"] == 2
    assert overview["usable_clip_count"] == 2
    assert overview["unsupported_file_count"] == 1
    assert {folder["name"] for folder in overview["folders"]} == {"action", "product"}
    assert all(folder["usable_count"] == 1 for folder in overview["folders"])
    assert all(len(folder["preview_paths"]) == 1 for folder in overview["folders"])

    saved = client.get("/assets/root", headers=auth_headers)
    assert saved.status_code == 200
    assert saved.json()["folder_count"] == 2
    categories = client.get("/assets/categories", headers=auth_headers).json()
    assert {category["name"] for category in categories} == {"action", "product"}


def test_asset_shorter_than_three_seconds_is_unavailable(
    client: TestClient, auth_headers: dict[str, str], tmp_path: Path
) -> None:
    root = tmp_path / "short-root"
    folder = root / "short-clips"
    folder.mkdir(parents=True)
    create_video(folder / "short.mp4", duration=2.9)
    response = client.post(
        "/assets/root/scan", headers=auth_headers, json={"root_directory": str(root)}
    )
    assert response.status_code == 200
    overview = response.json()
    assert overview["usable_clip_count"] == 0
    assert overview["folders"][0]["usable_count"] == 0
    clips = client.get("/assets", headers=auth_headers).json()
    assert clips[0]["issue"] == "too_short"
    assert "不足3秒" in clips[0]["issue_message"]


def test_end_to_end_render(client: TestClient, auth_headers: dict[str, str], tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    output = tmp_path / "outputs"
    create_video(source, "#1b9aaa", 3.5)
    request = {
        "name": "端到端测试",
        "template": {
            "id": "template-1",
            "name": "测试模板",
            "shot_count": 1,
            "category_ids": [],
            "default_shot_duration": 3,
            "title": "OutoCut",
            "subtitle": "稳定出片",
            "background_music": None,
            "background_music_volume": 0.18,
            "voice_enabled": False,
            "voice_id": None,
            "output": {"width": 360, "height": 640, "fps": 25, "codec": "libx264", "quality": 28},
            "captions": {
                "enabled": True,
                "font_name": "Microsoft YaHei",
                "font_path": None,
                "font_size": 28,
                "primary_color": "#FFFFFF",
                "outline_color": "#111111",
                "margin_v": 80,
            },
        },
        "asset_paths": [str(source)],
        "shots": [{"text": "这是一条端到端测试文案", "clip_path": None, "voice_path": None}],
        "output_dir": str(output),
        "seed": 42,
        "count": 1,
    }
    response = client.post("/jobs", headers=auth_headers, json=request)
    assert response.status_code == 200, response.text
    job_id = response.json()["id"]
    assert response.json()["state"] == "draft"
    clear_response = client.delete("/jobs/completed", headers=auth_headers)
    assert clear_response.status_code == 200
    assert clear_response.json()["deleted"] == 0
    assert client.get(f"/jobs/{job_id}", headers=auth_headers).status_code == 200
    start_response = client.post(f"/jobs/{job_id}/start", headers=auth_headers)
    assert start_response.status_code == 200, start_response.text
    state = "queued"
    payload = {}
    for _ in range(120):
        payload = client.get(f"/jobs/{job_id}", headers=auth_headers).json()
        state = payload["state"]
        if state in {"succeeded", "failed", "cancelled"}:
            break
        time.sleep(0.1)
    assert state == "succeeded", payload.get("error")
    result = Path(payload["output_path"])
    assert result.is_file()
    assert result.stat().st_size > 1_000
    assert result.parent == output / "Completed"
    artifacts = client.get(f"/jobs/{job_id}/artifacts", headers=auth_headers).json()
    assert {item["name"].rsplit(".", 1)[-1] for item in artifacts} == {"mp4", "ass", "json"}
    artifact_paths = {Path(item["path"]).suffix: Path(item["path"]) for item in artifacts}
    assert artifact_paths[".ass"].parent == output / "ass"
    assert artifact_paths[".json"].parent == output / "json"
    jobs = client.get("/jobs", headers=auth_headers).json()
    assert jobs[0]["template_name"]
    assert jobs[0]["shot_count"] == 1

    cancelled_response = client.post("/jobs", headers=auth_headers, json=request)
    cancelled_id = cancelled_response.json()["id"]
    assert client.post(f"/jobs/{cancelled_id}/cancel", headers=auth_headers).status_code == 200
    clear_response = client.delete("/jobs/completed", headers=auth_headers)
    assert clear_response.json()["deleted"] == 1
    cancelled_job = client.get(f"/jobs/{cancelled_id}", headers=auth_headers)
    assert cancelled_job.status_code == 200
    assert cancelled_job.json()["state"] == "cancelled"
    assert client.delete(f"/jobs/{cancelled_id}", headers=auth_headers).status_code == 200


def test_voice_enabled_job_is_validated_before_creation(
    client: TestClient, auth_headers: dict[str, str], tmp_path: Path
) -> None:
    source = tmp_path / "voice-validation.mp4"
    create_video(source, duration=3.2)
    request = {
        "name": "配音校验",
        "template": {
            "name": "配音校验",
            "voice_enabled": True,
            "voice_id": None,
            "output": {"width": 360, "height": 640, "fps": 25},
        },
        "asset_paths": [str(source)],
        "shots": [{"text": "需要生成配音", "clip_path": str(source)}],
        "output_dir": str(tmp_path / "outputs"),
    }

    missing_key = client.post("/jobs", headers=auth_headers, json=request)
    assert missing_key.status_code == 422
    assert "MiniMax Key" in missing_key.json()["detail"]

    client.post(
        "/settings/secrets",
        headers=auth_headers,
        json={"minimax_api_key": "test-key"},
    )
    missing_voice = client.post("/jobs", headers=auth_headers, json=request)
    assert missing_voice.status_code == 422
    assert "MiniMax 音色" in missing_voice.json()["detail"]

    request["template"]["voice_id"] = "voice-valid"
    request["shots"][0]["text"] = ""
    missing_copy = client.post("/jobs", headers=auth_headers, json=request)
    assert missing_copy.status_code == 422
    assert "缺少配音文案" in missing_copy.json()["detail"]


def test_render_without_voice_keeps_full_clip_and_respects_shot_mute(
    client: TestClient, auth_headers: dict[str, str], tmp_path: Path
) -> None:
    source = tmp_path / "source-with-audio.mp4"
    output = tmp_path / "audio-outputs"
    create_video_with_audio(source, 3.4)

    def render(muted: bool, name: str) -> Path:
        request = {
            "name": name,
            "template": {
                "name": name,
                "shot_count": 1,
                "default_shot_duration": 3,
                "voice_enabled": False,
                "background_music_enabled": False,
                "output": {"width": 360, "height": 640, "fps": 25, "codec": "libx264", "quality": 28},
                "captions": {"enabled": False},
                "title_settings": {"enabled": False},
            },
            "asset_paths": [str(source)],
            "shots": [{"text": "", "clip_path": str(source), "muted": muted}],
            "output_dir": str(output),
            "seed": 9,
            "count": 1,
        }
        created = client.post("/jobs", headers=auth_headers, json=request)
        assert created.status_code == 200, created.text
        job_id = created.json()["id"]
        assert client.post(f"/jobs/{job_id}/start", headers=auth_headers).status_code == 200
        payload: dict[str, object] = {}
        for _ in range(120):
            payload = client.get(f"/jobs/{job_id}", headers=auth_headers).json()
            if payload["state"] in {"succeeded", "failed", "cancelled"}:
                break
            time.sleep(0.1)
        assert payload["state"] == "succeeded", payload.get("error")
        return Path(str(payload["output_path"]))

    audible = render(False, "保留原声")
    muted = render(True, "静音素材")
    assert probe_duration(audible) >= 3.3
    assert probe_duration(muted) >= 3.3
    assert probe_mean_volume(audible) > -40
    assert probe_mean_volume(muted) < -80


def test_render_with_voice_and_background_music(
    client: TestClient, auth_headers: dict[str, str], tmp_path: Path
) -> None:
    source = tmp_path / "voice-music-source.mp4"
    voice = tmp_path / "voice.mp3"
    music = tmp_path / "music.m4a"
    output = tmp_path / "voice-music-outputs"
    create_video_with_audio(source, 3.4)
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "sine=frequency=880:duration=2",
         "-c:a", "libmp3lame", "-b:a", "128k", str(voice)],
        check=True,
    )
    # Short BGM forces the finite stream-loop branch of the final mix.
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "sine=frequency=330:duration=1.2",
         "-c:a", "aac", "-b:a", "128k", str(music)],
        check=True,
    )
    request = {
        "name": "\u914d\u97f3\u52a0\u80cc\u666f\u97f3\u4e50",
        "template": {
            "name": "voice-music",
            "shot_count": 1,
            "default_shot_duration": 3,
            "voice_enabled": True,
            "voice_id": "v1",
            "background_music_enabled": True,
            "background_music": str(music),
            "background_music_mode": "fixed",
            "background_music_volume": 0.18,
            "output": {"width": 360, "height": 640, "fps": 25, "codec": "libx264", "quality": 28},
            "captions": {"enabled": False},
            "title_settings": {"enabled": False},
        },
        "asset_paths": [str(source)],
        "shots": [{"text": "\u914d\u97f3\u6587\u6848", "clip_path": None, "voice_path": str(voice)}],
        "output_dir": str(output),
        "seed": 7,
        "count": 1,
    }
    created = client.post("/jobs", headers=auth_headers, json=request)
    assert created.status_code == 200, created.text
    job_id = created.json()["id"]
    assert client.post(f"/jobs/{job_id}/start", headers=auth_headers).status_code == 200
    payload: dict[str, object] = {}
    for _ in range(120):
        payload = client.get(f"/jobs/{job_id}", headers=auth_headers).json()
        if payload["state"] in {"succeeded", "failed", "cancelled"}:
            break
        time.sleep(0.1)
    assert payload["state"] == "succeeded", payload.get("error")
    result = Path(str(payload["output_path"]))
    assert result.is_file()
    assert result.stat().st_size > 1000
    assert probe_duration(result) >= 1.9
    assert probe_mean_volume(result) > -40
