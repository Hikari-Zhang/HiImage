"""第一阶段 Web API 安全契约测试。"""
import asyncio
import os

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from app.routers import settings, upscale


def _request(app: FastAPI, method: str, path: str, json: dict | None = None):
    async def send():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, path, json=json)

    return asyncio.run(send())


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (upscale.UpscaleRequest, {"image": "base64", "output_path": "C:/secret.png"}),
        (
            upscale.BatchUpscaleRequest,
            {"images": ["base64"], "output_dir": "C:/output", "is_file_path": True},
        ),
    ],
)
def test_upscale_models_reject_client_controlled_path_fields(model, payload):
    with pytest.raises(ValidationError):
        model(**payload)


def test_get_settings_masks_hf_token(monkeypatch):
    monkeypatch.setattr(
        settings,
        "get_all",
        lambda: {
            "network.hf_endpoint": "https://hf-mirror.example",
            "network.hf_token": "hf_super_secret",
        },
    )
    app = FastAPI()
    app.include_router(settings.router, prefix="/api")

    response = _request(app, "GET", "/api/settings")

    assert response.status_code == 200
    assert response.json()["hf_token_configured"] is True
    assert "hf_token" not in response.json()
    assert "hf_super_secret" not in response.text


def test_get_settings_response_uses_an_explicit_non_sensitive_allowlist(monkeypatch):
    monkeypatch.setattr(
        settings,
        "get_all",
        lambda: {
            "network.hf_token": "hf_super_secret",
            "network.future_secret": "must_not_leak",
        },
    )
    app = FastAPI()
    app.include_router(settings.router, prefix="/api")

    response = _request(app, "GET", "/api/settings")

    assert response.status_code == 200
    assert set(response.json()) == {
        "device",

        "hf_endpoint",
        "hf_token_configured",
        "github_mirror",
        "default_dilation",
        "disable_nsfw",

        "download_max_concurrent",
        "upscale_tile",
    }
    assert "hf_super_secret" not in response.text
    assert "must_not_leak" not in response.text
    assert not {
        key
        for key in response.json()
        if any(marker in key.lower() for marker in ("token", "secret", "password"))
        and key != "hf_token_configured"
    }


@pytest.mark.parametrize("payload", [{}, {"hf_token": ""}, {"hf_token": "   "}])
def test_empty_or_omitted_hf_token_preserves_existing_token(monkeypatch, payload):
    saved = []
    monkeypatch.setenv("HF_TOKEN", "hf_runtime_existing")
    monkeypatch.setenv("HF_ENDPOINT", "https://runtime-old.example")
    monkeypatch.setattr(
        settings,
        "get_all",
        lambda: {"network.hf_token": "hf_existing", "network.hf_endpoint": "https://old.example"},
    )
    monkeypatch.setattr(settings, "save", lambda values: saved.append(values.copy()))
    app = FastAPI()
    app.include_router(settings.router, prefix="/api")

    response = _request(
        app,
        "PUT",
        "/api/settings",
        {"hf_endpoint": "https://new.example", **payload},
    )

    assert response.status_code == 200
    assert saved[0]["network.hf_token"] == "hf_existing"
    assert saved[0]["network.hf_endpoint"] == "https://new.example"
    assert os.environ["HF_TOKEN"] == "hf_runtime_existing"
    assert os.environ["HF_ENDPOINT"] == "https://new.example"
    assert "hf_existing" not in response.text


def test_non_empty_hf_token_replaces_runtime_token_after_save(monkeypatch):
    saved = []
    monkeypatch.setenv("HF_TOKEN", "hf_runtime_existing")
    monkeypatch.setenv("HF_ENDPOINT", "https://runtime-old.example")
    monkeypatch.setattr(
        settings,
        "get_all",
        lambda: {
            "network.hf_token": "hf_existing",
            "network.hf_endpoint": "https://old.example",
        },
    )
    monkeypatch.setattr(settings, "save", lambda values: saved.append(values.copy()))
    app = FastAPI()
    app.include_router(settings.router, prefix="/api")

    response = _request(
        app,
        "PUT",
        "/api/settings",
        {
            "hf_token": " hf_replacement ",
            "hf_endpoint": "https://new.example",
        },
    )

    assert response.status_code == 200
    assert saved[0]["network.hf_token"] == "hf_replacement"
    assert os.environ["HF_TOKEN"] == "hf_replacement"
    assert os.environ["HF_ENDPOINT"] == "https://new.example"


def test_explicit_clear_hf_token_removes_existing_token(monkeypatch):
    saved = []
    monkeypatch.setenv("HF_TOKEN", "hf_runtime_existing")
    monkeypatch.setattr(
        settings,
        "get_all",
        lambda: {"network.hf_token": "hf_existing"},
    )
    monkeypatch.setattr(settings, "save", lambda values: saved.append(values.copy()))
    app = FastAPI()
    app.include_router(settings.router, prefix="/api")

    response = _request(
        app,
        "PUT",
        "/api/settings",
        {"clear_hf_token": True},
    )

    assert response.status_code == 200
    assert saved[0]["network.hf_token"] == ""
    assert "HF_TOKEN" not in os.environ


def test_save_failure_does_not_change_hf_environment(monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "hf_runtime_existing")
    monkeypatch.setenv("HF_ENDPOINT", "https://runtime-old.example")
    monkeypatch.setattr(
        settings,
        "get_all",
        lambda: {
            "network.hf_token": "hf_existing",
            "network.hf_endpoint": "https://old.example",
        },
    )

    def fail_save(_values):
        raise OSError("disk full")

    monkeypatch.setattr(settings, "save", fail_save)

    with pytest.raises(OSError, match="disk full"):
        asyncio.run(settings.update_settings(settings.SettingsUpdate(
            hf_token="hf_replacement",
            hf_endpoint="https://new.example",
        )))

    assert os.environ["HF_TOKEN"] == "hf_runtime_existing"
    assert os.environ["HF_ENDPOINT"] == "https://runtime-old.example"
