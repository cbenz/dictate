"""Controllers — state machines for batch and realtime dictation.

Plugins (transcription backend, injector, notifier) are loaded dynamically
from the user config directory at runtime. See plugin_loader.py.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from abc import ABC, abstractmethod
from enum import Enum, auto
from typing import Any, override

from . import config as cfg
from . import plugin_loader
from .sound_recorder import SoundRecorder, resolve_device

__all__ = [
    "BaseController",
    "BatchController",
    "RealtimeController",
    "State",
    "create_controller",
]


logger = logging.getLogger(__name__)


class State(Enum):
    IDLE = auto()
    RECORDING = auto()
    TRANSCRIBING = auto()
    RECORDING_TRANSCRIBING = auto()
    ERROR = auto()


# ── Helpers ───────────────────────────────────────────────────


def _load_feedback_notifiers(conf: cfg.AppConfig) -> list[Any]:
    """Load all configured feedback notifiers."""
    notifiers = []
    for name in conf.feedback_notifier.plugins:
        try:
            n = plugin_loader.load_plugin("feedback_notifier", name, conf.model_dump())
            notifiers.append(n)
        except Exception as exc:
            logger.warning("Failed to load notifier '%s': %s", name, exc)
    return notifiers


def _load_transcription_injector(conf: cfg.AppConfig):
    section = conf.transcription_injector
    name = section.plugin
    return plugin_loader.load_plugin("transcription_injector", name, section.model_dump())


def _load_transcription_backend(conf: cfg.AppConfig):
    section = conf.transcription_backend
    name = section.plugin
    return plugin_loader.load_plugin("transcription_backend", name, section.model_dump())


# ═════════════════════════════════════════════════════════════
#  BASE
# ═════════════════════════════════════════════════════════════


class BaseController(ABC):
    """Common state machine interface."""

    def __init__(self) -> None:
        self.state: State = State.IDLE
        self._backend: Any = None
        self._injector: Any = None
        self._notifiers: list[Any] = []
        self._recorder: SoundRecorder | None = None
        self._transcribe_task: asyncio.Task[Any] | None = None
        self._config = cfg.load()

    @abstractmethod
    @override
    async def start(self) -> dict[str, Any]:
        # Added manually above
        raise NotImplementedError

    @abstractmethod
    @override
    async def stop(self) -> dict[str, Any]:
        # Added manually above
        raise NotImplementedError

    async def cancel(self) -> dict[str, Any]:
        if self.state == State.IDLE:
            return {"status": "ok", "message": "Already idle"}
        if self._transcribe_task and not self._transcribe_task.done():
            self._transcribe_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._transcribe_task
        if self._recorder:
            self._recorder.stop()
        await self._cleanup()
        self.state = State.IDLE
        await self._notify("Dictation cancelled")
        logger.info("State → IDLE (cancelled)")
        return {"status": "ok", "message": "Cancelled"}

    async def flush(self) -> dict[str, Any]:
        """Flush pending transcription text (commit current segment)."""
        if self._backend is not None and hasattr(self._backend, "flush"):
            try:
                await self._backend.flush()
                return {"status": "ok", "message": "Flushed"}
            except Exception as exc:
                return {"status": "error", "message": str(exc)}
        return {"status": "ok", "message": "Nothing to flush"}

    async def ping(self) -> dict[str, Any]:
        return {"status": "ok", "message": "pong", "state": self.state.name}

    async def wait_state(self) -> dict[str, Any]:
        """Block until the state changes or timeout (30s)."""
        current = self.state
        for _ in range(10):  # 10 × 0.2s = 2s
            if self.state != current:
                return {"status": "ok", "message": "state changed", "state": self.state.name}
            await asyncio.sleep(0.2)
        return {"status": "ok", "message": "timeout", "state": self.state.name}

    async def acknowledge_error(self) -> dict[str, Any]:
        if self.state != State.ERROR:
            return {"status": "ok", "message": "Not in error state"}
        self.state = State.IDLE
        logger.info("State → IDLE (error acknowledged)")
        return {"status": "ok", "message": "Error acknowledged"}

    async def toggle(self) -> dict[str, Any]:
        if self.state == State.IDLE:
            return await self.start()
        if self.state in (State.RECORDING, State.TRANSCRIBING, State.RECORDING_TRANSCRIBING):
            return await self.stop()
        return {"status": "error", "message": f"Cannot toggle from state {self.state.name}"}

    async def _notify(self, message: str, urgency: str = "normal") -> None:
        for notifier in self._notifiers:
            try:
                await notifier.notify(message, urgency)
            except Exception:
                logger.exception("Notifier failed")

    async def _cleanup(self) -> None:
        if self._backend is not None:
            try:
                await self._backend.close()
            except Exception:
                logger.exception("Backend close error")
            self._backend = None
        self._injector = None
        self._recorder = None

    async def shutdown(self) -> None:
        if self.state != State.IDLE:
            with contextlib.suppress(asyncio.CancelledError):
                await self.cancel()
        await self._cleanup()
        logger.info("Controller shut down")


# ═════════════════════════════════════════════════════════════
#  REALTIME
# ═════════════════════════════════════════════════════════════


class RealtimeController(BaseController):
    """Recording + transcribing run concurrently."""

    @override
    async def start(self) -> dict[str, Any]:
        # Added manually above
        if self.state == State.RECORDING_TRANSCRIBING:
            return {"status": "ok", "message": "Already recording and transcribing"}

        try:
            device = resolve_device(self._config)
            self._recorder = SoundRecorder(device=device)
            self._recorder.start()

            self._notifiers = _load_feedback_notifiers(self._config)
            self._backend = _load_transcription_backend(self._config)
            self._injector = _load_transcription_injector(self._config)

            self.state = State.RECORDING_TRANSCRIBING
            self._transcribe_task = asyncio.create_task(self._transcribe_loop())
            await self._notify("🎤 Dictating…")
            logger.info("State → RECORDING_TRANSCRIBING")
            return {"status": "ok", "message": "Recording started"}
        except Exception as exc:
            self.state = State.ERROR
            await self._notify(f"❌ Recording failed: {exc}", urgency="critical")
            logger.exception("Start failed")
            return {"status": "error", "message": str(exc)}

    @override
    async def stop(self) -> dict[str, Any]:
        # Added manually above
        if self.state != State.RECORDING_TRANSCRIBING:
            return {"status": "ok", "message": "Already idle"}
        if self._recorder:
            self._recorder.stop()
        logger.info("Recording stopped — transcription finishes in background")
        return {"status": "ok", "message": "Transcribing in background"}

    async def _transcribe_loop(self) -> None:
        try:
            async for text in self._backend.transcribe(self._recorder.async_generator()):
                if text.startswith("[partial] "):
                    continue
                try:
                    logger.debug("Injection: %d chars, preview=%s", len(text), text[:80])
                    await self._injector.inject(text)
                except Exception as exc:
                    logger.exception("Injection failed: %s", exc)
                    await self._notify(f"❌ Injection failed: {exc}", urgency="critical")
                    self.state = State.ERROR
                    return
            logger.info("Transcription stream ended")
        except asyncio.CancelledError:
            logger.info("Transcription cancelled")
            raise
        except Exception as exc:
            logger.exception("Transcription failed")
            await self._notify(f"❌ Transcription failed: {exc}", urgency="critical")
            self.state = State.ERROR

        await self._cleanup()
        self.state = State.IDLE
        await self._notify("✅ Dictation complete")
        logger.info("State → IDLE (complete)")


# ═════════════════════════════════════════════════════════════
#  BATCH
# ═════════════════════════════════════════════════════════════


class BatchController(BaseController):
    """Record first, then transcribe the full audio."""

    @override
    async def start(self) -> dict[str, Any]:
        # Added manually above
        if self.state == State.RECORDING:
            return {"status": "ok", "message": "Already recording"}
        try:
            device = resolve_device(self._config)
            self._recorder = SoundRecorder(device=device)
            self._recorder.start()
            self._notifiers = _load_feedback_notifiers(self._config)
            self.state = State.RECORDING
            await self._notify("🎤 Recording…")
            logger.info("State → RECORDING")
            return {"status": "ok", "message": "Recording started"}
        except Exception as exc:
            self.state = State.ERROR
            await self._notify(f"❌ Recording failed: {exc}", urgency="critical")
            logger.exception("Start failed")
            return {"status": "error", "message": str(exc)}

    @override
    async def stop(self) -> dict[str, Any]:
        # Added manually above
        if self.state != State.RECORDING:
            return {"status": "ok", "message": "Already idle"}
        if self._recorder:
            self._recorder.stop()

        audio_chunks: list[bytes] = []
        while True:
            chunk = self._recorder.read_chunk()
            if chunk is None:
                break
            audio_chunks.append(chunk)

        self.state = State.TRANSCRIBING
        await self._notify("⏳ Transcribing…")
        logger.info("State → TRANSCRIBING")

        self._backend = _load_transcription_backend(self._config)
        self._injector = _load_transcription_injector(self._config)
        self._transcribe_task = asyncio.create_task(self._transcribe_full(audio_chunks))

        try:
            await self._transcribe_task
        except Exception as exc:
            logger.exception("Batch transcription failed")
            self.state = State.ERROR
            await self._notify(f"❌ Transcription failed: {exc}", urgency="critical")
            return {"status": "error", "message": str(exc)}

        await self._cleanup()
        self.state = State.IDLE
        await self._notify("✅ Dictation complete")
        logger.info("State → IDLE (complete)")
        return {"status": "ok", "message": "Finished"}

    async def _transcribe_full(self, chunks: list[bytes]) -> None:
        async def chunk_stream():
            for c in chunks:
                yield c

        async for text in self._backend.transcribe(chunk_stream()):
            if text.startswith("[partial] "):
                continue
            try:
                await self._injector.inject(text)
            except Exception as exc:
                logger.exception("Injection failed: %s", exc)
                await self._notify(f"❌ Injection failed: {exc}", urgency="critical")
                raise


# ═════════════════════════════════════════════════════════════
#  FACTORY
# ═════════════════════════════════════════════════════════════


def create_controller() -> BaseController:
    """Instantiate the controller matching the configured mode."""
    conf = cfg.load()
    mode = conf.transcription_backend.mode
    if mode == "batch":
        return BatchController()
    return RealtimeController()
