from __future__ import annotations

import asyncio
import os
import tempfile
from collections.abc import Callable
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile, status

from ...speech import (
    SpeechTranscriber,
    SpeechTranscription,
    SpeechTranscriptionError,
)

_CONTENT_SUFFIXES = {
    "audio/webm": ".webm",
    "video/webm": ".webm",
    "audio/mp4": ".m4a",
    "audio/mpeg": ".mp3",
    "audio/ogg": ".ogg",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
}


def _bool_env(key: str, default: bool = True) -> bool:
    value = os.getenv(key)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def create_speech_router(
    get_transcriber: Callable[[], SpeechTranscriber],
) -> APIRouter:
    router = APIRouter(prefix="/api/v2/speech", tags=["speech"])

    @router.post(
        "/transcriptions",
        response_model=SpeechTranscription,
    )
    async def create_transcription(
        audio: UploadFile = File(...),
    ) -> SpeechTranscription:
        if not _bool_env("SPEECH_TRANSCRIPTION_ENABLED", True):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="speech transcription is disabled",
            )

        content_type = (audio.content_type or "").split(";", 1)[0].lower()
        if content_type not in _CONTENT_SUFFIXES:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail=f"unsupported audio type: {content_type or 'unknown'}",
            )

        max_bytes = int(os.getenv("SPEECH_MAX_AUDIO_BYTES", str(8 * 1024 * 1024)))
        payload = await audio.read(max_bytes + 1)
        if not payload:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="audio payload is empty",
            )
        if len(payload) > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=f"audio payload exceeds {max_bytes} bytes",
            )

        path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                suffix=_CONTENT_SUFFIXES[content_type],
                delete=False,
            ) as temporary:
                temporary.write(payload)
                path = Path(temporary.name)
            return await asyncio.to_thread(
                get_transcriber().transcribe,
                path,
            )
        except SpeechTranscriptionError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            ) from exc
        finally:
            if path is not None:
                path.unlink(missing_ok=True)
            await audio.close()

    return router


__all__ = ["create_speech_router"]
