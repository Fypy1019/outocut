from __future__ import annotations

import array
import io
import math
import sys
import tarfile
import types
import wave
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from outocut_engine import asr as asr_module
from outocut_engine.douyin import DouyinResolveError


class FakeStreamResponse:
    def __init__(self, payload: bytes, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        pass

    def iter_bytes(self, size: int):
        for offset in range(0, len(self._payload), size):
            yield self._payload[offset : offset + size]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _make_wav(path: Path) -> None:
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        samples = array.array(
            "h", (int(5000 * math.sin(2 * math.pi * 440 * i / 16000)) for i in range(16000))
        )
        wav_file.writeframes(samples.tobytes())


def _make_model_tar() -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:bz2") as tar:
        for name, data in (
            ("model-dir/model.int8.onnx", b"onnx-bytes"),
            ("model-dir/tokens.txt", b"tokens-bytes"),
        ):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def test_local_model_status(tmp_path: Path) -> None:
    model_dir = tmp_path / "paraformer-zh"
    status = asr_module.local_model_status(model_dir)
    assert status["ready"] is False
    assert "model.int8.onnx" in status["missing_files"]
    assert status["sherpa_available"] is True

    model_dir.mkdir(parents=True, exist_ok=True)
    (model_dir / "model.int8.onnx").write_bytes(b"x" * 100)
    (model_dir / "tokens.txt").write_bytes(b"y" * 50)
    status = asr_module.local_model_status(model_dir)
    assert status["ready"] is False
    assert status["asr_ready"] is True
    assert status["punctuation_ready"] is False
    assert status["missing_files"] == ["punctuation/model.int8.onnx"]

    punct_dir = model_dir.parent / asr_module.PUNCT_MODEL_NAME
    punct_dir.mkdir(parents=True, exist_ok=True)
    (punct_dir / "model.int8.onnx").write_bytes(b"z" * 40)
    status = asr_module.local_model_status(model_dir)
    assert status["ready"] is True
    assert status["missing_files"] == []
    assert status["size_bytes"] == 190


def test_transcribe_local(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    model_dir = tmp_path / "model"
    wav_path = tmp_path / "in.wav"
    _make_wav(wav_path)

    with pytest.raises(DouyinResolveError) as exc_info:
        asr_module.transcribe_local(wav_path, model_dir)
    assert exc_info.value.code == "local_model_missing"

    model_dir.mkdir(parents=True, exist_ok=True)
    (model_dir / "model.int8.onnx").write_bytes(b"m")
    (model_dir / "tokens.txt").write_bytes(b"t")

    class FakeResult:
        text = "LOCAL_TEXT"

    class FakeStream:
        result = FakeResult()

        def accept_waveform(self, sample_rate: int, waveform) -> None:
            pass

    class FakeRecognizer:
        def create_stream(self):
            return FakeStream()

        def decode_stream(self, stream) -> None:
            pass

    class FakeOfflineRecognizer:
        @classmethod
        def from_paraformer(cls, **kwargs):
            return FakeRecognizer()

    fake_module = types.ModuleType("sherpa_onnx")
    fake_module.OfflineRecognizer = FakeOfflineRecognizer
    monkeypatch.setitem(sys.modules, "sherpa_onnx", fake_module)

    assert asr_module.transcribe_local(wav_path, model_dir) == "LOCAL_TEXT"


def test_transcribe_local_empty(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    model_dir = tmp_path / "model"
    model_dir.mkdir(parents=True, exist_ok=True)
    (model_dir / "model.int8.onnx").write_bytes(b"m")
    (model_dir / "tokens.txt").write_bytes(b"t")
    wav_path = tmp_path / "in.wav"
    _make_wav(wav_path)

    class FakeResult:
        text = ""

    class FakeStream:
        result = FakeResult()

        def accept_waveform(self, sample_rate: int, waveform) -> None:
            pass

    class FakeRecognizer:
        def create_stream(self):
            return FakeStream()

        def decode_stream(self, stream) -> None:
            pass

    class FakeOfflineRecognizer:
        @classmethod
        def from_paraformer(cls, **kwargs):
            return FakeRecognizer()

    fake_module = types.ModuleType("sherpa_onnx")
    fake_module.OfflineRecognizer = FakeOfflineRecognizer
    monkeypatch.setitem(sys.modules, "sherpa_onnx", fake_module)

    with pytest.raises(DouyinResolveError) as exc_info:
        asr_module.transcribe_local(wav_path, model_dir)
    assert exc_info.value.code == "asr_empty"


def test_transcribe_local_boost_retry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    model_dir = tmp_path / "model"
    model_dir.mkdir(parents=True, exist_ok=True)
    (model_dir / "model.int8.onnx").write_bytes(b"m")
    (model_dir / "tokens.txt").write_bytes(b"t")
    wav_path = tmp_path / "quiet.wav"
    with wave.open(str(wav_path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes(array.array("h", (2000 for _ in range(16000))).tobytes())

    calls = {"count": 0}

    class FakeResult:
        def __init__(self, text: str):
            self.text = text

    class FakeStream:
        def __init__(self):
            calls["count"] += 1

        def accept_waveform(self, sample_rate: int, waveform) -> None:
            pass

    class FakeRecognizer:
        def create_stream(self):
            return FakeStream()

        def decode_stream(self, stream) -> None:
            stream.result = FakeResult("TEXT_AFTER_BOOST" if calls["count"] > 1 else "")

    class FakeOfflineRecognizer:
        @classmethod
        def from_paraformer(cls, **kwargs):
            return FakeRecognizer()

    fake_module = types.ModuleType("sherpa_onnx")
    fake_module.OfflineRecognizer = FakeOfflineRecognizer
    monkeypatch.setitem(sys.modules, "sherpa_onnx", fake_module)

    assert asr_module.transcribe_local(wav_path, model_dir) == "TEXT_AFTER_BOOST"
    assert calls["count"] == 2


def test_split_waveform_overlap() -> None:
    frame_rate = 16000
    samples = array.array("h", ((i % 20000) - 10000 for i in range(frame_rate * 60)))
    chunks = asr_module._split_waveform(samples, frame_rate)
    assert len(chunks) == 3
    assert len(chunks[0]) == 28 * frame_rate
    assert len(chunks[1]) == 28 * frame_rate
    assert len(chunks[2]) == 8 * frame_rate
    assert chunks[0][-2 * frame_rate :] == chunks[1][: 2 * frame_rate]
    assert chunks[1][-2 * frame_rate :] == chunks[2][: 2 * frame_rate]


def test_merge_chunks_dedup() -> None:
    parts = [
        "\u590f\u5929\u5230\u4e86",
        "",
        "\u5230\u4e86\u4eca\u5929",
        "\u4eca\u5929\u5929\u6c14\u5f88\u597d",
    ]
    assert asr_module._merge_chunks(parts) == "\u590f\u5929\u5230\u4e86\u4eca\u5929\u5929\u6c14\u5f88\u597d"
    assert asr_module._merge_chunks(["\u4e0b", "\u4eca\u5929\u5929\u6c14\u5f88\u597d"]) == (
        "\u4e0b\u4eca\u5929\u5929\u6c14\u5f88\u597d"
    )


def test_transcribe_local_chunked_merge(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    model_dir = tmp_path / "model"
    model_dir.mkdir(parents=True, exist_ok=True)
    (model_dir / "model.int8.onnx").write_bytes(b"m")
    (model_dir / "tokens.txt").write_bytes(b"t")
    wav_path = tmp_path / "long.wav"
    with wave.open(str(wav_path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        samples = array.array("h", (0 for _ in range(16000 * 60)))
        for start in (0, 16000 * 58):
            for i in range(16000 * 2):
                samples[start + i] = int(5000 * math.sin(2 * math.pi * 440 * i / 16000))
        wav_file.writeframes(samples.tobytes())

    calls = {"count": 0}

    class FakeResult:
        def __init__(self, text: str):
            self.text = text

    class FakeStream:
        def __init__(self):
            self.waveform: list[float] = []
            calls["count"] += 1

        def accept_waveform(self, sample_rate: int, waveform) -> None:
            self.waveform = list(waveform)

    class FakeRecognizer:
        def create_stream(self):
            return FakeStream()

        def decode_stream(self, stream) -> None:
            if any(abs(sample) > 0.05 for sample in stream.waveform[:16000]):
                stream.result = FakeResult("\u4e0b")
            elif any(abs(sample) > 0.05 for sample in stream.waveform):
                stream.result = FakeResult(
                    "\u4eca\u5929\u5929\u6c14\u5f88\u597d\u6211\u4eec\u53bb\u516c\u56ed\u6563\u6b65"
                )
            else:
                stream.result = FakeResult("")

    class FakeOfflineRecognizer:
        @classmethod
        def from_paraformer(cls, **kwargs):
            return FakeRecognizer()

    fake_module = types.ModuleType("sherpa_onnx")
    fake_module.OfflineRecognizer = FakeOfflineRecognizer
    monkeypatch.setitem(sys.modules, "sherpa_onnx", fake_module)

    transcript = asr_module.transcribe_local(wav_path, model_dir)
    assert transcript == (
        "\u4e0b\u4eca\u5929\u5929\u6c14\u5f88\u597d\u6211\u4eec\u53bb\u516c\u56ed\u6563\u6b65"
    )
    assert calls["count"] >= 4


def test_transcribe_local_punctuation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    model_dir = tmp_path / "model"
    model_dir.mkdir(parents=True, exist_ok=True)
    (model_dir / "model.int8.onnx").write_bytes(b"m")
    (model_dir / "tokens.txt").write_bytes(b"t")
    punct_dir = model_dir.parent / asr_module.PUNCT_MODEL_NAME
    punct_dir.mkdir(parents=True, exist_ok=True)
    (punct_dir / "model.int8.onnx").write_bytes(b"p")
    wav_path = tmp_path / "in.wav"
    _make_wav(wav_path)

    class FakeResult:
        text = "LOCAL_TEXT"

    class FakeStream:
        result = FakeResult()

        def accept_waveform(self, sample_rate: int, waveform) -> None:
            pass

    class FakeRecognizer:
        def create_stream(self):
            return FakeStream()

        def decode_stream(self, stream) -> None:
            pass

    class FakeOfflineRecognizer:
        @classmethod
        def from_paraformer(cls, **kwargs):
            return FakeRecognizer()

    class FakePunctuation:
        def __init__(self, config):
            self.config = config

        def add_punctuation(self, text: str) -> str:
            return text + "\u3002"

    class FakePunctuationConfig:
        def __init__(self, model):
            self.model = model

    class FakePunctuationModelConfig:
        def __init__(self, ct_transformer: str, num_threads: int = 1, debug: bool = False):
            self.ct_transformer = ct_transformer
            assert ct_transformer == str(punct_dir / "model.int8.onnx")

    fake_module = types.ModuleType("sherpa_onnx")
    fake_module.OfflineRecognizer = FakeOfflineRecognizer
    fake_module.OfflinePunctuation = FakePunctuation
    fake_module.OfflinePunctuationConfig = FakePunctuationConfig
    fake_module.OfflinePunctuationModelConfig = FakePunctuationModelConfig
    monkeypatch.setitem(sys.modules, "sherpa_onnx", fake_module)

    assert asr_module.transcribe_local(wav_path, model_dir) == "LOCAL_TEXT\u3002"


def test_is_abnormally_short(tmp_path: Path) -> None:
    short_wav = tmp_path / "short.wav"
    with wave.open(str(short_wav), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes(b"\x00\x00" * (16000 * 5))
    assert asr_module.is_abnormally_short("LOCAL_TEXT", short_wav) is False
    assert asr_module.is_abnormally_short("\u4e0b", short_wav) is False

    long_wav = tmp_path / "long.wav"
    with wave.open(str(long_wav), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes(b"\x00\x00" * (16000 * 40))
    assert asr_module.is_abnormally_short("\u4e0b", long_wav) is True
    assert asr_module.is_abnormally_short("\u4eca\u5929\u5929\u6c14\u5f88\u597d", long_wav) is False

    assert asr_module.is_abnormally_short("\u4e0b", tmp_path / "missing.wav") is False


def test_download_local_model(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    payload = _make_model_tar()

    def fake_stream(method: str, url: str, **kwargs):
        assert url in (asr_module.MODEL_DOWNLOAD_URL, asr_module.PUNCT_DOWNLOAD_URL)
        return FakeStreamResponse(payload)

    monkeypatch.setattr(httpx, "stream", fake_stream)
    model_dir = tmp_path / "asr" / asr_module.MODEL_DIR_NAME
    asr_module.download_local_model(model_dir)
    assert (model_dir / "model.int8.onnx").is_file()
    assert (model_dir / "tokens.txt").is_file()
    punct_dir = model_dir.parent / asr_module.PUNCT_MODEL_NAME
    assert (punct_dir / "model.int8.onnx").is_file()
    assert asr_module.local_model_status(model_dir)["ready"] is True


def test_asr_model_api(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get("/asr/model", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["provider"] == "bailian"
    assert body["asr_model"] == "paraformer-realtime-v2"
    assert body["asr_models"] == [
        "paraformer-realtime-v2",
        "paraformer-realtime-v1",
    ]
    assert body["ready"] is False
    assert "model_dir" in body


def test_asr_model_download_api(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_download(model_dir: Path):
        model_dir.mkdir(parents=True, exist_ok=True)
        (model_dir / "model.int8.onnx").write_bytes(b"m")
        (model_dir / "tokens.txt").write_bytes(b"t")
        punct_dir = model_dir.parent / asr_module.PUNCT_MODEL_NAME
        punct_dir.mkdir(parents=True, exist_ok=True)
        (punct_dir / "model.int8.onnx").write_bytes(b"p")
        return model_dir

    monkeypatch.setattr(asr_module, "download_local_model", fake_download)
    response = client.post("/asr/model/download", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["ready"] is True
