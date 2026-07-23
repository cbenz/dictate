"""Type injector — types text character by character via wtype/ydotool.

Uses wtype (Wayland virtual-keyboard protocol) if available, otherwise ydotool type.
Both simulate real keystrokes, working in any application without paste-key guesswork.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess

__all__ = ["create_transcription_injector"]


logger = logging.getLogger(__name__)


class TypeInjector:
    """Injects text by typing it character by character."""

    def __init__(self) -> None:
        self._cmd = self._detect_cmd()
        logger.debug("TypeInjector initialized: cmd=%s", self._cmd)

    def _detect_cmd(self) -> list[str] | None:
        if shutil.which("wtype"):
            logger.debug("Detected type tool: wtype")
            return ["wtype", "-"]  # "-" reads text from stdin
        if shutil.which("ydotool"):
            logger.debug("Detected type tool: ydotool type")
            return ["ydotool", "type"]
        logger.warning("No type tool found (install wtype or ydotool)")
        return None

    async def inject(self, text: str) -> None:

        if self._cmd is None:
            logger.warning("Cannot type: no wtype or ydotool available")
            return

        logger.info("Typing: %s", text[:60])
        proc = await asyncio.create_subprocess_exec(
            *self._cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        logger.debug("Type subprocess started (pid=%d): %s", proc.pid, self._cmd)
        _stdout, _stderr = await proc.communicate(input=text.encode("utf-8"))
        logger.debug("Type subprocess exited with code=%d", proc.returncode)
        if proc.returncode not in (None, 0):
            logger.warning("Type command exited with code %d", proc.returncode)


def create_transcription_injector(config: dict) -> TypeInjector:
    logger.debug("Creating TypeInjector")
    return TypeInjector()
