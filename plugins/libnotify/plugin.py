"""Desktop notifications via notify-send (libnotify)."""

from __future__ import annotations

import logging
import subprocess

__all__ = ["create_feedback_notifier"]


logger = logging.getLogger(__name__)

NOTIFICATION_ID_HASH = hash("tiny-dictate") % (2**31)


class LibnotifyNotifier:
    async def notify(self, message: str, urgency: str = "normal") -> None:
        try:
            subprocess.run(
                [
                    "notify-send",
                    "--app-name=tiny-dictate",
                    f"--urgency={urgency}",
                    f"--replace-id={NOTIFICATION_ID_HASH}",
                    "🎤 Dictate",
                    message,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            logger.warning("notify-send failed: %s", exc)


def create_feedback_notifier(config: dict) -> LibnotifyNotifier:
    return LibnotifyNotifier()
