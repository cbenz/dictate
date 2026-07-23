"""Waybar integration for tiny-dictate.

Provides a custom module for waybar that shows a microphone icon
and allows toggling dictation with a click.

Run with: tiny-dictate waybar print-config
Run with: tiny-dictate waybar setup
"""

from __future__ import annotations

import json
import os
from pathlib import Path

MODULE_NAME = "tiny-dictate"
WAYBAR_DIR = Path.home() / ".config" / "waybar"
MODULE_FILE = WAYBAR_DIR / f"{MODULE_NAME}-module.jsonc"
MAIN_CONFIG = WAYBAR_DIR / "config.jsonc"

MODULE_CONFIG = {
    f"custom/{MODULE_NAME}": {
        "format": "{}",
        "exec": f"{MODULE_NAME} waybar status",
        "interval": 1,
        "return-type": "json",
        "exec-on-event": True,
        "on-click": f"{MODULE_NAME} toggle",
        "tooltip": True,
    }
}

MODULE_INCLUDE = f"{MODULE_NAME}-module.jsonc"


def print_config() -> None:
    """Print the waybar module configuration to stdout."""
    print(json.dumps(MODULE_CONFIG, indent=2))


def print_status() -> None:
    """Print current status as JSON for waybar."""
    from .ipc import send_command
    result = send_command("ping")
    state = result.get("state", "")
    if not state:
        print(json.dumps({"text": "🚫", "tooltip": "Dictation daemon not running", "class": "error"}))
    elif "RECORDING" in state:
        print(json.dumps({"text": "🔴", "tooltip": "Dictation recording…", "class": "recording"}))
    elif "TRANSCRIBING" in state:
        print(json.dumps({"text": "⏳", "tooltip": "Dictation transcribing…", "class": "transcribing"}))
    elif "ERROR" in state:
        print(json.dumps({"text": "❌", "tooltip": "Dictation error", "class": "error"}))
    else:
        print(json.dumps({"text": "🎤", "tooltip": "Dictation idle", "class": "idle"}))


def _find_include(config: dict) -> bool:
    """Check if our module is already included in the waybar config."""
    includes = config.get("include", [])
    if isinstance(includes, list) and MODULE_INCLUDE in includes:
        return True
    if isinstance(includes, str) and MODULE_INCLUDE in includes:
        return True
    return False


def _ensure_modules_entry(config: dict, module_name: str) -> bool:
    """Add the module to 'modules-right' if not present."""
    for key in ("modules-right", "modules-left", "modules-center"):
        modules = config.get(key, [])
        if module_name in modules:
            return False
    # Add to modules-right by default
    config.setdefault("modules-right", []).append(module_name)
    return True


def setup() -> None:
    """Install the waybar module configuration."""
    WAYBAR_DIR.mkdir(parents=True, exist_ok=True)

    # Write module config
    MODULE_FILE.write_text(json.dumps(MODULE_CONFIG, indent=2) + "\n")
    print(f"✅ Wrote {MODULE_FILE}")

    # Update main config
    if MAIN_CONFIG.exists():
        config = json.loads(MAIN_CONFIG.read_text())
        added_module = _ensure_modules_entry(config, f"custom/{MODULE_NAME}")
        if not _find_include(config):
            includes = config.setdefault("include", [])
            includes.append(MODULE_INCLUDE)
            MAIN_CONFIG.write_text(json.dumps(config, indent=2) + "\n")
            print(f"✅ Added include to {MAIN_CONFIG}")
        else:
            print("ℹ️  Module already included in waybar config")
        if added_module:
            print(f"✅ Added custom/{MODULE_NAME} to modules-right")
    else:
        print(f"⚠️  No waybar config found at {MAIN_CONFIG}")
        print(f"   Add this to your {MAIN_CONFIG}:")
        print(f'   "include": ["{MODULE_INCLUDE}"],')

    print()
    print("✅ Waybar module installed")
    import subprocess
    subprocess.run(["killall", "-SIGUSR2", "waybar"], capture_output=True)
    print("✅ Waybar reloaded")
