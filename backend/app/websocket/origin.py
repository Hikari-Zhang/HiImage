"""WebSocket Origin 校验。"""
from __future__ import annotations

import os
from urllib.parse import urlsplit

from fastapi import WebSocket


DEFAULT_DEVELOPMENT_ORIGINS = {
    "http://127.0.0.1:5173",
    "http://localhost:5173",
}


def _normalize_origin(value: str) -> tuple[str, str, int] | None:
    try:
        parsed = urlsplit(value.strip())
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            return None
        default_port = 443 if parsed.scheme == "https" else 80
        return parsed.scheme, parsed.hostname.lower(), parsed.port or default_port
    except ValueError:
        return None


def _trust_proxy_headers() -> bool:
    """仅显式 1/true 才信任反向代理提供的来源头。"""
    return os.getenv("HIIMAGE_TRUST_PROXY_HEADERS", "").strip().lower() in {
        "1",
        "true",
    }


def _request_origin(ws: WebSocket) -> tuple[str, str, int] | None:
    trust_proxy = _trust_proxy_headers()
    forwarded_proto = (
        ws.headers.get("x-forwarded-proto")
        if trust_proxy
        else None
    )
    scheme = (forwarded_proto.split(",", 1)[0].strip() if forwarded_proto else ws.url.scheme)
    scheme = {"ws": "http", "wss": "https"}.get(scheme.lower(), scheme.lower())
    if scheme not in {"http", "https"}:
        return None

    forwarded_host = (
        ws.headers.get("x-forwarded-host")
        if trust_proxy
        else None
    )
    host = (forwarded_host.split(",", 1)[0].strip() if forwarded_host else ws.headers.get("host", ""))
    return _normalize_origin(f"{scheme}://{host}")


def is_websocket_origin_allowed(ws: WebSocket) -> bool:
    """允许同源；未配置时允许默认开发源，显式配置时仅允许白名单。"""
    supplied = _normalize_origin(ws.headers.get("origin", ""))
    if supplied is None:
        return False
    if supplied == _request_origin(ws):
        return True

    configured = os.getenv("HIIMAGE_WS_ORIGINS")
    if configured is None:
        allowed = set(DEFAULT_DEVELOPMENT_ORIGINS)
    else:
        allowed = {
            item.strip()
            for item in configured.split(",")
            if item.strip()
        }
    return supplied in {
        normalized
        for item in allowed
        if (normalized := _normalize_origin(item)) is not None
    }


async def require_websocket_origin(ws: WebSocket) -> bool:
    """在 accept 前拒绝不可信 Origin。"""
    if is_websocket_origin_allowed(ws):
        return True
    await ws.close(code=1008, reason="WebSocket Origin not allowed")
    return False
