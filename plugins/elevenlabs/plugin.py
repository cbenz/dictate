"""ElevenLabs Scribe Realtime — WebSocket-based streaming transcription."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

__all__ = ["create_transcription_backend"]


logger = logging.getLogger(__name__)


SAMPLE_RATE = 16000


class ElevenLabsBackend:
    def __init__(self, api_key: str) -> None:
        from elevenlabs import ElevenLabs

        self._client = ElevenLabs(api_key=api_key)
        self._connection = None
        self._committed_queue: asyncio.Queue[str] = asyncio.Queue()
        self._error_event = asyncio.Event()
        self._error_message: str | None = None

    async def transcribe(self, audio_stream: AsyncIterator[bytes]) -> AsyncIterator[str]:
        from elevenlabs import AudioFormat, CommitStrategy, RealtimeAudioOptions, RealtimeEvents

        logger.info("Connecting to ElevenLabs Realtime STT...")
        self._connection = await self._client.speech_to_text.realtime.connect(
            RealtimeAudioOptions(
                model_id="scribe_v2_realtime",
                audio_format=AudioFormat.PCM_16000,
                sample_rate=SAMPLE_RATE,
                commit_strategy=CommitStrategy.VAD,
                include_timestamps=False,
                vad_silence_threshold_secs=0.3,
                min_speech_duration_ms=50,
                min_silence_duration_ms=50,
            )
        )
        logger.info("Connected to ElevenLabs")

        self._connection.on(RealtimeEvents.COMMITTED_TRANSCRIPT, self._on_committed)
        self._connection.on(RealtimeEvents.ERROR, self._on_error)

        send_done = False
        chunk_count = 0

        async def send_loop() -> None:
            nonlocal send_done, chunk_count
            try:
                async for chunk in audio_stream:
                    chunk_count += 1
                    chunk_b64 = base64.b64encode(chunk).decode("utf-8")
                    logger.debug("Sending audio chunk #%d (%d bytes)", chunk_count, len(chunk_b64))
                    await self._connection.send({"audio_base_64": chunk_b64, "sample_rate": SAMPLE_RATE})
                logger.debug("Audio stream ended — send_loop exiting")
            except asyncio.CancelledError:
                logger.debug("send_loop cancelled after %d chunks", chunk_count)
            except Exception as e:
                logger.exception("Send loop error at chunk #%d: %s", chunk_count, e)
            send_done = True

        send_task = asyncio.create_task(send_loop())
        try:
            logger.debug("Entering receive loop")
            while True:
                if self._error_event.is_set():
                    logger.debug("Error event detected, raising")
                    raise RuntimeError(self._error_message or "Transcription error")
                while not self._committed_queue.empty():
                    text = self._committed_queue.get_nowait()
                    if text.strip():
                        logger.debug("Yielding committed text: %s", text[:60])
                        yield text
                if send_done:
                    logger.debug("Send loop done and queue empty, exiting receive loop")
                    break
                await asyncio.sleep(0.05)
            logger.debug("Transcribe generator exiting")
        finally:
            send_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await send_task
            logger.debug("send_loop fully stopped")

    def _on_committed(self, data: dict) -> None:
        text = data.get("text", "")
        if text.strip():
            self._committed_queue.put_nowait(text)

    def _on_error(self, error) -> None:
        msg = str(error)
        logger.error("ElevenLabs error: %s", msg)
        self._error_message = msg
        self._error_event.set()

    async def close(self) -> None:
        if self._connection:
            await self._connection.close()
        logger.info("ElevenLabs connection closed")

    async def flush(self) -> None:
        """Force the current segment to commit immediately."""
        if self._connection:
            try:
                await self._connection.commit()
                logger.debug("Flush: commit() called")
            except Exception as exc:
                logger.warning("Flush failed: %s", exc)
        else:
            logger.warning("Flush: no active connection")


def create_transcription_backend(config: dict) -> ElevenLabsBackend:
    api_key = config.get("elevenlabs", {}).get("api_key") or os.getenv("ELEVENLABS_API_KEY")
    if not api_key:
        msg = "ELEVENLABS_API_KEY not found. Set it in config.toml under [elevenlabs] or in .env"
        raise RuntimeError(msg)
    return ElevenLabsBackend(api_key=api_key)
