from __future__ import annotations

import json
from typing import Any

import httpx

from outocut_engine import providers
from outocut_engine.models import RewriteRequest


def _fake_apost(captured: dict[str, Any], shots: list[str]):
    async def fake_apost(url: str, *, timeout: float, **kwargs: Any) -> httpx.Response:
        captured["url"] = url
        captured["timeout"] = timeout
        captured["json"] = kwargs["json"]
        content = json.dumps({"shots": shots}, ensure_ascii=False)
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    return fake_apost


async def test_faithful_rewrite_only_splits_copy(monkeypatch) -> None:
    captured: dict[str, Any] = {}
    shots = ["第一段原文。", "第二段原文。", "第三段原文。"]
    monkeypatch.setattr(providers.net, "apost", _fake_apost(captured, shots))

    client = providers.BailianClient(base_url="https://example.invalid", api_key="test-key")
    result = await client.rewrite(
        RewriteRequest(reference_text="第一段原文。第二段原文。第三段原文。", shot_count=3, faithful=True)
    )

    assert result == shots
    body = captured["json"]
    assert body["temperature"] == 0.2
    system_prompt = body["messages"][0]["content"]
    assert "不得改写" in system_prompt
    assert "严格保留用户提供的原文" in system_prompt


async def test_polish_rewrite_uses_warmer_temperature(monkeypatch) -> None:
    captured: dict[str, Any] = {}
    shots = ["改写后的第一句。", "改写后的第二句。"]
    monkeypatch.setattr(providers.net, "apost", _fake_apost(captured, shots))

    client = providers.BailianClient(base_url="https://example.invalid", api_key="test-key")
    result = await client.rewrite(
        RewriteRequest(reference_text="原文第一句。原文第二句。", shot_count=2, faithful=False)
    )

    assert result == shots
    assert captured["json"]["temperature"] == 0.65


async def test_rewrite_rejects_wrong_shot_count(monkeypatch) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(providers.net, "apost", _fake_apost(captured, ["只有一段。"]))

    client = providers.BailianClient(base_url="https://example.invalid", api_key="test-key")
    try:
        await client.rewrite(
            RewriteRequest(reference_text="原文第一句。原文第二句。", shot_count=3, faithful=True)
        )
    except providers.ProviderError as exc:
        assert exc.code == "shot_count_mismatch"
    else:
        raise AssertionError("expected shot_count_mismatch error")


async def test_deepseek_rewrite_uses_official_endpoint_and_no_response_format(monkeypatch) -> None:
    captured: dict[str, Any] = {}
    shots = ["第一段。", "第二段。"]
    monkeypatch.setattr(providers.net, "apost", _fake_apost(captured, shots))

    client = providers.DeepSeekClient(base_url="https://api.deepseek.com", api_key="ds-key")
    result = await client.rewrite(
        RewriteRequest(
            model="deepseek-v4-flash", reference_text="第一段。第二段。", shot_count=2, faithful=False
        )
    )

    assert result == shots
    assert captured["url"] == "https://api.deepseek.com/chat/completions"
    assert captured["json"]["model"] == "deepseek-v4-flash"
    assert "response_format" not in captured["json"]
