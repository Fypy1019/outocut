from __future__ import annotations

from typing import Literal

VideoCodec = Literal["libx264", "h264_nvenc"]
HDR_TRANSFERS = {"smpte2084", "arib-std-b67"}


def is_hdr_transfer(transfer: str | None) -> bool:
    return (transfer or "").casefold() in HDR_TRANSFERS


def normalized_video_filter(base_filter: str, hdr: bool) -> str:
    filters: list[str] = []
    if hdr:
        filters.extend(
            [
                "zscale=t=linear:npl=100",
                "format=gbrpf32le",
                "tonemap=tonemap=hable:desat=0",
                "zscale=p=bt709:t=bt709:m=bt709:r=tv",
            ]
        )
    if base_filter:
        filters.append(base_filter)
    filters.append("format=yuv420p")
    return ",".join(filters)


def hardware_decode_args(codec: VideoCodec) -> list[str]:
    return ["-hwaccel", "auto"] if codec == "h264_nvenc" else []


def encoder_args(codec: VideoCodec, quality: int, cpu_threads: int) -> list[str]:
    if codec == "h264_nvenc":
        return [
            "-c:v",
            "h264_nvenc",
            "-preset",
            "p5",
            "-tune",
            "hq",
            "-rc",
            "vbr",
            "-cq",
            str(quality),
            "-b:v",
            "0",
        ]
    return [
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        str(quality),
        "-threads",
        str(cpu_threads),
    ]


def sdr_output_args() -> list[str]:
    return [
        "-color_primaries",
        "bt709",
        "-color_trc",
        "bt709",
        "-colorspace",
        "bt709",
        "-color_range",
        "tv",
    ]
