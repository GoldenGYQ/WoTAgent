from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from wotagent.api.routers.speech import create_speech_router
from wotagent.speech import SpeechTranscription


class FakeTranscriber:
    def __init__(self) -> None:
        self.received: bytes | None = None

    def transcribe(self, audio_path: str | Path) -> SpeechTranscription:
        self.received = Path(audio_path).read_bytes()
        return SpeechTranscription(
            text="打开 living room 的灯",
            language="zh",
            language_probability=0.96,
            duration_seconds=1.8,
            processing_ms=42,
            model="fake-base",
        )


def _client(transcriber: FakeTranscriber) -> TestClient:
    app = FastAPI()
    app.include_router(create_speech_router(lambda: transcriber))
    return TestClient(app)


def test_speech_transcription_accepts_mobile_webm_upload() -> None:
    transcriber = FakeTranscriber()
    response = _client(transcriber).post(
        "/api/v2/speech/transcriptions",
        files={"audio": ("voice.webm", b"fake-webm", "audio/webm")},
    )

    assert response.status_code == 200
    assert response.json() == {
        "text": "打开 living room 的灯",
        "language": "zh",
        "language_probability": 0.96,
        "duration_seconds": 1.8,
        "processing_ms": 42,
        "model": "fake-base",
    }
    assert transcriber.received == b"fake-webm"


def test_speech_transcription_rejects_non_audio_upload() -> None:
    response = _client(FakeTranscriber()).post(
        "/api/v2/speech/transcriptions",
        files={"audio": ("note.txt", b"not audio", "text/plain")},
    )

    assert response.status_code == 415
    assert response.json()["detail"] == "unsupported audio type: text/plain"


def test_speech_transcription_enforces_upload_limit(monkeypatch) -> None:
    monkeypatch.setenv("SPEECH_MAX_AUDIO_BYTES", "4")
    response = _client(FakeTranscriber()).post(
        "/api/v2/speech/transcriptions",
        files={"audio": ("voice.webm", b"12345", "audio/webm")},
    )

    assert response.status_code == 413
