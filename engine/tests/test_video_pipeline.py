from __future__ import annotations

from outocut_engine.app import _ffmpeg_capabilities
from outocut_engine.video_pipeline import (
    encoder_args,
    hardware_decode_args,
    is_hdr_transfer,
    normalized_video_filter,
    sdr_output_args,
)


def test_hdr_pipeline_tonemaps_pq_and_hlg_to_bt709() -> None:
    assert is_hdr_transfer("smpte2084")
    assert is_hdr_transfer("arib-std-b67")
    filter_graph = normalized_video_filter("scale=1080:1920", True)
    assert "tonemap=tonemap=hable" in filter_graph
    assert "zscale=p=bt709:t=bt709:m=bt709:r=tv" in filter_graph
    assert filter_graph.endswith("format=yuv420p")
    assert sdr_output_args().count("bt709") == 3


def test_sdr_pipeline_does_not_apply_tonemap() -> None:
    filter_graph = normalized_video_filter("scale=1280:720", False)
    assert "tonemap" not in filter_graph
    assert filter_graph == "scale=1280:720,format=yuv420p"


def test_nvenc_pipeline_enables_hardware_decode_and_vbr_encoding() -> None:
    assert hardware_decode_args("h264_nvenc") == ["-hwaccel", "auto"]
    nvenc = encoder_args("h264_nvenc", 20, 8)
    assert "h264_nvenc" in nvenc
    assert "vbr" in nvenc
    assert "-threads" not in nvenc

    cpu = encoder_args("libx264", 20, 8)
    assert "libx264" in cpu
    assert cpu[-2:] == ["-threads", "8"]


def test_nvenc_capability_requires_a_successful_runtime_probe(monkeypatch) -> None:
    _ffmpeg_capabilities.cache_clear()

    class Result:
        def __init__(self, returncode: int, stdout: str = "", stderr: str = ""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    calls = 0

    def fake_run(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return Result(0, "ffmpeg version test\n V....D h264_nvenc")
        return Result(1, stderr="no NVIDIA device")

    monkeypatch.setattr("outocut_engine.app.subprocess.run", fake_run)
    available, _version, nvenc = _ffmpeg_capabilities("fake-ffmpeg")
    assert available is True
    assert nvenc is False
    assert calls == 2
    _ffmpeg_capabilities.cache_clear()
