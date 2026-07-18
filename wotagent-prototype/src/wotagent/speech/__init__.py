"""Local speech-to-text capability for human input."""

from .transcriber import (
    SpeechTranscriber,
    SpeechTranscription,
    SpeechTranscriptionError,
)

__all__ = [
    "SpeechTranscriber",
    "SpeechTranscription",
    "SpeechTranscriptionError",
]
