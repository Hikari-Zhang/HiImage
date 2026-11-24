import asyncio
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from app.routers import models


@pytest.mark.parametrize(
    ("os_name", "host", "can_open"),
    [("nt", "127.0.0.1", True), ("nt", "192.0.2.1", False), ("posix", "127.0.0.1", False)],
)
def test_model_folders_match_backend_capability_and_existing_paths(monkeypatch, tmp_path, os_name, host, can_open):
    model_file = tmp_path / "model.pt"
    model_file.touch()
    paths = {"installed": model_file, "missing": tmp_path / "missing"}
    entries = [{"id": model_id} for model_id in paths]
    results = {
        model_id: SimpleNamespace(model_id=model_id, status="ok", message="", file_path=str(path))
        for model_id, path in paths.items()
    }
    opened = []
    monkeypatch.setattr(models, "MODELS", entries)
    monkeypatch.setattr(models, "MODEL_BY_ID", paths)
    monkeypatch.setattr(models, "ModelChecker", lambda: SimpleNamespace(
        check_all=lambda: list(results.values()),
        check_model=lambda model_id, use_cache: results[model_id],
    ))
    monkeypatch.setattr(models, "os", SimpleNamespace(name=os_name, startfile=lambda *args: opened.append(args)))
    app = FastAPI()
    app.include_router(models.router, prefix="/api")

    async def scenario():
        transport = httpx.ASGITransport(app=app, client=(host, 12345))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            listing = (await client.get("/api/models/list")).json()
            assert listing["can_open_model_folders"] is can_open
            assert {model["id"]: model["has_local_path"] for model in listing["models"]} == {
                "installed": True, "missing": False,
            }
            headers = {"X-HiImage-Action": "open-folder"}
            installed = await client.post("/api/models/installed/open-folder", headers=headers)
            missing = await client.post("/api/models/missing/open-folder", headers=headers)
            assert installed.status_code == (200 if can_open else 403)
            assert missing.status_code == (404 if can_open else 403)

    asyncio.run(scenario())
    assert opened == ([(str(tmp_path), "explore")] if can_open else [])
