from __future__ import annotations

import sys
import wave
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import outocut_engine.app as app_module
from outocut_engine.douyin import DouyinResolveError, extract_douyin_url, resolve_douyin_copy


def _uni(*codes: str) -> str:
    return "".join(chr(int(code, 16)) for code in codes)


LOOK = _uni("770b", "770b", "3010")          # ???
CLOSE = _uni("3011")                          # ?
FUZHI = _uni("590d", "5236", "6253", "5f00", "6296", "97f3")  # ??????
FZLL = _uni("590d", "5236", "6b64", "94fe", "63a5")            # ?????
COPY1 = _uni("8fd9", "662f", "771f", "5b9e", "6587", "6848")   # ??????
COPY2 = _uni("8fd9", "5bb6", "5e97", "592a", "597d", "53d1", "4e86")  # ???????
NIHAO = _uni("4f60", "597d")                                    # ???
SHIJIE = _uni("4e16", "754c")                                   # ???
VOICEOVER = _uni("8fd9", "662f", "53e3", "64ad", "5185", "5bb9")  # ??????


class FakeResponse:
    def __init__(self, url: str, text: str = ""):
        self.url = httpx.URL(url)
        self.text = text


class FakeStreamResponse:
    def __init__(self, chunks: list[bytes], headers: dict[str, str] | None = None):
        self._chunks = chunks
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        pass

    def iter_bytes(self, size: int):
        return iter(self._chunks)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_extract_douyin_url() -> None:
    share = "8.88 " + FUZHI + LOOK + "xx" + CLOSE + "https://v.douyin.com/iyykkh/ " + FZLL
    assert extract_douyin_url(share) == "https://v.douyin.com/iyykkh/"
    assert extract_douyin_url("https://www.douyin.com/video/7310000000000000000") == (
        "https://www.douyin.com/video/7310000000000000000"
    )
    assert extract_douyin_url("https://example.com/a") == ""
    assert extract_douyin_url("\u666e\u901a\u6587\u6848") == ""


def test_parse_router_data_desc() -> None:
    from outocut_engine.douyin import _parse_copy_from_html

    router = (
        '{"loaderData":{"page":{"videoInfoRes":{"item_list":[{"desc":"'
        + COPY1
        + '","aweme_id":"1"}]}}}}'
    )
    html = "<script>window._ROUTER_DATA = " + router + "</script>"
    assert _parse_copy_from_html(html) == COPY1

    raw_html = '<script>var x = {"desc":"' + COPY2 + '"};</script>'
    assert _parse_copy_from_html(raw_html) == COPY2

    assert _parse_copy_from_html("<html><body>empty</body></html>") == ""


def test_share_text_fallback() -> None:
    from outocut_engine.douyin import _extract_share_text_copy

    paste = (
        "9.99 "
        + FUZHI
        + "\uff0c"
        + LOOK
        + "xx"
        + CLOSE
        + COPY2
        + "\u3002https://v.douyin.com/AbC/ "
        + FZLL
    )
    assert COPY2 in _extract_share_text_copy(paste)


def test_resolve_douyin_copy_success(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(url: str, **kwargs):
        if "v.douyin.com" in str(url):
            return FakeResponse("https://www.douyin.com/video/7312345678901234567")
        if "iesdouyin.com/share/video" in str(url):
            router = '{"loaderData":{"p":{"videoInfoRes":{"item_list":[{"desc":"' + COPY1 + '"}]}}}}'
            return FakeResponse(str(url), "<script>window._ROUTER_DATA = " + router + "</script>")
        return FakeResponse(str(url))

    monkeypatch.setattr(httpx, "get", fake_get)
    copy, source_url = resolve_douyin_copy("https://v.douyin.com/abc/", cookie="ttwid=1;")
    assert copy == COPY1
    assert source_url == "https://www.douyin.com/video/7312345678901234567"


def test_resolve_douyin_copy_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get_blocked(url: str, **kwargs):
        if "v.douyin.com" in str(url):
            return FakeResponse("https://www.douyin.com/")
        return FakeResponse(str(url), "<html></html>")

    monkeypatch.setattr(httpx, "get", fake_get_blocked)
    with pytest.raises(DouyinResolveError) as exc_info:
        resolve_douyin_copy("https://v.douyin.com/abc/", cookie="")
    assert exc_info.value.code == "needs_cookie"

    with pytest.raises(DouyinResolveError) as exc_info:
        resolve_douyin_copy("\u666e\u901a\u6587\u6848\u65e0\u94fe\u63a5", cookie="")
    assert exc_info.value.code == "no_link"

    def fake_get_notfound(url: str, **kwargs):
        if "v.douyin.com" in str(url):
            return FakeResponse("https://www.douyin.com/video/7312345678901234567")
        return FakeResponse(
            str(url),
            '<script>window._ROUTER_DATA = '
            '{"x":{"filter_list":[{"filter_reason":"SYSTEM_ITEM_NOT_EXIST"}]}}</script>',
        )

    monkeypatch.setattr(httpx, "get", fake_get_notfound)
    with pytest.raises(DouyinResolveError) as exc_info:
        resolve_douyin_copy("https://v.douyin.com/abc/", cookie="ttwid=1;")
    assert exc_info.value.code == "not_found"


def test_extract_play_urls() -> None:
    from outocut_engine.douyin import _extract_play_urls

    item = {
        "video": {
            "play_addr": {"url_list": ["https://www.douyin.com/aweme/v1/play/?video_id=1", "https://cdn/x.mp4"]},
            "download_addr": {"url_list": ["https://cdn/y.mp4", "https://cdn/y.mp4"]},
            "bit_rate": [{"play_addr": {"url_list": ["https://cdn/z.mp4"]}}],
        }
    }
    assert _extract_play_urls(item) == [
        "https://www.douyin.com/aweme/v1/play/?video_id=1",
        "https://cdn/x.mp4",
        "https://cdn/y.mp4",
        "https://cdn/z.mp4",
    ]
    assert _extract_play_urls({"video": {}}) == []

    uri_item = {"video": {"play_addr": {"uri": "v0d00abcd1234"}}}
    urls = _extract_play_urls(uri_item)
    assert urls[0] == "https://aweme.snssdk.com/aweme/v1/playwm/?video_id=v0d00abcd1234&ratio=720p&line=0"
    assert "https://aweme.snssdk.com/aweme/v1/play/?video_id=v0d00abcd1234&ratio=720p&line=0" in urls
    assert "https://www.douyin.com/aweme/v1/play/?video_id=v0d00abcd1234&ratio=720p&line=0" in urls
    assert "https://www.iesdouyin.com/aweme/v1/playwm/?video_id=v0d00abcd1234&ratio=720p&line=0" in urls
    assert "https://www.iesdouyin.com/aweme/v1/play/?video_id=v0d00abcd1234&ratio=720p&line=0" in urls

    music_item = {
        "video": {"play_addr": {"uri": "v0d00abcd1234"}},
        "music": {"play_url": {"url_list": ["https://sf-audio.example/music.mp3"]}},
    }
    music_urls = _extract_play_urls(music_item)
    assert music_urls[-1] == "https://sf-audio.example/music.mp3"


def test_absolutize_hls_playlist(tmp_path: Path) -> None:
    from outocut_engine.douyin import _absolutize_hls_playlist

    playlist = tmp_path / "index.m3u8"
    playlist.write_text(
        "#EXTM3U\n"
        "#EXT-X-VERSION:3\n"
        "#EXT-X-KEY:METHOD=AES-128,URI=\"key.key\"\n"
        "seg-000.ts\n"
        "hls/seg-001.ts?v=1\n"
        "https://cdn.example/seg-002.ts\n",
        encoding="utf-8",
    )
    _absolutize_hls_playlist(playlist, "https://cdn.example/path/index.m3u8")
    rewritten = playlist.read_text(encoding="utf-8")
    assert "URI=\"https://cdn.example/path/key.key\"" in rewritten
    assert "https://cdn.example/path/seg-000.ts" in rewritten
    assert "https://cdn.example/path/hls/seg-001.ts?v=1" in rewritten
    assert "https://cdn.example/seg-002.ts" in rewritten


def test_download_media(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from outocut_engine.douyin import _download_media

    dest = tmp_path / "media.bin"

    def fake_stream(method: str, url: str, **kwargs):
        assert url == "https://cdn.example/play.mp4"
        assert kwargs["headers"].get("Cookie") == "ttwid=1;"
        return FakeStreamResponse([b"a" * 1024, b"b" * 512])

    monkeypatch.setattr(httpx, "stream", fake_stream)
    _download_media("https://cdn.example/play.mp4", "ttwid=1;", dest)
    assert dest.stat().st_size == 1536

    monkeypatch.setattr(httpx, "stream", lambda method, url, **kwargs: FakeStreamResponse([]))
    with pytest.raises(DouyinResolveError) as exc_info:
        _download_media("https://cdn.example/play.mp4", "", dest)
    assert exc_info.value.code == "download_failed"

    monkeypatch.setattr(
        httpx,
        "stream",
        lambda method, url, **kwargs: FakeStreamResponse(
            [b"<html>verify</html>"], headers={"content-type": "text/html"}
        ),
    )
    with pytest.raises(DouyinResolveError) as exc_info:
        _download_media("https://cdn.example/play.mp4", "", dest)
    assert exc_info.value.code == "download_blocked"


def test_transcribe_audio(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from outocut_engine import douyin as douyin_module
    from outocut_engine.douyin import _transcribe_audio

    wav = tmp_path / "sample.wav"
    wav.write_bytes(b"RIFF....")

    seen_models: list[str] = []

    async def fake_ws_success(
        source: Path, api_key: str, proxy_url: str | None = None, model: str | None = None
    ):
        assert str(source) == str(wav)
        assert api_key == "sk-123"
        seen_models.append(model)
        return NIHAO + SHIJIE

    monkeypatch.setattr(douyin_module, "_transcribe_ws_chunk", fake_ws_success)
    assert _transcribe_audio(wav, "sk-123") == NIHAO + SHIJIE
    assert _transcribe_audio(wav, "sk-123", "paraformer-realtime-v1") == NIHAO + SHIJIE
    assert seen_models == ["paraformer-realtime-v2", "paraformer-realtime-v1"]

    with pytest.raises(DouyinResolveError) as exc_info:
        _transcribe_audio(wav, "")
    assert exc_info.value.code == "needs_asr_key"

    async def fake_ws_bad_key(
        source: Path, api_key: str, proxy_url: str | None = None, model: str | None = None
    ):
        raise DouyinResolveError(
            "\u767e\u70bc API Key \u65e0\u6548",
            code="invalid_asr_key",
        )

    monkeypatch.setattr(douyin_module, "_transcribe_ws_chunk", fake_ws_bad_key)
    with pytest.raises(DouyinResolveError) as exc_info:
        _transcribe_audio(wav, "sk-bad")
    assert exc_info.value.code == "invalid_asr_key"

    async def fake_ws_empty(
        source: Path, api_key: str, proxy_url: str | None = None, model: str | None = None
    ):
        return ""

    monkeypatch.setattr(douyin_module, "_transcribe_ws_chunk", fake_ws_empty)
    with pytest.raises(DouyinResolveError) as exc_info:
        _transcribe_audio(wav, "sk-123")
    assert exc_info.value.code == "asr_empty"

    async def fake_ws_server_error(
        source: Path, api_key: str, proxy_url: str | None = None, model: str | None = None
    ):
        raise DouyinResolveError("\u670d\u52a1\u5668\u5f02\u5e38", code="asr_failed")

    monkeypatch.setattr(douyin_module, "_transcribe_ws_chunk", fake_ws_server_error)
    with pytest.raises(DouyinResolveError) as exc_info:
        _transcribe_audio(wav, "sk-123")
    assert exc_info.value.code == "asr_failed"


def test_transcribe_audio_long_wav_chunks(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import wave

    from outocut_engine import douyin as douyin_module
    from outocut_engine.douyin import _transcribe_audio

    wav = tmp_path / "long.wav"
    frame_rate = 16000
    with wave.open(str(wav), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(frame_rate)
        wav_file.writeframes(bytes(2 * frame_rate * 120))

    calls: list[str] = []

    async def fake_ws(
        source: Path, api_key: str, proxy_url: str | None = None, model: str | None = None
    ):
        calls.append(str(source))
        index = len(calls) - 1
        return NIHAO if index == 0 else SHIJIE

    monkeypatch.setattr(douyin_module, "_transcribe_ws_chunk", fake_ws)
    assert _transcribe_audio(wav, "sk-123") == NIHAO + SHIJIE
    assert len(calls) == 3
    assert all(name.endswith(".wav") and ".chunk" in name for name in calls)
    leftovers = [child for child in tmp_path.iterdir() if ".chunk" in child.name]
    assert leftovers == []


def test_transcribe_audio_offline_model(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from outocut_engine import douyin as douyin_module
    from outocut_engine.douyin import _transcribe_audio

    wav = tmp_path / "sample.wav"
    wav.write_bytes(b"RIFF....")
    seen: list[str] = []

    def fake_async(audio_path: Path, api_key: str, model: str) -> str:
        seen.append(model)
        return "\u4f60\u597d\u4e16\u754c"

    monkeypatch.setattr(douyin_module, "_transcribe_async_file", fake_async)
    assert _transcribe_audio(wav, "sk-123", "paraformer-v2") == "\u4f60\u597d\u4e16\u754c"
    assert seen == ["paraformer-v2"]

    monkeypatch.setattr(
        douyin_module, "_transcribe_async_file", lambda audio_path, api_key, model: ""
    )
    with pytest.raises(DouyinResolveError) as exc_info:
        _transcribe_audio(wav, "sk-123", "paraformer-v1")
    assert exc_info.value.code == "asr_empty"


def test_transcribe_async_file_flow(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from outocut_engine.douyin import _transcribe_async_file

    class FakeResponse:
        def __init__(self, status_code: int, body: dict[str, Any]):
            self.status_code = status_code
            self._body = body

        def json(self) -> dict[str, Any]:
            return self._body

    wav = tmp_path / "sample.wav"
    wav.write_bytes(b"RIFF....")
    seen: list[tuple[str, str]] = []

    def fake_post(url: str, **kwargs: Any) -> FakeResponse:
        if url == "https://oss.example":
            seen.append(("oss_upload", kwargs["data"]["key"]))
            return FakeResponse(200, {})
        assert url.endswith("/transcription")
        seen.append(("submit", kwargs["json"]["model"]))
        assert kwargs["headers"]["X-DashScope-Async"] == "enable"
        return FakeResponse(200, {"output": {"task_id": "task-1"}})

    def fake_get(url: str, **kwargs: Any) -> FakeResponse:
        if "/uploads" in url:
            assert kwargs["params"] == {"action": "getPolicy", "model": "paraformer-v2"}
            seen.append(("cert", url))
            return FakeResponse(
                200,
                {
                    "data": {
                        "upload_host": "https://oss.example",
                        "upload_dir": "dashscope-instant/abc/2026-01-01/xyz",
                        "oss_access_key_id": "LTAI-test",
                        "signature": "sig",
                        "policy": "pol",
                        "x_oss_object_acl": "private",
                        "x_oss_forbid_overwrite": "true",
                    }
                },
            )
        if url.endswith("/tasks/task-1"):
            seen.append(("poll", url))
            return FakeResponse(
                200,
                {
                    "output": {
                        "task_status": "SUCCEEDED",
                        "results": [{"transcription_url": "https://oss.example/result.json"}],
                    }
                },
            )
        assert url.endswith("/result.json")
        seen.append(("fetch", url))
        return FakeResponse(200, {"transcription": {"text": "\u4f60\u597d\u4e16\u754c"}})

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(httpx, "get", fake_get)

    assert _transcribe_async_file(wav, "sk-123", "paraformer-v2") == "\u4f60\u597d\u4e16\u754c"
    assert seen == [
        ("cert", "https://dashscope.aliyuncs.com/api/v1/uploads"),
        ("oss_upload", "dashscope-instant/abc/2026-01-01/xyz/sample.wav"),
        ("submit", "paraformer-v2"),
        ("poll", "https://dashscope.aliyuncs.com/api/v1/tasks/task-1"),
        ("fetch", "https://oss.example/result.json"),
    ]

    def fake_post_error(url: str, **kwargs: Any) -> FakeResponse:
        return FakeResponse(400, {"message": "model not found"})

    monkeypatch.setattr(httpx, "post", fake_post_error)
    with pytest.raises(DouyinResolveError) as exc_info:
        _transcribe_async_file(wav, "sk-123", "paraformer-v2")
    assert exc_info.value.code == "asr_failed"
    assert "model not found" in str(exc_info.value)


def test_transcribe_async_file_transcripts_shape(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from outocut_engine.douyin import _transcribe_async_file

    class FakeResponse:
        def __init__(self, status_code: int, body: dict[str, Any]):
            self.status_code = status_code
            self._body = body

        def json(self) -> dict[str, Any]:
            return self._body

    wav = tmp_path / "sample.wav"
    wav.write_bytes(b"RIFF....")
    certificate = {
        "data": {
            "upload_host": "https://oss.example",
            "upload_dir": "dashscope-instant/abc/2026-01-01/xyz",
            "oss_access_key_id": "LTAI-test",
            "signature": "sig",
            "policy": "pol",
            "x_oss_object_acl": "private",
            "x_oss_forbid_overwrite": "true",
        }
    }

    def fake_post(url: str, **kwargs: Any) -> FakeResponse:
        if url == "https://oss.example":
            return FakeResponse(200, {})
        return FakeResponse(200, {"output": {"task_id": "task-1"}})

    def fake_get(url: str, **kwargs: Any) -> FakeResponse:
        if "/uploads" in url:
            return FakeResponse(200, certificate)
        if url.endswith("/tasks/task-1"):
            return FakeResponse(
                200,
                {
                    "output": {
                        "task_status": "SUCCEEDED",
                        "results": [{"transcription_url": "https://oss.example/result.json"}],
                    }
                },
            )
        return FakeResponse(
            200, {"transcripts": [{"text": "\u4f60\u597d"}, {"text": "\u4e16\u754c"}]}
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(httpx, "get", fake_get)
    assert _transcribe_async_file(wav, "sk-123", "paraformer-v2") == "\u4f60\u597d\u4e16\u754c"


def test_transcribe_async_file_download_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from outocut_engine.douyin import _transcribe_async_file

    class FakeResponse:
        def __init__(self, status_code: int, body: dict[str, Any]):
            self.status_code = status_code
            self._body = body

        def json(self) -> dict[str, Any]:
            return self._body

    wav = tmp_path / "sample.wav"
    wav.write_bytes(b"RIFF....")

    def fake_post(url: str, **kwargs: Any) -> FakeResponse:
        if url == "https://oss.example":
            return FakeResponse(200, {})
        return FakeResponse(200, {"output": {"task_id": "task-1"}})

    def fake_get(url: str, **kwargs: Any) -> FakeResponse:
        if "/uploads" in url:
            return FakeResponse(
                200,
                {
                    "data": {
                        "upload_host": "https://oss.example",
                        "upload_dir": "dashscope-instant/abc/2026-01-01/xyz",
                        "oss_access_key_id": "LTAI-test",
                        "signature": "sig",
                        "policy": "pol",
                        "x_oss_object_acl": "private",
                        "x_oss_forbid_overwrite": "true",
                    }
                },
            )
        return FakeResponse(
            200,
            {
                "output": {
                    "task_status": "FAILED",
                    "code": "FILE_DOWNLOAD_FAILED",
                    "message": "FILE_DOWNLOAD_FAILED",
                    "results": [],
                }
            },
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(httpx, "get", fake_get)
    with pytest.raises(DouyinResolveError) as exc_info:
        _transcribe_async_file(wav, "sk-123", "paraformer-v2")
    assert exc_info.value.code == "asr_failed"
    assert "\u5b9e\u65f6\u8bc6\u522b" in str(exc_info.value)


def test_merge_transcript_parts() -> None:
    from outocut_engine.douyin import _merge_transcript_parts

    assert _merge_transcript_parts(["\u4eca\u5929\u5929\u6c14", "\u5929\u6c14\u771f\u597d"]) == (
        "\u4eca\u5929\u5929\u6c14\u771f\u597d"
    )

    assert _merge_transcript_parts(
        [
            "\u8fd9\u4e2a\u5e26\u6709\u5b57\u6bcd\u56fe\u6848\u7684\u6b3e\u5f0f\u6302\u5728\u8116\u3002",
            "\u6b3e\u5f0f\u6302\u5728\u8116\u5b50\u4e0a\u8fd8\u633a\u597d\u770b\u7684\uff0c\u6709\u65f6\u5019",
        ]
    ) == (
        "\u8fd9\u4e2a\u5e26\u6709\u5b57\u6bcd\u56fe\u6848\u7684"
        "\u6b3e\u5f0f\u6302\u5728\u8116\u5b50\u4e0a\u8fd8\u633a\u597d\u770b\u7684\uff0c\u6709\u65f6\u5019"
    )
    assert _merge_transcript_parts(["", "\u4f60\u597d", " \u4e16\u754c "]) == "\u4f60\u597d\u4e16\u754c"
    assert _merge_transcript_parts([]) == ""


def test_resolve_douyin_voiceover_success(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from outocut_engine import douyin as douyin_module

    router = (
        '{"loaderData":{"p":{"videoInfoRes":{"item_list":[{"desc":"'
        + COPY1
        + '","video":{"play_addr":{"url_list":["https://cdn.example/play.mp4"]}}}]}}}}'
    )
    html = "<script>window._ROUTER_DATA = " + router + "</script>"

    monkeypatch.setattr(
        douyin_module,
        "_resolve_aweme_id",
        lambda text, cookie: ("7312345678901234567", "https://www.douyin.com/video/7312345678901234567"),
    )
    monkeypatch.setattr(douyin_module, "_fetch_share_page", lambda aweme_id, cookie: html)
    monkeypatch.setattr(
        douyin_module,
        "_acquire_audio",
        lambda ffmpeg, cookie, url, media_path, wav_path: wav_path.write_bytes(b"WAV") or None,
    )
    monkeypatch.setattr(
        douyin_module, "_transcribe_audio", lambda audio_path, api_key, asr_model=None: VOICEOVER
    )

    transcript, source_url = douyin_module.resolve_douyin_voiceover(
        "https://v.douyin.com/abc/", cookie="ttwid=1;", api_key="sk-1", ffmpeg="ffmpeg", temp_dir=tmp_path
    )
    assert transcript == VOICEOVER
    assert source_url == "https://www.douyin.com/video/7312345678901234567"
    assert list(tmp_path.glob("douyin_*")) == []


class FakeRunResult:
    def __init__(self, returncode: int = 0):
        self.returncode = returncode


def test_download_via_ffmpeg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from outocut_engine import douyin as douyin_module

    dest = tmp_path / "out.wav"

    def fake_run(ffmpeg: str, cookie: str, source: str, out: Path, **kwargs):
        assert source.startswith("http")
        out.write_bytes(b"WAV")
        return FakeRunResult(0)

    monkeypatch.setattr(douyin_module, "_run_ffmpeg_wav", fake_run)
    douyin_module._download_via_ffmpeg("ffmpeg", "ttwid=1;", "https://cdn.example/x.mp4", dest)
    assert dest.stat().st_size == 3

    monkeypatch.setattr(
        douyin_module,
        "_run_ffmpeg_wav",
        lambda ffmpeg, cookie, source, out, **kwargs: FakeRunResult(1),
    )
    with pytest.raises(DouyinResolveError) as exc_info:
        douyin_module._download_via_ffmpeg("ffmpeg", "", "https://cdn.example/x.mp4", dest)
    assert exc_info.value.code == "download_failed"


def test_acquire_audio_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from outocut_engine import douyin as douyin_module

    media_path = tmp_path / "src.bin"
    wav_path = tmp_path / "asr.wav"

    def fail_ffmpeg(ffmpeg: str, cookie: str, url: str, dest: Path, **kwargs):
        raise DouyinResolveError("\u89c6\u9891\u97f3\u9891\u4e0b\u8f7d\u5931\u8d25", code="download_failed")

    monkeypatch.setattr(douyin_module, "_download_via_ffmpeg", fail_ffmpeg)

    def fake_download(url: str, cookie: str, dest: Path, **kwargs):
        dest.write_bytes(b"FAKE")
        return dest

    monkeypatch.setattr(douyin_module, "_download_media", fake_download)
    monkeypatch.setattr(
        douyin_module,
        "_extract_audio_stream",
        lambda ffmpeg, cookie, source, dest, **kwargs: dest.write_bytes(b"WAV") or dest,
    )
    douyin_module._acquire_audio("ffmpeg", "", "https://cdn.example/x.mp4", media_path, wav_path)
    assert wav_path.stat().st_size == 3

    # HLS playlist downloaded via httpx is handed to ffmpeg so it can pull
    # the segments; when extraction still fails the error propagates.
    wav_path.unlink(missing_ok=True)
    extracted_sources: list[Path] = []

    def fake_extract_fail(ffmpeg: str, cookie: str, source: Path, dest: Path, **kwargs):
        extracted_sources.append(source)
        raise DouyinResolveError(
            "\u65e0\u6cd5\u4ece\u89c6\u9891\u4e2d\u63d0\u53d6\u97f3\u9891\u6d41",
            code="audio_extract_failed",
        )

    monkeypatch.setattr(douyin_module, "_extract_audio_stream", fake_extract_fail)
    monkeypatch.setattr(
        douyin_module,
        "_download_media",
        lambda url, cookie, dest, **kwargs: dest.write_bytes(b"#EXTM3U\n#EXT-X-VERSION:3")
        or dest,
    )
    with pytest.raises(DouyinResolveError) as exc_info:
        douyin_module._acquire_audio("ffmpeg", "", "https://cdn.example/x.m3u8", media_path, wav_path)
    assert exc_info.value.code == "audio_extract_failed"
    assert extracted_sources and all(source == media_path for source in extracted_sources)


def test_acquire_audio_ua_rotation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from outocut_engine import douyin as douyin_module

    media_path = tmp_path / "src.bin"
    wav_path = tmp_path / "asr.wav"

    def fail_ffmpeg(ffmpeg: str, cookie: str, url: str, dest: Path, **kwargs):
        raise DouyinResolveError("\u65e0\u6cd5\u83b7\u53d6\u5a92\u4f53\u6d41", code="download_failed")

    monkeypatch.setattr(douyin_module, "_download_via_ffmpeg", fail_ffmpeg)

    seen_uas: list[str] = []

    def blocked_first(url: str, cookie: str, dest: Path, user_agent: str = douyin_module.MOBILE_UA):
        seen_uas.append(user_agent)
        if user_agent == douyin_module.MOBILE_UA:
            raise DouyinResolveError("\u88ab\u98ce\u63a7\u62e6\u622a", code="download_blocked")
        dest.write_bytes(b"FAKE")
        return dest

    monkeypatch.setattr(douyin_module, "_download_media", blocked_first)
    monkeypatch.setattr(
        douyin_module,
        "_extract_audio_stream",
        lambda ffmpeg, cookie, source, dest, **kwargs: dest.write_bytes(b"WAV") or dest,
    )
    douyin_module._acquire_audio("ffmpeg", "", "https://cdn.example/x.mp4", media_path, wav_path)
    assert seen_uas == [douyin_module.MOBILE_UA, douyin_module.DESKTOP_UA]
    assert wav_path.stat().st_size == 3


def test_resolve_douyin_voiceover_local_short_result(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from outocut_engine import asr as asr_module
    from outocut_engine import douyin as douyin_module

    router = (
        '{"loaderData":{"p":{"videoInfoRes":{"item_list":[{"desc":"'
        + COPY1
        + '","video":{"play_addr":{"url_list":["https://cdn.example/play.mp4"]}}}]}}}}'
    )
    html = "<script>window._ROUTER_DATA = " + router + "</script>"
    monkeypatch.setattr(
        douyin_module, "_resolve_aweme_id", lambda text, cookie: ("1", "https://www.douyin.com/video/1")
    )
    monkeypatch.setattr(douyin_module, "_fetch_share_page", lambda aweme_id, cookie: html)

    def fake_acquire(ffmpeg: str, cookie: str, play_url: str, media_path: Path, audio_path: Path):
        with wave.open(str(audio_path), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(16000)
            wav_file.writeframes(b"\x00\x00" * (16000 * 40))

    monkeypatch.setattr(douyin_module, "_acquire_audio", fake_acquire)
    monkeypatch.setattr(asr_module, "transcribe_local", lambda audio_path, model_dir: "\u4e0b")

    with pytest.raises(DouyinResolveError) as exc_info:
        douyin_module.resolve_douyin_voiceover(
            "https://v.douyin.com/abc/",
            cookie="",
            api_key="",
            ffmpeg="ffmpeg",
            temp_dir=tmp_path,
            asr_provider="local",
            local_model_dir=tmp_path / "models" / "m",
        )
    assert exc_info.value.code == "asr_empty"
    debug_wavs = list(tmp_path.glob("douyin_*_debug.wav"))
    assert len(debug_wavs) == 1
    assert debug_wavs[0].stat().st_size > 0


def test_resolve_douyin_voiceover_local_provider(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from outocut_engine import asr as asr_module
    from outocut_engine import douyin as douyin_module

    router = (
        '{"loaderData":{"p":{"videoInfoRes":{"item_list":[{"desc":"'
        + COPY1
        + '","video":{"play_addr":{"url_list":["https://cdn.example/play.mp4"]}}}]}}}}'
    )
    html = "<script>window._ROUTER_DATA = " + router + "</script>"
    monkeypatch.setattr(
        douyin_module, "_resolve_aweme_id", lambda text, cookie: ("1", "https://www.douyin.com/video/1")
    )
    monkeypatch.setattr(douyin_module, "_fetch_share_page", lambda aweme_id, cookie: html)
    monkeypatch.setattr(douyin_module, "_acquire_audio", lambda *args, **kwargs: None)

    seen: dict[str, str] = {}

    def fake_local(audio_path: Path, model_dir: Path | None):
        seen["dir"] = str(model_dir) if model_dir else ""
        return "LOCAL_TEXT"

    monkeypatch.setattr(asr_module, "transcribe_local", fake_local)

    transcript, _ = douyin_module.resolve_douyin_voiceover(
        "https://v.douyin.com/abc/",
        cookie="",
        api_key="",
        ffmpeg="ffmpeg",
        temp_dir=tmp_path,
        asr_provider="local",
        local_model_dir=tmp_path / "models" / "m",
    )
    assert transcript == "LOCAL_TEXT"
    assert seen["dir"] == str(tmp_path / "models" / "m")


def test_resolve_douyin_voiceover_keeps_debug_wav(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from outocut_engine import douyin as douyin_module

    router = (
        '{"loaderData":{"p":{"videoInfoRes":{"item_list":[{"desc":"'
        + COPY1
        + '","video":{"play_addr":{"url_list":["https://cdn.example/play.mp4"]}}}]}}}}'
    )
    html = "<script>window._ROUTER_DATA = " + router + "</script>"
    monkeypatch.setattr(
        douyin_module, "_resolve_aweme_id", lambda text, cookie: ("1", "https://www.douyin.com/video/1")
    )
    monkeypatch.setattr(douyin_module, "_fetch_share_page", lambda aweme_id, cookie: html)
    monkeypatch.setattr(
        douyin_module,
        "_acquire_audio",
        lambda ffmpeg, cookie, url, media_path, wav_path: wav_path.write_bytes(b"WAV") or None,
    )

    def fail_empty(audio_path: Path, api_key: str, asr_model: str | None = None):
        raise DouyinResolveError("\u672a\u8bc6\u522b\u5230\u4eba\u58f0\u5185\u5bb9", code="asr_empty")

    monkeypatch.setattr(douyin_module, "_transcribe_audio", fail_empty)

    with pytest.raises(DouyinResolveError) as exc_info:
        douyin_module.resolve_douyin_voiceover(
            "https://v.douyin.com/abc/",
            cookie="",
            api_key="sk-1",
            ffmpeg="ffmpeg",
            temp_dir=tmp_path,
        )
    assert exc_info.value.code == "asr_empty"
    debug_files = list(tmp_path.glob("douyin_*_debug.wav"))
    assert len(debug_files) == 1
    assert debug_files[0].read_bytes() == b"WAV"


def test_resolve_douyin_voiceover_no_media(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from outocut_engine import douyin as douyin_module

    router = '{"loaderData":{"p":{"videoInfoRes":{"item_list":[{"desc":"' + COPY1 + '","video":{}}]}}}}'
    html = "<script>window._ROUTER_DATA = " + router + "</script>"
    monkeypatch.setattr(
        douyin_module, "_resolve_aweme_id", lambda text, cookie: ("1", "https://www.douyin.com/video/1")
    )
    monkeypatch.setattr(douyin_module, "_fetch_share_page", lambda aweme_id, cookie: html)

    with pytest.raises(DouyinResolveError) as exc_info:
        douyin_module.resolve_douyin_voiceover(
            "https://v.douyin.com/abc/", cookie="", api_key="", ffmpeg="ffmpeg", temp_dir=tmp_path
        )
    assert exc_info.value.code == "no_media"


def test_resolve_douyin_voiceover_needs_cookie(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from outocut_engine import douyin as douyin_module

    monkeypatch.setattr(
        douyin_module, "_resolve_aweme_id", lambda text, cookie: ("1", "https://www.douyin.com/video/1")
    )
    monkeypatch.setattr(
        douyin_module, "_fetch_share_page", lambda aweme_id, cookie: "<html>login wall</html>"
    )

    with pytest.raises(DouyinResolveError) as exc_info:
        douyin_module.resolve_douyin_voiceover(
            "https://v.douyin.com/abc/", cookie="", api_key="", ffmpeg="ffmpeg", temp_dir=tmp_path
        )
    assert exc_info.value.code == "needs_cookie"


def test_douyin_resolve_api(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, str] = {}

    def fake_resolve(
        text: str,
        cookie: str,
        api_key: str,
        ffmpeg: str,
        temp_dir: Path,
        asr_provider: str = "bailian",
        asr_model: str = "paraformer-realtime-v2",
        local_model_dir: Path | None = None,
    ):
        seen["cookie"] = cookie
        seen["api_key"] = api_key
        seen["asr_provider"] = asr_provider
        seen["asr_model"] = asr_model
        seen["local_model_dir"] = str(local_model_dir) if local_model_dir else ""
        if "bad" in text:
            raise DouyinResolveError("\u9700\u8981 Cookie \u624d\u80fd\u63d0\u53d6", code="needs_cookie")
        if "nokey" in text:
            raise DouyinResolveError("\u9700\u8981\u914d\u7f6e\u767e\u70bc Key", code="needs_asr_key")
        return (COPY1, "https://www.douyin.com/video/123")

    monkeypatch.setattr(app_module, "resolve_douyin_voiceover", fake_resolve)

    response = client.post("/links/douyin", headers=auth_headers, json={"text": "   "})
    assert response.status_code == 422
    assert response.json()["code"] == "empty"

    response = client.post("/links/douyin", headers=auth_headers, json={"text": "https://v.douyin.com/abc/"})
    assert response.status_code == 200
    assert response.json()["content"] == COPY1
    assert seen["cookie"] == ""
    assert seen["api_key"] == ""
    assert seen["asr_provider"] == "bailian"
    assert seen["asr_model"] == "paraformer-realtime-v2"
    assert seen["local_model_dir"].endswith("paraformer-zh")

    saved = client.post(
        "/settings/secrets",
        headers=auth_headers,
        json={"douyin_cookie": "ttwid=abc123", "bailian_api_key": "sk-abc"},
    )
    assert saved.json()["douyin"] is True
    assert saved.json()["bailian"] is True

    response = client.post("/links/douyin", headers=auth_headers, json={"text": "https://v.douyin.com/abc/"})
    assert response.status_code == 200
    assert seen["cookie"] == "ttwid=abc123"
    assert seen["api_key"] == "sk-abc"
    assert seen["asr_provider"] == "bailian"

    current = client.get("/settings", headers=auth_headers).json()
    current["asr_model"] = "paraformer-realtime-v1"
    saved_settings = client.put("/settings", headers=auth_headers, json=current)
    assert saved_settings.status_code == 200, saved_settings.text
    assert saved_settings.json()["asr_model"] == "paraformer-realtime-v1"

    response = client.post("/links/douyin", headers=auth_headers, json={"text": "https://v.douyin.com/abc/"})
    assert response.status_code == 200
    assert seen["asr_model"] == "paraformer-realtime-v1"

    response = client.post("/links/douyin", headers=auth_headers, json={"text": "bad https://v.douyin.com/x"})
    assert response.status_code == 422
    assert response.json()["code"] == "needs_cookie"

    response = client.post("/links/douyin", headers=auth_headers, json={"text": "nokey https://v.douyin.com/x"})
    assert response.status_code == 422
    assert response.json()["code"] == "needs_asr_key"
