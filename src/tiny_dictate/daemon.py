"""Daemon entry point (tiny-dictated).

Runs the Controller and the IPC server in an asyncio event loop.
"""

from __future__ import annotations

import asyncio
import logging
import os
import pathlib
import signal
import sys
import warnings

from .controller import create_controller
from .ipc import SOCKET_PATH, start_server

__all__ = ["main"]


# sounddevice uses numpy <2.5-incompatible shape assignment
warnings.filterwarnings("ignore", message="Setting the shape on a NumPy array")


logger = logging.getLogger("tiny-dictated")


async def dispatch(controller: Any, command: str) -> dict[str, Any]:
    """Route IPC commands to the controller."""
    route = {
        "start": controller.start,
        "stop": controller.stop,
        "cancel": controller.cancel,
        "toggle": controller.toggle,
        "flush": controller.flush,
        "acknowledge_error": controller.acknowledge_error,
        "ping": controller.ping,
        "wait_state": controller.wait_state,
    }
    handler = route.get(command)
    if handler is None:
        return {"status": "error", "message": f"Unknown command: {command}"}
    return await handler()


async def main_loop() -> None:
    """Start IPC server and controller, then run until shutdown."""
    controller = create_controller()

    async def dispatch_wrapper(command: str) -> dict[str, Any]:
        return await dispatch(controller, command)

    server = await start_server(dispatch_wrapper)
    logger.info("Daemon ready")

    # Handle shutdown signals
    shutdown_event = asyncio.Event()

    def _shutdown() -> None:
        if not shutdown_event.is_set():
            logger.info("Shutting down...")
            shutdown_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _shutdown)
        except NotImplementedError:
            signal.signal(sig, lambda s, f: _shutdown())

    try:
        await shutdown_event.wait()
    finally:
        await controller.shutdown()
        server.close()
        await server.wait_closed()
        if SOCKET_PATH.exists():
            SOCKET_PATH.unlink()
        logger.info("Daemon stopped")


def main() -> None:
    """Daemon entry point."""
    import argparse
    import fcntl

    parser = argparse.ArgumentParser(
        description="tiny-dictate daemon — background voice dictation service",
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # Single-instance lock via PID file
    from .config import RUNTIME_DIR

    lock_dir = RUNTIME_DIR
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / "daemon.lock"
    lock_fd = lock_path.open("w")
    try:
        fcntl.lockf(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock_fd.close()
        logger.exception("Another daemon instance is already running (%s)", lock_path)
        sys.exit(1)
    lock_fd.write(str(os.getpid()) + "\n")
    lock_fd.flush()

    # Load .env if present
    from dotenv import load_dotenv

    env_path = pathlib.Path(__file__).parent.parent.parent / ".env"
    load_dotenv(env_path)

    asyncio.run(main_loop())
