from __future__ import annotations

import os
import shutil
import struct
import sys
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from outocut_engine.fonts import font_measure, font_metrics, scan_font_directory, system_font_entries


def _system_font(name: str) -> Path | None:
    if sys.platform != "win32":
        return None
    candidate = Path(os.environ.get("WINDIR", r"C:\\Windows")) / "Fonts" / name
    return candidate if candidate.is_file() else None


def _craft_font(family: str) -> bytes:
    """Build a minimal sfnt (name/head/hhea/maxp/hmtx/cmap) with a UTF-16 family name.

    Glyph 0 (missing) advances 1000 units, glyph 1 ("A") 500 units and glyph 2
    (CJK "\u5b57") a full em of 2048 units, with units_per_em == 2048.
    """
    family_bytes = family.encode("utf-16-be")
    name_data = struct.pack(">HHH", 0, 1, 6 + 12)
    name_data += struct.pack(">HHHHHH", 3, 1, 0x0409, 1, len(family_bytes), 0)
    name_data += family_bytes
    head = struct.pack(">IIIIHH", 0x00010000, 0x00010000, 0, 0x5F0F3CF5, 0, 2048) + bytes(34)
    # version, ascender, descender, lineGap, advanceWidthMax, then zeros up to
    # offset 34 (numberOfHMetrics), which must be 3 to match the hmtx below.
    hhea = struct.pack(">Ihhh", 0x00010000, 2167, -536, 0) + bytes(24) + struct.pack(">H", 3)
    maxp = struct.pack(">IH", 0x00010000, 3) + bytes(26)
    # hmtx: advance + lsb for glyphs 0..2
    hmtx = b"".join(struct.pack(">Hh", advance, 0) for advance in (1000, 500, 2048))
    # cmap format 4 with two single-code segments: "A" -> 1, CJK "\u5b57" -> 2.
    seg_count_x2 = 4
    format4 = struct.pack(">HHHHHHH", 4, 32, 0, seg_count_x2, 4, 1, 0)
    format4 += struct.pack(">HH", 65, 0x5B57)
    format4 += struct.pack(">H", 0)
    format4 += struct.pack(">HH", 65, 0x5B57)
    format4 += struct.pack(">hh", 1 - 65, 2 - 0x5B57)
    format4 += struct.pack(">HH", 0, 0)
    cmap = struct.pack(">HH", 0, 1) + struct.pack(">HHI", 3, 1, 12) + format4

    def align(value: int) -> int:
        return (value + 3) & ~3

    tables = [
        (b"name", name_data),
        (b"head", head),
        (b"hhea", hhea),
        (b"maxp", maxp),
        (b"hmtx", hmtx),
        (b"cmap", cmap),
    ]
    header_size = 12 + 16 * len(tables)
    offsets: list[int] = []
    cursor = header_size
    for _tag, data in tables:
        offsets.append(cursor)
        cursor = align(cursor + len(data))
    font = struct.pack(">IHHHH", 0x00010000, len(tables), 0, 0, 0)
    for (tag, data), table_offset in zip(tables, offsets, strict=True):
        font += struct.pack(">4sIII", tag, 0, table_offset, len(data))
    for (_tag, data), table_offset in zip(tables, offsets, strict=True):
        font += bytes(table_offset - len(font))
        font += data
    return font


def test_crafted_sfnt_family_name(tmp_path: Path) -> None:
    target = tmp_path / "crafted.ttf"
    target.write_bytes(_craft_font("TestFamily"))
    entries = scan_font_directory(tmp_path)
    assert entries == [{"name": "TestFamily", "path": str(target)}]


def test_font_metrics_parses_head_and_hhea(tmp_path: Path) -> None:
    target = tmp_path / "crafted.ttf"
    target.write_bytes(_craft_font("MetricFamily"))
    assert font_metrics("MetricFamily", target) == (2048, 2167, -536)


def test_font_metrics_matches_family_case_insensitively(tmp_path: Path) -> None:
    target = tmp_path / "crafted.ttf"
    target.write_bytes(_craft_font("MetricFamily"))
    assert font_metrics("metricfamily", target) == (2048, 2167, -536)


def test_font_metrics_searches_directory(tmp_path: Path) -> None:
    target = tmp_path / "fonts" / "crafted.ttf"
    target.parent.mkdir()
    target.write_bytes(_craft_font("MetricFamily"))
    assert font_metrics("MetricFamily", target.parent) == (2048, 2167, -536)


def test_font_metrics_unknown_family_returns_none(tmp_path: Path) -> None:
    target = tmp_path / "crafted.ttf"
    target.write_bytes(_craft_font("MetricFamily"))
    assert font_metrics("Missing Family", target) is None
    assert font_metrics("MetricFamily", tmp_path / "nope.ttf") is None


def test_font_metrics_ignores_non_matching_face_in_collection(tmp_path: Path) -> None:
    other = tmp_path / "other.ttf"
    other.write_bytes(_craft_font("OtherFamily"))
    assert font_metrics("MetricFamily", tmp_path) is None


def test_font_measure_advance_widths(tmp_path: Path) -> None:
    target = tmp_path / "crafted.ttf"
    target.write_bytes(_craft_font("MetricFamily"))
    measurer = font_measure("MetricFamily", target)
    assert measurer is not None
    # libass scales by font_size / (ascender - descender) = font_size / 2703,
    # not by units_per_em (2048), so full-em glyphs render narrower than the
    # nominal font size -- matching real libass output.
    scale = 2048 / (2167 - (-536))
    assert measurer.text_width("A", 2048, 0.0) == pytest.approx(500.0 * scale)
    assert measurer.text_width("\u5b57", 2048, 0.0) == pytest.approx(2048.0 * scale)
    assert measurer.text_width("X", 2048, 0.0) == pytest.approx(1000.0 * scale)  # missing glyph
    assert measurer.text_width("A\u5b57", 2048, 1.0) == pytest.approx(2548.0 * scale + 2.0)
    # font_size scales the advances linearly.
    assert measurer.text_width("A\u5b57", 1024, 0.0) == pytest.approx(2548.0 * scale / 2)


def test_font_measure_matches_family_case_insensitively(tmp_path: Path) -> None:
    target = tmp_path / "crafted.ttf"
    target.write_bytes(_craft_font("MetricFamily"))
    assert font_measure("metricfamily", target) is not None


def test_font_measure_unknown_family_returns_none(tmp_path: Path) -> None:
    target = tmp_path / "crafted.ttf"
    target.write_bytes(_craft_font("MetricFamily"))
    assert font_measure("Missing Family", target) is None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows fonts required")
def test_font_metrics_microsoft_yahei() -> None:
    source = _system_font("msyh.ttc")
    if not source:
        pytest.skip("msyh.ttc not found")
    metrics = font_metrics("Microsoft YaHei", source)
    assert metrics is not None
    units_per_em, ascender, descender = metrics
    assert units_per_em == 2048
    assert ascender > 0 and descender < 0


def test_scan_missing_directory_returns_empty(tmp_path: Path) -> None:
    assert scan_font_directory(tmp_path / "nope") == []
    assert scan_font_directory(tmp_path / "missing.ttf") == []


def test_scan_ignores_non_font_files_and_deduplicates(tmp_path: Path) -> None:
    target = tmp_path / "fonts"
    target.mkdir()
    (target / "notes.txt").write_text("not a font", encoding="utf-8")
    crafted = target / "crafted.ttf"
    crafted.write_bytes(_craft_font("DupeFamily"))
    entries = scan_font_directory(target)
    assert len(entries) == 1
    assert entries[0]["name"] == "DupeFamily"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows fonts required")
def test_scan_font_directory_reads_real_family_names(tmp_path: Path) -> None:
    source = _system_font("arial.ttf") or _system_font("Arial.ttf")
    if not source:
        pytest.skip("arial.ttf not found")
    target = tmp_path / "my-fonts" / "Arial.ttf"
    target.parent.mkdir()
    shutil.copy2(source, target)
    entries = scan_font_directory(target.parent)
    assert any(entry["name"].lower() == "arial" and Path(entry["path"]) == target for entry in entries)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows fonts required")
def test_ttc_collection_parses_family_names() -> None:
    source = _system_font("msyh.ttc") or _system_font("simsun.ttc")
    if not source:
        pytest.skip("no ttc font found")
    entries = scan_font_directory(source)
    assert entries
    assert all(entry["name"] for entry in entries)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows fonts required")
def test_system_font_entries_include_common_families() -> None:
    entries = system_font_entries()
    assert entries
    names = {entry["name"].lower() for entry in entries}
    assert "arial" in names


def test_fonts_list_api_rejects_anonymous(client: TestClient) -> None:
    response = client.get("/fonts/list")
    assert response.status_code == 401


@pytest.mark.skipif(sys.platform != "win32", reason="Windows fonts required")
def test_fonts_list_api_system_and_directory(
    client: TestClient, auth_headers: dict[str, str], tmp_path: Path
) -> None:
    system = client.get("/fonts/list", headers=auth_headers)
    assert system.status_code == 200
    entries = system.json()
    assert entries
    assert {"name", "path"} <= set(entries[0])

    source = _system_font("arial.ttf") or _system_font("Arial.ttf")
    if not source:
        pytest.skip("arial.ttf not found")
    shutil.copy2(source, tmp_path / "Arial.ttf")
    directory = client.get(
        f"/fonts/list?directory={quote(str(tmp_path))}", headers=auth_headers
    )
    assert directory.status_code == 200
    folder_entries = directory.json()
    assert any(entry["name"].lower() == "arial" for entry in folder_entries)
