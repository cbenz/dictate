"""Clipboard injector — copies text and optionally pastes via simulated keystrokes."""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess

__all__ = ["create_transcription_injector"]

logger = logging.getLogger(__name__)


class ClipboardInjector:
    def __init__(self, paste_keys: str = "ctrl-v") -> None:
        self._paste_keys = paste_keys
        self._clipboard_cmd = self._detect_clipboard_cmd()
        logger.debug("ClipboardInjector initialized: cmd=%s, paste_keys=%s", self._clipboard_cmd, paste_keys)

    def _detect_clipboard_cmd(self) -> list[str]:
        if shutil.which("wl-copy"):
            logger.debug("Detected clipboard tool: wl-copy")
            return ["wl-copy"]
        if shutil.which("xclip"):
            logger.debug("Detected clipboard tool: xclip")
            return ["xclip", "-selection", "clipboard"]
        if shutil.which("xsel"):
            logger.debug("Detected clipboard tool: xsel")
            return ["xsel", "--clipboard", "--input"]
        msg = "No clipboard tool found. Install wl-clipboard (Wayland) or xclip/xsel (X11)."
        raise RuntimeError(msg)

    async def inject(self, text: str) -> None:
        logger.info("Copying to clipboard: %s", text[:60])
        proc = await asyncio.create_subprocess_exec(
            *self._clipboard_cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        logger.debug("Clipboard subprocess started (pid=%d)", proc.pid)
        _stdout, _stderr = await proc.communicate(input=text.encode("utf-8"))
        logger.debug("Clipboard subprocess exited with code=%d", proc.returncode)
        if proc.returncode != 0:
            msg = f"Clipboard command failed (exit {proc.returncode})"
            raise RuntimeError(msg)
        logger.debug("Clipboard copy succeeded, proceeding to paste")
        await self._paste()

    async def _paste(self) -> None:
        paste_map = {"ctrl-v": ["ctrl+v"], "ctrl-shift-v": ["ctrl+shift+v"], "shift-insert": ["shift+Insert"]}
        keys = paste_map.get(self._paste_keys, ["ctrl+v"])
        logger.debug("Paste keys: %s (from config: %s)", keys, self._paste_keys)
        if shutil.which("ydotool"):
            logger.debug("Pasting via ydotool key %s", keys)
            for key in keys:
                subprocess.run(["ydotool", "key", key], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                logger.debug("ydotool key %s sent", key)
        elif shutil.which("xdotool"):
            logger.debug("Pasting via xdotool key %s", keys)
            for key in keys:
                subprocess.run(["xdotool", "key", key], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                logger.debug("xdotool key %s sent", key)
        else:
            logger.warning("No paste tool found. Text is in clipboard, paste manually.")


def create_transcription_injector(config: dict) -> ClipboardInjector:
    paste_keys = config.get("injection", {}).get("paste_keys", "ctrl-v")
    logger.debug("Creating ClipboardInjector with paste_keys=%s", paste_keys)
    return ClipboardInjector(paste_keys=paste_keys)
