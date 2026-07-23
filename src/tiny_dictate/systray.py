"""System tray icon for tiny-dictate.

Uses GTK3 + AppIndicator3 (StatusNotifierItem protocol).
Works on Wayland and X11.

Run with: tiny-dictate systray
"""

from __future__ import annotations

import json
import logging
import os
import socket
import threading
import warnings
from pathlib import Path

logger = logging.getLogger(__name__)

SOCKET_PATH = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "tiny-dictate" / "ipc.sock"

warnings.filterwarnings("ignore", message="GLib.unix_signal_add_full")
warnings.filterwarnings("ignore", message="'asyncio.AbstractEventLoopPolicy'")
warnings.filterwarnings("ignore", message="'asyncio.get_event_loop_policy'")


def _send(command: str, timeout: float = 3.0) -> dict:
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect(str(SOCKET_PATH))
        payload = json.dumps({"command": command}).encode("utf-8")
        sock.sendall(payload)
        sock.shutdown(socket.SHUT_WR)
        raw = sock.recv(4096)
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {}
    finally:
        sock.close()


def run() -> None:
    """Run the systray icon (blocking GTK loop)."""
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        gi.require_version("AppIndicator3", "0.1")
        from gi.repository import Gtk, AppIndicator3, GLib
    except (ImportError, ValueError) as exc:
        print(f"❌ Systray not available: {exc}")
        print("   Install: gir1.2-gtk-3.0 gir1.2-appindicator3-0.1")
        raise SystemExit(1) from exc

    ICONS = {
        "disconnected": "dialog-error",
        "idle": "audio-input-microphone",
        "recording": "media-record",
        "transcribing": "emblem-important",
    }

    indicator = AppIndicator3.Indicator.new(
        "tiny-dictate",
        ICONS["idle"],
        AppIndicator3.IndicatorCategory.APPLICATION_STATUS,
    )
    indicator.set_status(AppIndicator3.IndicatorStatus.ACTIVE)

    def build_menu(state: str) -> Gtk.Menu:
        menu = Gtk.Menu()
        connected = state != "disconnected"
        recording = state in ("recording", "transcribing")

        def add_item(label: str, cmd: str | None, enabled: bool = True):
            mi = Gtk.MenuItem(label=label)
            mi.set_sensitive((cmd is None) or (enabled and connected))
            if cmd is None:
                mi.connect("activate", lambda _: Gtk.main_quit())
            else:
                mi.connect("activate", lambda _, c=cmd: _send(c))
            menu.append(mi)

        add_item("Start", "start", enabled=not recording)
        add_item("Stop", "stop", enabled=recording)
        menu.append(Gtk.SeparatorMenuItem())
        add_item("Quit", None)
        menu.show_all()
        return menu

    indicator.set_menu(build_menu(""))

    def set_state(state: str):
        icon = ICONS.get(state, ICONS["idle"])
        indicator.set_icon_full(icon, state)
        indicator.set_menu(build_menu(state))

    def state_watcher():
        while True:
            result = _send("wait_state", timeout=3600)
            if not result or "state" not in result:
                GLib.idle_add(set_state, "disconnected")
                import time
                time.sleep(2)
            else:
                GLib.idle_add(set_state, result["state"].lower())

    threading.Thread(target=state_watcher, daemon=True).start()

    result = _send("ping")
    if result and "state" in result:
        set_state(result["state"].lower())
    else:
        set_state("disconnected")

    print("✅ System tray icon ready")
    Gtk.main()
