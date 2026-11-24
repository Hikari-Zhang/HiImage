"""Exercise the actual proxy routes with a controllable Immich upstream."""
import io
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from PIL import Image

from app.routers import immich
from app.services import immich as service

TOKEN = "test-access-token-at-least-24"
HEADERS = {"X-HiImage-Immich-Access": TOKEN}
SOURCE, TARGET, OWNER, ALBUM = (str(uuid4()) for _ in range(4))
TAKEN = "2024-01-02T03:04:05Z"


def png(color):
    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), color).save(buffer, "PNG")
    return buffer.getvalue()


ORIGINAL = png("black")
RESULT = png("white")


class Upstream:
    def __init__(self):
        self.calls = []
        self.target_album = False
        self.trashed = False
        self.trash_enabled = True
        self.upload_status = "created"
        self.upload_error = False
        self.migration_error = False
        self.target_checksum = service.checksum(RESULT)
        self.features_checks = 0
        self.disable_before_delete = False
        self.delete_timeout = False

    def call(self, method, path, **options):
        self.calls.append((method, path))
        if path == "/server/version":
            return {"major": 2, "minor": 7, "patch": 5}
        if path == "/server/features":
            self.features_checks += 1
            return {"trash": self.trash_enabled and not (self.disable_before_delete and self.features_checks >= 3)}
        if path == "/users/me":
            return {"id": OWNER, "name": "Test account"}
        if path == "/shared-links":
            return []
        if path == "/search/metadata":
            return {"assets": {"items": [{"id": SOURCE, "type": "IMAGE", "originalFileName": "original.png"}],
                               "nextPage": None}}
        if path.endswith("/original") or path.endswith("/thumbnail"):
            return ORIGINAL
        if path == "/albums":
            source = options["params"].get("assetId")
            return [{"id": ALBUM, "albumName": "Test album"}] if source != TARGET or self.target_album else []
        if path == f"/albums/{ALBUM}/assets":
            if self.migration_error:
                raise HTTPException(403, "album denied")
            self.target_album = True
            return [{"success": True, "id": TARGET}]
        if path in {f"/assets/{SOURCE}", f"/assets/{TARGET}"}:
            original = path.endswith(SOURCE)
            return {"id": SOURCE if original else TARGET, "type": "IMAGE", "ownerId": OWNER,
                    "isTrashed": self.trashed if original else False, "visibility": "timeline",
                    "isOffline": False, "isEdited": False, "isFavorite": True, "updatedAt": TAKEN,
                    "fileCreatedAt": TAKEN, "hasMetadata": True,
                    "checksum": service.checksum(ORIGINAL) if original else self.target_checksum}
        if (method, path) == ("POST", "/assets"):
            if self.upload_error:
                raise HTTPException(502, "upload timeout")
            return {"id": TARGET, "status": self.upload_status}
        if (method, path) == ("DELETE", "/assets"):
            assert self.target_album
            assert options["json"] == {"ids": [SOURCE], "force": False}
            self.trashed = True
            if self.delete_timeout:
                raise HTTPException(502, "trash timeout")
            return None
        raise AssertionError((method, path))


@pytest.fixture()
def integration(monkeypatch, tmp_path):
    upstream = Upstream()
    monkeypatch.setenv("HIIMAGE_IMMICH_ACCESS_TOKEN", TOKEN)
    monkeypatch.setattr(service, "ROOT", tmp_path)
    monkeypatch.setattr(immich, "CONFIG", tmp_path / "connection.json")
    monkeypatch.setattr(service.Immich, "call", lambda _self, *args, **kwargs: upstream.call(*args, **kwargs))
    app = FastAPI()
    app.include_router(immich.router, prefix="/api")
    with TestClient(app, base_url="http://testserver/api/immich/") as client:
        assert client.get("connection").status_code == 401
        client.headers.update(HEADERS)
        yield client, upstream


def configure(client, mode="replace"):
    response = client.put("connection", json={"url": "http://immich:2283/api/", "key": "secret-key", "mode": mode})
    assert response.status_code == 200, response.text
    assert "secret-key" not in response.text
    assert response.json()["url"] == "http://immich:2283"
    return response.json()["generation"]


def confirmation(client):
    response = client.get(f"assets/{SOURCE}/original")
    assert response.content == ORIGINAL
    preview = client.post("replace/preview", json={"source": response.headers["X-Immich-Source"]})
    assert preview.status_code == 200, preview.text
    return preview.json()["confirmation"]


def submit(client, path, **fields):
    return client.post(path, data=fields, files={"image": ("test.png", RESULT, "image/png")})


def test_configure_browse_import_and_save(integration):
    client, upstream = integration
    generation = configure(client, "upload")
    assert client.get("assets", params={"album": ALBUM}).json()["items"][0]["id"] == SOURCE
    assert client.get(f"assets/{SOURCE}/thumbnail").content == ORIGINAL
    token = client.get(f"assets/{SOURCE}/original").headers["X-Immich-Source"]
    operation = str(uuid4())
    upstream.migration_error = True
    first = submit(client, "save", operation=operation, generation=generation, album=ALBUM, source=token)
    assert first.json()["album_pending"] is True
    upstream.migration_error = False
    result = submit(client, "save", operation=operation, generation=generation, album=ALBUM, source=token)
    assert result.json()["id"] == TARGET
    assert not result.json().get("album_pending")
    assert upstream.calls.count(("POST", "/assets")) == 1
    assert ("DELETE", "/assets") not in upstream.calls


@pytest.mark.parametrize("failure", ["upload_error", "duplicate", "checksum", "migration_error", "disable_before_delete"])
def test_original_stays_when_replacement_cannot_be_verified(integration, failure):
    client, upstream = integration
    configure(client)
    if failure == "duplicate":
        upstream.upload_status = "duplicate"
    elif failure == "checksum":
        upstream.target_checksum = "wrong"
    else:
        setattr(upstream, failure, True)
    result = submit(client, "replace", operation=str(uuid4()), confirmation=confirmation(client))
    assert result.status_code in {409, 502} or result.json()["migration_pending"] is True
    assert ("DELETE", "/assets") not in upstream.calls


@pytest.mark.parametrize("delete_timeout", [False, True])
def test_verified_replace_is_idempotent(integration, delete_timeout):
    client, upstream = integration
    configure(client)
    upstream.delete_timeout = delete_timeout
    params = {"operation": str(uuid4()), "confirmation": confirmation(client)}
    first = submit(client, "replace", **params)
    assert first.status_code == (502 if delete_timeout else 200)
    second = submit(client, "replace", **params)
    assert second.json()["state"] == "replaced"
    assert upstream.calls.count(("POST", "/assets")) == 1
    assert upstream.calls.count(("DELETE", "/assets")) == 1
    assert upstream.calls.index(("PUT", f"/albums/{ALBUM}/assets")) < upstream.calls.index(("DELETE", "/assets"))




def test_rejects_forged_source_and_changed_connection(integration):
    client, upstream = integration
    generation = configure(client, "upload")
    forged = immich.seal({"id": SOURCE, "generation": generation, "expires": 4102444800}, TOKEN)
    assert client.post("replace/preview", json={"source": forged}).status_code == 409
    configure(client, "replace")
    result = submit(client, "save", operation=str(uuid4()), generation=generation)
    assert result.status_code == 409
    assert ("POST", "/assets") not in upstream.calls


def test_migration_can_resume_without_uploading_again(integration):
    client, upstream = integration
    configure(client)
    source = confirmation(client)
    upstream.migration_error = True
    first = submit(client, "replace", operation=str(uuid4()), confirmation=source)
    assert first.json()["migration_pending"] is True
    assert client.get("operations").json()[0]["state"] == "migration_pending"

    upstream.migration_error = False
    resumed = submit(client, "replace", operation=str(uuid4()), confirmation=confirmation(client))
    assert resumed.json()["state"] == "replaced"
    assert upstream.calls.count(("POST", "/assets")) == 1
    assert client.get("operations").json()[0]["state"] == "replaced"
