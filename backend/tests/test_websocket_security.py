"""WebSocket Origin 与握手参数安全测试。"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.routers.logs import router as logs_router
from app.websocket.progress import router as progress_router


def make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(progress_router, prefix="/api")
    app.include_router(logs_router, prefix="/api")
    return app


def make_client() -> TestClient:
    return TestClient(make_app())


def test_websocket_origin_protection_matrix_covers_every_route():
    actual = {
        route.path
        for route in make_app().routes
        if route.path.startswith("/api/ws/")
    }
    assert actual == {"/api/ws/progress", "/api/ws/logs"}


@pytest.mark.parametrize(
    "path",
    ["/api/ws/progress?task_id=task-a", "/api/ws/logs"],
)
@pytest.mark.parametrize("headers", [{}, {"origin": "https://evil.example"}])
def test_websockets_reject_missing_or_wrong_origin_with_1008(path, headers):
    with make_client() as client:
        with pytest.raises(WebSocketDisconnect) as error:
            with client.websocket_connect(path, headers=headers):
                pass
    assert error.value.code == 1008


@pytest.mark.parametrize("origin", ["http://127.0.0.1:5173", "http://localhost:5173"])
def test_development_origins_are_allowed_by_default(origin):
    with make_client() as client:
        with client.websocket_connect(
            "/api/ws/progress?task_id=task-a",
            headers={"origin": origin},
        ):
            pass


def test_forged_forwarded_headers_are_rejected_by_default(monkeypatch):
    monkeypatch.setenv("HIIMAGE_WS_ORIGINS", "")
    monkeypatch.delenv("HIIMAGE_TRUST_PROXY_HEADERS", raising=False)
    with make_client() as client:
        with pytest.raises(WebSocketDisconnect) as error:
            with client.websocket_connect(
                "/api/ws/progress?task_id=task-a",
                headers={
                    "origin": "https://images.example:8443",
                    "x-forwarded-proto": "https",
                    "x-forwarded-host": "images.example:8443",
                },
            ):
                pass
    assert error.value.code == 1008


@pytest.mark.parametrize("value", ["1", "true", "TRUE"])
def test_same_origin_proxy_headers_are_allowed_when_trusted(monkeypatch, value):
    monkeypatch.setenv("HIIMAGE_WS_ORIGINS", "")
    monkeypatch.setenv("HIIMAGE_TRUST_PROXY_HEADERS", value)
    with make_client() as client:
        with client.websocket_connect(
            "/api/ws/progress?task_id=task-a",
            headers={
                "origin": "https://images.example:8443",
                "x-forwarded-proto": "https",
                "x-forwarded-host": "images.example:8443",
            },
        ):
            pass


@pytest.mark.parametrize("value", ["", "0", "yes", "on", "invalid"])
def test_invalid_or_implicit_proxy_trust_values_are_safe_false(monkeypatch, value):
    monkeypatch.setenv("HIIMAGE_WS_ORIGINS", "")
    monkeypatch.setenv("HIIMAGE_TRUST_PROXY_HEADERS", value)
    with make_client() as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                "/api/ws/logs",
                headers={
                    "origin": "https://images.example",
                    "x-forwarded-proto": "https",
                    "x-forwarded-host": "images.example",
                },
            ):
                pass


def test_empty_origin_config_disables_development_allowlist(monkeypatch):
    monkeypatch.setenv("HIIMAGE_WS_ORIGINS", "")
    with make_client() as client:
        with pytest.raises(WebSocketDisconnect) as error:
            with client.websocket_connect(
                "/api/ws/logs",
                headers={"origin": "http://localhost:5173"},
            ):
                pass
    assert error.value.code == 1008


def test_configured_comma_separated_origins_replace_development_allowlist(monkeypatch):
    monkeypatch.setenv(
        "HIIMAGE_WS_ORIGINS",
        "https://desktop.example, https://lan.example:8443",
    )
    with make_client() as client:
        with client.websocket_connect(
            "/api/ws/logs",
            headers={"origin": "https://desktop.example"},
        ):
            pass
        with client.websocket_connect(
            "/api/ws/logs",
            headers={"origin": "https://lan.example:8443"},
        ):
            pass
        with pytest.raises(WebSocketDisconnect) as error:
            with client.websocket_connect(
                "/api/ws/logs",
                headers={"origin": "http://localhost:5173"},
            ):
                pass
    assert error.value.code == 1008


@pytest.mark.parametrize(
    "path",
    [
        "/api/ws/progress",
        "/api/ws/progress?task_id=",
        "/api/ws/progress?task_id=%20%20%20",
    ],
)
def test_progress_websocket_requires_non_empty_task_id(path):
    with make_client() as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                path,
                headers={"origin": "http://localhost:5173"},
            ):
                pass
