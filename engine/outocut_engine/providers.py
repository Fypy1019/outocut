from __future__ import annotations

import binascii
import json
import logging
import re
import subprocess
from pathlib import Path
from typing import Any

import httpx

from . import net
from .models import PersonaProfile, RewriteRequest, TtsRequest, VoiceCloneRequest
from .processes import WINDOWS_NO_WINDOW

logger = logging.getLogger("outocut_engine.providers")


class ProviderError(RuntimeError):
    def __init__(self, code: str, message: str, status_code: int = 502):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def _raise_for_provider(response: httpx.Response, provider: str) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if response.is_success:
        base_resp = payload.get("base_resp") or {}
        if base_resp.get("status_code", 0) in (0, None):
            return payload
    message = (
        payload.get("message")
        or payload.get("error", {}).get("message")
        or payload.get("base_resp", {}).get("status_msg")
        or response.text[:500]
        or f"{provider} 请求失败"
    )
    lowered = message.casefold()
    if response.status_code in {401, 403} or "api key" in lowered or "鉴权" in message:
        raise ProviderError(
            "invalid_api_key",
            f"{provider} API Key 无效或没有语音接口权限；请确认 MiniMax 区域与 Key 来源一致",
            401,
        )
    if response.status_code == 429 or "rate" in lowered or "频繁" in message:
        raise ProviderError("rate_limited", f"{provider} 请求过于频繁，请稍后重试", 429)
    if "balance" in lowered or "余额" in message or "quota" in lowered:
        raise ProviderError("quota_exhausted", f"{provider} 余额或额度不足", 402)
    logger.error("%s 接口请求失败（HTTP %s）：%s", provider, response.status_code, message)
    raise ProviderError("provider_error", f"{provider} API 请求失败：{message}", response.status_code or 502)


async def _post_json(url: str, *, timeout: float, provider: str, **kwargs: Any) -> httpx.Response:
    """POST with unified Chinese network-error reporting."""
    try:
        return await net.apost(url, timeout=timeout, **kwargs)
    except httpx.HTTPError as exc:
        logger.error("%s 网络请求失败：%s", provider, exc)
        raise ProviderError(
            "network_error",
            f"{provider} 网络请求失败：{exc.__class__.__name__}，请检查网络或代理设置",
            502,
        ) from exc


def _persona_context(persona: PersonaProfile | None) -> str:
    if not persona:
        return "未提供特定人设，只能使用参考文本中明确存在的事实。"
    return "\n".join(
        (
            f"行业：{persona.industry}",
            f"目标人群：{persona.audience}",
            f"卖点：{persona.selling_points}",
            f"经历：{persona.experience}",
            f"口吻：{persona.tone}",
            f"禁用表达：{'、'.join(persona.prohibited_claims)}",
        )
    )


class BailianClient:
    def __init__(self, base_url: str, api_key: str, *, label: str = "百炼", json_mode: bool = True):
        if not api_key:
            raise ProviderError("missing_api_key", f"请先配置{label} API Key", 400)
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.label = label
        self.json_mode = json_mode

    async def rewrite(self, request: RewriteRequest) -> list[str]:
        if request.faithful:
            system_prompt = (
                "你是短视频分镜文案分段助手。必须严格保留用户提供的原文措辞、内容和顺序，"
                "不得改写、润色、增删或总结任何内容，只把原文按语义自然划分为指定数量的段落，"
                "每段对应一个镜头，段与段之间保持口播连贯。"
                '仅返回JSON对象，格式为{"shots":["..."]}，不得输出Markdown。'
            )
            temperature = 0.2
        else:
            system_prompt = (
                "你是短视频分镜文案编辑。只能使用用户提供的参考文本和人设事实，不得编造价格、地址、"
                "数量、赠品、排名、效果承诺或体验评价。每个镜头是一句自然、简短的口播。"
                '仅返回JSON对象，格式为{"shots":["..."]}，不得输出Markdown。'
            )
            temperature = 0.65
        user_prompt = (
            f"必须生成恰好{request.shot_count}个镜头。\n"
            f"人设：\n{_persona_context(request.persona)}\n"
            f"补充要求：{request.extra_instructions or '无'}\n"
            f"参考文本：\n{request.reference_text}"
        )
        payload_body = {
            "model": request.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
        }
        if self.json_mode:
            payload_body["response_format"] = {"type": "json_object"}
        response = await _post_json(
            f"{self.base_url}/chat/completions",
            provider=self.label,
            timeout=60,
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=payload_body,
        )
        payload = _raise_for_provider(response, self.label)
        try:
            content = payload["choices"][0]["message"]["content"]
            content = re.sub(r"^```(?:json)?|```$", "", content.strip(), flags=re.IGNORECASE).strip()
            shots = json.loads(content)["shots"]
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise ProviderError("invalid_response", f"{self.label}返回的文案不是合法 JSON") from exc
        if not isinstance(shots, list) or len(shots) != request.shot_count:
            raise ProviderError(
                "shot_count_mismatch", f"{self.label}返回镜头数不正确，应为 {request.shot_count}"
            )
        normalized = [str(text).strip() for text in shots]
        if any(not text for text in normalized):
            raise ProviderError("empty_shot", f"{self.label}返回了空镜头文案")
        return normalized


class DeepSeekClient(BailianClient):
    """DeepSeek 官方 API（OpenAI 兼容），仅用于文案转写。"""
    def __init__(self, base_url: str, api_key: str):
        super().__init__(base_url, api_key, label="DeepSeek", json_mode=False)


class MiniMaxClient:
    def __init__(self, base_url: str, api_key: str, ffprobe: str = "ffprobe"):
        if not api_key:
            raise ProviderError("missing_api_key", "请先配置 MiniMax API Key", 400)
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.ffprobe = ffprobe
        self.headers = {"Authorization": f"Bearer {api_key}"}
        self.provider_label = "MiniMax 中国站" if "minimaxi.com" in self.base_url else "MiniMax 国际站"

    async def list_voices(self) -> list[dict[str, Any]]:
        response = await _post_json(
            f"{self.base_url}/get_voice",
            provider=self.provider_label,
            timeout=30,
            headers={**self.headers, "Content-Type": "application/json"},
            json={"voice_type": "all"},
        )
        payload = _raise_for_provider(response, self.provider_label)
        voices: list[dict[str, Any]] = []
        for key in ("system_voice", "voice_cloning", "voice_generation"):
            for voice in payload.get(key, []) or []:
                voices.append({"type": key, **voice})
        return voices

    async def tts(self, request: TtsRequest, output_path: Path) -> Path:
        response = await _post_json(
            f"{self.base_url}/t2a_v2",
            provider=self.provider_label,
            timeout=120,
                headers={**self.headers, "Content-Type": "application/json"},
                json={
                    "model": request.model,
                    "text": request.text,
                    "stream": False,
                    "language_boost": "auto",
                    "output_format": "hex",
                    "voice_setting": {
                        "voice_id": request.voice_id,
                        "speed": request.speed,
                        "vol": 1,
                        "pitch": 0,
                    },
                    "audio_setting": {
                        "sample_rate": 32000,
                        "bitrate": 128000,
                        "format": "mp3",
                        "channel": 1,
                    },
                },
            )
        payload = _raise_for_provider(response, self.provider_label)
        try:
            audio = binascii.unhexlify(payload["data"]["audio"])
        except (KeyError, TypeError, binascii.Error) as exc:
            raise ProviderError("invalid_audio", "MiniMax 未返回有效音频") from exc
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(audio)
        return output_path

    async def clone(self, request: VoiceCloneRequest) -> dict[str, Any]:
        source = Path(request.source_path)
        if source.suffix.casefold() not in {".wav", ".mp3", ".m4a"}:
            raise ProviderError("invalid_audio_format", "复刻音频仅支持 wav、mp3、m4a", 400)
        if not source.is_file():
            raise ProviderError("missing_audio", "复刻音频不存在", 400)
        if source.stat().st_size > 20 * 1024 * 1024:
            raise ProviderError("audio_too_large", "复刻音频不能超过 20MB", 400)
        try:
            probe = subprocess.run(
                [
                    self.ffprobe,
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    str(source),
                ],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
                creationflags=WINDOWS_NO_WINDOW,
            )
            duration = float(probe.stdout.strip()) if probe.returncode == 0 else 0
        except (OSError, subprocess.TimeoutExpired, ValueError):
            duration = 0
        if duration < 10 or duration > 300:
            raise ProviderError("invalid_audio_duration", "复刻音频时长必须在10秒至5分钟之间", 400)
        with source.open("rb") as file_handle:
            upload_response = await _post_json(
                f"{self.base_url}/files/upload",
                provider=self.provider_label,
                timeout=120,
                headers=self.headers,
                data={"purpose": "voice_clone"},
                files={"file": (source.name, file_handle, "application/octet-stream")},
            )
            upload_payload = _raise_for_provider(upload_response, self.provider_label)
            file_id = upload_payload.get("file", {}).get("file_id") or upload_payload.get("file_id")
            if not file_id:
                raise ProviderError("upload_failed", "MiniMax 上传成功但未返回 file_id")
            clone_response = await _post_json(
                f"{self.base_url}/voice_clone",
                provider=self.provider_label,
                timeout=120,
                headers={**self.headers, "Content-Type": "application/json"},
                json={
                    "file_id": int(file_id),
                    "voice_id": request.voice_id,
                    "text": request.preview_text,
                    "model": request.model,
                    "need_noise_reduction": True,
                    "need_volume_normalization": True,
                },
            )
        return _raise_for_provider(clone_response, self.provider_label)
