from __future__ import annotations

import asyncio
import json
import logging
import shutil
import subprocess
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import __version__, asr, fonts
from .assets import AssetScanner
from .config import Settings
from .db import Database, utc_now
from .douyin import ASR_AVAILABLE_MODELS, DouyinResolveError, resolve_douyin_voiceover
from .ecom_design import (
    EcomCancelRequest,
    EcomDesignService,
    EcomImageRequest,
    EcomSearchRequest,
    EcomTextRequest,
)
from .engine_log import setup_logging
from .jobs import JobManager
from .media_batches import MediaBatchManager
from .media_queue import MediaTaskScheduler
from .models import (
    AppSettings,
    AssetCategory,
    AssetClip,
    AssetRootOverview,
    AssetRootScanRequest,
    FontInfo,
    JobCreate,
    LinkResolveRequest,
    LinkResolveResponse,
    MixTemplate,
    OverlayBatchCreate,
    OverlayCompositeRequest,
    OverlayRenderRequest,
    PersonaProfile,
    ResolutionBatchCreate,
    ResolutionConvertRequest,
    ResolutionScanRequest,
    RewriteRequest,
    RewriteResponse,
    SecretUpdate,
    TtsRequest,
    VoiceCloneRequest,
    VoiceProfile,
)
from .overlays import OverlayProcessingCancelled, OverlayProcessingError, OverlayProcessor
from .processes import WINDOWS_NO_WINDOW
from .providers import BailianClient, DeepSeekClient, MiniMaxClient, ProviderError
from .renderer import FFmpegRenderer
from .resolution import ResolutionProcessingError, ResolutionProcessor
from .security import SecretProtectionError, protect_secret, unprotect_secret

logger = logging.getLogger("outocut_engine")


def _json_row(row) -> dict[str, Any]:
    return json.loads(row["payload"])


@lru_cache(maxsize=8)
def _ffmpeg_capabilities(ffmpeg_path: str) -> tuple[bool, str, bool]:
    """Probe once per engine process so health polling never respawns FFmpeg.

    Listing ``h264_nvenc`` only proves that the bundled FFmpeg was compiled
    with NVENC support.  Initialising a one-frame encode also verifies that
    the current machine has a usable NVIDIA driver/device.
    """
    try:
        result = subprocess.run(
            [ffmpeg_path, "-encoders"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            creationflags=WINDOWS_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False, "", False
    combined = "\n".join(part for part in (result.stderr, result.stdout) if part)
    version = next((line.strip() for line in combined.splitlines() if line.startswith("ffmpeg version")), "")
    ffmpeg_available = result.returncode == 0
    if not ffmpeg_available or "h264_nvenc" not in combined:
        return ffmpeg_available, version, False
    try:
        runtime_probe = subprocess.run(
            [
                ffmpeg_path,
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                # Current NVENC generations reject very small frames. 256x256
                # is still cheap while satisfying the encoder minimum.
                "color=c=black:s=256x256:d=0.04:r=25",
                "-frames:v",
                "1",
                "-c:v",
                "h264_nvenc",
                "-f",
                "null",
                "-",
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=10,
            check=False,
            creationflags=WINDOWS_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ffmpeg_available, version, False
    return ffmpeg_available, version, runtime_probe.returncode == 0


def create_app() -> FastAPI:
    settings = Settings.from_env()
    settings.ensure_directories()
    setup_logging(settings.data_root)
    logger.info("引擎启动：版本 %s，数据目录 %s", __version__, settings.data_root)
    db = Database(settings.data_root / "outocut.db")
    scanner = AssetScanner(settings, db)
    saved_limit_row = db.fetch_one(
        "SELECT value FROM app_settings WHERE key='max_concurrent_jobs'"
    )
    try:
        saved_media_limit = max(1, min(4, int(saved_limit_row["value"]))) if saved_limit_row else 1
    except (TypeError, ValueError):
        saved_media_limit = 1
    media_queue = MediaTaskScheduler(saved_media_limit)
    _ffmpeg_ok, _ffmpeg_version, nvenc_available = _ffmpeg_capabilities(settings.ffmpeg)
    codec_row = db.fetch_one("SELECT value FROM app_settings WHERE key='default_codec'")
    preferred_codec = str(codec_row["value"]) if codec_row else "libx264"
    renderer = FFmpegRenderer(settings, media_queue)
    overlay_processor = OverlayProcessor(
        settings, media_queue, preferred_codec, nvenc_available
    )
    resolution_processor = ResolutionProcessor(
        settings, media_queue, preferred_codec, nvenc_available
    )
    media_batches = MediaBatchManager(db, overlay_processor, resolution_processor)

    def get_setting(key: str, default: str = "") -> str:
        row = db.fetch_one("SELECT value FROM app_settings WHERE key=?", (key,))
        return str(row["value"]) if row else default

    def get_secret(key: str) -> str:
        row = db.fetch_one("SELECT protected_value FROM secrets WHERE key=?", (key,))
        return unprotect_secret(row["protected_value"]) if row else ""

    def provider_factory(name: str):
        if name == "bailian":
            return BailianClient(
                get_setting("bailian_base_url", AppSettings().bailian_base_url), get_secret("bailian_api_key")
            )
        if name == "deepseek":
            return DeepSeekClient(
                get_setting("deepseek_base_url", AppSettings().deepseek_base_url),
                get_secret("deepseek_api_key"),
            )
        if name == "minimax":
            return MiniMaxClient(
                get_setting("minimax_base_url", AppSettings().minimax_base_url),
                get_secret("minimax_api_key"),
                settings.ffprobe,
            )
        raise KeyError(name)

    def ecom_design_service() -> EcomDesignService:
        defaults = AppSettings()
        return EcomDesignService(
            text_mode=get_setting("ecom_text_api_mode", defaults.ecom_text_api_mode),  # type: ignore[arg-type]
            text_base_url=get_setting("ecom_text_base_url", defaults.ecom_text_base_url),
            text_model=get_setting("ecom_text_model", defaults.ecom_text_model),
            text_api_key=get_secret("ecom_text_api_key"),
            text_timeout_seconds=int(
                get_setting("ecom_text_timeout_seconds", str(defaults.ecom_text_timeout_seconds))
            ),
            image_mode=get_setting("ecom_image_api_mode", defaults.ecom_image_api_mode),  # type: ignore[arg-type]
            image_base_url=get_setting("ecom_image_base_url", defaults.ecom_image_base_url),
            image_model=get_setting("ecom_image_model", defaults.ecom_image_model),
            image_api_key=get_secret("ecom_image_api_key"),
            search_enabled=get_setting("ecom_search_enabled", "False").casefold() == "true",
            tavily_api_key=get_secret("ecom_tavily_api_key"),
        )

    ecom_active_requests: dict[str, set[asyncio.Task[Any]]] = {}

    async def tracked_ecom_request(workflow_id: str, operation: Any) -> Any:
        task = asyncio.current_task()
        if not workflow_id or task is None:
            return await operation
        ecom_active_requests.setdefault(workflow_id, set()).add(task)
        try:
            return await operation
        finally:
            active = ecom_active_requests.get(workflow_id)
            if active is not None:
                active.discard(task)
                if not active:
                    ecom_active_requests.pop(workflow_id, None)

    def saved_voice_profiles() -> dict[str, VoiceProfile]:
        profiles: dict[str, VoiceProfile] = {}
        for row in db.fetch_all("SELECT payload FROM voice_profiles ORDER BY updated_at DESC"):
            profile = VoiceProfile.model_validate_json(row["payload"])
            profiles[profile.voice_id] = profile
        return profiles

    def save_voice_profile(profile: VoiceProfile) -> None:
        now = utc_now()
        db.execute(
            """INSERT INTO voice_profiles(voice_id, display_name, payload, created_at, updated_at)
               VALUES(?, ?, ?, ?, ?)
               ON CONFLICT(voice_id) DO UPDATE SET display_name=excluded.display_name,
               payload=excluded.payload, updated_at=excluded.updated_at""",
            (profile.voice_id, profile.voice_name, profile.model_dump_json(), now, now),
        )

    manager = JobManager(db, renderer, provider_factory)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            yield
        finally:
            media_batches.close()

    app = FastAPI(title="OutoCut Engine", version=__version__, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173", "null"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )

    async def authenticate(authorization: Annotated[str | None, Header()] = None) -> None:
        if authorization != f"Bearer {settings.session_token}":
            raise HTTPException(status_code=401, detail="无效的本地会话令牌")

    secured = [Depends(authenticate)]

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.error("接口 %s 处理失败：%s", request.url.path, exc, exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "code": "internal_error",
                "message": "服务器内部错误，详情请查看引擎日志（设置 → 日志）",
            },
        )

    @app.exception_handler(ProviderError)
    async def provider_error_handler(request: Request, exc: ProviderError) -> JSONResponse:
        logger.error("供应商接口调用失败（%s）：%s", request.url.path, exc, exc_info=True)
        return JSONResponse(status_code=exc.status_code, content={"code": exc.code, "message": str(exc)})

    @app.exception_handler(SecretProtectionError)
    async def secret_error_handler(request: Request, exc: SecretProtectionError) -> JSONResponse:
        logger.error("密钥存储处理失败（%s）：%s", request.url.path, exc, exc_info=True)
        return JSONResponse(status_code=500, content={"code": "secret_store_error", "message": str(exc)})

    @app.get("/health", dependencies=secured)
    async def health() -> dict[str, Any]:
        ffmpeg_available, ffmpeg_version, nvenc_available = _ffmpeg_capabilities(settings.ffmpeg)
        return {
            "status": "ok",
            "version": __version__,
            "data_root": str(settings.data_root),
            "ffmpeg": ffmpeg_available,
            "ffmpeg_version": ffmpeg_version,
            "nvenc": nvenc_available,
            "disk_free_bytes": shutil.disk_usage(settings.data_root).free,
        }

    @app.get("/fonts/list", dependencies=secured, response_model=list[FontInfo])
    async def list_fonts(directory: str | None = Query(default=None)) -> list[FontInfo]:
        """List font family names from a local directory or the system fonts."""
        if directory and directory.strip():
            entries = await asyncio.to_thread(fonts.scan_font_directory, directory.strip())
        else:
            entries = await asyncio.to_thread(fonts.system_font_entries)
        return [FontInfo(**entry) for entry in entries]

    @app.post("/links/douyin", dependencies=secured)
    async def resolve_douyin_link(payload: LinkResolveRequest):
        text = payload.text.strip()
        if not text:
            return JSONResponse(
                status_code=422,
                content={
                    "code": "empty",
                    "message": "\u8bf7\u7c98\u8d34\u6296\u97f3\u5206\u4eab\u94fe\u63a5\u6216\u53e3\u4ee4",
                },
            )
        try:
            content, source_url = await asyncio.to_thread(
                resolve_douyin_voiceover,
                text,
                get_secret("douyin_cookie"),
                get_secret("bailian_api_key"),
                settings.ffmpeg,
                settings.data_root / "temp",
                get_setting("asr_provider", "bailian"),
                get_setting("asr_model", "paraformer-realtime-v2"),
                asr.local_model_dir(settings.data_root),
            )
        except DouyinResolveError as exc:
            logger.warning("抖音链接解析失败：%s", exc)
            return JSONResponse(status_code=422, content={"code": exc.code, "message": str(exc)})
        return LinkResolveResponse(content=content, source_url=source_url)

    @app.get("/asr/model", dependencies=secured)
    async def asr_model_info() -> dict[str, Any]:
        model_dir = asr.local_model_dir(settings.data_root)
        return {
            "provider": get_setting("asr_provider", "bailian"),
            "asr_model": get_setting("asr_model", "paraformer-realtime-v2"),
            "asr_models": list(ASR_AVAILABLE_MODELS),
            "model_dir": str(model_dir),
            **asr.local_model_status(model_dir),
        }

    @app.post("/asr/model/download", dependencies=secured)
    async def asr_model_download() -> dict[str, Any]:
        model_dir = asr.local_model_dir(settings.data_root)
        try:
            await asyncio.to_thread(asr.download_local_model, model_dir)
        except DouyinResolveError as exc:
            logger.warning("本地识别模型下载失败：%s", exc)
            return JSONResponse(status_code=422, content={"code": exc.code, "message": str(exc)})
        return {
            "provider": get_setting("asr_provider", "bailian"),
            "model_dir": str(model_dir),
            **asr.local_model_status(model_dir),
        }

    @app.get("/settings", dependencies=secured, response_model=AppSettings)
    async def read_settings() -> AppSettings:
        defaults = AppSettings()
        values = {
            field: get_setting(field, str(getattr(defaults, field)))
            for field in (
                "output_directory",
                "cache_directory",
                "asset_root_directory",
                "bailian_base_url",
                "ecom_text_api_mode",
                "ecom_text_base_url",
                "ecom_text_model",
                "ecom_image_api_mode",
                "ecom_image_base_url",
                "ecom_image_model",
                "ecom_search_enabled",
                "minimax_base_url",
                "deepseek_base_url",
                "default_model",
                "rewrite_provider",
                "default_voice_model",
                "default_codec",
                "rewrite_instructions",
                "asr_provider",
                "asr_model",
            )
        }
        for field in (
            "max_concurrent_jobs",
            "default_width",
            "default_height",
            "default_fps",
            "default_quality",
            "ecom_text_timeout_seconds",
        ):
            values[field] = int(get_setting(field, str(getattr(defaults, field))))
        values["cache_directory"] = values["cache_directory"] or str(settings.data_root / "cache")
        values["output_directory"] = values["output_directory"] or str(settings.data_root / "exports")
        values["configured"] = {
            "bailian": db.fetch_one("SELECT 1 FROM secrets WHERE key='bailian_api_key'") is not None,
            "ecom_text": db.fetch_one("SELECT 1 FROM secrets WHERE key='ecom_text_api_key'") is not None,
            "ecom_image": db.fetch_one("SELECT 1 FROM secrets WHERE key='ecom_image_api_key'") is not None,
            "ecom_tavily": db.fetch_one("SELECT 1 FROM secrets WHERE key='ecom_tavily_api_key'") is not None,
            "minimax": db.fetch_one("SELECT 1 FROM secrets WHERE key='minimax_api_key'") is not None,
            "deepseek": db.fetch_one("SELECT 1 FROM secrets WHERE key='deepseek_api_key'") is not None,
            "douyin": db.fetch_one("SELECT 1 FROM secrets WHERE key='douyin_cookie'") is not None,
        }
        return AppSettings.model_validate(values)

    @app.put("/settings", dependencies=secured, response_model=AppSettings)
    async def update_settings(payload: AppSettings) -> AppSettings:
        for key, value in payload.model_dump(exclude={"configured"}).items():
            db.execute(
                """INSERT INTO app_settings(key, value) VALUES(?, ?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                (key, str(value)),
            )
        media_queue.set_limit(payload.max_concurrent_jobs)
        overlay_processor.preferred_codec = payload.default_codec
        resolution_processor.preferred_codec = payload.default_codec
        return await read_settings()

    @app.get("/media-queue", dependencies=secured)
    async def media_queue_status() -> dict[str, object]:
        return media_queue.status()

    @app.post("/settings/secrets", dependencies=secured)
    async def update_secrets(payload: SecretUpdate) -> dict[str, bool]:
        for key, value in payload.model_dump(exclude_none=True).items():
            if not value:
                db.execute("DELETE FROM secrets WHERE key=?", (key,))
                continue
            db.execute(
                """INSERT INTO secrets(key, protected_value, updated_at) VALUES(?, ?, ?)
                   ON CONFLICT(key) DO UPDATE SET protected_value=excluded.protected_value,
                   updated_at=excluded.updated_at""",
                (key, protect_secret(value), utc_now()),
            )
        return (await read_settings()).configured

    @app.get("/ecom-design/configuration", dependencies=secured)
    async def ecom_design_configuration() -> dict[str, Any]:
        return ecom_design_service().configuration()

    @app.post("/ecom-design/text", dependencies=secured)
    async def ecom_design_text(payload: EcomTextRequest) -> dict[str, str]:
        text = await tracked_ecom_request(payload.workflow_id, ecom_design_service().text(payload))
        return {"text": text}

    @app.post("/ecom-design/image", dependencies=secured)
    async def ecom_design_image(payload: EcomImageRequest) -> dict[str, str]:
        image_url = await tracked_ecom_request(payload.workflow_id, ecom_design_service().image(payload))
        return {"image_url": image_url}

    @app.post("/ecom-design/search", dependencies=secured)
    async def ecom_design_search(payload: EcomSearchRequest) -> dict[str, Any]:
        return await tracked_ecom_request(payload.workflow_id, ecom_design_service().search(payload))

    @app.post("/ecom-design/cancel", dependencies=secured)
    async def ecom_design_cancel(payload: EcomCancelRequest) -> dict[str, int]:
        tasks = list(ecom_active_requests.pop(payload.workflow_id, set()))
        current = asyncio.current_task()
        cancelled = 0
        for task in tasks:
            if task is current or task.done():
                continue
            task.cancel()
            cancelled += 1
        logger.info("中止生成套图流程：workflow_id=%s requests=%d", payload.workflow_id, cancelled)
        return {"cancelled": cancelled}

    @app.get("/assets/root", dependencies=secured, response_model=AssetRootOverview)
    async def asset_root_overview() -> AssetRootOverview:
        root_value = get_setting("asset_root_directory")
        return scanner.root_overview(Path(root_value)) if root_value else AssetRootOverview()

    @app.post("/assets/root/scan", dependencies=secured, response_model=AssetRootOverview)
    async def scan_asset_root(payload: AssetRootScanRequest) -> AssetRootOverview:
        root = Path(payload.root_directory)
        if not root.is_dir():
            raise HTTPException(status_code=400, detail="素材根目录不存在")
        db.execute(
            """INSERT INTO app_settings(key, value) VALUES('asset_root_directory', ?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
            (str(root.resolve()),),
        )
        try:
            return scanner.scan_root(root)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/assets/categories", dependencies=secured, response_model=list[AssetCategory])
    async def list_categories() -> list[AssetCategory]:
        return [
            AssetCategory.model_validate(dict(row))
            for row in db.fetch_all("SELECT * FROM asset_categories ORDER BY name")
        ]

    @app.post("/assets/categories", dependencies=secured, response_model=AssetCategory)
    async def create_category(category: AssetCategory) -> AssetCategory:
        if not Path(category.directory).is_dir():
            raise HTTPException(status_code=400, detail="素材目录不存在")
        try:
            db.execute(
                """INSERT INTO asset_categories(id, name, directory, clip_count, issue_count)
                   VALUES(?, ?, ?, 0, 0)""",
                (category.id, category.name, category.directory),
            )
        except Exception as exc:
            raise HTTPException(status_code=409, detail="素材分类名称或目录已存在") from exc
        return category

    @app.delete("/assets/categories/{category_id}", dependencies=secured)
    async def delete_category(category_id: str) -> dict[str, bool]:
        db.execute("DELETE FROM asset_categories WHERE id=?", (category_id,))
        return {"deleted": True}

    @app.post("/assets/scan", dependencies=secured, response_model=list[AssetClip])
    async def scan_assets(category_id: str) -> list[AssetClip]:
        row = db.fetch_one("SELECT * FROM asset_categories WHERE id=?", (category_id,))
        if not row:
            raise HTTPException(status_code=404, detail="素材分类不存在")
        try:
            return scanner.scan(AssetCategory.model_validate(dict(row)))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/assets", dependencies=secured, response_model=list[AssetClip])
    async def list_assets(category_id: str | None = Query(default=None)) -> list[AssetClip]:
        return scanner.list_clips(category_id)

    @app.get("/personas", dependencies=secured, response_model=list[PersonaProfile])
    async def list_personas() -> list[PersonaProfile]:
        return [
            PersonaProfile.model_validate(_json_row(row))
            for row in db.fetch_all("SELECT payload FROM personas ORDER BY updated_at DESC")
        ]

    @app.post("/personas", dependencies=secured, response_model=PersonaProfile)
    async def save_persona(payload: PersonaProfile) -> PersonaProfile:
        db.upsert_json("personas", payload.id, payload.name, payload.model_dump(mode="json"))
        return payload

    @app.delete("/personas/{record_id}", dependencies=secured)
    async def delete_persona(record_id: str) -> dict[str, bool]:
        db.execute("DELETE FROM personas WHERE id=?", (record_id,))
        return {"deleted": True}

    @app.get("/templates", dependencies=secured, response_model=list[MixTemplate])
    async def list_templates() -> list[MixTemplate]:
        return [
            MixTemplate.model_validate(_json_row(row))
            for row in db.fetch_all("SELECT payload FROM mix_templates ORDER BY updated_at DESC")
        ]

    @app.post("/templates", dependencies=secured, response_model=MixTemplate)
    async def save_template(payload: MixTemplate) -> MixTemplate:
        db.upsert_json("mix_templates", payload.id, payload.name, payload.model_dump(mode="json"))
        return payload

    @app.delete("/templates/{record_id}", dependencies=secured)
    async def delete_template(record_id: str) -> dict[str, bool]:
        db.execute("DELETE FROM mix_templates WHERE id=?", (record_id,))
        return {"deleted": True}

    @app.post("/ai/rewrite", dependencies=secured, response_model=RewriteResponse)
    async def rewrite(payload: RewriteRequest) -> RewriteResponse:
        provider = get_setting("rewrite_provider", "bailian")
        client: BailianClient = provider_factory(provider)
        return RewriteResponse(shots=await client.rewrite(payload))

    @app.get("/voices", dependencies=secured, response_model=list[VoiceProfile])
    async def list_voices() -> list[VoiceProfile]:
        client: MiniMaxClient = provider_factory("minimax")
        remote_voices = await client.list_voices()
        saved = saved_voice_profiles()
        profiles: list[VoiceProfile] = []
        remote_ids: set[str] = set()
        for item in remote_voices:
            voice_id = str(item.get("voice_id") or "").strip()
            if not voice_id:
                continue
            remote_ids.add(voice_id)
            local = saved.get(voice_id)
            raw_description = item.get("description") or []
            description = (
                [str(value) for value in raw_description]
                if isinstance(raw_description, list)
                else [str(raw_description)]
            )
            profiles.append(
                VoiceProfile(
                    voice_id=voice_id,
                    voice_name=(local.voice_name if local else str(item.get("voice_name") or voice_id)),
                    type=item.get("type") or "system_voice",
                    description=description,
                    created_time=str(item.get("created_time") or (local.created_time if local else "")),
                    preview_path=(
                        local.preview_path
                        if local and local.preview_path and Path(local.preview_path).is_file()
                        else None
                    ),
                    saved_locally=local is not None,
                )
            )
        profiles.extend(profile for voice_id, profile in saved.items() if voice_id not in remote_ids)
        order = {"voice_cloning": 0, "voice_generation": 1, "system_voice": 2}
        return sorted(profiles, key=lambda item: (order[item.type], item.voice_name.casefold()))

    @app.post("/voices/clone", dependencies=secured)
    async def clone_voice(payload: VoiceCloneRequest):
        client: MiniMaxClient = provider_factory("minimax")
        await client.clone(payload)
        preview_path = settings.data_root / "voices" / "previews" / f"{payload.voice_id}.mp3"
        preview_error = ""
        try:
            await client.tts(
                TtsRequest(
                    text=payload.preview_text,
                    voice_id=payload.voice_id,
                    model=payload.model,
                ),
                preview_path,
            )
        except ProviderError as exc:
            preview_error = str(exc)
        profile = VoiceProfile(
            voice_id=payload.voice_id,
            voice_name=payload.voice_name,
            type="voice_cloning",
            description=["OutoCut 声音复刻"],
            created_time=utc_now(),
            preview_path=str(preview_path) if preview_path.is_file() else None,
            saved_locally=True,
        )
        save_voice_profile(profile)
        return {"voice": profile, "preview_error": preview_error}

    @app.post("/tts", dependencies=secured)
    async def create_tts(payload: TtsRequest):
        client: MiniMaxClient = provider_factory("minimax")
        output = settings.data_root / "voices" / f"tts-{utc_now().replace(':', '-')}.mp3"
        await client.tts(payload, output)
        return {"path": str(output)}

    @app.post("/overlays/render", dependencies=secured)
    async def render_overlay(payload: OverlayRenderRequest):
        try:
            return await asyncio.to_thread(overlay_processor.render, payload)
        except OverlayProcessingError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/resolutions/scan", dependencies=secured)
    async def scan_resolutions(payload: ResolutionScanRequest):
        try:
            return await asyncio.to_thread(resolution_processor.scan, payload.root_directory)
        except ResolutionProcessingError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/resolutions/convert", dependencies=secured)
    async def convert_resolution(payload: ResolutionConvertRequest):
        try:
            return await asyncio.to_thread(resolution_processor.convert, payload)
        except ResolutionProcessingError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/overlays/composite", dependencies=secured)
    async def render_composite_overlay(payload: OverlayCompositeRequest):
        try:
            return await asyncio.to_thread(overlay_processor.render_composite, payload)
        except OverlayProcessingCancelled as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except OverlayProcessingError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/overlays/composite/{operation_id}/cancel", dependencies=secured)
    async def cancel_composite_overlay(operation_id: str):
        return {"cancelled": overlay_processor.cancel(operation_id)}

    @app.post("/media-batches/overlays", dependencies=secured)
    async def create_overlay_batch(payload: OverlayBatchCreate):
        return media_batches.create(
            "overlay", [item.model_dump(mode="json") for item in payload.items]
        )

    @app.post("/media-batches/resolutions", dependencies=secured)
    async def create_resolution_batch(payload: ResolutionBatchCreate):
        return media_batches.create(
            "resolution", [item.model_dump(mode="json") for item in payload.items]
        )

    @app.get("/media-batches/latest/{kind}", dependencies=secured)
    async def latest_media_batch(kind: str):
        if kind not in {"overlay", "resolution"}:
            raise HTTPException(status_code=404, detail="媒体批次类型不存在")
        return media_batches.latest(kind)  # type: ignore[arg-type]

    @app.get("/media-batches/{batch_id}", dependencies=secured)
    async def get_media_batch(batch_id: str):
        try:
            return media_batches.get(batch_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="媒体批次不存在") from exc

    @app.post("/media-batches/{batch_id}/pause", dependencies=secured)
    async def pause_media_batch(batch_id: str):
        try:
            return media_batches.pause(batch_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="媒体批次不存在") from exc

    @app.post("/media-batches/{batch_id}/resume", dependencies=secured)
    async def resume_media_batch(batch_id: str):
        try:
            return media_batches.resume(batch_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="媒体批次不存在") from exc

    @app.delete("/media-batches/{batch_id}", dependencies=secured)
    async def delete_media_batch(batch_id: str):
        try:
            media_batches.delete(batch_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="媒体批次不存在") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"deleted": True}

    @app.delete("/media-batches/{batch_id}/tasks/{task_id}", dependencies=secured)
    async def delete_media_batch_task(batch_id: str, task_id: str):
        try:
            media_batches.delete_task(batch_id, task_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="媒体子任务不存在") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"deleted": True}

    @app.get("/jobs", dependencies=secured)
    async def list_jobs():
        return manager.list()

    @app.post("/jobs", dependencies=secured)
    async def create_job(payload: JobCreate):
        if payload.template.voice_enabled:
            missing_copy = [
                str(index + 1)
                for index, shot in enumerate(payload.shots)
                if not shot.text.strip()
            ]
            if missing_copy:
                raise HTTPException(
                    status_code=422,
                    detail=f"智能配音已开启，镜头 {'、'.join(missing_copy)} 缺少配音文案",
                )
            pending_tts = [shot for shot in payload.shots if not shot.voice_path]
            if pending_tts and not get_secret("minimax_api_key").strip():
                raise HTTPException(
                    status_code=422,
                    detail="智能配音已开启，请先在系统设置中配置 MiniMax Key",
                )
            if pending_tts and not (payload.template.voice_id or "").strip():
                raise HTTPException(
                    status_code=422,
                    detail="智能配音已开启，请先选择真实 MiniMax 音色",
                )
        return manager.create(payload)

    @app.post("/jobs/start-all", dependencies=secured)
    async def start_all_jobs():
        return {"started": manager.start_all()}

    @app.get("/jobs-queue", dependencies=secured)
    async def job_queue_status():
        return manager.queue_status()

    @app.post("/jobs-queue/pause", dependencies=secured)
    async def pause_job_queue():
        return manager.pause_queue()

    @app.post("/jobs-queue/resume", dependencies=secured)
    async def resume_job_queue():
        return manager.resume_queue()

    @app.post("/jobs/{job_id}/start", dependencies=secured)
    async def start_job(job_id: str):
        try:
            return manager.start(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="任务不存在") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/jobs/{job_id}", dependencies=secured)
    async def get_job(job_id: str):
        try:
            return manager.get(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="任务不存在") from exc

    @app.post("/jobs/{job_id}/cancel", dependencies=secured)
    async def cancel_job(job_id: str):
        try:
            return manager.cancel(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="任务不存在") from exc

    @app.post("/jobs/{job_id}/retry", dependencies=secured)
    async def retry_job(job_id: str):
        try:
            return manager.retry(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="任务不存在") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.delete("/jobs/completed", dependencies=secured)
    async def clear_completed_jobs():
        return {"deleted": manager.delete_completed()}

    @app.delete("/jobs/{job_id}", dependencies=secured)
    async def delete_job(job_id: str):
        try:
            manager.delete(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="任务不存在") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"deleted": True}

    @app.get("/jobs/{job_id}/artifacts", dependencies=secured)
    async def job_artifacts(job_id: str):
        try:
            record = manager.get(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="任务不存在") from exc
        if not record.output_path:
            return []
        video = Path(record.output_path)
        output_root = video.parent.parent
        candidates = [
            video,
            output_root / "ass" / video.with_suffix(".ass").name,
            output_root / "json" / video.with_suffix(".json").name,
        ]
        return [
            {"name": path.name, "path": str(path), "size": path.stat().st_size}
            for path in candidates
            if path.is_file()
        ]

    return app
