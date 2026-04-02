from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ask_user_via_feishu.config import SERVER_VERSION, Settings
from ask_user_via_feishu.daemon.runtime import (
    DAEMON_PROTOCOL_VERSION,
    DaemonMetadata,
    build_compatibility_hash,
    ensure_runtime_dir,
    load_metadata,
    load_token,
    runtime_dir_for_settings,
    startup_lock_path,
)

logger = logging.getLogger(__name__)


class DaemonBootstrapError(RuntimeError):
    """Raised when the local daemon cannot be started or reused."""


class DaemonCompatibilityError(DaemonBootstrapError):
    """Raised when a healthy daemon exists but is incompatible with this client."""


@dataclass(frozen=True)
class DaemonConnectionInfo:
    runtime_dir: Path
    metadata: DaemonMetadata
    token: str


def exit_old_daemon(
    settings: Settings,
    *,
    base_dir: Path | None = None,
    timeout_seconds: float = 1.0,
) -> bool:
    runtime_dir = runtime_dir_for_settings(settings, base_dir=base_dir)
    metadata = load_metadata(runtime_dir)
    token = load_token(runtime_dir)
    if metadata is None or not token:
        return False
    health = _fetch_health(
        metadata,
        token,
        require_ready=False,
        probe_name="startup-version-check",
    )
    if health is None:
        return False
    daemon_version = str(health.get("version") or "").strip()
    if not daemon_version or daemon_version == SERVER_VERSION:
        return False
    logger.info(
        "Found outdated shared daemon version=%s current=%s; requesting exit.",
        daemon_version,
        SERVER_VERSION,
    )
    requested = _post_exit(
        metadata,
        token,
        reason="version_mismatch",
        requested_by_version=SERVER_VERSION,
    )
    if not requested:
        logger.warning(
            "Failed to request exit for outdated shared daemon version=%s current=%s.",
            daemon_version,
            SERVER_VERSION,
        )
        return False
    _wait_for_exit(
        runtime_dir,
        metadata,
        token,
        timeout_seconds=timeout_seconds,
    )
    return True


def ensure_daemon_running(
    settings: Settings,
    *,
    base_dir: Path | None = None,
    timeout_seconds: float = 5.0,
) -> DaemonConnectionInfo:
    runtime_dir = runtime_dir_for_settings(settings, base_dir=base_dir)
    existing = _try_load_healthy_daemon(runtime_dir, settings)
    if existing is not None:
        return existing

    with _StartupFileLock(startup_lock_path(runtime_dir)):
        existing = _try_load_healthy_daemon(runtime_dir, settings)
        if existing is not None:
            return existing
        _spawn_daemon_process(runtime_dir, settings)
        return _wait_for_ready_daemon(runtime_dir, settings, timeout_seconds=timeout_seconds)


def _wait_for_ready_daemon(
    runtime_dir: Path,
    settings: Settings,
    *,
    timeout_seconds: float,
) -> DaemonConnectionInfo:
    deadline = time.monotonic() + timeout_seconds
    last_error = "daemon did not become ready"
    while time.monotonic() < deadline:
        try:
            existing = _try_load_healthy_daemon(runtime_dir, settings)
        except DaemonCompatibilityError:
            raise
        except DaemonBootstrapError as exc:
            last_error = str(exc)
        else:
            if existing is not None:
                return existing
        time.sleep(0.1)
    raise DaemonBootstrapError(f"Timed out waiting for shared long-connection daemon: {last_error}")


def _try_load_healthy_daemon(runtime_dir: Path, settings: Settings) -> DaemonConnectionInfo | None:
    metadata = load_metadata(runtime_dir)
    token = load_token(runtime_dir)
    if metadata is None or not token:
        return None

    expected_compatibility_hash = build_compatibility_hash(settings)
    health = _fetch_health(metadata, token, require_ready=True, probe_name="bootstrap")
    if health is None:
        return None

    if metadata.protocol_version != DAEMON_PROTOCOL_VERSION or health.get("protocol_version") != DAEMON_PROTOCOL_VERSION:
        raise DaemonCompatibilityError("Shared long-connection daemon protocol version is incompatible.")
    if metadata.compatibility_hash != expected_compatibility_hash:
        raise DaemonCompatibilityError("Shared long-connection daemon config is incompatible with current settings.")
    if str(health.get("daemon_epoch") or "").strip() != metadata.daemon_epoch:
        raise DaemonBootstrapError("Shared long-connection daemon epoch changed unexpectedly.")
    return DaemonConnectionInfo(runtime_dir=runtime_dir, metadata=metadata, token=token)


def _fetch_health(
    metadata: DaemonMetadata,
    token: str,
    *,
    require_ready: bool,
    probe_name: str,
) -> dict[str, object] | None:
    request = Request(
        f"http://127.0.0.1:{metadata.port}/v1/health",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Daemon-Probe": probe_name,
        },
        method="GET",
    )
    try:
        with urlopen(request, timeout=0.5) as response:
            raw = response.read().decode("utf-8")
    except (HTTPError, URLError, TimeoutError, OSError):
        return None
    try:
        payload = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    if not payload.get("ok"):
        return None
    if require_ready and not payload.get("ready"):
        return None
    return payload


def _post_exit(
    metadata: DaemonMetadata,
    token: str,
    *,
    reason: str,
    requested_by_version: str,
) -> bool:
    body = json.dumps(
        {
            "reason": reason,
            "requested_by_version": requested_by_version,
        }
    ).encode("utf-8")
    request = Request(
        f"http://127.0.0.1:{metadata.port}/v1/exit",
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=0.5) as response:
            raw = response.read().decode("utf-8")
    except (HTTPError, URLError, TimeoutError, OSError):
        return False
    try:
        payload = json.loads(raw)
    except ValueError:
        return False
    return isinstance(payload, dict) and bool(payload.get("ok"))


def _wait_for_exit(
    runtime_dir: Path,
    metadata: DaemonMetadata,
    token: str,
    *,
    timeout_seconds: float,
) -> None:
    deadline = time.monotonic() + max(timeout_seconds, 0.0)
    while time.monotonic() < deadline:
        current_health = _fetch_health(
            metadata,
            token,
            require_ready=False,
            probe_name="startup-version-check",
        )
        try:
            current_metadata = load_metadata(runtime_dir)
        except FileNotFoundError:
            current_metadata = None
        try:
            current_token = load_token(runtime_dir)
        except FileNotFoundError:
            current_token = ""
        metadata_cleared = current_metadata is None or current_metadata.daemon_epoch != metadata.daemon_epoch
        token_cleared = not current_token or current_token != token
        if current_health is None and metadata_cleared and token_cleared:
            return
        time.sleep(0.05)


def _spawn_daemon_process(runtime_dir: Path, settings: Settings) -> None:
    ensure_runtime_dir(runtime_dir)
    command = [
        sys.executable,
        "-m",
        "ask_user_via_feishu",
        "--shared-longconn-daemon",
        "--daemon-runtime-dir",
        str(runtime_dir),
    ]
    daemon_env = dict(os.environ)
    daemon_env.update(
        {
            "APP_ID": settings.app_id,
            "APP_SECRET": settings.app_secret,
            "OWNER_OPEN_ID": settings.owner_open_id,
            "BASE_URL": settings.base_url,
            "API_TIMEOUT_SECONDS": str(settings.api_timeout_seconds),
            "LOG_LEVEL": settings.log_level,
            "REACTION_ENABLED": "true" if settings.reaction_enabled else "false",
            "REACTION_EMOJI_TYPE": settings.reaction_emoji_type,
            "ASK_TIMEOUT_SECONDS": str(settings.ask_timeout_seconds),
            "ASK_REMINDER_MAX_ATTEMPTS": str(settings.ask_reminder_max_attempts),
            "ASK_TIMEOUT_REMINDER_TEXT": settings.ask_timeout_reminder_text,
            "ASK_TIMEOUT_DEFAULT_ANSWER": settings.ask_timeout_default_answer,
            "DAEMON_IDLE_TIMEOUT_SECONDS": str(settings.daemon_idle_timeout_seconds),
            "DAEMON_IDLE_CHECK_INTERVAL_SECONDS": str(settings.daemon_idle_check_interval_seconds),
            "DAEMON_MIN_UPTIME_SECONDS": str(settings.daemon_min_uptime_seconds),
        }
    )
    kwargs: dict[str, object] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
        "env": daemon_env,
    }
    if os.name == "nt":
        creationflags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        kwargs["creationflags"] = creationflags
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(command, **kwargs)


class _StartupFileLock:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._file: object | None = None

    def __enter__(self) -> "_StartupFileLock":
        ensure_runtime_dir(self._path.parent)
        open(self._path, "ab").close()
        lock_file = open(self._path, "r+b")
        lock_file.seek(0, os.SEEK_END)
        if lock_file.tell() == 0:
            lock_file.write(b"0")
            lock_file.flush()
        lock_file.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        self._file = lock_file
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self._file is None:
            return
        if os.name == "nt":
            import msvcrt

            self._file.seek(0)
            msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        self._file.close()
        self._file = None
