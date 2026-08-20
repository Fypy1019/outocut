from __future__ import annotations

from pathlib import Path

import pytest

from outocut_engine.config import Settings
from outocut_engine.models import JobCreate
from outocut_engine.renderer import FFmpegRenderer, _wrap_caption_text


def _renderer() -> FFmpegRenderer:
    return FFmpegRenderer(
        Settings(
            data_root=Path("."),
            session_token="test-token",
            ffmpeg="ffmpeg",
            ffprobe="ffprobe",
        )
    )


def _write_ass(width: int, height: int, tmp_path: Path) -> str:
    request = JobCreate.model_validate(
        {
            "name": "caption-width",
            "template": {"name": "t", "shot_count": 1, "output": {"width": width, "height": height}},
            "asset_paths": ["asset.mp4"],
            "shots": [{"text": "长文本" * 50}],
            "output_dir": ".",
        }
    )
    ass_path = tmp_path / "captions.ass"
    _renderer()._write_ass(request, [3.0], ass_path)
    return ass_path.read_text(encoding="utf-8-sig")


def test_caption_ass_limits_width_to_two_percent_margins(tmp_path: Path) -> None:
    content = _write_ass(1080, 1920, tmp_path)
    style = next(line for line in content.splitlines() if line.startswith("Style: Caption"))
    fields = style.split(",")
    assert fields[19] == "22"  # MarginL == round(width * 0.02)
    assert fields[20] == "22"  # MarginR == round(width * 0.02)
    assert fields[-1] == "0"  # WrapStyle: smart wrap


def test_caption_ass_margins_scale_with_width(tmp_path: Path) -> None:
    content = _write_ass(720, 1280, tmp_path)
    style = next(line for line in content.splitlines() if line.startswith("Style: Caption"))
    fields = style.split(",")
    assert fields[19] == "14"
    assert fields[20] == "14"


def test_caption_ass_keeps_minimum_margin_for_narrow_widths(tmp_path: Path) -> None:
    content = _write_ass(480, 854, tmp_path)
    style = next(line for line in content.splitlines() if line.startswith("Style: Caption"))
    fields = style.split(",")
    # round(width * 0.02) == 10, below the 12px floor, so the floor applies
    assert fields[19] == "12"


def test_caption_ass_format_has_wrapstyle(tmp_path: Path) -> None:
    content = _write_ass(1080, 1920, tmp_path)
    assert "Encoding, WrapStyle" in content
    title = next(line for line in content.splitlines() if line.startswith("Style: Title"))
    assert title.endswith(",1,0")


def _caption_dialogue_text(width: int, height: int, tmp_path: Path) -> str:
    content = _write_ass(width, height, tmp_path)
    dialogue = next(line for line in content.splitlines() if line.startswith("Dialogue: 2"))
    text = dialogue.split(",", 9)[9]
    # strip the zero-width fade override prefix
    prefix = r"{\fad(180,180)}"
    return text[len(prefix) :] if text.startswith(prefix) else text


class _UniformMeasure:
    """Fake measurer that charges a fixed pixel width per glyph."""

    def __init__(self, per_char: float) -> None:
        self.per_char = per_char

    def text_width(self, text: str, font_size: int, spacing: float = 1.0) -> float:
        return self.per_char * len(text)


def test_caption_ass_hard_wraps_long_cjk_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Microsoft YaHei metrics: 52px glyphs advance only ~40.4px, so the 1036px
    # caption block fits 25 glyphs per line instead of the naive 19.
    monkeypatch.setattr(
        "outocut_engine.renderer.font_measure",
        lambda family, path=None: _UniformMeasure(40.4),
    )
    text = _caption_dialogue_text(1080, 1920, tmp_path)
    lines = text.split("\\N")
    assert len(lines) > 1
    assert all(len(line) == 25 for line in lines[:-1])
    assert 0 < len(lines[-1]) <= 25


def test_caption_ass_wrap_falls_back_when_font_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Without a resolvable font the conservative font_size + spacing applies:
    # 1036 / 53 = 19 glyphs per line.
    monkeypatch.setattr("outocut_engine.renderer.font_measure", lambda family, path=None: None)
    text = _caption_dialogue_text(1080, 1920, tmp_path)
    lines = text.split("\\N")
    assert all(len(line) == 19 for line in lines[:-1])
    assert 0 < len(lines[-1]) <= 19


def test_wrap_caption_text_uses_measured_width() -> None:
    def measure(piece: str) -> float:
        return 40.4 * len(piece)
    wrapped = _wrap_caption_text("\u957f\u6587\u672c" * 30, 1036, 52, measure=measure)
    lines = wrapped.split("\n")
    assert all(len(line) == 25 for line in lines[:-1])
    assert 0 < len(lines[-1]) <= 25


def test_wrap_caption_text_falls_back_without_metrics() -> None:
    wrapped = _wrap_caption_text("\u957f\u6587\u672c" * 30, 1036, 52)
    lines = wrapped.split("\n")
    assert all(len(line) == 19 for line in lines[:-1])


def test_wrap_caption_text_breaks_by_measured_width() -> None:
    # "AB\u5b57" repeats: A/B advance 20px, CJK glyph 40px -> each triple 80px.
    # The 100px block fits "AB\u5b57A" (100px) but not one more glyph, proving
    # the wrap follows real widths instead of character counts.
    widths = {"A": 20.0, "B": 20.0, "\u5b57": 40.0}

    def measure(piece: str) -> float:
        return sum(widths.get(ch, 40.0) for ch in piece)
    wrapped = _wrap_caption_text("AB\u5b57" * 12, 100, 52, measure=measure)
    lines = wrapped.split("\n")
    assert lines[0] == "AB\u5b57A"
    assert "".join(lines) == "AB\u5b57" * 12
    assert all(len(line) in (1, 3, 4) for line in lines)


def test_caption_ass_wrap_keeps_existing_newlines(tmp_path: Path) -> None:
    request = JobCreate.model_validate(
        {
            "name": "caption-newline",
            "template": {"name": "t", "shot_count": 1, "output": {"width": 1080, "height": 1920}},
            "asset_paths": ["asset.mp4"],
            "shots": [{"text": "\u7b2c\u4e00\u884c\n\u7b2c\u4e8c\u884c" + "\u957f\u6587\u672c" * 30}],
            "output_dir": ".",
        }
    )
    ass_path = tmp_path / "captions.ass"
    _renderer()._write_ass(request, [3.0], ass_path)
    content = ass_path.read_text(encoding="utf-8-sig")
    dialogue = next(line for line in content.splitlines() if line.startswith("Dialogue: 2"))
    text = dialogue.split(",", 9)[9]
    prefix = r"{\fad(180,180)}"
    text = text[len(prefix) :] if text.startswith(prefix) else text
    assert text.count("\\N") >= 1
    assert text.startswith("\u7b2c\u4e00\u884c\\N\u7b2c\u4e8c\u884c")

