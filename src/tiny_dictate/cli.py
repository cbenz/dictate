"""CLI entry point (tiny-dictate).

Sends commands to the running daemon via IPC Unix socket.
Manages lifecycle and plugins.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import config as cfg
from . import plugin_registry as pr
from .ipc import send_command

__all__ = ["main"]


logger = logging.getLogger(__name__)


# ── Runtime commands (IPC to daemon) ─────────────────────────


def _cmd_daemon(command: str) -> None:
    """Send a command to the daemon and print the result. Exit with code 1 on errors."""
    result = send_command(command)
    msg = result.get("message", result.get("status", "unknown"))
    if result.get("status") == "error":
        sys.exit(1)


def cmd_start(args: argparse.Namespace) -> None:
    _cmd_daemon("start")


def cmd_stop(args: argparse.Namespace) -> None:
    _cmd_daemon("stop")


def cmd_flush(args: argparse.Namespace) -> None:
    _cmd_daemon("flush")


def cmd_cancel(args: argparse.Namespace) -> None:
    _cmd_daemon("cancel")


def cmd_toggle(args: argparse.Namespace) -> None:
    _cmd_daemon("toggle")


def cmd_status(args: argparse.Namespace) -> None:
    result = send_command("ping")
    if result.get("state"):
        pass
    elif result.get("status") == "error":
        sys.exit(1)
    else:
        pass
    result = send_command("ping")
    if result.get("state"):
        pass
    else:
        pass


def cmd_list_devices(args: argparse.Namespace) -> None:
    pass


# ── Lifecycle commands ───────────────────────────────────────


def cmd_install(args: argparse.Namespace) -> None:
    """Install the systemd --user service."""
    if not args.yes:
        confirm = input("Install tiny-dictate systemd service? [y/N] ").strip().lower()
        if confirm != "y":
            return

    service_src = Path(__file__).parent / "data" / "tiny-dictated.service"
    if not service_src.exists():
        sys.exit(1)

    service_dst = Path.home() / ".config" / "systemd" / "user" / "tiny-dictated.service"
    service_dst.parent.mkdir(parents=True, exist_ok=True)

    content = service_src.read_text()
    bin_path = shutil.which("tiny-dictated") or str(Path(sys.prefix) / "bin" / "tiny-dictated")
    content = content.replace("{{ TINY_DICTATED_BIN }}", bin_path)
    service_dst.write_text(content)

    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "tiny-dictated"], check=True)
    subprocess.run(["systemctl", "--user", "start", "tiny-dictated"], check=True)


def cmd_uninstall(args: argparse.Namespace) -> None:
    """Uninstall the systemd --user service."""
    if not args.yes:
        confirm = input("This will stop and remove the tiny-dictate service. Continue? [y/N] ").strip().lower()
        if confirm != "y":
            return

    service_path = Path.home() / ".config" / "systemd" / "user" / "tiny-dictated.service"
    if not service_path.exists():
        return

    subprocess.run(["systemctl", "--user", "stop", "tiny-dictated"], check=False)
    subprocess.run(["systemctl", "--user", "disable", "tiny-dictated"], check=False)
    service_path.unlink()
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)


# ── Plugins commands ─────────────────────────────────────────


def cmd_config(args: argparse.Namespace) -> None:
    """Config subcommands."""
    if args.config_cmd == "print-default":
        cfg.print_default()


def cmd_plugins(args: argparse.Namespace) -> None:
    """Dispatch plugins subcommands."""
    conf = cfg.load()
    registry = pr.create_registry(conf, registry_id=args.registry)

    if args.plugin_cmd == "list":
        _cmd_plugins_list(registry)
    elif args.plugin_cmd == "install":
        _cmd_plugins_install(registry, args.id, recreate_venv=args.recreate_venv)
    elif args.plugin_cmd == "uninstall":
        _cmd_plugins_uninstall(args.id)


def _cmd_plugins_list(registry: pr.PluginRegistry) -> None:
    available = asyncio.run(registry.list_plugins())
    installed = pr.list_installed()
    installed_ids = {p.id for p in installed}

    if not available:
        return

    for p in available:
        marker = "✓" if p.id in installed_ids else " "


def _cmd_plugins_install(registry: pr.PluginRegistry, plugin_id: str, recreate_venv: bool = False) -> None:
    if recreate_venv:
        import shutil

        from .config import PLUGINS_DIR

        venv_dir = PLUGINS_DIR / plugin_id / "venv"
        if venv_dir.exists():
            shutil.rmtree(venv_dir)
            logger.info("Removed old venv for %s", plugin_id)
    try:
        asyncio.run(registry.install_plugin(plugin_id))
    except FileExistsError as exc:
        pass
    except Exception as exc:
        sys.exit(1)


def _cmd_plugins_uninstall(plugin_id: str) -> None:
    try:
        pr.uninstall_plugin(plugin_id)
    except FileNotFoundError as exc:
        pass
    except Exception as exc:
        sys.exit(1)


# ── Setup wizard ─────────────────────────────────────────────


def cmd_systray(args: argparse.Namespace) -> None:
    """Start the system tray icon (blocking)."""
    from . import systray

    systray.run()


def cmd_waybar(args: argparse.Namespace) -> None:
    """Waybar integration."""
    from . import waybar
    if args.waybar_cmd == "print-config":
        waybar.print_config()
    elif args.waybar_cmd == "status":
        waybar.print_status()
    elif args.waybar_cmd == "setup":
        waybar.setup()


def cmd_setup(args: argparse.Namespace) -> None:
    """Interactive first-run setup wizard."""
    conf = cfg.load()

    # Step 1: Check prerequisites
    missing = [cmd for cmd in ["notify-send"] if not shutil.which(cmd)]

    # Step 2: Backend
    if not cfg.api_key():
        key = input("  Enter ElevenLabs API key (or set ELEVENLABS_API_KEY in .env): ").strip()
        if key:
            os.environ["ELEVENLABS_API_KEY"] = key
    else:
        pass

    # Step 3: Injection
    choice = input("  Choice [1]: ").strip() or "1"
    if choice == "2":
        path = input("  Output file path [~/dictation.txt]: ").strip()
        injector = conf.transcription_injector
        injector.plugins = ["file"]
        injector.path = path or str(Path.home() / "dictation.txt")
    else:
        keys_choice = input("  Choice [1]: ").strip() or "1"
        paste_map = {"1": "ctrl-v", "2": "ctrl-shift-v", "3": "shift-insert"}
        conf.transcription_injector.paste_keys = paste_map.get(keys_choice, "ctrl-v")

    # Step 4: Keyboard shortcuts

    # Step 5: Install
    cfg.save(conf)
    cmd_install(argparse.Namespace(yes=True))


# ═════════════════════════════════════════════════════════════
#  MAIN
# ═════════════════════════════════════════════════════════════


def main() -> None:
    parser = argparse.ArgumentParser(
        description="tiny-dictate — voice dictation for the Linux desktop",
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")

    subparsers = parser.add_subparsers(dest="command", required=True)

    # Runtime
    subparsers.add_parser("start", help="Start dictation")
    subparsers.add_parser("stop", help="Stop dictation and transcribe")
    subparsers.add_parser("cancel", help="Cancel current dictation")
    subparsers.add_parser("flush", help="Flush pending transcription text")
    subparsers.add_parser("toggle", help="Toggle dictation on/off")
    subparsers.add_parser("status", help="Check daemon status")
    subparsers.add_parser("list-devices", help="List audio devices")

    # Lifecycle
    install_p = subparsers.add_parser("install", help="Install systemd service")
    install_p.add_argument("-y", action="store_true", help="Skip confirmation")
    uninstall_p = subparsers.add_parser("uninstall", help="Uninstall systemd service")
    uninstall_p.add_argument("-y", action="store_true", help="Skip confirmation")
    subparsers.add_parser("systray", help="Start system tray icon")
    subparsers.add_parser("setup", help="First-run wizard")

    # Config command
    config_p = subparsers.add_parser("config", help="Configuration utilities")
    config_sub = config_p.add_subparsers(dest="config_cmd", required=True)
    config_sub.add_parser("print-default", help="Print default config to stdout")

    # Plugins sub-command group
    plugins_p = subparsers.add_parser("plugin", help="Manage plugins")

    # Waybar sub-command group
    waybar_p = subparsers.add_parser("waybar", help="Waybar integration")
    waybar_sub = waybar_p.add_subparsers(dest="waybar_cmd", required=True)
    waybar_sub.add_parser("print-config", help="Print waybar module config")
    waybar_sub.add_parser("status", help="Print waybar status JSON")
    waybar_sub.add_parser("setup", help="Install waybar module config")
    plugins_p.add_argument("--registry", help="Registry ID to use (default from config)")
    plugins_sub = plugins_p.add_subparsers(dest="plugin_cmd", required=True)

    plugins_sub.add_parser("list", help="List available and installed plugins")

    install_plugin = plugins_sub.add_parser("install", help="Install a plugin")
    install_plugin.add_argument("id", help="Plugin name (e.g. elevenlabs)")
    install_plugin.add_argument("--recreate-venv", action="store_true", help="Delete and recreate the plugin venv")

    uninstall_plugin = plugins_sub.add_parser("uninstall", help="Uninstall a plugin")
    uninstall_plugin.add_argument("id", help="Plugin name")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    cmd_map: dict[str, callable] = {
        "start": cmd_start,
        "stop": cmd_stop,
        "cancel": cmd_cancel,
        "flush": cmd_flush,
        "toggle": cmd_toggle,
        "status": cmd_status,
        "list-devices": cmd_list_devices,
        "install": cmd_install,
        "uninstall": cmd_uninstall,
        "setup": cmd_setup,
        "systray": cmd_systray,
        "waybar": cmd_waybar,
        "config": cmd_config,
        "plugin": cmd_plugins,
    }

    handler = cmd_map.get(args.command)
    if handler:
        handler(args)
    else:
        parser.print_help()
        sys.exit(1)
