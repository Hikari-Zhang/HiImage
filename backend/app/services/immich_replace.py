"""Preflight and ordered replacement steps for the validated Immich API contract."""
from fastapi import HTTPException

from app.services.immich import asset_id


def snapshot(remote, source):
    if remote.config["mode"] != "replace":
        raise HTTPException(403, "请在 Immich 设置中启用替换")
    version = remote.version()
    if tuple(version.get(key) for key in ("major", "minor", "patch")) != (2, 7, 5):
        raise HTTPException(409, "此版本只对 Immich 2.7.5 开放替换，可另存为新图片")
    if remote.call("GET", "/server/features").get("trash") is not True:
        raise HTTPException(409, "Immich 回收站未启用，无法替换")
    owner = remote.call("GET", "/users/me")["id"]
    row = remote.asset(source["id"])
    if (row.get("ownerId") != owner or row.get("type") != "IMAGE"
            or row.get("checksum") != source["checksum"]
            or row.get("isTrashed") is not False or row.get("visibility") != "timeline"
            or row.get("isOffline") is not False or row.get("isEdited") is not False
            or row.get("libraryId") or row.get("livePhotoVideoId") or row.get("stack")
            or not isinstance(row.get("isFavorite"), bool) or not row.get("updatedAt")):
        raise HTTPException(409, "原图已变化或属于无法安全替换的资产")
    links = remote.call("GET", "/shared-links")
    if not isinstance(links, list) or any(
        not isinstance(link.get("assets"), list)
        or any(item.get("id") == source["id"] for item in link["assets"])
        for link in links
    ):
        raise HTTPException(409, "原图存在直接分享链接或无法核验分享状态")
    albums = sorted(({"id": asset_id(item["id"]), "name": item["albumName"]}
                     for item in remote.albums(source["id"])), key=lambda item: item["id"])
    return {"owner": owner, "updated": row["updatedAt"], "favorite": row["isFavorite"], "albums": albums}
