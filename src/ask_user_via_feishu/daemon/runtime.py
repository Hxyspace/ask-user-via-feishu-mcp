from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import uuid

from ask_user_via_feishu.config import Settings

DAEMON_PROTOCOL_VERSION = "1"
DAEMON_HOST = "127.0.0.1"
DAEMON_RUNTIME_BASE_DIR_ENV = "ASK_USER_VIA_FEISHU_DAEMON_BASE_DIR"


@dataclass(frozen=True)
class DaemonMetadata:
    pid: int
    port: int
    daemon_epoch: str
    protocol_version: str
    compatibility_hash: str
    started_at: str
    app_id: str

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)

    @classmethod
    def from_json(cls, raw: str) -> "DaemonMetadata":
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("Daemon metadata JSON must be an object.")
        return cls(
            pid=int(data["pid"]),
            port=int(data["port"]),
            daemon_epoch=str(data["daemon_epoch"]),
            protocol_version=str(data["protocol_version"]),
            compatibility_hash=str(data["compatibility_hash"]),
            started_at=str(data["started_at"]),
            app_id=str(data["app_id"]),
        )


def runtime_dir_for_settings(settings: Settings, *, base_dir: Path | None = None) -> Path:
    runtime_dir = resolve_runtime_base_dir(base_dir) / "shared-longconn-daemon" / build_identity_hash(settings)
    ensure_runtime_dir(runtime_dir)
    return runtime_dir


def resolve_runtime_base_dir(base_dir: Path | None = None) -> Path:
    if base_dir is not None:
        resolved = base_dir.expanduser().resolve()
    else:
        env_override = os.environ.get(DAEMON_RUNTIME_BASE_DIR_ENV, "").strip()
        if env_override:
            resolved = Path(env_override).expanduser().resolve()
        elif os.name == "nt":
            local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
            root = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
            resolved = (root / "ask-user-via-feishu").resolve()
        elif sys.platform == "darwin":
            resolved = (Path.home() / "Library" / "Application Support" / "ask-user-via-feishu").resolve()
        else:
            xdg_state_home = os.environ.get("XDG_STATE_HOME", "").strip()
            root = Path(xdg_state_home) if xdg_state_home else Path.home() / ".local" / "state"
            resolved = (root / "ask-user-via-feishu").resolve()
    ensure_runtime_dir(resolved)
    return resolved


def ensure_runtime_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        try:
            os.chmod(path, 0o700)
        except OSError:
            pass
    return path


def metadata_path(runtime_dir: Path) -> Path:
    return runtime_dir / "metadata.json"


def token_path(runtime_dir: Path) -> Path:
    return runtime_dir / "auth.token"


def startup_lock_path(runtime_dir: Path) -> Path:
    return runtime_dir / "startup.lock"


def build_identity_hash(settings: Settings) -> str:
    payload = {
        "app_id": settings.app_id.strip(),
        "app_secret_fingerprint": _sha256_text(settings.app_secret.strip()),
    }
    return _sha256_text(json.dumps(payload, sort_keys=True, separators=(",", ":")))


def build_compatibility_hash(settings: Settings) -> str:
    payload = {
        "protocol_version": DAEMON_PROTOCOL_VERSION,
        "base_url": settings.base_url.strip(),
        "reaction_enabled": bool(settings.reaction_enabled),
        "reaction_emoji_type": settings.reaction_emoji_type.strip(),
        "daemon_idle_timeout_seconds": int(settings.daemon_idle_timeout_seconds),
        "daemon_idle_check_interval_seconds": int(settings.daemon_idle_check_interval_seconds),
        "daemon_min_uptime_seconds": int(settings.daemon_min_uptime_seconds),
    }
    return _sha256_text(json.dumps(payload, sort_keys=True, separators=(",", ":")))


def load_metadata(runtime_dir: Path) -> DaemonMetadata | None:
    path = metadata_path(runtime_dir)
    if not path.exists():
        return None
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return None
    return DaemonMetadata.from_json(raw)


def load_token(runtime_dir: Path) -> str:
    path = token_path(runtime_dir)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def write_metadata(runtime_dir: Path, metadata: DaemonMetadata) -> None:
    _write_text_atomic(metadata_path(runtime_dir), metadata.to_json() + "\n")


def write_token(runtime_dir: Path, token: str) -> None:
    _write_text_atomic(token_path(runtime_dir), token.strip() + "\n")


def remove_runtime_file(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return


def current_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_text_atomic(path: Path, content: str) -> None:
    ensure_runtime_dir(path.parent)
    temp_path = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    with open(temp_path, "w", encoding="utf-8") as temp_file:
        temp_file.write(content)
        temp_file.flush()
        os.fsync(temp_file.fileno())
    if os.name != "nt":
        try:
            os.chmod(temp_path, 0o600)
        except OSError:
            pass
    os.replace(temp_path, path)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
