"""Protocols for pluggable plugins.

Each plugin is a Python module installed in the user's config directory
at ~/.config/tiny-dictate/plugins/<name>/plugin.py.

The module must expose a `create(config: dict) -> <Protocol>` factory function.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


__all__ = [
    "FeedbackNotifier",
    "TranscriptionBackend",
    "TranscriptionInjector",
]


@runtime_checkable
class TranscriptionBackend(Protocol):
    """Streaming speech-to-text backend."""

    async def transcribe(self, audio_stream: AsyncIterator[bytes]) -> AsyncIterator[str]:
        """Transcribe audio chunks, yielding text fragments."""
        ...

    async def close(self) -> None:
        """Release resources."""
        ...


@runtime_checkable
class TranscriptionInjector(Protocol):
    """Injects transcribed text into the user environment."""

    async def inject(self, text: str) -> None:
        """Place text into the active context (clipboard, file, etc.)."""
        ...


@runtime_checkable
class FeedbackNotifier(Protocol):
    """Shows user feedback (notifications, tray balloon, etc.)."""

    async def notify(self, message: str, urgency: str = "normal") -> None:
        """Display a message to the user."""
        ...
