"""HiImage's authenticated Immich integration."""
import base64
import binascii
import hmac
import json
import os
import time
from datetime import datetime, timezone
from threading import Lock
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from typing import Literal

from app.services import immich as service
from app.services.immich_replace import snapshot

CONFIG = service.ROOT / "connection.json"
write_lock = Lock()


def authorize(access: str = Header("", alias="X-HiImage-Immich-Access")) -> None:
    secret = os.environ.get("HIIMAGE_IMMICH_ACCESS_TOKEN", "")
    if len(secret) < 24:
        raise HTTPException(503, "请先在 HiImage 后端设置至少 24 字符的 HIIMAGE_IMMICH_ACCESS_TOKEN")
    if not hmac.compare_digest(access.encode(), secret.encode()):
        raise HTTPException(401, "HiImage Immich 访问令牌无效")


router = APIRouter(prefix="/immich", dependencies=[Depends(authorize)])


class Connection(BaseModel):
    url: str
    key: str = ""
    mode: Literal["read", "upload", "replace"] = "upload"


def config():
    return service.read_record(CONFIG)


def client():
    current = config()
    if not current:
        raise HTTPException(409, "请先配置 Immich")
    return service.Immich(current)


def public(current):
    return {"configured": bool(current), "url": current["url"] if current else "",
            "mode": current["mode"] if current else "upload",
            "generation": current["generation"] if current else ""}


def candidate(data):
    url = service.normalize_url(data.url)
    current = config()
    key = data.key.strip()
    if not key and current and current["url"] == url:
        key = current["key"]
    if not key or any(char.isspace() for char in key) or not key.isascii():
        raise HTTPException(422, "请输入 Immich API Key，切换服务地址时需重新输入")
    if current and (url, key, data.mode) == (current["url"], current["key"], current["mode"]):
        return current
    return service.new_connection(url, key, data.mode)


def probe(remote):
    version = remote.version()
    user = remote.call("GET", "/users/me")
    remote.search(size=1)
    return {"version": ".".join(str(version[k]) for k in ("major", "minor", "patch")),
            "account": user.get("name") or user.get("email") or user["id"]}


@router.get("/connection")
def get_connection():
    return public(config())


@router.post("/connection/test")
def test_connection(data: Connection):
    return probe(service.Immich(candidate(data)))


@router.put("/connection")
def save_connection(data: Connection):
    with write_lock:
        current = candidate(data)
        result = probe(service.Immich(current))
        service.write_record(CONFIG, current)
        return {**public(current), **result}


@router.get("/albums")
def albums(remote=Depends(client)):
    return [{"id": entry["id"], "name": entry["albumName"]} for entry in remote.albums()]


@router.get("/assets")
def assets(page: int = 1, name: str = "", album: UUID | None = None,
           after: str = "", before: str = "", remote=Depends(client)):
    if not 1 <= page <= 10000 or len(name) > 255:
        raise HTTPException(422, "筛选参数无效")
    filters = {"page": page, "order": "desc"}
    if album:
        filters["albumIds"] = [str(album)]
    if name:
        filters["originalFileName"] = name
    if after:
        filters["takenAfter"] = timestamp(after)
    if before:
        filters["takenBefore"] = timestamp(before)
    result = remote.search(**filters)
    return {"next": result.get("nextPage"), "items": [
        {"id": row["id"], "name": row["originalFileName"], "taken": row.get("localDateTime")}
        for row in result["items"] if row.get("type") == "IMAGE"
    ]}


@router.get("/assets/{source}/thumbnail")
def thumbnail(source: UUID, size: Literal["thumbnail", "preview"] = "thumbnail", remote=Depends(client)):
    data = remote.call("GET", f"/assets/{source}/thumbnail", params={"size": size},
                       limit=(8 if size == "preview" else 2) * 1024 * 1024, binary=True)
    mime, _ = service.image_format(data)
    return Response(data, media_type=mime, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).isoformat()
    except (ValueError, AttributeError):
        raise HTTPException(422, "日期格式无效") from None


def seal(data, secret):
    payload = base64.urlsafe_b64encode(json.dumps(data).encode()).decode()
    return payload + "." + hmac.digest(secret.encode(), payload.encode(), "sha256").hex()


def unseal(token, secret, generation):
    try:
        payload, signature = token.split(".", 1)
        expected = hmac.digest(secret.encode(), payload.encode(), "sha256").hex()
        if not hmac.compare_digest(signature.encode(), expected.encode()):
            raise ValueError()
        data = json.loads(base64.urlsafe_b64decode(payload))
        if data["generation"] == generation and data["expires"] > time.time():
            return data
    except (ValueError, KeyError, binascii.Error):
        pass
    raise HTTPException(409, "Immich 来源已失效，请重新导入")


def taken_at(asset):
    return (asset.get("exifInfo") or {}).get("dateTimeOriginal") or asset.get("fileCreatedAt")


@router.get("/assets/{source}/original")
def original(source: UUID, remote=Depends(client)):
    row = remote.asset(source)
    if row.get("type") != "IMAGE" or row.get("isTrashed") or row.get("visibility") == "locked":
        raise HTTPException(415, "此资产无法导入")
    data = remote.call("GET", f"/assets/{source}/original", limit=service.image_limit(), binary=True)
    mime, _ = service.image_format(data)
    token = seal({"id": str(source), "checksum": service.checksum(data), "taken": taken_at(row),
                  "generation": remote.config["generation"], "expires": time.time() + 86400}, remote.config["signing_key"])
    return Response(data, media_type=mime, headers={"X-Immich-Source": token, "Cache-Control": "no-store",
                                                    "X-Content-Type-Options": "nosniff"})


@router.post("/save")
def save_image(image: UploadFile = File(...), operation: UUID = Form(...), generation: str = Form(...),
               album: UUID | None = Form(None), source: str = Form(""), use_duplicate: bool = Form(False),
               taken: str = Form("")):
    with write_lock:
        remote = client()
        if generation != remote.config["generation"]:
            raise HTTPException(409, "Immich 连接已变化，请重新打开保存窗口")
        if remote.config["mode"] == "read":
            raise HTTPException(403, "此连接为只读")
        data, mime, name = output_file(image)
        if source:
            taken = unseal(source, remote.config["signing_key"], remote.config["generation"]).get("taken") or taken
        taken = timestamp(taken) if taken else datetime.now(timezone.utc).isoformat()
        path = operation_path(remote, operation)
        record = service.read_record(path)
        fingerprint = {"kind": "save", "checksum": service.checksum(data), "album": str(album)}
        if record and record["fingerprint"] != fingerprint:
            raise HTTPException(409, "操作已绑定其他结果")
        if not record or record["state"] == "rejected":
            record = upload_result(remote, path, fingerprint, data, name, mime, taken, str(operation))
        if record["state"] == "uploading":
            raise HTTPException(409, "上传状态待确认，请在 Immich 中检查结果")
        if record["state"] not in {"created", "duplicate"}:
            raise HTTPException(502, "Immich 返回了意外的上传状态")
        if album and (record["state"] == "created" or use_duplicate) and not record.get("album_added"):
            try:
                remote.add_to_album(str(album), record["target"])
            except HTTPException:
                return {**outcome(remote, record), "album_pending": True}
            record["album_added"] = True
            service.write_record(path, record)
        return outcome(remote, record)


def upload_result(remote, path, fingerprint, data, name, mime, taken, operation, *, favorite=None):
    record = {"fingerprint": fingerprint, "state": "uploading", "name": name}
    service.write_record(path, record)
    try:
        uploaded = remote.upload(data, name, mime, taken, operation, favorite=favorite)
    except HTTPException as exc:
        if exc.status_code in {401, 403, 413, 429}:
            record["state"] = "rejected"
            service.write_record(path, record)
        raise
    record.update(state=uploaded["status"], target=service.asset_id(uploaded["id"]))
    service.write_record(path, record)
    return record


def output_file(image):
    data = image.file.read(service.image_limit() + 1)
    if len(data) > service.image_limit():
        raise HTTPException(413, "结果文件超过上传限制")
    mime, extension = service.image_format(data)
    name = (image.filename or "HiImage").replace("\\", "/").split("/")[-1].rsplit(".", 1)[0]
    name = "".join(c for c in name if c.isprintable() and c not in '<>:"|?*').strip()[:160] or "HiImage"
    return data, mime, f"{name}.{extension}"


def operation_path(remote, operation):
    return service.ROOT / "operations" / remote.config["generation"] / f"{operation}.json"


def outcome(remote, record):
    target = record["target"]
    return {"id": target, "state": record["state"], "url": f"{remote.config['url']}/photos/{target}",
            "album_added": bool(record.get("album_added"))}


class ReplacePreview(BaseModel):
    source: str


@router.post("/replace/preview")
def preview_replace(data: ReplacePreview, remote=Depends(client)):
    source = unseal(data.source, remote.config["signing_key"], remote.config["generation"])
    if not source.get("taken"):
        raise HTTPException(409, "无法确认原图拍摄时间")
    details = snapshot(remote, source)
    return {"albums": details["albums"], "favorite": details["favorite"], "taken": source["taken"],
            "confirmation": seal({**source, "snapshot": details, "expires": time.time() + 1800}, remote.config["signing_key"])}


@router.post("/replace")
def replace_image(image: UploadFile = File(...), operation: UUID = Form(...),
                  confirmation: str = Form(...)):
    with write_lock:
        remote = client()
        source = unseal(confirmation, remote.config["signing_key"], remote.config["generation"])
        expected = source.get("snapshot")
        if not expected:
            raise HTTPException(409, "请先确认替换详情")
        path = operation_path(remote, f"replace-{source['id']}")
        data, mime, name = output_file(image)
        fingerprint = {"kind": "replace",
                       "checksum": service.checksum(data), "snapshot": expected}
        record = service.read_record(path)
        if record and record["fingerprint"] != fingerprint:
            raise HTTPException(409, "此原图已有替换记录，请检查 Immich 中的处理结果")
        if record and record["state"] in {"trashing", "replaced"}:
            row = remote.asset(source["id"])
            if row.get("isTrashed") is True:
                record["state"] = "replaced"
                service.write_record(path, record)
                return outcome(remote, record)
            raise HTTPException(409, "移入回收站状态待确认，请在 Immich 中检查原图和新图")
        if snapshot(remote, source) != expected:
            raise HTTPException(409, "原图状态或关联已改变，请重新预检")
        if not record or record["state"] == "rejected":
            record = upload_result(remote, path, fingerprint, data, name, mime, timestamp(source["taken"]),
                                   str(operation), favorite=expected["favorite"])
        if record["state"] == "uploading":
            raise HTTPException(409, "上传状态待确认，原图仍保留，请在 Immich 中检查结果")
        if record["state"] != "created" or record["target"] == source["id"]:
            raise HTTPException(409, "结果未创建独立的新图片，原图仍保留")
        target = record["target"]
        try:
            verify_target(remote.asset(target), source["taken"], expected, fingerprint["checksum"])
            required = {album["id"] for album in expected["albums"]}
            existing = {album["id"] for album in remote.albums(target)}
            for album in sorted(required - existing):
                remote.add_to_album(album, target)
            if required - {album["id"] for album in remote.albums(target)}:
                raise HTTPException(409, "相册迁移尚未验证")
            if snapshot(remote, source) != expected:
                raise HTTPException(409, "原图状态或关联已改变")
            verify_target(remote.asset(target), source["taken"], expected, fingerprint["checksum"])
        except HTTPException as exc:
            return {**outcome(remote, record), "migration_pending": True, "message": exc.detail}

        record["state"] = "trashing"
        service.write_record(path, record)
        remote.call("DELETE", "/assets", json={"ids": [source["id"]], "force": False})
        if remote.asset(source["id"]).get("isTrashed") is not True:
            raise HTTPException(409, "移入回收站状态待确认，请在 Immich 中检查")
        record["state"] = "replaced"
        service.write_record(path, record)
        return outcome(remote, record)


def verify_target(asset, taken, expected, digest):
    if (asset.get("ownerId") != expected["owner"]
            or asset.get("type") != "IMAGE" or asset.get("visibility") != "timeline"
            or asset.get("checksum") != digest or asset.get("isTrashed") is not False
            or asset.get("isFavorite") is not expected["favorite"]
            or not asset.get("hasMetadata") or not taken_at(asset)
            or timestamp(taken_at(asset)) != timestamp(taken)):
        raise HTTPException(409, "新图的内容、归属、收藏或拍摄时间尚未验证")


@router.get("/operations")
def recent_operations(remote=Depends(client)):
    directory = operation_path(remote, "").parent
    with write_lock:
        paths = sorted(directory.glob("*.json"), key=lambda entry: entry.stat().st_mtime, reverse=True)[:20]
        results = []
        for path in paths:
            record = service.read_record(path)
            state = record["state"]
            kind = record["fingerprint"]["kind"]
            if kind == "replace" and state == "created":
                state = "migration_pending"
            if kind == "save" and state == "created" and record["fingerprint"]["album"] != "None" and not record.get("album_added"):
                state = "album_pending"
            link = outcome(remote, record)["url"] if record.get("target") else None
            results.append({"name": record["name"], "state": state, "kind": kind, "url": link})
        return results
