"""Network helpers with proxy fallback.

On Windows, httpx (trust_env=True) honors the system proxy configured in the
registry.  When a proxy tool (Clash, v2rayN, ...) is enabled but not actually
running - for example after a crash or a node switch - every outbound request
fails with httpx.ConnectError.  These helpers retry such connection-layer
failures over a direct connection, so the engine keeps working whether or not
a system proxy is configured, and on machines without any proxy at all.
"""
from __future__ import annotations

import contextlib
import logging
from collections.abc import Iterator
from typing import Any

import httpx

_RETRYABLE_EXCEPTIONS = (httpx.ConnectError, httpx.ProxyError)


logger = logging.getLogger("outocut_engine.net")


def get(url: str, **kwargs: Any) -> httpx.Response:
    return _request("get", url, **kwargs)


def post(url: str, **kwargs: Any) -> httpx.Response:
    return _request("post", url, **kwargs)


def _request(method: str, url: str, **kwargs: Any) -> httpx.Response:
    try:
        return getattr(httpx, method)(url, trust_env=True, **kwargs)
    except _RETRYABLE_EXCEPTIONS as exc:
        logger.warning("系统代理连接失败，已切换直连重试：%s", exc)
        return getattr(httpx, method)(url, trust_env=False, **kwargs)


@contextlib.contextmanager
def stream(method: str, url: str, **kwargs: Any) -> Iterator[httpx.Response]:
    """Stream a response, retrying once over a direct connection when the
    system proxy is unreachable at the connection layer."""
    try:
        with httpx.stream(method, url, trust_env=True, **kwargs) as response:
            yield response
    except _RETRYABLE_EXCEPTIONS as exc:
        logger.warning("系统代理连接失败，已切换直连重试（流式）：%s", exc)
        with httpx.stream(method, url, trust_env=False, **kwargs) as response:
            yield response


async def apost(url: str, *, timeout: float, **kwargs: Any) -> httpx.Response:
    """POST through an async client, retrying once over a direct connection."""
    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=True) as client:
            return await client.post(url, **kwargs)
    except _RETRYABLE_EXCEPTIONS as exc:
        logger.warning("系统代理连接失败，已切换直连重试：%s", exc)
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            return await client.post(url, **kwargs)
