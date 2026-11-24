"""Bounded, server-side access to the configured Immich API."""
import base64
import hashlib
import io
import json
import os
import secrets
import tempfile
from pathlib import Path
from threading import BoundedSemaphore
from time import monotonic
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import requests
from fastapi import HTTPException
from PIL import Image

from app.config import get
from app.services.image_codec import enforce_rgba_budget
from core.paths import CONFIG_DIR

ROOT = CONFIG_DIR / "immich"
UPSTREAM_SLOTS = BoundedSemaphore(6)
JSON_LIMIT = 8 * 1024 * 1024
FORMATS = {"PNG": ("image/png", "png"), "JPEG": ("image/jpeg", "jpg"), "WEBP": ("image/webp", "webp")}


def image_limit():
    return int(get("image.max_decode_mb", 30)) * 1024 * 1024


def read_record(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None


def write_record(path: Path, data: dict):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False, mode="w", encoding="utf-8") as file:
        name = file.name
        try:
            json.dump(data, file)
            file.flush()
            os.fsync(file.fileno())
            file.close()
            os.replace(name, path)
        finally:
            Path(name).unlink(missing_ok=True)


def normalize_url(raw):
    value = raw.strip().rstrip("/")
    try:
        parsed = urlsplit(value)
        parsed.port
        if (parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username
                and not parsed.password and not parsed.query and not parsed.fragment
                and not any(c.isspace() or c == "\\" for c in value)):
            return value.removesuffix("/api")
    except ValueError:
        pass
    raise HTTPException(422, "请输入有效的 Immich HTTP(S) 服务地址")


def asset_id(value):
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError):
        raise HTTPException(502, "Immich 返回了无效的资产 ID") from None


def checksum(data):
    return base64.b64encode(hashlib.sha1(data).digest()).decode("ascii")


def image_format(data):
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format not in FORMATS or getattr(image, "is_animated", False):
                raise HTTPException(415, "请选择 PNG、JPEG 或 WebP 静态图片")
            enforce_rgba_budget(*image.size, max_mb=int(get("image.max_input_rgba_mb", 256)), label="输入")
            image.load()
            return FORMATS[image.format]
    except Image.DecompressionBombError:
        raise HTTPException(413, "图片像素量超过限制") from None
    except (OSError, ValueError):
        raise HTTPException(422, "图片无法解码") from None


class Immich:
    def __init__(self, config):
        self.config = config

    def call(self, method, path, *, limit=JSON_LIMIT, binary=False, **kwargs):
        if not UPSTREAM_SLOTS.acquire(timeout=10):
            raise HTTPException(429, "Immich 请求繁忙，请稍后重试")
        deadline = monotonic() + 45
        try:
            with requests.Session() as session:
                session.trust_env = False
                with session.request(
                    method, self.config["url"] + "/api" + path,
                    headers={"x-api-key": self.config["key"]}, timeout=(5, 25),
                    allow_redirects=False, stream=True, **kwargs,
                ) as response:
                    if not 200 <= response.status_code < 300:
                        status = response.status_code
                        raise HTTPException(status if status in {401, 403, 404, 413, 429} else 502,
                                            f"Immich 请求失败（{status}）")
                    data = bytearray()
                    for chunk in response.iter_content(64 * 1024):
                        if monotonic() > deadline:
                            raise HTTPException(502, "Immich 响应超时")
                        data.extend(chunk)
                        if len(data) > limit:
                            raise HTTPException(413, "Immich 响应超过大小限制")
                    return bytes(data) if binary else json.loads(data) if data else None
        except requests.RequestException:
            raise HTTPException(502, "Immich 连接失败或超时，请检查服务状态") from None
        except (ValueError, UnicodeError):
            raise HTTPException(502, "Immich 返回了无效响应") from None
        finally:
            UPSTREAM_SLOTS.release()

    def version(self):
        return self.call("GET", "/server/version")

    def search(self, **filters):
        return self.call("POST", "/search/metadata", json={"type": "IMAGE", "size": 24, **filters})["assets"]

    def albums(self, source=None):
        params = {"assetId": source} if source else {}
        return self.call("GET", "/albums", params=params)

    def asset(self, source):
        return self.call("GET", f"/assets/{source}")

    def upload(self, data, name, mime, taken, operation, *, favorite=None):
        fields = {"fileCreatedAt": taken, "fileModifiedAt": taken}
        if favorite is not None:
            fields["isFavorite"] = str(favorite).lower()
        if self.version()["major"] == 2:
            fields.update(deviceId="HiImage", deviceAssetId=operation)
        return self.call("POST", "/assets", data=fields, files={"assetData": (name, data, mime)})

    def add_to_album(self, album, target):
        result = self.call("PUT", f"/albums/{album}/assets", json={"ids": [target]})
        if not any(row.get("id") == target and row.get("success") is True for row in result):
            if album not in {entry["id"] for entry in self.albums(target)}:
                raise HTTPException(409, "图片已上传，加入相册失败")


def new_connection(url, key, mode):
    return {"url": url, "key": key, "mode": mode, "generation": uuid4().hex,
            "signing_key": secrets.token_hex(32)}
