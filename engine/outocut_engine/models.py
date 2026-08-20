from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator


class AssetIssue(StrEnum):
    NONE = "none"
    MISSING = "missing"
    UNSUPPORTED = "unsupported"
    CORRUPT = "corrupt"
    HDR = "hdr"
    TOO_SHORT = "too_short"


class AssetClip(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    category_id: str
    path: str
    name: str
    duration: float = 0
    width: int = 0
    height: int = 0
    fps: float = 0
    codec: str = ""
    rotation: int = 0
    has_audio: bool = False
    hdr: bool = False
    issue: AssetIssue = AssetIssue.NONE
    issue_message: str = ""


class AssetCategory(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    name: Annotated[str, Field(min_length=1, max_length=80)]
    directory: str
    scanned_at: str | None = None
    clip_count: int = 0
    issue_count: int = 0

    @field_validator("directory")
    @classmethod
    def normalize_directory(cls, value: str) -> str:
        return str(Path(value).expanduser().resolve())


class AssetFolderView(AssetCategory):
    usable_count: int = 0
    unsupported_file_count: int = 0
    hdr_count: int = 0
    preview_paths: list[str] = Field(default_factory=list)


class AssetRootScanRequest(BaseModel):
    root_directory: str

    @field_validator("root_directory")
    @classmethod
    def normalize_root_directory(cls, value: str) -> str:
        return str(Path(value).expanduser().resolve())


class AssetRootOverview(BaseModel):
    root_directory: str = ""
    folders: list[AssetFolderView] = Field(default_factory=list)
    folder_count: int = 0
    usable_clip_count: int = 0
    unsupported_file_count: int = 0
    insufficient_folder_count: int = 0
    scanned_at: str | None = None


class PersonaProfile(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    name: Annotated[str, Field(min_length=1, max_length=80)]
    industry: str = ""
    audience: str = ""
    selling_points: str = ""
    experience: str = ""
    tone: str = "真实、自然、口语化"
    prohibited_claims: list[str] = Field(default_factory=list)


class FontInfo(BaseModel):
    name: str
    path: str


class OutputSettings(BaseModel):
    width: Annotated[int, Field(ge=320, le=7680)] = 1080
    height: Annotated[int, Field(ge=320, le=7680)] = 1920
    fps: Annotated[int, Field(ge=15, le=120)] = 30
    codec: Literal["libx264", "h264_nvenc"] = "libx264"
    quality: Annotated[int, Field(ge=16, le=32)] = 23


class SubtitleSettings(BaseModel):
    enabled: bool = True
    font_name: str = "Microsoft YaHei"
    font_path: str | None = None
    font_size: Annotated[int, Field(ge=18, le=120)] = 52
    primary_color: str = "#FFFFFF"
    outline_color: str = "#111111"
    outline_width: Annotated[int, Field(ge=0, le=20)] = 3
    margin_v: int = 180
    animation: Literal["none", "fade"] = "fade"


class TitleSettings(BaseModel):
    enabled: bool = True
    start_time: Annotated[float, Field(ge=0, le=3600)] = 0
    end_time: Annotated[float, Field(ge=0, le=3600)] = 0
    font_name: str = "Microsoft YaHei"
    font_path: str | None = None
    font_size: Annotated[int, Field(ge=18, le=160)] = 83
    primary_color: str = "#FFFFFF"
    centered: bool = True
    outline_width: Annotated[int, Field(ge=0, le=20)] = 1
    outline_color: str = "#111111"
    shadow_color: str = "#7A7070"
    shadow_x: Annotated[int, Field(ge=-30, le=30)] = 2
    shadow_y: Annotated[int, Field(ge=-30, le=30)] = 2


class TemplateShot(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    original_text: str = ""
    rewritten_text: str = ""
    duration: Annotated[float, Field(ge=0, le=60)] = 0
    category_id: str = ""
    muted: bool = False


class MixTemplate(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    name: Annotated[str, Field(min_length=1, max_length=100)]
    shot_count: Annotated[int, Field(ge=1, le=100)] = 8
    category_ids: list[str] = Field(default_factory=list)
    default_shot_duration: Annotated[float, Field(ge=0.5, le=60)] = 3.0
    title: str = ""
    subtitle: str = ""
    background_music: str | None = None
    background_music_enabled: bool = True
    background_music_directory: str = ""
    background_music_mode: Literal["random", "fixed"] = "random"
    background_music_volume: Annotated[float, Field(ge=0, le=1)] = 0.18
    voice_enabled: bool = True
    voice_id: str | None = None
    voice_name: str = ""
    voice_speed: Annotated[float, Field(ge=0.5, le=2)] = 1.0
    voice_volume: Annotated[float, Field(ge=0, le=2)] = 1.0
    reference_text: str = ""
    copy_style: str = "专业介绍"
    shots: list[TemplateShot] = Field(default_factory=list)
    output: OutputSettings = Field(default_factory=OutputSettings)
    captions: SubtitleSettings = Field(default_factory=SubtitleSettings)
    title_settings: TitleSettings = Field(default_factory=TitleSettings)


class ShotInput(BaseModel):
    text: str = ""
    clip_path: str | None = None
    voice_path: str | None = None
    category_id: str | None = None
    muted: bool = False


class AssetGroupSnapshot(BaseModel):
    category_id: str
    name: str
    asset_paths: list[str] = Field(default_factory=list)
    asset_durations: dict[str, float] = Field(default_factory=dict)


class JobCreate(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=120)] = "混剪任务"
    template: MixTemplate
    asset_paths: list[str]
    asset_groups: list[AssetGroupSnapshot] = Field(default_factory=list)
    shots: list[ShotInput]
    output_dir: str
    cache_dir: str = ""
    seed: int = 1
    count: Annotated[int, Field(ge=1, le=100)] = 1

    @model_validator(mode="after")
    def validate_inputs(self) -> JobCreate:
        if not self.asset_paths and not any(group.asset_paths for group in self.asset_groups):
            raise ValueError("至少需要一个可用视频素材")
        if not self.shots:
            raise ValueError("至少需要一个镜头")
        groups = {group.category_id: group for group in self.asset_groups}
        for index, shot in enumerate(self.shots):
            if not shot.category_id or shot.category_id not in groups:
                continue
            group = groups[shot.category_id]
            if group.asset_durations and not any(
                duration + 0.001 >= 3.0 for duration in group.asset_durations.values()
            ):
                raise ValueError(
                    f"镜头 {index + 1} 的素材文件夹“{group.name}”中没有时长达到3秒的可用视频"
                )
        return self


class JobState(StrEnum):
    DRAFT = "draft"
    QUEUED = "queued"
    PREPARING_AUDIO = "preparing_audio"
    RENDERING = "rendering"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobRecord(BaseModel):
    id: str
    name: str
    state: JobState
    progress: float = 0
    error: str | None = None
    output_path: str | None = None
    created_at: str
    updated_at: str
    request: JobCreate


class RewriteRequest(BaseModel):
    reference_text: Annotated[str, Field(min_length=1, max_length=20_000)]
    shot_count: Annotated[int, Field(ge=1, le=100)]
    persona: PersonaProfile | None = None
    extra_instructions: str = ""
    model: str = "qwen-plus"
    # True: keep the original wording and only split the copy into shot_count
    # segments; False: rewrite/polish into per-shot spoken copy.
    faithful: bool = False


class RewriteResponse(BaseModel):
    shots: list[str]


class TtsRequest(BaseModel):
    text: Annotated[str, Field(min_length=1, max_length=10_000)]
    voice_id: Annotated[str, Field(min_length=1, max_length=256)]
    model: str = "speech-2.8-hd"
    speed: Annotated[float, Field(ge=0.5, le=2)] = 1.0


class VoiceProfile(BaseModel):
    voice_id: str
    voice_name: str
    type: Literal["system_voice", "voice_cloning", "voice_generation"]
    description: list[str] = Field(default_factory=list)
    created_time: str = ""
    preview_path: str | None = None
    saved_locally: bool = False


class VoiceCloneRequest(BaseModel):
    source_path: str
    voice_name: Annotated[str, Field(min_length=1, max_length=100)]
    voice_id: Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{7,255}$")]
    preview_text: Annotated[str, Field(min_length=1, max_length=1000)] = (
        "欢迎使用 OutoCut，声音复刻测试成功。"
    )
    model: str = "speech-2.8-hd"

    @field_validator("voice_id")
    @classmethod
    def validate_voice_id_ending(cls, value: str) -> str:
        if value.endswith(("-", "_")):
            raise ValueError("voice_id 不能以横线或下划线结尾")
        return value


class OverlayRenderRequest(BaseModel):
    video_path: str
    overlay_path: str
    output_dir: str
    mode: Literal["watermark", "sticker", "pixel"]
    opacity: Annotated[float, Field(ge=0.05, le=1)] = 0.5
    scale: Annotated[float, Field(ge=0.01, le=3)] = 0.5
    position: Literal[
        "top_left", "top_right", "center", "bottom_left", "bottom_right"
    ] = "bottom_right"


class FullFrameOverlaySettings(BaseModel):
    path: str
    opacity: Annotated[float, Field(ge=0.05, le=1)] = 0.5


class StickerOverlaySettings(BaseModel):
    path: str
    opacity: Annotated[float, Field(ge=0.05, le=1)] = 0.8
    scale: Annotated[float, Field(ge=0.01, le=3)] = 0.5
    positions: list[
        Literal["top_left", "top_right", "center", "bottom_left", "bottom_right"]
    ] = Field(default_factory=lambda: ["bottom_right"], min_length=1)


class OverlayCompositeRequest(BaseModel):
    video_path: str
    output_dir: str
    operation_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{8,128}$")] | None = None
    watermark: FullFrameOverlaySettings | None = None
    sticker: StickerOverlaySettings | None = None
    pixel: FullFrameOverlaySettings | None = None

    @model_validator(mode="after")
    def require_layer(self) -> OverlayCompositeRequest:
        if not any((self.watermark, self.sticker, self.pixel)):
            raise ValueError("至少需要开启一种处理方式")
        return self


class ResolutionScanRequest(BaseModel):
    root_directory: str


class ResolutionConvertRequest(BaseModel):
    video_path: str
    source_root: str
    output_dir: str = ""
    replace_original: bool = False
    target_width: Annotated[int, Field(ge=720, le=2560)]
    target_height: Annotated[int, Field(ge=720, le=2560)]

    @model_validator(mode="after")
    def require_qualified_target(self) -> ResolutionConvertRequest:
        portrait = self.target_height >= self.target_width
        qualified = (
            720 <= self.target_width <= 1440 and 1280 <= self.target_height <= 2560
            if portrait
            else 1280 <= self.target_width <= 2560 and 720 <= self.target_height <= 1440
        )
        if not qualified:
            raise ValueError("目标分辨率必须位于对应横竖版的合格范围内")
        if not self.replace_original and not self.output_dir.strip():
            raise ValueError("导出到新文件夹时必须选择导出目录")
        return self


class OverlayBatchCreate(BaseModel):
    items: Annotated[list[OverlayCompositeRequest], Field(min_length=1, max_length=1000)]


class ResolutionBatchItem(ResolutionConvertRequest):
    relative_path: str = ""
    source_width: int = 0
    source_height: int = 0
    orientation: Literal["portrait", "landscape"]


class ResolutionBatchCreate(BaseModel):
    items: Annotated[list[ResolutionBatchItem], Field(min_length=1, max_length=1000)]


class SecretUpdate(BaseModel):
    bailian_api_key: str | None = None
    minimax_api_key: str | None = None
    deepseek_api_key: str | None = None
    douyin_cookie: str | None = None


class LinkResolveRequest(BaseModel):
    text: Annotated[str, Field(min_length=1, max_length=5000)]


class LinkResolveResponse(BaseModel):
    content: str
    source_url: str = ""


class AppSettings(BaseModel):
    output_directory: str = ""
    cache_directory: str = ""
    asset_root_directory: str = ""
    max_concurrent_jobs: Annotated[int, Field(ge=1, le=4)] = 1
    default_width: Annotated[int, Field(ge=320, le=7680)] = 1080
    default_height: Annotated[int, Field(ge=320, le=7680)] = 1920
    default_fps: Annotated[int, Field(ge=15, le=120)] = 30
    default_codec: Literal["libx264", "h264_nvenc"] = "libx264"
    default_quality: Annotated[int, Field(ge=16, le=32)] = 18
    bailian_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    minimax_base_url: str = "https://api.minimaxi.com/v1"
    deepseek_base_url: str = "https://api.deepseek.com"
    default_model: str = "qwen-plus"
    rewrite_provider: Literal["bailian", "deepseek"] = "bailian"
    default_voice_model: str = "speech-2.8-hd"
    asr_provider: Literal["bailian", "local"] = "bailian"
    asr_model: str = "paraformer-realtime-v2"
    rewrite_instructions: str = "更像真实本地商家口播，语言自然接地气，不要太广告腔，避免夸张承诺。"
    configured: dict[str, bool] = Field(default_factory=dict)


class ApiError(BaseModel):
    code: str
    message: str
    details: dict[str, Any] | None = None
