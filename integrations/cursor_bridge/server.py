"""Minimal OpenAI-compatible HTTP server for Cursor SDK (stdlib + optional threading)."""

from __future__ import annotations

import json
import logging
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

from integrations.cursor_bridge.adapter import (
    models_list_payload,
    parse_json_body,
    run_chat_completion,
)

logger = logging.getLogger(__name__)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 18765


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _check_auth(headers: Any, required_key: Optional[str]) -> bool:
    if not required_key:
        return True
    auth = headers.get("Authorization") if hasattr(headers, "get") else None
    if not auth or not str(auth).startswith("Bearer "):
        return False
    token = str(auth)[7:].strip()
    return token == required_key


class CursorBridgeHandler(BaseHTTPRequestHandler):
    """Serve /v1/chat/completions and /v1/models for Hermes custom provider."""

    server_version = "CursorBridge/0.1"

    def log_message(self, format: str, *args: Any) -> None:
        logger.info("%s - %s", self.address_string(), format % args)

    def _send_json(self, status: int, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length <= 0:
            return b""
        return self.rfile.read(length)

    def _default_model(self) -> str:
        return os.environ.get("CURSOR_BRIDGE_MODEL", "composer-2.5")

    def _default_cwd(self) -> Optional[str]:
        value = os.environ.get("CURSOR_BRIDGE_CWD", "").strip()
        return value or None

    def _required_api_key(self) -> Optional[str]:
        return (os.environ.get("CURSOR_API_KEY") or "").strip() or None

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in ("/health", "/v1/health"):
            self._send_json(200, {"status": "ok", "service": "cursor_bridge"})
            return
        if path == "/v1/models":
            if not _check_auth(self.headers, self._required_api_key()):
                self._send_json(401, {"error": {"message": "Unauthorized"}})
                return
            self._send_json(200, models_list_payload(self._default_model()))
            return
        self._send_json(404, {"error": {"message": "Not found"}})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path != "/v1/chat/completions":
            self._send_json(404, {"error": {"message": "Not found"}})
            return
        if not _check_auth(self.headers, self._required_api_key()):
            self._send_json(401, {"error": {"message": "Unauthorized"}})
            return
        body_bytes = self._read_body()
        data, err = parse_json_body(body_bytes)
        if err:
            self._send_json(400, {"error": {"message": err}})
            return
        if data is None:
            self._send_json(400, {"error": {"message": "Invalid JSON"}})
            return
        if data.get("stream"):
            self._send_json(501, {
                "error": {
                    "message": "Streaming not implemented; set stream=false",
                    "type": "not_implemented",
                }
            })
            return
        status, payload = run_chat_completion(
            data,
            default_model=self._default_model(),
            default_cwd=self._default_cwd(),
        )
        self._send_json(status, payload)


def serve_forever(
    host: Optional[str] = None,
    port: Optional[int] = None,
) -> ThreadingHTTPServer:
    bind_host = host or os.environ.get("CURSOR_BRIDGE_HOST", DEFAULT_HOST)
    bind_port = port if port is not None else _env_int("CURSOR_BRIDGE_PORT", DEFAULT_PORT)
    httpd = ThreadingHTTPServer((bind_host, bind_port), CursorBridgeHandler)
    logger.info("cursor_bridge listening on http://%s:%s/v1", bind_host, bind_port)
    return httpd
