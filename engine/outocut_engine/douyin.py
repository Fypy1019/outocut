from __future__ import annotations

import asyncio
import hashlib
import html as html_lib
import json
import re
import shutil
import subprocess
import time
import urllib.parse
import uuid
import wave
from pathlib import Path
from typing import Any

import httpx
import websockets

from . import net
from .processes import WINDOWS_NO_WINDOW

REQUEST_TIMEOUT = 15.0
DOWNLOAD_TIMEOUT = 120.0
ASR_TIMEOUT = 300.0
# DashScope's realtime recognition is served over WebSocket; the HTTP
# recognition endpoints do not accept paraformer-realtime-v2 with a
# workspace API key. The protocol mirrors the official dashscope SDK.
DASHSCOPE_WS_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"
# paraformer-v2 is served by the async transcription API which needs an
# uploaded file URL (oss:// via DashScope's upload certificate flow);
# paraformer-realtime-v2 accepts local audio over WebSocket in one-shot
# sessions up to 60 seconds.
ASR_MODEL = "paraformer-realtime-v2"
# Realtime models stream local audio over the WebSocket recognition
# endpoint; offline recording-file models (paraformer-v2/v1, ...) run
# through the async transcription API, which needs the audio uploaded
# to DashScope storage first.
ASR_REALTIME_MODELS = (
    "paraformer-realtime-v2",
    "paraformer-realtime-v1",
    "sensenova-paraformer-streaming",
)
# Models verified to work with a plain Bailian API key on any machine.
# The async transcription models (paraformer-v2/v1, sensenova-paraformer)
# stay implemented below but are not offered in the UI because they need
# OSS file access that ordinary workspace keys do not have.
ASR_AVAILABLE_MODELS = (
    "paraformer-realtime-v2",
    "paraformer-realtime-v1",
)
ASR_MODELS = (
    "paraformer-realtime-v2",
    "paraformer-realtime-v1",
    "sensenova-paraformer-streaming",
    "paraformer-v2",
    "paraformer-v1",
    "sensenova-paraformer",
)
DASHSCOPE_UPLOAD_URL = "https://dashscope.aliyuncs.com/api/v1/uploads"
DASHSCOPE_TRANSCRIPTION_URL = "https://dashscope.aliyuncs.com/api/v1/services/audio/asr/transcription"
DASHSCOPE_TASK_URL = "https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}"
ASR_CHUNK_SECONDS = 58.0
ASR_CHUNK_OVERLAP_SECONDS = 1.0
DOUYIN_HOST_MARKERS = ("douyin.com", "iesdouyin.com")
DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)
# UA cycle used when a media request is blocked: Douyin's anti-bot rules
# are partly UA based, so swapping between mobile and desktop clients can
# get past a block that the other UA trips.
_MEDIA_UA_CYCLE = (MOBILE_UA, DESKTOP_UA)


class DouyinResolveError(RuntimeError):
    """Raised when a Douyin share link cannot be resolved to copy text."""

    def __init__(self, message: str, *, code: str = "parse_failed"):
        super().__init__(message)
        self.code = code


_URL_RE = re.compile(
    r"https?://[^\s\"'<>\uff0c\u3002\uff1b;\u3001\uff08\uff09()]+",
    re.IGNORECASE,
)
_AWEME_ID_RE = re.compile(r"/(?:video|note|share/video)/(\d+)", re.IGNORECASE)
_MODAL_ID_RE = re.compile(r"(?:modal_id|itemId|aweme_id)[=/](\d+)", re.IGNORECASE)
_ROUTER_DATA_RE = re.compile(r"window\._ROUTER_DATA\s*=\s*(\{.*?\})\s*</script>", re.S)
_RENDER_DATA_RE = re.compile(
    r"<script[^>]*id=\"RENDER_DATA\"[^>]*type=\"application/json\"[^>]*>(.*?)</script>",
    re.S,
)
_META_DESC_RE = re.compile(
    r'<meta[^>]+(?:name|property)="(?:og:description|description)"[^>]+content="([^"]*)"',
    re.I,
)
_RAW_DESC_RE = re.compile(r'"desc"\s*:\s*"((?:[^"\\]|\\.)*)"')
_SHARE_COPY_RE = re.compile(
    r"\u770b\u770b\u3010[^\u3011]*\u3011\s*(.*?)"
    r"(?:https?://|\u590d\u5236\u6b64\u94fe\u63a5|\u590d\u5236\u6253\u5f00\u6296\u97f3)",
    re.S,
)


def extract_douyin_url(text: str) -> str:
    """Return the first Douyin URL found inside arbitrary paste text."""
    for url in _URL_RE.findall(text):
        url = url.rstrip(".,\uff0c\u3002;\uff1b")
        host = (url.split("://", 1)[-1].split("/", 1)[0]).casefold()
        if any(marker in host for marker in DOUYIN_HOST_MARKERS):
            return url
    return ""


def _extract_aweme_id(url: str) -> str:
    match = _AWEME_ID_RE.search(url)
    if match:
        return match.group(1)
    match = _MODAL_ID_RE.search(url)
    return match.group(1) if match else ""


def _find_item_list(node: Any) -> list[dict[str, Any]] | None:
    """Recursively locate the aweme item_list inside Douyin's embedded JSON."""
    if isinstance(node, dict):
        items = node.get("item_list")
        if isinstance(items, list) and items:
            return items
        for value in node.values():
            found = _find_item_list(value)
            if found is not None:
                return found
    elif isinstance(node, list):
        for value in node:
            found = _find_item_list(value)
            if found is not None:
                return found
    return None


def _find_first_item(html: str) -> dict[str, Any] | None:
    """Return the first aweme item embedded in a Douyin share page."""
    router = _ROUTER_DATA_RE.search(html)
    if router:
        try:
            items = _find_item_list(json.loads(router.group(1)))
            if items:
                return items[0]
        except json.JSONDecodeError:
            pass
    render_data = _RENDER_DATA_RE.search(html)
    if render_data:
        try:
            data = json.loads(urllib.parse.unquote(render_data.group(1)))
            items = _find_item_list(data)
            if items:
                return items[0]
        except (json.JSONDecodeError, ValueError):
            pass
    return None


def _parse_copy_from_html(html: str) -> str:
    item = _find_first_item(html)
    if item:
        desc = (item.get("desc") or "").strip()
        if desc:
            return desc
    raw = _RAW_DESC_RE.search(html)
    if raw:
        try:
            desc = json.loads(f'"{raw.group(1)}"').strip()
            if desc:
                return desc
        except json.JSONDecodeError:
            pass
    meta = _META_DESC_RE.search(html)
    if meta:
        desc = html_lib.unescape(meta.group(1)).strip()
        if len(desc) >= 8 and "douyin" not in desc[:30].casefold():
            return desc
    return ""


def _extract_share_text_copy(text: str) -> str:
    """Fallback: pull the description embedded in the share paste itself."""
    match = _SHARE_COPY_RE.search(text)
    if match:
        copy = match.group(1).strip(" \t\r\n").strip("\uff0c").strip("\u3002")
        if copy:
            return copy
    return ""


def _fetch_share_page(aweme_id: str, cookie: str) -> str:
    headers = {
        "User-Agent": MOBILE_UA,
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": f"https://www.douyin.com/video/{aweme_id}",
    }
    if cookie:
        headers["Cookie"] = cookie
    try:
        response = net.get(
            f"https://www.iesdouyin.com/share/video/{aweme_id}",
            headers=headers,
            follow_redirects=True,
            timeout=REQUEST_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise DouyinResolveError(
            f"\u7f51\u7edc\u8bf7\u6c42\u5931\u8d25\uff1a{exc.__class__.__name__}",
            code="network",
        ) from exc
    return response.text


def _resolve_aweme_id(text: str, cookie: str) -> tuple[str, str]:
    """Follow the share link and return (aweme_id, final_url)."""
    url = extract_douyin_url(text)
    if not url:
        raise DouyinResolveError(
            "\u672a\u68c0\u6d4b\u5230\u6296\u97f3\u94fe\u63a5\uff0c"
            "\u8bf7\u7c98\u8d34\u5b8c\u6574\u7684\u6296\u97f3\u5206\u4eab\u94fe\u63a5\u6216\u53e3\u4ee4",
            code="no_link",
        )
    headers = {
        "User-Agent": DESKTOP_UA,
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": "https://www.douyin.com/",
    }
    if cookie:
        headers["Cookie"] = cookie
    try:
        response = net.get(url, headers=headers, follow_redirects=True, timeout=REQUEST_TIMEOUT)
    except httpx.HTTPError as exc:
        raise DouyinResolveError(
            f"\u7f51\u7edc\u8bf7\u6c42\u5931\u8d25\uff1a{exc.__class__.__name__}",
            code="network",
        ) from exc
    return _extract_aweme_id(str(response.url)), str(response.url)


def resolve_douyin_copy(text: str, cookie: str = "") -> tuple[str, str]:
    """Resolve a Douyin share link/paste and return (copy, source_url)."""
    aweme_id, final_url = _resolve_aweme_id(text, cookie)
    if not aweme_id:
        copy = _extract_share_text_copy(text)
        if copy:
            return copy, final_url
        if cookie:
            raise DouyinResolveError(
                "\u672a\u80fd\u89e3\u6790\u6296\u97f3\u94fe\u63a5\uff0c"
                "Cookie \u53ef\u80fd\u5df2\u5931\u6548\uff0c"
                "\u8bf7\u5728\u8bbe\u7f6e\u4e2d\u66f4\u65b0\u540e\u91cd\u8bd5",
                code="needs_cookie",
            )
        raise DouyinResolveError(
            "\u6296\u97f3\u77ed\u94fe\u63a5\u89e3\u6790\u88ab\u98ce\u63a7\u62e6\u622a\uff0c"
            "\u9700\u8981\u7f51\u9875 Cookie \u624d\u80fd\u63d0\u53d6\u6587\u6848\uff1b"
            "\u8bf7\u6253\u5f00\u201c\u8bbe\u7f6e \u2192 \u6587\u6848\u914d\u7f6e\u8bbe\u7f6e\u201d"
            "\u7c98\u8d34\u6296\u97f3\u7f51\u9875\u7248 Cookie \u540e\u91cd\u8bd5",
            code="needs_cookie",
        )
    html = _fetch_share_page(aweme_id, cookie)
    desc = _parse_copy_from_html(html)
    if desc:
        return desc, final_url
    copy = _extract_share_text_copy(text)
    if copy:
        return copy, final_url
    if "SYSTEM_ITEM_NOT_EXIST" in html or "\u4e0d\u5b58\u5728" in html:
        raise DouyinResolveError(
            "\u8be5\u6296\u97f3\u89c6\u9891\u4e0d\u5b58\u5728\u6216\u5df2\u88ab\u5220\u9664\uff0c"
            "\u8bf7\u786e\u8ba4\u94fe\u63a5\u6709\u6548",
            code="not_found",
        )
    if cookie:
        raise DouyinResolveError(
            "\u6296\u97f3\u672a\u8fd4\u56de\u6587\u6848\u6570\u636e\uff0c"
            "Cookie \u53ef\u80fd\u5df2\u5931\u6548\uff0c"
            "\u8bf7\u5728\u8bbe\u7f6e\u4e2d\u66f4\u65b0\u540e\u91cd\u8bd5",
            code="needs_cookie",
        )
    raise DouyinResolveError(
        "\u6296\u97f3\u672a\u8fd4\u56de\u53ef\u7528\u7684\u6587\u6848\u6570\u636e\uff0c"
        "\u9700\u8981\u7f51\u9875 Cookie \u624d\u80fd\u63d0\u53d6\uff1b"
        "\u8bf7\u6253\u5f00\u201c\u8bbe\u7f6e \u2192 \u6587\u6848\u914d\u7f6e\u8bbe\u7f6e\u201d"
        "\u7c98\u8d34\u6296\u97f3\u7f51\u9875\u7248 Cookie \u540e\u91cd\u8bd5",
        code="needs_cookie",
    )


def _url_host(url: str) -> str:
    try:
        return urllib.parse.urlparse(url).netloc
    except ValueError:
        return ""


def _play_url_variants(uri: str) -> list[str]:
    """Build play URLs from a video_id.

    The aweme.snssdk.com playwm endpoint redirects straight to the media CDN
    and works without a cookie, while the douyin.com/iesdouyin.com endpoints
    often route through Douyin's anti-bot proxy which rejects ffmpeg clients.
    """
    return [
        f"https://aweme.snssdk.com/aweme/v1/playwm/?video_id={uri}&ratio=720p&line=0",
        f"https://aweme.snssdk.com/aweme/v1/play/?video_id={uri}&ratio=720p&line=0",
        f"https://www.douyin.com/aweme/v1/play/?video_id={uri}&ratio=720p&line=0",
        f"https://www.iesdouyin.com/aweme/v1/playwm/?video_id={uri}&ratio=720p&line=0",
        f"https://www.iesdouyin.com/aweme/v1/play/?video_id={uri}&ratio=720p&line=0",
    ]


def _extract_play_urls(item: dict[str, Any]) -> list[str]:
    """Collect candidate video/audio play URLs from an aweme item dict."""
    candidates: list[str] = []
    uris: list[str] = []
    video = item.get("video") or {}
    for key in ("play_addr", "download_addr", "play_addr_h264", "play_addr_265"):
        address = video.get(key)
        if not isinstance(address, dict):
            continue
        for url in address.get("url_list") or []:
            if isinstance(url, str) and url.startswith("http"):
                candidates.append(url)
        uri = address.get("uri")
        if isinstance(uri, str) and uri:
            uris.append(uri)
    for bit_rate in video.get("bit_rate") or []:
        if not isinstance(bit_rate, dict):
            continue
        address = bit_rate.get("play_addr")
        if not isinstance(address, dict):
            continue
        for url in address.get("url_list") or []:
            if isinstance(url, str) and url.startswith("http"):
                candidates.append(url)
        uri = address.get("uri")
        if isinstance(uri, str) and uri:
            uris.append(uri)
    for uri in uris:
        candidates.extend(_play_url_variants(uri))
    # Audio-only fallback: for original voiceover videos the music entry
    # usually carries the same full audio track.
    for music in (item.get("music"), video.get("music")):
        if not isinstance(music, dict):
            continue
        address = music.get("play_url")
        if not isinstance(address, dict):
            continue
        for url in address.get("url_list") or []:
            if isinstance(url, str) and url.startswith("http"):
                candidates.append(url)
    seen: set[str] = set()
    return [url for url in candidates if not (url in seen or seen.add(url))]


def _media_headers(cookie: str, user_agent: str = MOBILE_UA) -> dict[str, str]:
    headers = {
        "User-Agent": user_agent,
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": "https://www.douyin.com/",
    }
    if cookie:
        headers["Cookie"] = cookie
    return headers


def _looks_like_html(path: Path) -> bool:
    """Detect anti-bot/verification pages served with a masked content type."""
    try:
        with path.open("rb") as file_handle:
            head = file_handle.read(512)
    except OSError:
        return False
    stripped = head.lstrip()
    return (
        stripped.startswith(b"<!doctype")
        or stripped.startswith(b"<html")
        or stripped.startswith(b"{")
        or stripped.startswith(b"[")
        or b"<script" in head[:256]
    )


def _download_media(url: str, cookie: str, dest: Path, user_agent: str = MOBILE_UA) -> Path:
    """Download media bytes with transient retries; reject non-media payloads."""
    last_detail = ""
    for attempt in range(3):
        try:
            with net.stream(
                "GET",
                url,
                headers=_media_headers(cookie, user_agent),
                follow_redirects=True,
                timeout=DOWNLOAD_TIMEOUT,
            ) as response:
                response.raise_for_status()
                content_type = (response.headers.get("content-type") or "").casefold()
                if "text/html" in content_type or "application/json" in content_type:
                    raise DouyinResolveError(
                        "\u64ad\u653e\u5730\u5740\u8fd4\u56de\u4e86\u9a8c\u8bc1/\u5c01\u9501\u9875\u9762\uff0c"
                        "\u53ef\u80fd\u9700\u8981\u6296\u97f3 Cookie \u6216\u88ab\u98ce\u63a7\u62e6\u622a",
                        code="download_blocked",
                    )
                with dest.open("wb") as file_handle:
                    for chunk in response.iter_bytes(1024 * 256):
                        file_handle.write(chunk)
            break
        except DouyinResolveError:
            raise
        except httpx.HTTPError as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            detail = f"HTTP {status}" if status else exc.__class__.__name__
            last_detail = f"{detail} ({_url_host(url)})"
            if attempt < 2:
                time.sleep(1.0 * (attempt + 1))
            else:
                raise DouyinResolveError(
                    f"\u89c6\u9891\u97f3\u9891\u4e0b\u8f7d\u5931\u8d25\uff1a{last_detail}",
                    code="download_failed",
                ) from exc
    if dest.stat().st_size == 0:
        raise DouyinResolveError(
            "\u89c6\u9891\u97f3\u9891\u4e0b\u8f7d\u7ed3\u679c\u4e3a\u7a7a",
            code="download_failed",
        )
    if _looks_like_html(dest):
        raise DouyinResolveError(
            "\u64ad\u653e\u5730\u5740\u8fd4\u56de\u4e86\u9a8c\u8bc1/\u5c01\u9501\u9875\u9762\uff0c"
            "\u53ef\u80fd\u9700\u8981\u6296\u97f3 Cookie \u6216\u88ab\u98ce\u63a7\u62e6\u622a",
            code="download_blocked",
        )
    return dest


def _run_ffmpeg_wav(
    ffmpeg: str, cookie: str, source: str, dest: Path, user_agent: str = MOBILE_UA
) -> subprocess.CompletedProcess[str]:
    header_line = f"User-Agent: {user_agent}\r\nReferer: https://www.douyin.com/\r\n"
    if cookie:
        header_line += f"Cookie: {cookie}\r\n"
    command = [ffmpeg, "-y", "-headers", header_line]
    if source.startswith("http"):
        command += ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5"]
    command += [
        "-i",
        source,
        "-vn",
        "-af",
        "loudnorm=I=-16:TP=-1.5:LRA=11",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        str(dest),
    ]
    try:
        return subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=DOWNLOAD_TIMEOUT,
            check=False,
            creationflags=WINDOWS_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DouyinResolveError(
            "\u65e0\u6cd5\u4ece\u89c6\u9891\u4e2d\u63d0\u53d6\u97f3\u9891\u6d41\uff0c"
            "\u8bf7\u786e\u8ba4\u89c6\u9891\u5305\u542b\u97f3\u9891",
            code="audio_extract_failed",
        ) from exc


def _extract_audio_stream(
    ffmpeg: str, cookie: str, source: Path, dest: Path, user_agent: str = MOBILE_UA
) -> Path:
    result = _run_ffmpeg_wav(ffmpeg, cookie, str(source), dest, user_agent=user_agent)
    if result.returncode != 0 or not dest.is_file() or dest.stat().st_size == 0:
        raise DouyinResolveError(
            "\u65e0\u6cd5\u4ece\u89c6\u9891\u4e2d\u63d0\u53d6\u97f3\u9891\u6d41\uff0c"
            "\u8bf7\u786e\u8ba4\u89c6\u9891\u5305\u542b\u97f3\u9891",
            code="audio_extract_failed",
        )
    return dest


def _download_via_ffmpeg(
    ffmpeg: str, cookie: str, url: str, dest: Path, user_agent: str = MOBILE_UA
) -> Path:
    result = _run_ffmpeg_wav(ffmpeg, cookie, url, dest, user_agent=user_agent)
    if result.returncode != 0 or not dest.is_file() or dest.stat().st_size == 0:
        raise DouyinResolveError(
            "\u89c6\u9891\u97f3\u9891\u4e0b\u8f7d\u5931\u8d25\uff1affmpeg "
            "\u65e0\u6cd5\u83b7\u53d6\u5a92\u4f53\u6d41",
            code="download_failed",
        )
    return dest


def _is_hls_playlist(path: Path) -> bool:
    try:
        with path.open("rb") as file_handle:
            head = file_handle.read(64)
    except OSError:
        return False
    return head.lstrip().startswith(b"#EXTM3U")


def _absolutize_hls_playlist(playlist: Path, url: str) -> None:
    """Rewrite relative segment URLs inside a local m3u8 to absolute CDN
    URLs so ffmpeg can fetch them with the media request headers."""
    try:
        text = playlist.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    base = url.rsplit("/", 1)[0] + "/" if "/" in url else url

    def to_absolute(uri: str) -> str:
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", uri):
            return uri
        return base + uri

    text = re.sub(
        r"(?m)^([^#\s][^\r\n]*)$",
        lambda match: to_absolute(match.group(1)),
        text,
    )
    text = re.sub(
        r'URI="([^"]+)"',
        lambda match: f'URI="{to_absolute(match.group(1))}"',
        text,
    )
    try:
        playlist.write_text(text, encoding="utf-8")
    except OSError:
        pass


def _acquire_audio(ffmpeg: str, cookie: str, url: str, media_path: Path, wav_path: Path) -> None:
    """Produce a transcribable wav from a play URL, or raise DouyinResolveError."""
    errors: list[DouyinResolveError] = []
    # Direct ffmpeg pull is the fast path. Retry once with the alternate UA
    # when the first attempt trips an anti-bot rule.
    for index, user_agent in enumerate(_MEDIA_UA_CYCLE):
        if index:
            time.sleep(1.0)
        try:
            _download_via_ffmpeg(ffmpeg, cookie, url, wav_path, user_agent=user_agent)
            return
        except DouyinResolveError as exc:
            errors.append(exc)
            if exc.code != "download_blocked":
                break
    # httpx download then local ffmpeg extraction. Swap UA on blocked
    # responses and re-download once when the stream was truncated (the CDN
    # occasionally drops connections mid-stream, leaving a file ffmpeg
    # cannot parse).
    for user_agent in _MEDIA_UA_CYCLE:
        for attempt in range(2):
            try:
                _download_media(url, cookie, media_path, user_agent=user_agent)
                if _is_hls_playlist(media_path):
                    # HLS playlists carry the segment URLs; rewrite relative
                    # segments to absolute CDN URLs, then let ffmpeg pull
                    # them from the local playlist file.
                    _absolutize_hls_playlist(media_path, url)
                    _extract_audio_stream(
                        ffmpeg, cookie, media_path, wav_path, user_agent=user_agent
                    )
                    return
                _extract_audio_stream(
                    ffmpeg, cookie, media_path, wav_path, user_agent=user_agent
                )
                return
            except DouyinResolveError as exc:
                errors.append(exc)
                if exc.code == "download_blocked":
                    break
                if attempt == 0:
                    time.sleep(1.5)
    for error in errors:
        if error.code == "download_blocked":
            raise error from None
    raise errors[-1] from None


def _wav_duration(audio_path: Path) -> float | None:
    """Return the wave duration in seconds, or None when not a wave file."""
    try:
        with wave.open(str(audio_path), "rb") as wav_file:
            return wav_file.getnframes() / max(wav_file.getframerate(), 1)
    except (OSError, wave.Error):
        return None


def _split_wav_chunks(audio_path: Path) -> list[Path]:
    """Split a long wave into <=ASR_CHUNK_SECONDS chunks with a small overlap."""
    with wave.open(str(audio_path), "rb") as wav_file:
        frame_rate = wav_file.getframerate()
        channels = wav_file.getnchannels()
        sample_width = wav_file.getsampwidth()
        total_frames = wav_file.getnframes()
        frames = wav_file.readframes(total_frames)
    frames_per_chunk = int(ASR_CHUNK_SECONDS * frame_rate)
    overlap_frames = int(ASR_CHUNK_OVERLAP_SECONDS * frame_rate)
    bytes_per_frame = channels * sample_width
    step = max(frames_per_chunk - overlap_frames, 1)
    chunks: list[Path] = []
    start = 0
    while start < total_frames:
        end = min(start + frames_per_chunk, total_frames)
        chunk = Path(f"{audio_path}.chunk{len(chunks)}.wav")
        with wave.open(str(chunk), "wb") as out_file:
            out_file.setnchannels(channels)
            out_file.setsampwidth(sample_width)
            out_file.setframerate(frame_rate)
            out_file.writeframes(frames[start * bytes_per_frame : end * bytes_per_frame])
        chunks.append(chunk)
        if end == total_frames:
            break
        start += step
    return chunks


def _merge_transcript_parts(parts: list[str]) -> str:
    """Concatenate chunk transcripts, dropping duplicated overlap regions.

    Overlap matching ignores punctuation so a sentence finalized at a chunk
    boundary (e.g. "????" vs "???????...") still aligns; the whole
    later part is kept because it usually covers the boundary more fully.
    """
    result = ""
    for part in parts:
        part = (part or "").strip()
        if not part:
            continue
        if not result:
            result = part
            continue
        result_flat = "".join(char for char in result if char.isalnum())
        part_flat = "".join(char for char in part if char.isalnum())
        overlap = 0
        max_overlap = min(len(result_flat), len(part_flat), 24)
        for size in range(1, max_overlap + 1):
            if result_flat[-size:] == part_flat[:size]:
                overlap = size
        if overlap == 0:
            result += part
            continue
        # Map the overlap start inside result (in punctuation-stripped
        # coordinates) back to the original string, then keep everything
        # before it and append the full later part.
        flat_index = len(result_flat) - overlap
        original_index = 0
        seen = 0
        while seen < flat_index and original_index < len(result):
            if result[original_index].isalnum():
                seen += 1
            original_index += 1
        result = result[:original_index] + part
    return result


def _system_proxy_url() -> str | None:
    """Return the Windows system HTTP proxy when enabled, else None."""
    try:
        import winreg
    except ImportError:
        return None
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        ) as key:
            enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
            server, _ = winreg.QueryValueEx(key, "ProxyServer")
        if enabled and server:
            return server if "://" in server else f"http://{server}"
    except OSError:
        pass
    return None


async def _transcribe_ws_chunk(
    source: Path, api_key: str, proxy_url: str | None = None, model: str = ASR_MODEL
) -> str:
    """Transcribe one audio file over DashScope's realtime websocket API.

    paraformer-realtime-v2 emits interim hypotheses followed by a finalized
    sentence (with end_time); only finalized sentences are collected, and
    punctuation comes back by default.
    """
    task_id = uuid.uuid4().hex
    start_message = {
        "header": {"task_id": task_id, "streaming": "duplex", "action": "run-task"},
        "payload": {
            "model": model,
            "task_group": "audio",
            "task": "asr",
            "function": "recognition",
            "input": {},
            "parameters": {
                "enable_punctuation_prediction": True,
                "enable_semantic_sentence_detection": True,
            },
        },
    }
    connect_kwargs: dict[str, Any] = {
        "additional_headers": {"Authorization": f"Bearer {api_key}"},
        "max_size": 16 * 1024 * 1024,
        "open_timeout": REQUEST_TIMEOUT,
    }
    last_exc: Exception | None = None
    websocket = None
    for proxy in (None, proxy_url):
        if proxy is None and last_exc is not None:
            continue
        try:
            websocket = await websockets.connect(DASHSCOPE_WS_URL, proxy=proxy, **connect_kwargs)
            break
        except Exception as exc:  # noqa: BLE001 - mapped to ASR errors below
            last_exc = exc
    if websocket is None:
        raise last_exc  # type: ignore[misc]
    sentences: list[str] = []
    try:
        async with websocket:
            await websocket.send(json.dumps(start_message, ensure_ascii=False))
            while True:
                raw = await asyncio.wait_for(websocket.recv(), timeout=ASR_TIMEOUT)
                if isinstance(raw, bytes):
                    continue
                message = json.loads(raw)
                event = (message.get("header") or {}).get("event", "")
                if event == "task-started":
                    break
                if event == "task-failed":
                    raise DouyinResolveError(
                        f"\u8bed\u97f3\u8bc6\u522b\u5931\u8d25\uff1a"
                        f"{(message.get('header') or {}).get('error_message')}",
                        code="asr_failed",
                    )
            with source.open("rb") as file_handle:
                while True:
                    data = file_handle.read(16 * 1024)
                    if not data:
                        break
                    await websocket.send(data)
            await websocket.send(
                json.dumps(
                    {
                        "header": {
                            "task_id": task_id,
                            "streaming": "duplex",
                            "action": "finish-task",
                        },
                        "payload": {"input": {}},
                    }
                )
            )
            while True:
                raw = await asyncio.wait_for(websocket.recv(), timeout=ASR_TIMEOUT)
                if isinstance(raw, bytes):
                    continue
                message = json.loads(raw)
                event = (message.get("header") or {}).get("event", "")
                if event == "result-generated":
                    sentence = ((message.get("payload") or {}).get("output") or {}).get(
                        "sentence"
                    ) or {}
                    if sentence.get("end_time") is not None:
                        text = str(sentence.get("text") or "").strip()
                        if text:
                            sentences.append(text)
                elif event == "task-finished":
                    break
                elif event == "task-failed":
                    raise DouyinResolveError(
                        f"\u8bed\u97f3\u8bc6\u522b\u5931\u8d25\uff1a"
                        f"{(message.get('header') or {}).get('error_message')}",
                        code="asr_failed",
                    )
    finally:
        # ensure the websocket object is closed even when asyncio.run exits
        try:
            await websocket.close()
        except Exception:
            pass
    return "".join(sentences)


def _transcribe_chunk(source: Path, headers: dict[str, str], model: str = ASR_MODEL) -> str:
    """Send one audio chunk to DashScope realtime recognition and return text."""
    api_key = (headers.get("Authorization") or "").removeprefix("Bearer ").strip()
    try:
        return asyncio.run(_transcribe_ws_chunk(source, api_key, _system_proxy_url(), model))
    except DouyinResolveError:
        raise
    except websockets.exceptions.InvalidStatus as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status in {401, 403}:
            raise DouyinResolveError(
                "\u767e\u70bc API Key \u65e0\u6548\u6216\u6ca1\u6709"
                "\u8bed\u97f3\u8bc6\u522b\u6743\u9650\uff0c"
                "\u8bf7\u68c0\u67e5\u540e\u91cd\u8bd5",
                code="invalid_asr_key",
            ) from exc
        raise DouyinResolveError(
            f"\u8bed\u97f3\u8bc6\u522b\u8bf7\u6c42\u5931\u8d25\uff1aHTTP "
            f"{status or exc.__class__.__name__}",
            code="asr_failed",
        ) from exc
    except Exception as exc:
        raise DouyinResolveError(
            f"\u8bed\u97f3\u8bc6\u522b\u8bf7\u6c42\u5931\u8d25\uff1a{exc.__class__.__name__}",
            code="asr_failed",
        ) from exc


def _asr_http_error(prefix: str, response: httpx.Response) -> DouyinResolveError:
    """Build a DouyinResolveError from a DashScope HTTP response."""
    message = prefix
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        error = body.get("message") or (body.get("error") or {}).get("message")
        if error:
            message = f"{prefix}\uff1a{error}"
    code = "invalid_asr_key" if response.status_code in (401, 403) else "asr_failed"
    return DouyinResolveError(message, code=code)


def _asr_request(method: str, url: str, **kwargs: Any) -> httpx.Response:
    """Issue an httpx request, retrying once over a direct connection when the
    system proxy is unreachable or resets the connection mid-request."""
    try:
        return getattr(httpx, method)(url, trust_env=True, **kwargs)
    except httpx.HTTPError:
        return getattr(httpx, method)(url, trust_env=False, **kwargs)


def _upload_audio_file(audio_path: Path, api_key: str, model: str) -> str:
    """Upload a local audio file to DashScope storage; returns an oss:// URL.

    DashScope hands out a short-lived OSS upload certificate, then the file
    is posted to the certificate's upload host as multipart form data.
    """
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        response = _asr_request(
            "get",
            DASHSCOPE_UPLOAD_URL,
            params={"action": "getPolicy", "model": model},
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise DouyinResolveError(
            f"\u83b7\u53d6\u4e0a\u4f20\u51ed\u8bc1\u5931\u8d25\uff1a{exc.__class__.__name__}",
            code="asr_failed",
        ) from exc
    if response.status_code != 200:
        raise _asr_http_error("\u83b7\u53d6\u4e0a\u4f20\u51ed\u8bc1\u5931\u8d25", response)
    certificate = response.json().get("data") or {}
    upload_host = certificate.get("upload_host")
    if not upload_host:
        raise DouyinResolveError(
            "\u83b7\u53d6\u4e0a\u4f20\u51ed\u8bc1\u5931\u8d25\uff1a"
            "\u54cd\u5e94\u4e2d\u7f3a\u5c11\u4e0a\u4f20\u5730\u5740",
            code="asr_failed",
        )
    form = {
        "OSSAccessKeyId": certificate.get("oss_access_key_id"),
        "Signature": certificate.get("signature"),
        "policy": certificate.get("policy"),
        "key": f"{certificate.get('upload_dir')}/{audio_path.name}",
        "x-oss-object-acl": certificate.get("x_oss_object_acl"),
        "x-oss-forbid-overwrite": certificate.get("x_oss_forbid_overwrite"),
        "success_action_status": "200",
        "x-oss-content-type": "application/octet-stream",
    }
    try:
        response = _asr_request(
            "post",
            upload_host,
            data=form,
            files={"file": (audio_path.name, audio_path.read_bytes(), "application/octet-stream")},
            timeout=DOWNLOAD_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise DouyinResolveError(
            f"\u97f3\u9891\u4e0a\u4f20\u5931\u8d25\uff1a{exc.__class__.__name__}",
            code="asr_failed",
        ) from exc
    if response.status_code != 200:
        raise _asr_http_error("\u97f3\u9891\u4e0a\u4f20\u5931\u8d25", response)
    return f"oss://{form['key']}"


def _submit_transcription_task(uploaded_url: str, api_key: str, model: str) -> str:
    """Submit an async transcription task for an uploaded audio URL."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "X-DashScope-Async": "enable",
    }
    payload = {"model": model, "input": {"file_urls": [uploaded_url]}}
    try:
        response = _asr_request(
            "post",
            DASHSCOPE_TRANSCRIPTION_URL,
            headers=headers,
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise DouyinResolveError(
            f"\u63d0\u4ea4\u8f6c\u5199\u4efb\u52a1\u5931\u8d25\uff1a{exc.__class__.__name__}",
            code="asr_failed",
        ) from exc
    if response.status_code != 200:
        raise _asr_http_error("\u63d0\u4ea4\u8f6c\u5199\u4efb\u52a1\u5931\u8d25", response)
    task_id = (response.json().get("output") or {}).get("task_id")
    if not task_id:
        raise DouyinResolveError(
            "\u63d0\u4ea4\u8f6c\u5199\u4efb\u52a1\u5931\u8d25\uff1a"
            "\u54cd\u5e94\u4e2d\u7f3a\u5c11\u4efb\u52a1 ID",
            code="asr_failed",
        )
    return task_id


def _wait_transcription_task(task_id: str, api_key: str) -> str:
    """Poll an async transcription task until it finishes; returns the result URL."""
    headers = {"Authorization": f"Bearer {api_key}"}
    url = DASHSCOPE_TASK_URL.format(task_id=task_id)
    deadline = time.monotonic() + ASR_TIMEOUT
    while time.monotonic() < deadline:
        try:
            response = _asr_request("get", url, headers=headers, timeout=REQUEST_TIMEOUT)
        except httpx.HTTPError as exc:
            raise DouyinResolveError(
                f"\u67e5\u8be2\u8f6c\u5199\u4efb\u52a1\u5931\u8d25\uff1a{exc.__class__.__name__}",
                code="asr_failed",
            ) from exc
        if response.status_code != 200:
            raise _asr_http_error("\u67e5\u8be2\u8f6c\u5199\u4efb\u52a1\u5931\u8d25", response)
        output = response.json().get("output") or {}
        status = output.get("task_status")
        if status == "SUCCEEDED":
            results = output.get("results") or []
            transcription_url = results[0].get("transcription_url") if results else None
            if not transcription_url:
                raise DouyinResolveError(
                    "\u8f6c\u5199\u4efb\u52a1\u5b8c\u6210\u4f46\u7f3a\u5c11\u7ed3\u679c\u5730\u5740",
                    code="asr_failed",
                )
            return transcription_url
        if status in ("FAILED", "CANCELED"):
            failure_code = str(output.get("code") or "")
            failure_detail = str(output.get("message") or "")
            if failure_code in ("FILE_DOWNLOAD_FAILED", "FILE_403_FORBIDDEN", "SERVER_ERROR"):
                raise DouyinResolveError(
                    "\u8f6c\u5199\u4efb\u52a1\u5931\u8d25\uff1a"
                    "\u5f55\u97f3\u6587\u4ef6\u6a21\u578b\u65e0\u6cd5\u8bfb\u53d6\u4e0a\u4f20\u7684\u97f3\u9891"
                    "\uff08\u5f53\u524d\u767e\u70bc Key \u7684 OSS \u6587\u4ef6"
                    "\u8bbf\u95ee\u53d7\u9650\uff09\uff1b"
                    "\u8bf7\u5728\u8bbe\u7f6e\u4e2d\u5207\u6362\u4e3a\u201c\u5b9e\u65f6\u8bc6\u522b\u201d\u6a21\u578b"
                    "\uff08\u63a8\u8350\uff09\u6216\u66f4\u6362\u5177\u5907 OSS \u6743\u9650\u7684 API Key",
                    code="asr_failed",
                )
            raise DouyinResolveError(
                f"\u8f6c\u5199\u4efb\u52a1\u5931\u8d25\uff1a{failure_code or status}"
                + (
                    f"\uff08{failure_detail}\uff09"
                    if failure_detail and failure_detail != failure_code
                    else ""
                ),
                code="asr_failed",
            )
        time.sleep(2.0)
    raise DouyinResolveError("\u8f6c\u5199\u4efb\u52a1\u8d85\u65f6", code="asr_failed")


def _fetch_transcript(transcription_url: str, api_key: str) -> str:
    """Fetch the transcript text from an async transcription result URL."""
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        response = _asr_request("get", transcription_url, headers=headers, timeout=DOWNLOAD_TIMEOUT)
    except httpx.HTTPError as exc:
        raise DouyinResolveError(
            f"\u83b7\u53d6\u8f6c\u5199\u7ed3\u679c\u5931\u8d25\uff1a{exc.__class__.__name__}",
            code="asr_failed",
        ) from exc
    if response.status_code != 200:
        raise _asr_http_error("\u83b7\u53d6\u8f6c\u5199\u7ed3\u679c\u5931\u8d25", response)
    body = response.json()
    transcript_text = ""
    if isinstance(body, dict):
        transcription = body.get("transcription")
        if isinstance(transcription, dict):
            transcript_text = str(transcription.get("text") or "")
        if not transcript_text:
            transcripts = body.get("transcripts")
            if isinstance(transcripts, list):
                transcript_text = "".join(
                    str(item.get("text") or "")
                    for item in transcripts
                    if isinstance(item, dict)
                )
    return transcript_text.strip()


def _transcribe_async_file(audio_path: Path, api_key: str, model: str) -> str:
    """Transcribe a local audio file with an offline DashScope ASR model.

    Offline models (paraformer-v2, paraformer-v1, sensenova-paraformer) are
    served by the async transcription API: upload the audio to DashScope
    storage, submit a task, poll until it finishes, then fetch the text.
    """
    uploaded_url = _upload_audio_file(audio_path, api_key, model)
    task_id = _submit_transcription_task(uploaded_url, api_key, model)
    transcription_url = _wait_transcription_task(task_id, api_key)
    return _fetch_transcript(transcription_url, api_key)


def _transcribe_audio(audio_path: Path, api_key: str, model: str = ASR_MODEL) -> str:
    if not api_key:
        raise DouyinResolveError(
            "\u9700\u8981\u914d\u7f6e\u963f\u91cc\u4e91\u767e\u70bc API Key\uff1a"
            "\u8bf7\u6253\u5f00\u201c\u8bbe\u7f6e \u2192 \u6587\u6848\u914d\u7f6e\u8bbe\u7f6e\u201d"
            "\u586b\u5199\u540e\u91cd\u8bd5",
            code="needs_asr_key",
        )
    if model not in ASR_REALTIME_MODELS:
        transcript = _transcribe_async_file(audio_path, api_key, model)
        if not transcript:
            raise DouyinResolveError(
                "\u672a\u8bc6\u522b\u5230\u4eba\u58f0\u5185\u5bb9\uff0c"
                "\u8bf7\u786e\u8ba4\u89c6\u9891\u5305\u542b\u53e3\u64ad\u914d\u97f3",
                code="asr_empty",
            )
        return transcript
    headers = {"Authorization": f"Bearer {api_key}"}
    duration = _wav_duration(audio_path)
    chunk_paths: list[Path] = []
    try:
        if duration is not None and duration > ASR_CHUNK_SECONDS:
            chunk_paths = _split_wav_chunks(audio_path)
        sources = chunk_paths if chunk_paths else [audio_path]
        parts = [_transcribe_chunk(source, headers, model) for source in sources]
    finally:
        for chunk_path in chunk_paths:
            try:
                chunk_path.unlink(missing_ok=True)
            except OSError:
                pass
    transcript = _merge_transcript_parts(parts)
    if not transcript:
        raise DouyinResolveError(
            "\u672a\u8bc6\u522b\u5230\u4eba\u58f0\u5185\u5bb9\uff0c"
            "\u8bf7\u786e\u8ba4\u89c6\u9891\u5305\u542b\u53e3\u64ad\u914d\u97f3",
            code="asr_empty",
        )
    return transcript


def resolve_douyin_voiceover(
    text: str,
    cookie: str,
    api_key: str,
    ffmpeg: str,
    temp_dir: Path,
    asr_provider: str = "bailian",
    asr_model: str = ASR_MODEL,
    local_model_dir: Path | None = None,
) -> tuple[str, str]:
    """Resolve a Douyin share link and transcribe its spoken voiceover to text."""
    aweme_id, final_url = _resolve_aweme_id(text, cookie)
    if not aweme_id:
        raise DouyinResolveError(
            "\u672a\u80fd\u89e3\u6790\u6296\u97f3\u94fe\u63a5\u4e2d\u7684\u89c6\u9891 ID\uff0c"
            "\u8bf7\u786e\u8ba4\u94fe\u63a5\u5b8c\u6574\u6216\u66f4\u65b0 Cookie",
            code="needs_cookie",
        )
    html = _fetch_share_page(aweme_id, cookie)
    if "SYSTEM_ITEM_NOT_EXIST" in html or "\u4e0d\u5b58\u5728" in html:
        raise DouyinResolveError(
            "\u8be5\u6296\u97f3\u89c6\u9891\u4e0d\u5b58\u5728\u6216\u5df2\u88ab\u5220\u9664\uff0c"
            "\u8bf7\u786e\u8ba4\u94fe\u63a5\u6709\u6548",
            code="not_found",
        )
    item = _find_first_item(html)
    if item is None:
        if cookie:
            raise DouyinResolveError(
                "\u6296\u97f3\u672a\u8fd4\u56de\u89c6\u9891\u6570\u636e\uff0c"
                "Cookie \u53ef\u80fd\u5df2\u5931\u6548\uff0c"
                "\u8bf7\u5728\u8bbe\u7f6e\u4e2d\u66f4\u65b0\u540e\u91cd\u8bd5",
                code="needs_cookie",
            )
        raise DouyinResolveError(
            "\u9700\u8981\u7f51\u9875 Cookie \u624d\u80fd\u83b7\u53d6\u89c6\u9891\u97f3\u9891\uff1b"
            "\u8bf7\u6253\u5f00\u201c\u8bbe\u7f6e \u2192 \u6587\u6848\u914d\u7f6e\u8bbe\u7f6e\u201d"
            "\u7c98\u8d34\u6296\u97f3\u7f51\u9875\u7248 Cookie \u540e\u91cd\u8bd5",
            code="needs_cookie",
        )
    play_urls = _extract_play_urls(item)
    if not play_urls:
        raise DouyinResolveError(
            "\u672a\u80fd\u5728\u9875\u9762\u4e2d\u627e\u5230\u89c6\u9891\u64ad\u653e\u5730\u5740\uff0c"
            "\u8bf7\u786e\u8ba4\u94fe\u63a5\u6709\u6548\u6216\u66f4\u65b0 Cookie",
            code="no_media",
        )
    token = hashlib.sha1(f"{aweme_id}|{final_url}".encode()).hexdigest()[:16]
    temp_dir.mkdir(parents=True, exist_ok=True)
    media_path = temp_dir / f"douyin_{token}_src.bin"
    audio_path = temp_dir / f"douyin_{token}_asr.wav"
    try:
        errors: list[DouyinResolveError] = []
        for index, play_url in enumerate(play_urls):
            if index:
                # A transient block often affects several URLs at once;
                # pause briefly before hammering the next one.
                time.sleep(1.0)
            try:
                _acquire_audio(ffmpeg, cookie, play_url, media_path, audio_path)
                errors.clear()
                break
            except DouyinResolveError as exc:
                errors.append(exc)
        if errors:
            hint = (
                "\u8bf7\u786e\u8ba4\u6296\u97f3 Cookie \u6709\u6548"
                "\uff08\u53ef\u80fd\u5df2\u8fc7\u671f\uff09\uff0c\u66f4\u65b0\u540e\u91cd\u8bd5"
                if cookie
                else (
                    "\u672a\u914d\u7f6e\u6296\u97f3 Cookie \u65f6\u64ad\u653e\u5730\u5740"
                    "\u53ef\u80fd\u88ab\u62e6\u622a\uff0c\u8bf7\u5728\u8bbe\u7f6e\u4e2d\u6dfb\u52a0\u540e\u91cd\u8bd5"
                )
            )
            blocked = sum(1 for error in errors if error.code == "download_blocked")
            blocked_note = (
                f"\uff08\u5176\u4e2d {blocked} \u4e2a\u5730\u5740\u88ab\u98ce\u63a7\u62e6\u622a\uff09"
                if blocked
                else ""
            )
            raise DouyinResolveError(
                "\u89c6\u9891\u97f3\u9891\u4e0b\u8f7d\u5931\u8d25\uff1a"
                f"\u5df2\u5c1d\u8bd5 {len(play_urls)} \u4e2a\u64ad\u653e\u5730\u5740\u5747\u5931\u8d25"
                f"{blocked_note}\uff1b"
                f"\u6700\u540e\u4e00\u6b21\uff1a{errors[-1]}\uff1b"
                f"{hint}\u6296\u97f3 CDN \u5076\u53d1\u4e0d\u7a33\u5b9a\uff0c"
                "\u5efa\u8bae\u7a0d\u540e\u91cd\u8bd5",
                code="download_failed",
            )
        try:
            if asr_provider == "local":
                from .asr import is_abnormally_short, transcribe_local

                transcript = transcribe_local(audio_path, local_model_dir)
                if is_abnormally_short(transcript, audio_path):
                    raise DouyinResolveError(
                        "\u8bc6\u522b\u7ed3\u679c\u5f02\u5e38\u504f\u77ed\uff08"
                        f"\u4ec5 {len(transcript)} \u5b57\uff09\uff0c"
                        "\u8bf7\u786e\u8ba4\u89c6\u9891\u5305\u542b\u6e05\u6670\u53e3\u64ad",
                        code="asr_empty",
                    )
            else:
                transcript = _transcribe_audio(audio_path, api_key, asr_model)
        except DouyinResolveError as exc:
            if exc.code == "asr_empty":
                debug_path = temp_dir / f"douyin_{token}_debug.wav"
                try:
                    shutil.copy2(audio_path, debug_path)
                    message = f"{exc}\uff1b\u8c03\u8bd5\u97f3\u9891\u5df2\u4fdd\u5b58\u5728\uff1a{debug_path}"
                except OSError:
                    message = str(exc)
                raise DouyinResolveError(message, code="asr_empty") from exc
            raise
    finally:
        for path in (media_path, audio_path):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
    return transcript, final_url
