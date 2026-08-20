"""Local font discovery for the caption/title font picker.

Parses the sfnt ``name`` table directly (TrueType / OpenType / collections) so
the engine has no runtime dependency on fontconfig or fontTools.  The extracted
family name is what libass matches against when burning captions with a
``fontsdir``.
"""

from __future__ import annotations

import os
import struct
from collections.abc import Iterator
from pathlib import Path

FONT_SUFFIXES = frozenset({".ttf", ".otf", ".ttc", ".otc"})

_NAME_ID_FAMILY = 1
_NAME_ID_TYPOGRAPHIC_FAMILY = 16

_PLATFORM_WINDOWS = 3
_PLATFORM_MAC = 1
_ENCODING_UTF16 = 1
_LANG_ENGLISH = 0x0409


def _decode_name(data: bytes, platform_id: int, encoding_id: int) -> str | None:
    if platform_id == _PLATFORM_WINDOWS and encoding_id in (0, 1, 10):
        try:
            return data.decode("utf-16-be")
        except UnicodeDecodeError:
            return None
    if platform_id == _PLATFORM_MAC:
        try:
            return data.decode("mac-roman")
        except (UnicodeDecodeError, LookupError):
            return None
    return None


def _parse_name_table(data: bytes) -> str | None:
    if len(data) < 6:
        return None
    (format_tag, count, string_offset) = struct.unpack_from(">HHH", data, 0)
    if format_tag not in (0, 1):
        return None
    candidates: list[tuple[tuple[bool, bool, bool, bool], str]] = []
    for index in range(count):
        record = 6 + index * 12
        if record + 12 > len(data):
            break
        platform_id, encoding_id, language_id, name_id, length, offset = struct.unpack_from(
            ">HHHHHH", data, record
        )
        if name_id not in (_NAME_ID_FAMILY, _NAME_ID_TYPOGRAPHIC_FAMILY):
            continue
        start = string_offset + offset
        if start + length > len(data):
            continue
        decoded = _decode_name(data[start : start + length], platform_id, encoding_id)
        if not decoded:
            continue
        family = decoded.rstrip("\x00").strip()
        if not family:
            continue
        # Prefer the plain family name (what libass matches), then Windows
        # UTF-16 entries, then English language variants.
        preference = (
            name_id == _NAME_ID_FAMILY,
            platform_id == _PLATFORM_WINDOWS,
            encoding_id == _ENCODING_UTF16,
            language_id == _LANG_ENGLISH,
        )
        candidates.append((preference, family))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def _sfnt_family(handle, offset: int) -> str | None:
    handle.seek(offset)
    header = handle.read(12)
    if len(header) < 12:
        return None
    (num_tables,) = struct.unpack_from(">H", header, 4)
    handle.seek(offset + 12)
    records = handle.read(16 * num_tables)
    name_offset = name_length = None
    for index in range(num_tables):
        record = records[index * 16 : (index + 1) * 16]
        if len(record) < 16:
            break
        tag, _checksum, table_offset, table_length = struct.unpack(">4sIII", record)
        if tag == b"name":
            name_offset, name_length = table_offset, table_length
            break
    if name_offset is None or name_length <= 0:
        return None
    # Some font collections store table offsets relative to the collection
    # file instead of the face; try both bases.
    for base in (offset, 0):
        handle.seek(base + name_offset)
        family = _parse_name_table(handle.read(min(name_length, 1 << 16)))
        if family:
            return family
    return None


def _families_from_file(path: Path) -> list[str]:
    try:
        with path.open("rb") as handle:
            tag = handle.read(4)
            if tag == b"ttcf":
                header = handle.read(8)
                if len(header) < 8:
                    return []
                (num_fonts,) = struct.unpack_from(">I", header, 4)
                offsets = handle.read(4 * num_fonts)
                families: list[str] = []
                for index in range(num_fonts):
                    if index * 4 + 4 > len(offsets):
                        break
                    (offset,) = struct.unpack_from(">I", offsets, index * 4)
                    family = _sfnt_family(handle, offset)
                    if family:
                        families.append(family)
                return families
            if tag in (b"\x00\x01\x00\x00", b"OTTO", b"true", b"typ1"):
                family = _sfnt_family(handle, 0)
                return [family] if family else []
    except OSError:
        return []
    return []


def _iter_font_files(directory: Path) -> Iterator[Path]:
    try:
        for root, _dirs, names in os.walk(directory):
            for name in names:
                if Path(name).suffix.lower() in FONT_SUFFIXES:
                    yield Path(root) / name
    except OSError:
        return


def scan_font_directory(directory: str | Path, limit: int = 1000) -> list[dict[str, str]]:
    """Scan ``directory`` (recursively) and return ``{name, path}`` entries.

    A single font file path may be passed as well; its parent directory is
    scanned then.  Results are deduplicated by (family, path) and sorted by
    family name.
    """
    target = Path(directory)
    if not target.exists():
        return []
    if target.is_file():
        target = target.parent
    if not target.is_dir():
        return []
    entries: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for path in _iter_font_files(target):
        if len(entries) >= limit:
            break
        for family in _families_from_file(path):
            key = (family, str(path))
            if key in seen:
                continue
            seen.add(key)
            entries.append({"name": family, "path": str(path)})
    entries.sort(key=lambda entry: (entry["name"].lower(), entry["path"].lower()))
    return entries


def _system_font_directories() -> list[Path]:
    """Standard system font directories (Windows)."""
    directories: list[Path] = []
    if os.name == "nt":
        windir = os.environ.get("WINDIR") or r"C:\Windows"
        directories.append(Path(windir) / "Fonts")
        local = os.environ.get("LOCALAPPDATA")
        if local:
            directories.append(Path(local) / "Microsoft" / "Windows" / "Fonts")
    return directories


def system_font_entries() -> list[dict[str, str]]:
    """Scan the standard system font directories (Windows)."""
    directories = _system_font_directories()
    entries: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for directory in directories:
        for entry in scan_font_directory(directory):
            key = (entry["name"], entry["path"])
            if key in seen:
                continue
            seen.add(key)
            entries.append(entry)
    entries.sort(key=lambda entry: (entry["name"].lower(), entry["path"].lower()))
    return entries


def _table_records(handle, offset: int) -> dict[bytes, tuple[int, int]]:
    """Map sfnt table tags to ``(offset, length)`` for the face at ``offset``."""
    handle.seek(offset)
    header = handle.read(12)
    if len(header) < 12:
        return {}
    (num_tables,) = struct.unpack_from(">H", header, 4)
    handle.seek(offset + 12)
    records = handle.read(16 * num_tables)
    tables: dict[bytes, tuple[int, int]] = {}
    for index in range(num_tables):
        record = records[index * 16 : (index + 1) * 16]
        if len(record) < 16:
            break
        tag, _checksum, table_offset, table_length = struct.unpack(">4sIII", record)
        tables[tag] = (table_offset, table_length)
    return tables


def _face_metrics(handle, offset: int) -> tuple[int, int, int] | None:
    """Return ``(units_per_em, ascender, descender)`` for one sfnt face."""
    tables = _table_records(handle, offset)
    head = tables.get(b"head")
    hhea = tables.get(b"hhea")
    if not head or not hhea:
        return None
    for base in (0, offset):
        try:
            handle.seek(base + head[0] + 18)
            (units_per_em,) = struct.unpack(">H", handle.read(2))
            handle.seek(base + hhea[0] + 4)
            ascender, descender = struct.unpack(">hh", handle.read(4))
        except struct.error:
            continue
        if units_per_em > 0 and ascender > 0 and descender < ascender:
            return units_per_em, ascender, descender
    return None


def _iter_faces(handle) -> Iterator[tuple[int, str | None]]:
    """Yield ``(face_offset, family_name_or_None)`` for a font file."""
    handle.seek(0)
    tag = handle.read(4)
    if tag == b"ttcf":
        header = handle.read(8)
        if len(header) < 8:
            return
        (num_fonts,) = struct.unpack_from(">I", header, 4)
        offsets = handle.read(4 * num_fonts)
        for index in range(num_fonts):
            if index * 4 + 4 > len(offsets):
                break
            (face_offset,) = struct.unpack_from(">I", offsets, index * 4)
            yield face_offset, _sfnt_family(handle, face_offset)
        return
    if tag in (b"\x00\x01\x00\x00", b"OTTO", b"true", b"typ1"):
        yield 0, _sfnt_family(handle, 0)


def _font_files_for(path: str | Path | None) -> list[Path]:
    """Font files to inspect: explicit file/directory or system directories."""
    if path is None:
        files: list[Path] = []
        for directory in _system_font_directories():
            files.extend(_iter_font_files(directory))
        return files
    target = Path(path)
    if target.is_file():
        return [target]
    if target.is_dir():
        return list(_iter_font_files(target))
    return []


def _iter_matching_faces(family: str, path: str | Path | None) -> Iterator[tuple[Path, int]]:
    """Yield ``(font_file, face_offset)`` for every face whose family matches."""
    wanted = family.casefold()
    for candidate in _font_files_for(path):
        try:
            with candidate.open("rb") as handle:
                for offset, face_family in _iter_faces(handle):
                    if face_family is not None and face_family.casefold() == wanted:
                        yield candidate, offset
        except OSError:
            continue


def font_metrics(family: str, path: str | Path | None = None) -> tuple[int, int, int] | None:
    """Return ``(units_per_em, ascender, descender)`` for ``family``.

    ``path`` may point at a font file or a directory; when omitted the system
    font directories are scanned.  The metrics describe how wide libass
    advances each full-width glyph (``font_size * upem / (asc - desc)``) so
    :func:`font_measure` uses these to wrap captions at the real rendered
    glyph width instead of assuming the nominal font size.
    """
    for candidate, offset in _iter_matching_faces(family, path):
        try:
            with candidate.open("rb") as handle:
                metrics = _face_metrics(handle, offset)
        except OSError:
            continue
        if metrics:
            return metrics
    return None


def _parse_cmap_format4(handle, offset: int):
    """Return a BMP codepoint -> glyph id callable for a format 4 subtable."""
    handle.seek(offset)
    # The format 4 header is exactly 14 bytes (Python 3.14+ struct.unpack
    # rejects trailing bytes).
    header = handle.read(14)
    if len(header) < 14:
        return None
    (_fmt, length, _language, seg_count_x2, _search, _entry, _range) = struct.unpack(">7H", header)
    seg_count = seg_count_x2 // 2
    if seg_count <= 0 or seg_count > 8192:
        return None
    # Read the whole subtable: lookups via idRangeOffset address into the
    # glyphIdArray, which lives after the segment headers (msyh.ttc keeps
    # ~100KB of glyph ids there).
    rest = handle.read(length - 14)
    if len(rest) < 2 + 8 * seg_count:
        return None
    end_code = struct.unpack_from(f">{seg_count}H", rest, 0)
    start_code = struct.unpack_from(f">{seg_count}H", rest, 2 * seg_count + 2)
    id_delta = struct.unpack_from(f">{seg_count}h", rest, 4 * seg_count + 2)
    id_range_offset = struct.unpack_from(f">{seg_count}H", rest, 6 * seg_count + 2)
    glyph_array = rest[2 + 8 * seg_count :]

    def lookup(char: int) -> int:
        code = char & 0xFFFF
        for index in range(seg_count):
            if start_code[index] <= code <= end_code[index]:
                if id_range_offset[index] == 0:
                    return (code + id_delta[index]) & 0xFFFF
                address = (
                    id_range_offset[index]
                    + 2 * (code - start_code[index])
                    + 2 * index
                    - seg_count_x2
                )
                if address < 0 or address + 2 > len(glyph_array):
                    return 0
                (glyph,) = struct.unpack_from(">H", glyph_array, address)
                if glyph == 0:
                    return 0
                return (glyph + id_delta[index]) & 0xFFFF
        return 0

    return lookup


def _parse_cmap_format12(handle, offset: int):
    """Return a Unicode codepoint -> glyph id callable for a format 12 subtable."""
    handle.seek(offset)
    header = handle.read(16)
    if len(header) < 16:
        return None
    (_fmt, _reserved, _length, _language, n_groups) = struct.unpack(">HHIII", header)
    if n_groups <= 0 or n_groups > 1 << 20:
        return None
    groups_data = handle.read(12 * n_groups)
    if len(groups_data) < 12 * n_groups:
        return None
    groups = [
        struct.unpack_from(">III", groups_data, index * 12) for index in range(n_groups)
    ]

    def lookup(char: int) -> int:
        low, high = 0, len(groups) - 1
        while low <= high:
            mid = (low + high) // 2
            start, end, glyph = groups[mid]
            if char < start:
                high = mid - 1
            elif char > end:
                low = mid + 1
            else:
                return glyph + (char - start)
        return 0

    return lookup


def _parse_cmap(handle, offset: int, length: int):
    """Return a codepoint -> glyph id callable for the best cmap subtable."""
    handle.seek(offset)
    header = handle.read(4)
    if len(header) < 4:
        return None
    (_version, num_tables) = struct.unpack(">HH", header)
    records = handle.read(8 * num_tables)
    subtables: list[tuple[int, int, int]] = []
    for index in range(num_tables):
        record = records[index * 8 : (index + 1) * 8]
        if len(record) < 8:
            break
        platform_id, encoding_id, sub_offset = struct.unpack(">HHI", record)
        if sub_offset < length:
            subtables.append((platform_id, encoding_id, sub_offset))
    # Prefer Windows Unicode subtables, then Unicode-platform subtables.
    subtables.sort(key=lambda item: (item[0] != 3, item[1] not in (1, 10)))
    for _platform_id, _encoding_id, sub_offset in subtables:
        sub_offset += offset
        handle.seek(sub_offset)
        (format_id,) = struct.unpack(">H", handle.read(2))
        lookup = None
        if format_id == 4:
            lookup = _parse_cmap_format4(handle, sub_offset)
        elif format_id == 12:
            lookup = _parse_cmap_format12(handle, sub_offset)
        if lookup is not None:
            return lookup
    return None


class FontMeasure:
    """Per-glyph advance widths for one resolved font face.

    Parsed straight from the sfnt ``cmap``/``hmtx`` tables so text width can
    be measured without fontTools or Pillow.  libass scales glyph advances by
    ``font_size / (ascender - descender)`` rather than the nominal em, so
    Microsoft YaHei (upem 2048, asc 2167, desc -536) renders each full-width
    glyph at only ~0.76x the font size -- which is why counting characters
    never matches the rendered output.
    """

    __slots__ = ("_advances", "_cmap", "_ascender", "_descender")

    def __init__(
        self, advances: list[int], cmap, ascender: int, descender: int
    ) -> None:
        self._advances = advances
        self._cmap = cmap
        self._ascender = ascender
        self._descender = descender

    def text_width(self, text: str, font_size: int, spacing: float = 1.0) -> float:
        """Rendered pixel width of ``text`` at ``font_size`` (plus spacing)."""
        scale = font_size / (self._ascender - self._descender)
        fallback = self._advances[-1] if self._advances else 0
        width = 0.0
        for char in text:
            glyph = self._cmap(ord(char))
            advance = self._advances[glyph] if glyph < len(self._advances) else fallback
            width += advance * scale + spacing
        return width


def _parse_measure(handle, offset: int) -> FontMeasure | None:
    """Build a :class:`FontMeasure` for the sfnt face at ``offset``."""
    metrics = _face_metrics(handle, offset)
    if metrics is None:
        return None
    _units_per_em, ascender, descender = metrics
    tables = _table_records(handle, offset)
    hhea = tables.get(b"hhea")
    hmtx = tables.get(b"hmtx")
    cmap = tables.get(b"cmap")
    if not (hhea and hmtx and cmap):
        return None
    for base in (0, offset):
        try:
            handle.seek(base + hhea[0] + 34)
            (number_of_hmetrics,) = struct.unpack(">H", handle.read(2))
            if number_of_hmetrics <= 0:
                continue
            handle.seek(base + hmtx[0])
            hmtx_data = handle.read(4 * number_of_hmetrics)
            if len(hmtx_data) < 4 * number_of_hmetrics:
                continue
            advances = [
                struct.unpack_from(">H", hmtx_data, index * 4)[0]
                for index in range(number_of_hmetrics)
            ]
            lookup = _parse_cmap(handle, base + cmap[0], min(cmap[1], 1 << 20))
            if lookup is None:
                continue
            return FontMeasure(advances, lookup, ascender, descender)
        except (OSError, struct.error):
            continue
    return None


def font_measure(family: str, path: str | Path | None = None) -> FontMeasure | None:
    """Return a :class:`FontMeasure` for ``family`` or ``None``.

    ``path`` may point at a font file or a directory; when omitted the system
    font directories are scanned.  Used by the caption wrapper to break lines
    at the real rendered pixel width instead of by character count.
    """
    wanted = family.casefold()
    if path is None:
        files: list[Path] = []
        for directory in _system_font_directories():
            files.extend(_iter_font_files(directory))
    else:
        target = Path(path)
        if target.is_file():
            files = [target]
        elif target.is_dir():
            files = list(_iter_font_files(target))
        else:
            files = []
    for candidate in files:
        try:
            with candidate.open("rb") as handle:
                for face_offset, face_family in _iter_faces(handle):
                    if face_family is not None and face_family.casefold() == wanted:
                        measurer = _parse_measure(handle, face_offset)
                        if measurer:
                            return measurer
        except OSError:
            continue
    return None
