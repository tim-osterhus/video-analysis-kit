from __future__ import annotations

import json
from pathlib import Path

from video_intelligence.local_transcription import (
    format_timestamp,
    render_transcript,
    write_transcript_json,
)


def test_timestamp_format():
    assert format_timestamp(65.1) == "01:05"
    assert format_timestamp(3661) == "1:01:01"


def test_render_and_write(tmp_path: Path):
    segments = [{"start": 2.0, "end": 3.0, "text": "A claim"}]
    assert render_transcript(segments) == "[00:02] A claim"
    output = tmp_path / "transcript.json"
    write_transcript_json(output, segments, {"backend": "mlx-whisper"})
    payload = json.loads(output.read_text())
    assert payload["segments"] == segments
    assert payload["metadata"]["backend"] == "mlx-whisper"


def test_faster_whisper_is_local_and_normalizes_segments(tmp_path, monkeypatch, capsys):
    import sys
    from types import SimpleNamespace

    from video_intelligence.local_transcription import transcribe_local

    model = tmp_path / "model"
    model.mkdir()

    class WhisperModel:
        def __init__(self, model_path, *, device, local_files_only):
            assert model_path == str(model)
            assert device == "cpu"
            assert local_files_only is True
            print("loading model")

        def transcribe(self, audio, *, word_timestamps):
            assert word_timestamps is False

            def segments():
                print("decoding", file=sys.stderr)
                yield SimpleNamespace(start=0.123, end=1.256, text="  First  ")
                yield SimpleNamespace(start=2, end=3, text="   ")

            return segments(), SimpleNamespace(language="en")

    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=WhisperModel))
    log = tmp_path / "transcription.log"
    segments, metadata = transcribe_local(
        tmp_path / "audio.wav",
        model=str(model),
        cache_root=tmp_path / "cache",
        backend="faster-whisper",
        device="cpu",
        offset_seconds=10,
        log_path=log,
    )
    assert segments == [{"start": 10.12, "end": 11.26, "text": "First"}]
    assert metadata["backend"] == "faster-whisper"
    assert metadata["language"] == "en"
    assert metadata["segment_count"] == 1
    assert "decoding" in log.read_text()
    assert "loading model" in log.read_text()
    assert capsys.readouterr().out == ""


def test_mlx_defaults_preserve_normalization_and_failure_log(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace

    import pytest

    from video_intelligence.local_transcription import LocalTranscriptionError, transcribe_local

    model = tmp_path / "model"
    model.mkdir()

    def transcribe(audio, **kwargs):
        assert kwargs["path_or_hf_repo"] == str(model)
        print("backend failure details", file=sys.stderr)
        raise RuntimeError("decode failed")

    monkeypatch.setitem(sys.modules, "mlx_whisper", SimpleNamespace(transcribe=transcribe))
    log = tmp_path / "failure.log"
    with pytest.raises(LocalTranscriptionError, match="decode failed"):
        transcribe_local(
            tmp_path / "audio.wav", model=str(model), cache_root=tmp_path / "cache", log_path=log
        )
    assert "backend failure details" in log.read_text()


def test_missing_local_model_rejected_before_backend_import(tmp_path):
    import pytest

    from video_intelligence.local_transcription import LocalTranscriptionError, transcribe_local

    with pytest.raises(LocalTranscriptionError, match="local model"):
        transcribe_local(
            tmp_path / "audio.wav",
            model="Systran/faster-whisper-small",
            cache_root=tmp_path / "cache",
            backend="faster-whisper",
        )


def test_worker_emits_only_json_and_forwards_cuda(tmp_path, monkeypatch, capsys):
    import sys
    from types import SimpleNamespace

    from video_intelligence.transcription_worker import main

    model = tmp_path / "model"
    model.mkdir()

    class WhisperModel:
        def __init__(self, path, *, device, local_files_only):
            assert device == "cuda"
            assert local_files_only is True
            print("backend initialization")

        def transcribe(self, audio, *, word_timestamps):
            return iter([SimpleNamespace(start=1.1, end=2.2, text=" hello ")]), SimpleNamespace(
                language="en"
            )

    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=WhisperModel))
    log = tmp_path / "worker.log"
    assert (
        main(
            [
                str(tmp_path / "audio.wav"),
                "--backend",
                "faster-whisper",
                "--device",
                "cuda",
                "--model",
                str(model),
                "--cache-root",
                str(tmp_path / "cache"),
                "--offset-seconds",
                "4",
                "--log",
                str(log),
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["segments"] == [{"start": 5.1, "end": 6.2, "text": "hello"}]
    assert "backend initialization" in log.read_text()


def test_mlx_default_success_preserves_metadata_and_offsets(tmp_path, monkeypatch, capsys):
    import sys
    from types import SimpleNamespace

    from video_intelligence.local_transcription import transcribe_local

    model = tmp_path / "model"
    model.mkdir()

    def transcribe(audio, *, path_or_hf_repo, word_timestamps, verbose):
        assert path_or_hf_repo == str(model)
        assert word_timestamps is False
        assert verbose is False
        print("loading")
        return {
            "language": "fr",
            "segments": [{"start": 1.234, "end": 2.345, "text": " Bonjour "}, {"text": " "}],
        }

    monkeypatch.setitem(sys.modules, "mlx_whisper", SimpleNamespace(transcribe=transcribe))
    segments, metadata = transcribe_local(
        tmp_path / "audio.wav", model=str(model), cache_root=tmp_path / "cache", offset_seconds=4
    )
    assert segments == [{"start": 5.23, "end": 6.35, "text": "Bonjour"}]
    assert metadata["backend"] == "mlx-whisper"
    assert metadata["language"] == "fr"
    assert metadata["segment_count"] == 1
    assert capsys.readouterr().out == ""
