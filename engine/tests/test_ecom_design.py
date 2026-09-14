from __future__ import annotations

import httpx
import pytest

from outocut_engine import ecom_design
from outocut_engine.ecom_design import (
    EcomDesignService,
    EcomImageRequest,
    EcomSearchRequest,
    EcomTextRequest,
)
from outocut_engine.providers import ProviderError


def service(**overrides: object) -> EcomDesignService:
    values: dict[str, object] = {
        "text_mode": "chat-completions",
        "text_base_url": "https://text.example/v1",
        "text_model": "text-model",
        "text_api_key": "text-key",
        "text_timeout_seconds": 600,
        "image_mode": "images",
        "image_base_url": "https://image.example/v1",
        "image_model": "image-model",
        "image_api_key": "image-key",
        "search_enabled": True,
        "tavily_api_key": "tavily-key",
    }
    values.update(overrides)
    return EcomDesignService(**values)  # type: ignore[arg-type]


def response(status: int, payload: dict[str, object], url: str) -> httpx.Response:
    return httpx.Response(status, json=payload, request=httpx.Request("POST", url))


@pytest.mark.asyncio
async def test_chat_completions_text_request(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    async def fake_post(url: str, **kwargs: object) -> httpx.Response:
        captured.update(url=url, **kwargs)
        return response(200, {"choices": [{"message": {"content": "分析完成"}}]}, url)

    monkeypatch.setattr(ecom_design.net, "apost", fake_post)
    result = await service().text(EcomTextRequest(prompt="分析商品", system_prompt="系统规则"))

    assert result == "分析完成"
    assert captured["url"] == "https://text.example/v1/chat/completions"
    assert captured["timeout"] == 600
    body = captured["json"]
    assert isinstance(body, dict)
    assert body["messages"][0] == {"role": "system", "content": "系统规则"}
    assert captured["headers"]["Authorization"] == "Bearer text-key"


@pytest.mark.asyncio
async def test_text_request_can_use_shorter_stage_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    async def fake_post(url: str, **kwargs: object) -> httpx.Response:
        captured.update(url=url, **kwargs)
        return response(200, {"choices": [{"message": {"content": "分析完成"}}]}, url)

    monkeypatch.setattr(ecom_design.net, "apost", fake_post)
    await service().text(EcomTextRequest(prompt="分析竞品", timeout_seconds=300))

    assert captured["timeout"] == 300


@pytest.mark.asyncio
async def test_responses_image_result_is_returned_as_data_url(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_post(url: str, **kwargs: object) -> httpx.Response:
        assert url == "https://image.example/v1/responses"
        body = kwargs["json"]
        assert isinstance(body, dict)
        assert body["tools"][0]["action"] == "generate"
        return response(
            200,
            {"output": [{"type": "image_generation_call", "output_format": "png", "result": "YWJj"}]},
            url,
        )

    monkeypatch.setattr(ecom_design.net, "apost", fake_post)
    result = await service(image_mode="responses").image(EcomImageRequest(prompt="商品主图"))
    assert result == "data:image/png;base64,YWJj"


@pytest.mark.asyncio
async def test_images_edit_sends_template_and_product_as_ordered_image_array(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def fake_post(url: str, **kwargs: object) -> httpx.Response:
        captured.update(url=url, **kwargs)
        return response(200, {"data": [{"b64_json": "YWJj"}]}, url)

    monkeypatch.setattr(ecom_design.net, "apost", fake_post)
    result = await service().image(EcomImageRequest(
        prompt="只替换产品",
        template_image_base64="data:image/png;base64,dGVtcGxhdGU=",
        product_image_base64="data:image/jpeg;base64,cHJvZHVjdA==",
    ))

    assert result == "data:image/png;base64,YWJj"
    assert captured["url"] == "https://image.example/v1/images/edits"
    files = captured["files"]
    assert isinstance(files, list)
    assert [entry[0] for entry in files] == ["image[]", "image[]"]
    assert files[0][1][0] == "reference-1.png"
    assert files[0][1][1] == b"template"
    assert files[1][1][0] == "reference-2.jpg"
    assert files[1][1][1] == b"product"


@pytest.mark.asyncio
async def test_images_edit_sends_brand_logo_as_third_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def fake_post(url: str, **kwargs: object) -> httpx.Response:
        captured.update(url=url, **kwargs)
        return response(200, {"data": [{"b64_json": "YWJj"}]}, url)

    monkeypatch.setattr(ecom_design.net, "apost", fake_post)
    await service().image(EcomImageRequest(
        prompt="换产品并重设计品牌",
        template_image_base64="data:image/png;base64,dGVtcGxhdGU=",
        product_image_base64="data:image/jpeg;base64,cHJvZHVjdA==",
        image_base64="data:image/webp;base64,bG9nbw==",
    ))

    files = captured["files"]
    assert isinstance(files, list)
    assert [entry[0] for entry in files] == ["image[]", "image[]", "image[]"]
    assert files[0][1][1] == b"template"
    assert files[1][1][1] == b"product"
    assert files[2][1][0] == "reference-3.webp"
    assert files[2][1][1] == b"logo"


@pytest.mark.asyncio
async def test_responses_edit_includes_both_reference_images(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def fake_post(url: str, **kwargs: object) -> httpx.Response:
        captured.update(url=url, **kwargs)
        return response(
            200,
            {"output": [{"type": "image_generation_call", "result": "YWJj"}]},
            url,
        )

    monkeypatch.setattr(ecom_design.net, "apost", fake_post)
    await service(image_mode="responses").image(EcomImageRequest(
        prompt="只替换产品",
        template_image_base64="data:image/png;base64,dGVtcGxhdGU=",
        product_image_base64="data:image/png;base64,cHJvZHVjdA==",
    ))

    body = captured["json"]
    assert isinstance(body, dict)
    content = body["input"][0]["content"]
    assert [item["type"] for item in content] == ["input_text", "input_image", "input_image"]
    assert content[1]["image_url"].endswith("dGVtcGxhdGU=")
    assert content[2]["image_url"].endswith("cHJvZHVjdA==")


@pytest.mark.asyncio
async def test_search_requires_enablement() -> None:
    with pytest.raises(ProviderError, match="启用联网竞品搜索") as caught:
        await service(search_enabled=False).search(EcomSearchRequest(query="竞品"))
    assert caught.value.status_code == 400


@pytest.mark.asyncio
async def test_text_timeout_has_actionable_message(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_post(url: str, **kwargs: object) -> httpx.Response:
        request = httpx.Request("POST", url)
        raise httpx.ReadTimeout("slow provider", request=request)

    monkeypatch.setattr(ecom_design.net, "apost", fake_post)
    with pytest.raises(ProviderError, match="超过 600 秒") as caught:
        await service().text(EcomTextRequest(prompt="生成差异化策略"))
    assert caught.value.code == "read_timeout"
    assert caught.value.status_code == 504


def test_ecom_configuration_uses_encrypted_system_settings(
    client, auth_headers: dict[str, str]
) -> None:
    current = client.get("/settings", headers=auth_headers).json()
    current.update(
        {
            "ecom_text_api_mode": "claude",
            "ecom_text_base_url": "https://claude.example/v1",
            "ecom_text_model": "claude-model",
            "ecom_image_api_mode": "responses",
            "ecom_image_base_url": "https://image.example/v1",
            "ecom_image_model": "image-model",
            "ecom_search_enabled": True,
        }
    )
    assert client.put("/settings", headers=auth_headers, json=current).status_code == 200
    secrets = client.post(
        "/settings/secrets",
        headers=auth_headers,
        json={
            "ecom_text_api_key": "text-key",
            "ecom_image_api_key": "image-key",
            "ecom_tavily_api_key": "search-key",
        },
    )
    assert secrets.status_code == 200

    configured = client.get("/ecom-design/configuration", headers=auth_headers)
    assert configured.status_code == 200
    assert configured.json() == {
        "llm": {"configured": True, "apiMode": "claude", "model": "claude-model"},
        "image": {"configured": True, "apiMode": "responses", "model": "image-model"},
        "search": {"enabled": True, "configured": True},
    }


def test_cancel_unknown_ecom_workflow(client, auth_headers: dict[str, str]) -> None:
    response = client.post(
        "/ecom-design/cancel",
        headers=auth_headers,
        json={"workflow_id": "workflow-not-running"},
    )
    assert response.status_code == 200
    assert response.json() == {"cancelled": 0}
