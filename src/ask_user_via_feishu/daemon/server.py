from __future__ import annotations

import json
import logging
import os
import secrets
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import secrets
import threading
from typing import Any, Callable
import uuid

from ask_user_via_feishu.config import SERVER_NAME, SERVER_VERSION, Settings
from ask_user_via_feishu.errors import FeishuAPIError, MessageValidationError, RetryableAskError
from ask_user_via_feishu.daemon.runtime import (
    DAEMON_HOST,
    DAEMON_PROTOCOL_VERSION,
    DaemonMetadata,
    build_compatibility_hash,
    current_timestamp,
    ensure_runtime_dir,
    load_metadata,
    load_token,
    metadata_path,
    remove_runtime_file,
    token_path,
    write_metadata,
    write_token,
)

logger = logging.getLogger(__name__)


class SharedLongConnDaemonServer:
    def __init__(
        self,
        settings: Settings,
        runtime_dir: Path,
        *,
        ask_handler: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        send_handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] | None = None,
        status_provider: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self._settings = settings
        self._runtime_dir = ensure_runtime_dir(runtime_dir)
        self._daemon_epoch = uuid.uuid4().hex
        self._token = secrets.token_urlsafe(32)
        self._cleanup_lock = threading.Lock()
        self._cleaned_up = False
        self._ask_handler = ask_handler
        self._send_handlers = dict(send_handlers or {})
        self._status_provider = status_provider
        self._server = ThreadingHTTPServer((DAEMON_HOST, 0), self._build_handler())
        self._server.daemon_threads = True
        self._server.timeout = 0.5
        self._thread: threading.Thread | None = None
        self._metadata = DaemonMetadata(
            pid=os.getpid(),
            port=int(self._server.server_address[1]),
            daemon_epoch=self._daemon_epoch,
            protocol_version=DAEMON_PROTOCOL_VERSION,
            compatibility_hash=build_compatibility_hash(settings),
            started_at=current_timestamp(),
            app_id=settings.app_id.strip(),
        )

    @property
    def metadata(self) -> DaemonMetadata:
        return self._metadata

    @property
    def runtime_dir(self) -> Path:
        return self._runtime_dir

    @property
    def token(self) -> str:
        return self._token

    def start_background(self) -> threading.Thread:
        self._publish_runtime_files()
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
            self._thread.start()
        return self._thread

    def serve_forever(self) -> None:
        self._publish_runtime_files()
        logger.info(
            "Shared long-connection daemon listening on %s:%s epoch=%s",
            DAEMON_HOST,
            self._metadata.port,
            self._daemon_epoch,
        )
        try:
            self._server.serve_forever()
        finally:
            self._server.server_close()
            self._cleanup_runtime_files()

    def shutdown(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._cleanup_runtime_files()

    def close(self) -> None:
        self._server.server_close()
        self._cleanup_runtime_files()

    def _publish_runtime_files(self) -> None:
        write_token(self._runtime_dir, self._token)
        write_metadata(self._runtime_dir, self._metadata)

    def _cleanup_runtime_files(self) -> None:
        with self._cleanup_lock:
            if self._cleaned_up:
                return
            self._cleaned_up = True
        current_metadata = load_metadata(self._runtime_dir)
        current_token = load_token(self._runtime_dir)
        if current_metadata is not None and current_metadata.daemon_epoch == self._metadata.daemon_epoch:
            remove_runtime_file(metadata_path(self._runtime_dir))
        if current_token and current_token == self._token:
            remove_runtime_file(token_path(self._runtime_dir))

    def _build_handler(self) -> type[BaseHTTPRequestHandler]:
        daemon = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                daemon._handle_get(self)

            def do_POST(self) -> None:  # noqa: N802
                daemon._handle_post(self)

            def log_message(self, format: str, *args: object) -> None:
                logger.debug("Daemon HTTP %s - %s", self.address_string(), format % args)

        return Handler

    def _handle_get(self, handler: BaseHTTPRequestHandler) -> None:
        if not self._is_authorized(handler):
            self._send_json(handler, HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "unauthorized"})
            return

        if handler.path == "/v1/health":
            status_payload = self._status_provider() if self._status_provider is not None else {}
            long_connection_state = str(status_payload.get("long_connection_state") or "stopped")
            daemon_state = str(status_payload.get("daemon_state") or "serving")
            self._send_json(
                handler,
                HTTPStatus.OK,
                {
                    "ok": True,
                    "ready": daemon_state == "serving" and long_connection_state != "failed",
                    "service": SERVER_NAME,
                    "version": SERVER_VERSION,
                    "protocol_version": self._metadata.protocol_version,
                    "daemon_epoch": self._daemon_epoch,
                    "daemon_state": daemon_state,
                    "long_connection_state": long_connection_state,
                },
            )
            return

        if handler.path == "/v1/status":
            status_payload = self._status_provider() if self._status_provider is not None else {}
            self._send_json(
                handler,
                HTTPStatus.OK,
                {
                    "ok": True,
                    "protocol_version": self._metadata.protocol_version,
                    "daemon_epoch": self._daemon_epoch,
                    "daemon_state": str(status_payload.get("daemon_state") or "serving"),
                    "failure_reason": str(status_payload.get("failure_reason") or ""),
                    "long_connection_state": str(status_payload.get("long_connection_state") or "stopped"),
                    "pending_ask": bool(status_payload.get("pending_ask") or False),
                    "pending_question_id": str(status_payload.get("pending_question_id") or ""),
                    "identity": {
                        "app_id": self._settings.app_id.strip(),
                    },
                },
            )
            return

        self._send_json(handler, HTTPStatus.NOT_FOUND, {"ok": False, "error": "not_found"})

    def _handle_post(self, handler: BaseHTTPRequestHandler) -> None:
        if not self._is_authorized(handler):
            self._send_json(handler, HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "unauthorized"})
            return
        route_handler: Callable[[dict[str, Any]], dict[str, Any]] | None = None
        if handler.path == "/v1/ask_and_wait":
            route_handler = self._ask_handler
        else:
            route_handler = self._send_handlers.get(handler.path)
        if route_handler is None:
            self._send_json(handler, HTTPStatus.NOT_FOUND, {"ok": False, "error": "not_found"})
            return
        try:
            request_payload = self._read_json_body(handler)
            response_payload = route_handler(request_payload)
        except ValueError as exc:
            status = HTTPStatus.CONFLICT if "already exists" in str(exc) else HTTPStatus.BAD_REQUEST
            self._send_json(handler, status, {"ok": False, "error": str(exc)})
            return
        except MessageValidationError as exc:
            self._send_json(handler, HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
            return
        except FeishuAPIError as exc:
            self._send_json(handler, HTTPStatus.BAD_GATEWAY, {"ok": False, "error": str(exc)})
            return
        except RetryableAskError as exc:
            self._send_json(
                handler,
                HTTPStatus.SERVICE_UNAVAILABLE,
                {
                    "ok": False,
                    "error": str(exc),
                    "error_code": f"ask_retryable_{exc.retry_stage}",
                },
            )
            return
        except RuntimeError as exc:
            self._send_json(handler, HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(exc)})
            return
        if "protocol_version" not in response_payload:
            response_payload["protocol_version"] = self._metadata.protocol_version
        if "daemon_epoch" not in response_payload:
            response_payload["daemon_epoch"] = self._daemon_epoch
        self._send_json(handler, HTTPStatus.OK, response_payload)

    def _is_authorized(self, handler: BaseHTTPRequestHandler) -> bool:
        authorization = str(handler.headers.get("Authorization") or "").strip()
        expected = f"Bearer {self._token}"
        return bool(authorization) and secrets.compare_digest(authorization, expected)

    def _send_json(
        self,
        handler: BaseHTTPRequestHandler,
        status_code: HTTPStatus,
        payload: dict[str, object],
    ) -> None:
        body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
        handler.send_response(int(status_code))
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    def _read_json_body(self, handler: BaseHTTPRequestHandler) -> dict[str, Any]:
        content_length = int(str(handler.headers.get("Content-Length") or "0") or "0")
        raw = handler.rfile.read(content_length).decode("utf-8") if content_length else ""
        payload = json.loads(raw or "{}")
        if not isinstance(payload, dict):
            raise ValueError("request body must be a JSON object")
        return payload
