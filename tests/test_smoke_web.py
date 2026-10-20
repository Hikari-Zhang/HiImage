from __future__ import annotations

import base64
import hashlib
from urllib.parse import parse_qs, urlsplit

import pytest

from scripts import smoke_web


def test_build_probe_urls_keeps_web_prefix_and_uses_matching_websocket_scheme() -> None:
    probes = smoke_web.build_probe_urls("https://example.test/hiimage/")

    assert probes.root == "https://example.test/hiimage/"
    assert probes.health == "https://example.test/hiimage/api/health"
    assert probes.spa_fallback.startswith("https://example.test/hiimage/__smoke__/route-")
    websocket = urlsplit(probes.websocket)
    assert websocket.scheme == "wss"
    assert websocket.netloc == "example.test"
    assert websocket.path == "/hiimage/api/ws/progress"
    assert parse_qs(websocket.query)["task_id"][0]
    assert urlsplit(probes.spa_fallback).path != "/hiimage/"


@pytest.mark.parametrize(
    ("kind", "status", "content_type", "body"),
    [
        ("root", 200, "text/html; charset=utf-8", '<div id="root"></div>'),
        ("spa", 200, "text/html", '<div id="root"></div>'),
        ("health", 200, "application/json", '{"status":"ok","version":"2.0.0"}'),
    ],
)
def test_validate_response_accepts_expected_web_contract(
    kind: str,
    status: int,
    content_type: str,
    body: str,
) -> None:
    smoke_web.validate_response(kind, status, content_type, body)


@pytest.mark.parametrize(
    ("kind", "status", "content_type", "body", "message"),
    [
        ("root", 404, "text/html", "missing", "HTTP 200"),
        ("spa", 200, "application/json", "{}", "HTML"),
        ("health", 200, "application/json", '{"status":"down"}', "status=ok"),
    ],
)
def test_validate_response_rejects_false_positive_smoke_results(
    kind: str,
    status: int,
    content_type: str,
    body: str,
    message: str,
) -> None:
    with pytest.raises(smoke_web.SmokeFailure, match=message):
        smoke_web.validate_response(kind, status, content_type, body)


def test_websocket_probe_sends_same_origin_header(monkeypatch) -> None:
    class Socket:
        request = ""

        def sendall(self, data: bytes) -> None:
            self.request = data.decode("ascii")

        def recv(self, _size: int) -> bytes:
            key = next(
                line.split(":", 1)[1].strip()
                for line in self.request.splitlines()
                if line.lower().startswith("sec-websocket-key:")
            )
            accept = base64.b64encode(
                hashlib.sha1(
                    f"{key}258EAFA5-E914-47DA-95CA-C5AB0DC85B11".encode("ascii")
                ).digest()
            ).decode("ascii")
            return (
                "HTTP/1.1 101 Switching Protocols\r\n"
                f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
            ).encode("ascii")

        def close(self) -> None:
            pass

    socket = Socket()
    monkeypatch.setattr(smoke_web.socket, "create_connection", lambda *_args, **_kwargs: socket)

    smoke_web.probe_websocket(
        "ws://images.example:8080/api/ws/progress?task_id=abc",
        1,
    )

    assert "Origin: http://images.example:8080\r\n" in socket.request
