"""Audio capture via sounddevice."""

from __future__ import annotations

import asyncio
import logging
import queue
import threading
from typing import TYPE_CHECKING, Any

import sounddevice as sd

if TYPE_CHECKING:
    from .config import AppConfig

__all__ = [
    "CHANNELS",
    "CHUNK_SIZE",
    "SAMPLE_RATE",
    "SoundRecorder",
    "list_devices",
    "resolve_device",
]


logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000
CHANNELS = 1
CHUNK_DURATION = 0.5  # seconds per chunk
CHUNK_SIZE = int(SAMPLE_RATE * CHUNK_DURATION)


class SoundRecorder:
    """Capture audio from a microphone in a background thread.

    Yields raw PCM int16 chunks via an async generator.
    """

    def __init__(self, device: int | str | None = None) -> None:
        self.device = device
        self._queue: queue.Queue = queue.Queue()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def _callback(self, indata: Any, frames: int, time_info: Any, status: sd.CallbackFlags) -> None:
        """Sounddevice callback — put audio chunk into thread-safe queue."""
        if status:
            logger.warning("Audio callback status: %s", status)
        self._queue.put(indata.copy())

    def _run(self) -> None:
        """Thread target: open an InputStream and block until stopped."""
        try:
            with sd.InputStream(
                device=self.device,
                channels=CHANNELS,
                samplerate=SAMPLE_RATE,
                blocksize=CHUNK_SIZE,
                callback=self._callback,
                dtype="int16",
            ):
                self._stop_event.wait()
        except Exception as exc:
            logger.exception("SoundRecorder error: %s", exc)
            self._queue.put(exc)  # signal error to consumer

    def start(self) -> None:
        """Start capturing audio in a daemon thread."""
        if self._thread and self._thread.is_alive():
            logger.warning("SoundRecorder already running")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info(
            "Recording started (device=%s, %d Hz, %d channels)",
            self.device or "default",
            SAMPLE_RATE,
            CHANNELS,
        )

    def stop(self) -> None:
        """Stop capturing audio."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2)
        logger.info("Recording stopped")

    def read_chunk(self) -> bytes | None:
        """Read one audio chunk (non-blocking). Returns None if empty."""
        try:
            item = self._queue.get_nowait()
            if isinstance(item, Exception):
                raise item
            return item.tobytes()
        except queue.Empty:
            return None

    async def async_generator(self) -> AsyncGenerator[bytes, None]:
        """Async generator yielding PCM int16 chunks."""
        while not self._stop_event.is_set():
            chunk = self.read_chunk()
            if chunk is not None:
                yield chunk
            else:
                await asyncio.sleep(0.01)


def list_devices() -> str:
    """Return a formatted list of audio devices."""
    devices = sd.query_devices()
    lines = ["Available audio devices:\n"]
    lines.append(str(devices))
    default = sd.default.device[0]
    lines.append(f"\nDefault input device: {default} — {devices[default]['name']}")
    return "\n".join(lines)


def resolve_device(cfg: AppConfig) -> int | str | None:
    """Resolve the configured device to an index or name."""
    source = cfg.audio.default_source
    if not source:
        return None
    try:
        return int(source)
    except ValueError, TypeError:
        return source
