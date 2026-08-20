from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest


def test_get_falls_back_to_direct(monkeypatch: pytest.MonkeyPatch) -> None:
    from outocut_engine import net

    calls: list[dict[str, Any]] = []

    def fake_get(url: str, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            assert kwargs["trust_env"] is True
            raise httpx.ConnectError("proxy unreachable")
        assert kwargs["trust_env"] is False
        return httpx.Response(200, text="ok")

    monkeypatch.setattr(httpx, "get", fake_get)
    response = net.get("https://example.com/x")
    assert response.status_code == 200
    assert len(calls) == 2


def test_get_does_not_retry_http_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    from outocut_engine import net

    def fake_get(url: str, **kwargs):
        raise httpx.HTTPStatusError(
            "boom",
            request=httpx.Request("GET", url),
            response=httpx.Response(500),
        )

    monkeypatch.setattr(httpx, "get", fake_get)
    with pytest.raises(httpx.HTTPStatusError):
        net.get("https://example.com/x")


class _FakeStreamResponse:
    def __init__(self) -> None:
        self.headers = {"content-type": "application/octet-stream"}

    def raise_for_status(self) -> None:
        pass

    def iter_bytes(self, size: int):
        return iter([b"abc", b"def"])

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_stream_falls_back_to_direct(monkeypatch: pytest.MonkeyPatch) -> None:
    from outocut_engine import net

    calls: list[dict[str, Any]] = []

    def fake_stream(method: str, url: str, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise httpx.ConnectError("proxy unreachable")
        return _FakeStreamResponse()

    monkeypatch.setattr(httpx, "stream", fake_stream)
    chunks = []
    with net.stream("GET", "https://example.com/x") as response:
        for chunk in response.iter_bytes(8):
            chunks.append(chunk)
    assert b"".join(chunks) == b"abcdef"
    assert len(calls) == 2


def test_apost_falls_back_to_direct(monkeypatch: pytest.MonkeyPatch) -> None:
    from outocut_engine import net

    class FakeClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            FakeClient.instances.append(self)

        async def __aenter__(self):
            if len(FakeClient.instances) == 1:
                raise httpx.ConnectError("proxy unreachable")
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url: str, **kwargs):
            return httpx.Response(200, text="ok")

    FakeClient.instances = []
    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    async def run() -> httpx.Response:
        return await net.apost("https://example.com/x", timeout=30)

    response = asyncio.run(run())
    assert response.status_code == 200
    assert len(FakeClient.instances) == 2
    assert FakeClient.instances[0].kwargs["trust_env"] is True
    assert FakeClient.instances[1].kwargs["trust_env"] is False
