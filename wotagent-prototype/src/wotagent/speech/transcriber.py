from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class SpeechTranscriptionError(RuntimeError):
    """Raised when the local model cannot transcribe an audio file."""


class SpeechTranscription(BaseModel):
    text: str
    language: str | None = None
    language_probability: float | None = Field(default=None, ge=0, le=1)
    duration_seconds: float = Field(default=0, ge=0)
    processing_ms: int = Field(default=0, ge=0)
    model: str


class SpeechTranscriber:
    """Lazily loaded faster-whisper transcriber optimized for local CPU use."""

    def __init__(
        self,
        *,
        model_size: str | None = None,
        device: str | None = None,
        compute_type: str | None = None,
        initial_prompt: str | None = None,
    ) -> None:
        self.model_size = model_size or os.getenv("SPEECH_MODEL_SIZE", "base")
        self.device = device or os.getenv("SPEECH_DEVICE", "cpu")
        self.compute_type = compute_type or os.getenv(
            "SPEECH_COMPUTE_TYPE",
            "int8",
        )
        self.initial_prompt = initial_prompt or os.getenv(
            "SPEECH_INITIAL_PROMPT",
            "WoTAgent, IoT, MCP, Thing Description, TD, RBAC, "
            "张爷爷, 李奶奶, 健康监测, 智能家居",
        )
        self._model: Any | None = None
        self._model_lock = threading.Lock()
        self._transcribe_lock = threading.Lock()

    def _get_model(self) -> Any:
        if self._model is not None:
            return self._model
        with self._model_lock:
            if self._model is not None:
                return self._model
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise SpeechTranscriptionError(
                    "faster-whisper is not installed"
                ) from exc
            try:
                try:
                    self._model = WhisperModel(
                        self.model_size,
                        device=self.device,
                        compute_type=self.compute_type,
                        local_files_only=True,
                    )
                except Exception:
                    # Only contact the model hub when this machine has not
                    # downloaded the configured model yet.
                    self._model = WhisperModel(
                        self.model_size,
                        device=self.device,
                        compute_type=self.compute_type,
                    )
            except Exception as exc:
                raise SpeechTranscriptionError(
                    f"unable to load speech model '{self.model_size}': {exc}"
                ) from exc
        return self._model

    def transcribe(self, audio_path: str | Path) -> SpeechTranscription:
        started = time.perf_counter()
        model = self._get_model()
        try:
            # CTranslate2 inference on a shared model is serialized to keep the
            # experiment server's CPU and memory use predictable.
            with self._transcribe_lock:
                segments, info = model.transcribe(
                    str(audio_path),
                    language=None,
                    multilingual=True,
                    beam_size=3,
                    vad_filter=True,
                    condition_on_previous_text=False,
                    initial_prompt=self.initial_prompt,
                )
                text = "".join(segment.text for segment in segments).strip()
        except Exception as exc:
            raise SpeechTranscriptionError(
                f"audio transcription failed: {exc}"
            ) from exc

        return SpeechTranscription(
            text=text,
            language=getattr(info, "language", None),
            language_probability=getattr(
                info,
                "language_probability",
                None,
            ),
            duration_seconds=float(getattr(info, "duration", 0) or 0),
            processing_ms=round((time.perf_counter() - started) * 1000),
            model=self.model_size,
        )
