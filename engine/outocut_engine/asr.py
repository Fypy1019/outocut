from __future__ import annotations

import array
import math
import shutil
import tarfile
import tempfile
import wave
from pathlib import Path
from typing import Any

import httpx

from . import net
from .douyin import DouyinResolveError

MODEL_DOWNLOAD_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
    "sherpa-onnx-paraformer-zh-2023-09-14.tar.bz2"
)
MODEL_DIR_NAME = "paraformer-zh"
MODEL_REQUIRED_FILES = ("model.int8.onnx", "tokens.txt")

PUNCT_DOWNLOAD_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/punctuation-models/"
    "sherpa-onnx-punct-ct-transformer-zh-en-vocab272727-2024-04-12-int8.tar.bz2"
)
PUNCT_MODEL_NAME = "punctuation-zh"
PUNCT_REQUIRED_FILES = ("model.int8.onnx",)
DOWNLOAD_TIMEOUT = 600.0

_SILENCE_RMS = 80.0
# Single-pass decoding normally handles long voiceovers, but as a safety net
# the whole file is re-decoded in overlapping windows (and with volume boost)
# whenever the first pass returns nothing or suspiciously little text. This
# recovers segments that a stalled pass may have missed.
_CHUNK_SECONDS = 28.0
_CHUNK_OVERLAP_SECONDS = 2.0
_SHORT_RETRY_MIN_DURATION = 20.0
_SHORT_RETRY_MIN_CHARS = 12

_ERROR_MODEL_MISSING = (
    "\u672c\u5730\u8bc6\u522b\u6a21\u578b\u5c1a\u672a\u4e0b\u8f7d\uff0c"
    "\u8bf7\u6253\u5f00\u201c\u8bbe\u7f6e \u2192 \u6587\u6848\u914d\u7f6e\u8bbe\u7f6e\u201d"
    "\u70b9\u51fb\u201c\u4e0b\u8f7d\u672c\u5730\u8bc6\u522b\u6a21\u578b\u201d"
)
_ERROR_EMPTY = (
    "\u672a\u8bc6\u522b\u5230\u4eba\u58f0\u5185\u5bb9\uff0c"
    "\u8bf7\u786e\u8ba4\u89c6\u9891\u5305\u542b\u53e3\u64ad\u914d\u97f3"
)


def local_model_dir(data_root: Path) -> Path:
    return data_root / "asr" / MODEL_DIR_NAME


def sherpa_available() -> bool:
    try:
        import sherpa_onnx  # noqa: F401
    except ImportError:
        return False
    return True


def local_model_status(model_dir: Path) -> dict[str, Any]:
    asr_missing = [name for name in MODEL_REQUIRED_FILES if not (model_dir / name).is_file()]
    punct_dir = model_dir.parent / PUNCT_MODEL_NAME
    punct_missing = [
        f"punctuation/{name}"
        for name in PUNCT_REQUIRED_FILES
        if not (punct_dir / name).is_file()
    ]
    missing = asr_missing + punct_missing
    total = sum(
        (model_dir / name).stat().st_size
        for name in MODEL_REQUIRED_FILES
        if (model_dir / name).is_file()
    ) + sum(
        (punct_dir / name).stat().st_size
        for name in PUNCT_REQUIRED_FILES
        if (punct_dir / name).is_file()
    )
    return {
        "ready": not missing,
        "asr_ready": not asr_missing,
        "punctuation_ready": not punct_missing,
        "missing_files": missing,
        "size_bytes": total,
        "sherpa_available": sherpa_available(),
    }


def _find_model_dir(root: Path, required: tuple[str, ...]) -> Path | None:
    for child in root.iterdir():
        if child.is_dir():
            if all((child / name).is_file() for name in required):
                return child
            nested = _find_model_dir(child, required)
            if nested is not None:
                return nested
    return None


def _download_and_extract(dest: Path, url: str, required: tuple[str, ...]) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="outocut-asr-") as tmp:
        tmp_path = Path(tmp)
        archive = tmp_path / "model.tar.bz2"
        try:
            with net.stream("GET", url, follow_redirects=True, timeout=DOWNLOAD_TIMEOUT) as response:
                response.raise_for_status()
                with archive.open("wb") as file_handle:
                    for chunk in response.iter_bytes(1024 * 512):
                        file_handle.write(chunk)
        except httpx.HTTPError as exc:
            raise DouyinResolveError(
                f"\u672c\u5730\u8bc6\u522b\u6a21\u578b\u4e0b\u8f7d\u5931\u8d25\uff1a{exc.__class__.__name__}",
                code="model_download_failed",
            ) from exc
        extract_root = tmp_path / "extract"
        extract_root.mkdir()
        try:
            with tarfile.open(archive, "r:bz2") as tar:
                tar.extractall(extract_root)
        except (tarfile.TarError, OSError) as exc:
            raise DouyinResolveError(
                "\u672c\u5730\u8bc6\u522b\u6a21\u578b\u89e3\u538b\u5931\u8d25",
                code="model_download_failed",
            ) from exc
        found = _find_model_dir(extract_root, required)
        if found is None:
            raise DouyinResolveError(
                "\u672c\u5730\u8bc6\u522b\u6a21\u578b\u538b\u7f29\u5305\u5185\u5bb9\u4e0d\u5b8c\u6574",
                code="model_download_failed",
            )
        if dest.exists():
            shutil.rmtree(dest)
        shutil.move(str(found), str(dest))


_LEGACY_MODEL_DIR_NAMES = ("paraformer-zh-small",)


def _cleanup_legacy_models(model_dir: Path) -> None:
    for name in _LEGACY_MODEL_DIR_NAMES:
        legacy = model_dir.parent / name
        if legacy.is_dir() and legacy != model_dir:
            try:
                shutil.rmtree(legacy)
            except OSError:
                pass


def download_local_model(model_dir: Path, url: str = MODEL_DOWNLOAD_URL) -> Path:
    _download_and_extract(model_dir, url, MODEL_REQUIRED_FILES)
    _download_and_extract(
        model_dir.parent / PUNCT_MODEL_NAME, PUNCT_DOWNLOAD_URL, PUNCT_REQUIRED_FILES
    )
    if not local_model_status(model_dir)["ready"]:
        raise DouyinResolveError(
            "\u672c\u5730\u8bc6\u522b\u6a21\u578b\u6587\u4ef6\u4e0d\u5b8c\u6574",
            code="model_download_failed",
        )
    _cleanup_legacy_models(model_dir)
    return model_dir


def _decode_waveform(recognizer: Any, frame_rate: int, samples: array.array) -> str:
    stream = recognizer.create_stream()
    stream.accept_waveform(frame_rate, [sample / 32768.0 for sample in samples])
    recognizer.decode_stream(stream)
    return (stream.result.text or "").strip()


def _split_waveform(samples: array.array, frame_rate: int) -> list[array.array]:
    chunk_size = int(_CHUNK_SECONDS * frame_rate)
    step = max(chunk_size - int(_CHUNK_OVERLAP_SECONDS * frame_rate), 1)
    chunks: list[array.array] = []
    start = 0
    while start < len(samples):
        end = min(start + chunk_size, len(samples))
        chunks.append(samples[start:end])
        if end == len(samples):
            break
        start += step
    return chunks


def _merge_chunks(parts: list[str]) -> str:
    text = ""
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if not text:
            text = part
            continue
        max_k = min(len(text), len(part))
        overlap = 0
        for k in range(max_k, 0, -1):
            if text[-k:] == part[:k]:
                overlap = k
                break
        text += part[overlap:]
    return text


def _boost_samples(samples: array.array, rms: float) -> array.array:
    scale = min(3000.0 / max(rms, 1.0), 8.0)
    return array.array(
        "h", (max(-32767, min(32767, int(sample * scale))) for sample in samples)
    )


def _decode_chunks(recognizer: Any, frame_rate: int, samples: array.array) -> str:
    parts: list[str] = []
    for chunk in _split_waveform(samples, frame_rate):
        chunk_rms = math.sqrt(sum(sample * sample for sample in chunk) / len(chunk))
        chunk_text = _decode_waveform(recognizer, frame_rate, chunk)
        if not chunk_text and chunk_rms >= _SILENCE_RMS:
            chunk_text = _decode_waveform(
                recognizer, frame_rate, _boost_samples(chunk, chunk_rms)
            )
        if chunk_text:
            parts.append(chunk_text)
    return _merge_chunks(parts)


def _best_candidate(candidates: list[str]) -> str:
    best = ""
    for candidate in candidates:
        candidate = (candidate or "").strip()
        if not candidate:
            continue
        if not best or len(candidate) >= len(best) + _SHORT_RETRY_MIN_CHARS:
            best = candidate
    return best


def _add_punctuation(text: str, model_dir: Path) -> str:
    """Restore punctuation with the bundled offline punctuation model.

    The punctuation model is optional: when it is missing or fails to load,
    the raw transcript is returned unchanged.
    """
    if not text:
        return text
    try:
        import sherpa_onnx
        punct_model = model_dir.parent / PUNCT_MODEL_NAME / "model.int8.onnx"
        if not punct_model.is_file() or not hasattr(sherpa_onnx, "OfflinePunctuation"):
            return text
        punctuator = sherpa_onnx.OfflinePunctuation(
            sherpa_onnx.OfflinePunctuationConfig(
                model=sherpa_onnx.OfflinePunctuationModelConfig(
                    ct_transformer=str(punct_model), num_threads=1, debug=False
                )
            )
        )
        return punctuator.add_punctuation(text)
    except Exception:
        return text


def is_abnormally_short(transcript: str, audio_path: Path) -> bool:
    """Return True when a long clip produced almost no transcript text."""
    try:
        with wave.open(str(audio_path), "rb") as wav_file:
            duration = wav_file.getnframes() / max(wav_file.getframerate(), 1)
    except (OSError, wave.Error):
        return False
    return duration >= _SHORT_RETRY_MIN_DURATION and len(transcript) <= 2


def transcribe_local(audio_path: Path, model_dir: Path | None) -> str:
    try:
        import sherpa_onnx
    except ImportError as exc:
        raise DouyinResolveError(
            "\u672a\u5b89\u88c5\u672c\u5730\u8bed\u97f3\u8bc6\u522b\u7ec4\u4ef6\uff08sherpa-onnx\uff09\uff0c"
            "\u8bf7\u91cd\u65b0\u5b89\u88c5/\u66f4\u65b0\u5f15\u64ce\u7248\u672c",
            code="local_asr_unavailable",
        ) from exc
    model_path = (model_dir or Path()) / "model.int8.onnx"
    tokens_path = (model_dir or Path()) / "tokens.txt"
    if not (model_path.is_file() and tokens_path.is_file()):
        raise DouyinResolveError(_ERROR_MODEL_MISSING, code="local_model_missing")
    try:
        recognizer = sherpa_onnx.OfflineRecognizer.from_paraformer(
            paraformer=str(model_path),
            tokens=str(tokens_path),
            num_threads=2,
            sample_rate=16000,
            feature_dim=80,
            decoding_method="greedy_search",
            debug=False,
        )
        with wave.open(str(audio_path), "rb") as wav_file:
            frame_rate = wav_file.getframerate()
            samples = array.array("h", wav_file.readframes(wav_file.getnframes()))
    except DouyinResolveError:
        raise
    except Exception as exc:
        raise DouyinResolveError(
            f"\u672c\u5730\u8bed\u97f3\u8bc6\u522b\u5931\u8d25\uff1a{exc.__class__.__name__}",
            code="asr_failed",
        ) from exc
    if not samples:
        raise DouyinResolveError(
            "\u97f3\u9891\u6587\u4ef6\u4e3a\u7a7a\u6216\u65e0\u6cd5\u8bfb\u53d6",
            code="asr_empty",
        )
    rms = math.sqrt(sum(sample * sample for sample in samples) / len(samples))
    duration = len(samples) / max(frame_rate, 1)
    if rms < _SILENCE_RMS:
        raise DouyinResolveError(
            "\u63d0\u53d6\u5230\u7684\u97f3\u9891\u8fd1\u4e4e\u9759\u97f3\uff0c"
            "\u8bf7\u786e\u8ba4\u89c6\u9891\u5305\u542b\u4eba\u58f0\u53e3\u64ad",
            code="asr_empty",
        )
    transcript = _decode_waveform(recognizer, frame_rate, samples)
    if not transcript and rms >= _SILENCE_RMS:
        transcript = _decode_waveform(recognizer, frame_rate, _boost_samples(samples, rms))
    if not transcript or (
        duration >= _SHORT_RETRY_MIN_DURATION and len(transcript) < _SHORT_RETRY_MIN_CHARS
    ):
        candidates = [transcript] if transcript else []
        candidates.append(_decode_chunks(recognizer, frame_rate, samples))
        if rms >= _SILENCE_RMS:
            boosted = _boost_samples(samples, rms)
            candidates.append(_decode_waveform(recognizer, frame_rate, boosted))
            candidates.append(_decode_chunks(recognizer, frame_rate, boosted))
        transcript = _best_candidate(candidates)
    if not transcript:
        raise DouyinResolveError(
            "\u97f3\u9891\u65f6\u957f\u7ea6 "
            f"{duration:.0f} \u79d2\uff0c\u672a\u8bc6\u522b\u5230\u4eba\u58f0\u5185\u5bb9\uff1b"
            "\u5982\u679c\u786e\u8ba4\u89c6\u9891\u5305\u542b\u53e3\u64ad\uff0c"
            "\u53ef\u5c1d\u8bd5\u5207\u6362\u5230\u767e\u70bc API \u8f6c\u5199\uff0c"
            "\u6216\u5c06\u63d0\u793a\u4e2d\u7684\u8c03\u8bd5\u97f3\u9891"
            "\u53d1\u7ed9\u6211\u4eec\u5206\u6790",
            code="asr_empty",
        )
    return _add_punctuation(transcript, model_dir)
