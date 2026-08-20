"""Kryptic daemon client for Python.

During development startup, ``kryptic.inject()`` asks the local Kryptic daemon for
the current project's secrets and puts them into ``os.environ``. Outside development it
is a no-op. It never raises - a missing daemon means the application simply starts with
whatever environment it already has.

Protocol: daemon/PROTOCOL.md v1 (newline-delimited JSON over a local socket).
"""

from __future__ import annotations

import json
import os
import socket
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

PROTOCOL_VERSION = 1

__all__ = ["inject", "InjectResult"]


@dataclass
class InjectResult:
    injected: int
    skipped: bool
    reason: Optional[str] = None


def inject(
    environment: Optional[str] = None,
    project_id: Optional[str] = None,
    timeout_ms: Optional[int] = None,
) -> InjectResult:
    """Fetch secrets from the daemon and inject them into ``os.environ``.

    Existing environment variables are never overwritten. Call before any
    ``os.environ`` reads (e.g. at the top of ``manage.py`` for Django).
    """
    skip_reason = _should_skip()
    if skip_reason:
        return InjectResult(injected=0, skipped=True, reason=skip_reason)

    config = _find_kryptic_json()

    project_id = project_id or os.environ.get("KRYPTIC_PROJECT_ID") or (config or {}).get("projectId")
    if not project_id:
        _warn("no kryptic.json found (and no KRYPTIC_PROJECT_ID set) - nothing to inject.")
        return InjectResult(injected=0, skipped=True, reason="no_project")

    environment = (
        environment
        or os.environ.get("KRYPTIC_ENV")
        or (config or {}).get("defaultEnvironment")
        or "development"
    )

    timeout = (timeout_ms or int(os.environ.get("KRYPTIC_TIMEOUT_MS", "2000"))) / 1000.0

    try:
        response = _request(
            {"v": PROTOCOL_VERSION, "type": "secrets", "projectId": project_id, "environment": environment},
            timeout,
        )
    except OSError as e:
        _warn(f"daemon not reachable ({e}) - continuing without injected secrets.")
        return InjectResult(injected=0, skipped=True, reason="daemon_unreachable")
    except ValueError:
        _warn("daemon sent an invalid response - continuing without injected secrets.")
        return InjectResult(injected=0, skipped=True, reason="invalid_response")

    if not response.get("ok"):
        error = response.get("error", "internal")
        _warn(f"daemon refused the request ({error}): {response.get('message', '')}")
        return InjectResult(injected=0, skipped=True, reason=error)

    injected = 0
    for secret in response.get("secrets", []):
        key = secret.get("key")
        if not key or key in os.environ:  # real environment always wins
            continue
        os.environ[key] = secret.get("value", "")
        injected += 1

    return InjectResult(injected=injected, skipped=False)


# ---------- internals ----------


def _should_skip() -> Optional[str]:
    if os.environ.get("KRYPTIC_DISABLED") == "true":
        return "disabled"

    # Python has no single convention; honor the common ones.
    for variable in ("ENVIRONMENT", "ENV", "PYTHON_ENV", "APP_ENV"):
        value = os.environ.get(variable, "").lower()
        if value in ("production", "prod", "staging"):
            return f"{variable.lower()}_{value}"

    return None


def _socket_path() -> str:
    override = os.environ.get("KRYPTIC_SOCKET_PATH")
    if override:
        return override

    if sys.platform == "win32":
        return r"\\.\pipe\kryptic-daemon"

    if sys.platform == "linux":
        runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
        if runtime_dir:
            return str(Path(runtime_dir) / "kryptic-daemon.sock")

    return "/tmp/kryptic-daemon.sock"


def _request(payload: dict, timeout: float) -> dict:
    line = (json.dumps(payload) + "\n").encode("utf-8")

    if sys.platform == "win32" and _socket_path().startswith("\\\\.\\pipe\\"):
        raw = _round_trip_named_pipe(line, timeout)
    else:
        raw = _round_trip_unix_socket(line, timeout)

    return json.loads(raw.decode("utf-8"))


def _round_trip_unix_socket(line: bytes, timeout: float) -> bytes:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(timeout)
        client.connect(_socket_path())
        client.sendall(line)

        buffer = b""
        while b"\n" not in buffer:
            chunk = client.recv(4096)
            if not chunk:
                raise OSError("connection closed")
            buffer += chunk

        return buffer.split(b"\n", 1)[0]


def _round_trip_named_pipe(line: bytes, timeout: float) -> bytes:
    """Round trip over a Windows named pipe.

    The daemon serves a byte-mode pipe, so a plain file handle works - no win32
    bindings needed. Mirrors the .NET client: the timeout covers connecting (the
    pipe may briefly report "busy" between served clients); the read then blocks
    until the daemon replies, which it does immediately or not at all.
    """
    deadline = time.monotonic() + timeout
    while True:
        try:
            pipe = open(_socket_path(), "r+b", buffering=0)  # noqa: SIM115 - closed below
            break
        except OSError:
            if time.monotonic() >= deadline:
                raise OSError("timed out connecting to the daemon pipe") from None
            time.sleep(0.05)

    try:
        pipe.write(line)

        buffer = b""
        while b"\n" not in buffer:
            chunk = pipe.read(4096)
            if not chunk:
                raise OSError("connection closed")
            buffer += chunk

        return buffer.split(b"\n", 1)[0]
    finally:
        pipe.close()


def _find_kryptic_json() -> Optional[dict]:
    directory = Path.cwd()
    while True:
        candidate = directory / "kryptic.json"
        if candidate.is_file():
            try:
                return json.loads(candidate.read_text(encoding="utf-8"))
            except ValueError:
                _warn(f"could not parse {candidate} - ignoring it.")
                return None
        if directory.parent == directory:
            return None
        directory = directory.parent


def _warn(message: str) -> None:
    if os.environ.get("KRYPTIC_SILENT") == "true":
        return
    print(f"[kryptic] {message}", file=sys.stderr)
