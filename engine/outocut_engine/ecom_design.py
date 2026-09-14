from __future__ import annotations

import asyncio
import base64
import binascii
import logging
import time
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, Field

from . import net
from .providers import ProviderError

logger = logging.getLogger("outocut_engine.ecom_design")


class EcomTextRequest(BaseModel):
    workflow_id: str = Field(default="", max_length=100)
    prompt: str = Field(min_length=1, max_length=200_000)
    system_prompt: str = Field(default="", max_length=100_000)
    max_tokens: int = Field(default=4096, ge=1, le=32_768)
    temperature: float = Field(default=0.7, ge=0, le=2)
    timeout_seconds: int | None = Field(default=None, ge=60, le=1800)


class EcomImageRequest(BaseModel):
    workflow_id: str = Field(default="", max_length=100)
    prompt: str = Field(min_length=1, max_length=100_000)
    image_base64: str | None = None
    template_image_base64: str | None = None
    product_image_base64: str | None = None
    size: str = Field(default="1024x1024", max_length=40)
    quality: str = Field(default="high", max_length=40)
    output_format: Literal["png", "jpeg", "webp"] = "png"


class EcomSearchRequest(BaseModel):
    workflow_id: str = Field(default="", max_length=100)
    query: str = Field(min_length=1, max_length=2_000)
    search_depth: Literal["basic", "advanced"] = "advanced"
    max_results: int = Field(default=10, ge=1, le=20)


class EcomCancelRequest(BaseModel):
    workflow_id: str = Field(min_length=1, max_length=100)


def _decode_image(value: str, label: str) -> tuple[bytes, str, str]:
    try:
        header, encoded = value.split(",", 1) if "," in value else ("", value)
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ProviderError("invalid_image", f"{label}图片数据无效", 400) from exc
    mime = next(
        (candidate for candidate in ("image/png", "image/jpeg", "image/webp") if candidate in header),
        "image/png",
    )
    if not raw or len(raw) > 100 * 1024 * 1024:
        raise ProviderError("invalid_image", f"{label}图片为空或超过 100MB 限制", 400)
    extension = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}[mime]
    return raw, mime, extension


def _endpoint(base_url: str, suffix: str, label: str) -> str:
    value = base_url.strip().rstrip("/")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ProviderError("invalid_endpoint", f"{label} Base URL 无效", 400)
    return f"{value}{suffix}"


def _message(payload: Any, fallback: str) -> str:
    if not isinstance(payload, dict):
        return fallback
    error = payload.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or fallback)
    return str(payload.get("message") or error or fallback)


def _json(response: httpx.Response, label: str) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        snippet = response.text[:200].strip()
        raise ProviderError(
            "invalid_response",
            f"{label}返回了非 JSON 响应，请检查 Base URL：{snippet}",
            502,
        ) from exc
    if not response.is_success:
        raise ProviderError(
            "provider_error",
            f"{label}请求失败（HTTP {response.status_code}）：{_message(payload, '未知错误')}",
            response.status_code or 502,
        )
    if not isinstance(payload, dict):
        raise ProviderError("invalid_response", f"{label}响应格式无效", 502)
    return payload


async def _post(label: str, url: str, **kwargs: Any) -> httpx.Response:
    timeout = float(kwargs.pop("timeout", 180))
    started = time.monotonic()
    try:
        async with asyncio.timeout(timeout):
            response = await net.apost(url, timeout=timeout, **kwargs)
        logger.info(
            "%s响应完整接收：status=%d elapsed=%.1fs bytes=%d",
            label,
            response.status_code,
            time.monotonic() - started,
            len(response.content),
        )
        return response
    except (httpx.ReadTimeout, TimeoutError) as exc:
        logger.warning("%s等待响应超时：timeout=%ss", label, timeout)
        raise ProviderError(
            "read_timeout",
            f"{label}等待响应超过 {int(timeout)} 秒。系统未自动重试，以避免重复调用计费；"
            "请检查模型供应商状态，或调整该阶段/系统超时设置",
            504,
        ) from exc
    except httpx.HTTPError as exc:
        logger.error("%s网络请求失败：%s", label, exc)
        raise ProviderError(
            "network_error", f"{label}网络请求失败：{exc.__class__.__name__}", 502
        ) from exc


class EcomDesignService:
    def __init__(
        self,
        *,
        text_mode: Literal["chat-completions", "responses", "claude"],
        text_base_url: str,
        text_model: str,
        text_api_key: str,
        text_timeout_seconds: int,
        image_mode: Literal["images", "responses"],
        image_base_url: str,
        image_model: str,
        image_api_key: str,
        search_enabled: bool,
        tavily_api_key: str,
    ) -> None:
        self.text_mode = text_mode
        self.text_base_url = text_base_url
        self.text_model = text_model.strip()
        self.text_api_key = text_api_key
        self.text_timeout_seconds = text_timeout_seconds
        self.image_mode = image_mode
        self.image_base_url = image_base_url
        self.image_model = image_model.strip()
        self.image_api_key = image_api_key
        self.search_enabled = search_enabled
        self.tavily_api_key = tavily_api_key

    def configuration(self) -> dict[str, Any]:
        return {
            "llm": {
                "configured": bool(self.text_api_key and self.text_model and self.text_base_url),
                "apiMode": self.text_mode,
                "model": self.text_model,
            },
            "image": {
                "configured": bool(self.image_api_key and self.image_model and self.image_base_url),
                "apiMode": self.image_mode,
                "model": self.image_model,
            },
            "search": {
                "enabled": self.search_enabled,
                "configured": bool(self.tavily_api_key),
            },
        }

    async def text(self, request: EcomTextRequest) -> str:
        if not self.text_api_key:
            raise ProviderError("missing_api_key", "请先在系统设置中配置生成套图文本 LLM Key", 400)
        if not self.text_model:
            raise ProviderError("missing_model", "生成套图文本模型不能为空", 400)

        if self.text_mode == "claude":
            url = _endpoint(self.text_base_url, "/messages", "文本 LLM")
            headers = {
                "x-api-key": self.text_api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            }
            body = {
                "model": self.text_model,
                "system": request.system_prompt or None,
                "messages": [{"role": "user", "content": request.prompt}],
                "max_tokens": request.max_tokens,
                "temperature": request.temperature,
            }
        elif self.text_mode == "responses":
            url = _endpoint(self.text_base_url, "/responses", "文本 LLM")
            headers = {"Authorization": f"Bearer {self.text_api_key}", "Content-Type": "application/json"}
            body = {
                "model": self.text_model,
                "input": [
                    *(
                        [{"role": "system", "content": request.system_prompt}]
                        if request.system_prompt
                        else []
                    ),
                    {"role": "user", "content": request.prompt},
                ],
                "max_output_tokens": request.max_tokens,
            }
        else:
            url = _endpoint(self.text_base_url, "/chat/completions", "文本 LLM")
            headers = {"Authorization": f"Bearer {self.text_api_key}", "Content-Type": "application/json"}
            body = {
                "model": self.text_model,
                "messages": [
                    *(
                        [{"role": "system", "content": request.system_prompt}]
                        if request.system_prompt
                        else []
                    ),
                    {"role": "user", "content": request.prompt},
                ],
                "max_tokens": request.max_tokens,
                "temperature": request.temperature,
                "stream": False,
            }

        logger.info("生成套图文本请求：mode=%s model=%s", self.text_mode, self.text_model)
        payload = _json(
            await _post(
                "文本 LLM",
                url,
                headers=headers,
                json=body,
                timeout=min(
                    request.timeout_seconds or self.text_timeout_seconds,
                    self.text_timeout_seconds,
                ),
            ),
            "文本 LLM",
        )
        if self.text_mode == "claude":
            text = "".join(
                str(item.get("text") or "")
                for item in payload.get("content", [])
                if isinstance(item, dict)
            )
        elif self.text_mode == "responses":
            text = str(payload.get("output_text") or "")
            if not text:
                text = "".join(
                    str(content.get("text") or "")
                    for item in payload.get("output", []) if isinstance(item, dict)
                    for content in item.get("content", []) if isinstance(content, dict)
                )
        else:
            choices = payload.get("choices") or []
            text = str(choices[0].get("message", {}).get("content") or "") if choices else ""
        if not text.strip():
            raise ProviderError("invalid_response", "文本 LLM 未返回可用文本", 502)
        return text

    async def image(self, request: EcomImageRequest) -> str:
        if not self.image_api_key:
            raise ProviderError("missing_api_key", "请先在系统设置中配置生成套图图片 LLM Key", 400)
        if not self.image_model:
            raise ProviderError("missing_model", "生成套图图片模型不能为空", 400)
        headers = {"Authorization": f"Bearer {self.image_api_key}"}
        guard = "Use the following text as the complete prompt. Do not rewrite it:\n"
        reference_images = [
            image for image in (
                request.template_image_base64,
                request.product_image_base64,
                request.image_base64,
            ) if image
        ]
        if self.image_mode == "responses":
            url = _endpoint(self.image_base_url, "/responses", "图片 LLM")
            tool = {
                "type": "image_generation",
                "action": "edit" if reference_images else "generate",
                "size": request.size,
                "quality": request.quality,
                "output_format": request.output_format,
            }
            content: Any = guard + request.prompt
            if reference_images:
                content = [{"role": "user", "content": [
                    {"type": "input_text", "text": guard + request.prompt},
                    *(
                        {"type": "input_image", "image_url": image}
                        for image in reference_images
                    ),
                ]}]
            body = {
                "model": self.image_model,
                "input": content,
                "tools": [tool],
                "tool_choice": "required",
            }
            response = await _post(
                "图片 LLM",
                url,
                headers={**headers, "Content-Type": "application/json"},
                json=body,
                timeout=600,
            )
        elif reference_images:
            url = _endpoint(self.image_base_url, "/images/edits", "图片 LLM")
            decoded = [
                _decode_image(
                    value,
                    "模板" if index == 0 and request.template_image_base64 else "商品",
                )
                for index, value in enumerate(reference_images)
            ]
            files = [
                (
                    "image[]" if len(decoded) > 1 else "image",
                    (f"reference-{index + 1}.{extension}", raw, mime),
                )
                for index, (raw, mime, extension) in enumerate(decoded)
            ]
            response = await _post(
                "图片 LLM", url, headers=headers,
                files=files,
                data={
                    "prompt": request.prompt, "model": self.image_model, "n": "1",
                    "size": request.size, "quality": request.quality, "response_format": "b64_json",
                },
                timeout=600,
            )
        else:
            url = _endpoint(self.image_base_url, "/images/generations", "图片 LLM")
            body = {
                "model": self.image_model, "prompt": request.prompt, "n": 1,
                "size": request.size, "quality": request.quality, "response_format": "b64_json",
            }
            response = await _post(
                "图片 LLM",
                url,
                headers={**headers, "Content-Type": "application/json"},
                json=body,
                timeout=600,
            )

        logger.info(
            "生成套图图片请求：mode=%s model=%s edit=%s",
            self.image_mode,
            self.image_model,
            bool(reference_images),
        )
        payload = _json(response, "图片 LLM")
        if self.image_mode == "responses":
            for item in payload.get("output", []):
                if (
                    isinstance(item, dict)
                    and item.get("type") == "image_generation_call"
                    and item.get("result")
                ):
                    mime = (
                        "image/jpeg"
                        if item.get("output_format") == "jpeg"
                        else f"image/{request.output_format}"
                    )
                    return f"data:{mime};base64,{item['result']}"
        data = payload.get("data") or []
        if data and isinstance(data[0], dict):
            if data[0].get("b64_json"):
                return f"data:image/{request.output_format};base64,{data[0]['b64_json']}"
            if data[0].get("url"):
                return str(data[0]["url"])
        raise ProviderError("invalid_response", "图片 LLM 未返回图片数据", 502)

    async def search(self, request: EcomSearchRequest) -> dict[str, Any]:
        if not self.search_enabled:
            raise ProviderError("search_disabled", "请先在系统设置中启用联网竞品搜索", 400)
        if not self.tavily_api_key:
            raise ProviderError("missing_api_key", "请先在系统设置中配置 Tavily API Key", 400)
        body = {
            "api_key": self.tavily_api_key,
            "query": request.query,
            "search_depth": request.search_depth,
            "max_results": request.max_results,
            "include_answer": True,
            "include_raw_content": False,
        }
        logger.info("生成套图竞品搜索：query=%s", request.query[:120])
        return _json(
            await _post("Tavily 搜索", "https://api.tavily.com/search", json=body, timeout=90),
            "Tavily 搜索",
        )
