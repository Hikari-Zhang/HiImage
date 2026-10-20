#!/usr/bin/env python3
"""Smoke-test an already running HiImage web deployment."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import socket
import ssl
import sys
import uuid
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen


class SmokeFailure(RuntimeError):
    """Raised when a live endpoint violates the expected web contract."""


@dataclass(frozen=True)
class ProbeUrls:
    """Resolved URLs for one smoke run."""

    root: str
    health: str
    spa_fallback: str
    websocket: str


def build_probe_urls(base_url: str) -> ProbeUrls:
    """Normalize a deployment URL and derive HTTP/WebSocket probe targets."""
    parsed = urlsplit(base_url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("base URL must be an absolute http(s) URL")
    prefix = parsed.path.rstrip("/") + "/"
    root = urlunsplit((parsed.scheme, parsed.netloc, prefix, "", ""))
    token = uuid.uuid4().hex

    def child(path: str, scheme: str | None = None) -> str:
        return urlunsplit(
            (scheme or parsed.scheme, parsed.netloc, f"{prefix}{path}", "", "")
        )

    return ProbeUrls(
        root=root,
        health=child("api/health"),
        spa_fallback=child(f"__smoke__/route-{token}"),
        websocket=child(
            f"api/ws/progress?task_id={uuid.uuid4().hex}",
            "wss" if parsed.scheme == "https" else "ws",
        ),
    )


def validate_response(kind: str, status: int, content_type: str, body: str) -> None:
    """Validate one HTTP response without performing network I/O."""
    if status != 200:
        raise SmokeFailure(f"{kind} expected HTTP 200, received {status}")
    if kind in {"root", "spa"}:
        if "text/html" not in content_type.lower():
            raise SmokeFailure(f"{kind} expected HTML content type")
        if 'id="root"' not in body and "id='root'" not in body:
            raise SmokeFailure(f"{kind} response is not the HiImage HTML shell")
        return
    if kind == "health":
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as error:
            raise SmokeFailure("health response is not valid JSON") from error
        if payload.get("status") != "ok":
            raise SmokeFailure("health response must report status=ok")
        return
    raise ValueError(f"unknown response kind: {kind}")


def probe_http(kind: str, url: str, timeout: float) -> None:
    """Fetch and validate one HTTP target."""
    request = Request(url, headers={"User-Agent": "HiImage-smoke/1"})
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            validate_response(
                kind,
                response.status,
                response.headers.get("Content-Type", ""),
                body,
            )
    except HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        validate_response(kind, error.code, error.headers.get("Content-Type", ""), body)
    except (OSError, URLError) as error:
        raise SmokeFailure(f"{kind} request failed: {error}") from error


def probe_websocket(url: str, timeout: float) -> None:
    """Perform a standards-compliant WebSocket opening handshake."""
    parsed = urlsplit(url)
    host = parsed.hostname
    if host is None:
        raise SmokeFailure("WebSocket URL has no host")
    port = parsed.port or (443 if parsed.scheme == "wss" else 80)
    origin_scheme = "https" if parsed.scheme == "wss" else "http"
    origin = f"{origin_scheme}://{parsed.netloc}"
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    expected_accept = base64.b64encode(
        hashlib.sha1(
            f"{key}258EAFA5-E914-47DA-95CA-C5AB0DC85B11".encode("ascii")
        ).digest()
    ).decode("ascii")
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    raw_socket = socket.create_connection((host, port), timeout=timeout)
    connection: socket.socket = raw_socket
    try:
        if parsed.scheme == "wss":
            connection = ssl.create_default_context().wrap_socket(
                raw_socket, server_hostname=host
            )
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {parsed.netloc}\r\n"
            f"Origin: {origin}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        connection.sendall(request.encode("ascii"))
        response = b""
        while b"\r\n\r\n" not in response and len(response) < 16384:
            chunk = connection.recv(4096)
            if not chunk:
                break
            response += chunk
        header_text = response.decode("iso-8859-1")
        lines = header_text.split("\r\n")
        headers = {
            name.strip().lower(): value.strip()
            for line in lines[1:]
            if ":" in line
            for name, value in [line.split(":", 1)]
        }
        if not lines or " 101 " not in lines[0]:
            raise SmokeFailure(f"WebSocket expected HTTP 101, received {lines[0] or 'no response'}")
        if headers.get("sec-websocket-accept") != expected_accept:
            raise SmokeFailure("WebSocket Sec-WebSocket-Accept mismatch")
    except (OSError, ssl.SSLError) as error:
        raise SmokeFailure(f"WebSocket handshake failed: {error}") from error
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url",
        default=os.environ.get("HIIMAGE_WEB_URL", "http://127.0.0.1:8080"),
        help="running HiImage web base URL (default: %(default)s)",
    )
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument(
        "--skip-websocket",
        action="store_true",
        help="skip the WebSocket proxy handshake",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate arguments and print probes without network access",
    )
    args = parser.parse_args()
    try:
        probes = build_probe_urls(args.url)
        if args.check:
            print(json.dumps(probes.__dict__, ensure_ascii=False, indent=2))
            print("[CHECK] Probe configuration valid; no live service was contacted")
            return 0
        for kind, url in (
            ("root", probes.root),
            ("health", probes.health),
            ("spa", probes.spa_fallback),
        ):
            probe_http(kind, url, args.timeout)
            print(f"[OK] {kind}: {url}")
        if not args.skip_websocket:
            probe_websocket(probes.websocket, args.timeout)
            print(f"[OK] websocket: {probes.websocket}")
    except (SmokeFailure, ValueError) as error:
        print(f"[FAIL] {error}", file=sys.stderr)
        return 1
    print("[OK] HiImage live web smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
