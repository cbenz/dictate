"""IPC between CLI and daemon via Unix socket (JSON protocol).

The daemon listens on a Unix socket at ~/.cache/tiny-dictate/ipc.sock.
The CLI connects, sends a JSON command, receives a JSON response, and disconnects.
"""

import asyncio
import json
import logging

from .config import RUNTIME_DIR

__all__ = ["send_command", "start_server"]

logger = logging.getLogger(__name__)

SOCKET_DIR = RUNTIME_DIR
SOCKET_PATH = SOCKET_DIR / "ipc.sock"


def _ensure_socket_dir() -> None:
    SOCKET_DIR.mkdir(parents=True, exist_ok=True)


# ── Client (CLI side) ────────────────────────────────────────


def send_command(command: str) -> dict[str, Any]:
    """Send a command to the running daemon and return the response."""
    import socket

    _ensure_socket_dir()
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(5.0)
    try:
        sock.connect(str(SOCKET_PATH))
        payload = json.dumps({"command": command}).encode("utf-8")
        sock.sendall(payload)
        sock.shutdown(socket.SHUT_WR)
        raw = sock.recv(4096)
        return json.loads(raw.decode("utf-8"))
    except FileNotFoundError:
        return {"status": "error", "message": "Daemon not running"}
    except (ConnectionRefusedError, TimeoutError, OSError) as exc:
        return {"status": "error", "message": str(exc)}
    finally:
        sock.close()


# ── Server (Daemon side) ─────────────────────────────────────


async def handle_client(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    dispatch: Any,
) -> None:
    """Handle one IPC client connection."""
    try:
        raw = await reader.read(4096)
        if not raw:
            return
        data = json.loads(raw.decode("utf-8"))
        command = data.get("command", "")
        if command == "ping":
            logger.debug("IPC command: %s", command)
        else:
            logger.info("IPC command: %s", command)

        result = await dispatch(command)
        response = json.dumps(result).encode("utf-8")
        writer.write(response)
        await writer.drain()
    except json.JSONDecodeError:
        error = json.dumps({"status": "error", "message": "Invalid JSON"}).encode("utf-8")
        writer.write(error)
        await writer.drain()
    except Exception as exc:
        logger.exception("IPC handler error")
        error = json.dumps({"status": "error", "message": str(exc)}).encode("utf-8")
        writer.write(error)
        await writer.drain()
    finally:
        writer.close()


async def start_server(dispatch: Any) -> asyncio.AbstractServer:
    """Start the Unix socket server and return it."""
    _ensure_socket_dir()
    if SOCKET_PATH.exists():
        SOCKET_PATH.unlink()

    server = await asyncio.start_unix_server(
        lambda r, w: handle_client(r, w, dispatch),
        path=str(SOCKET_PATH),
    )
    logger.info("IPC server listening on %s", SOCKET_PATH)
    return server
