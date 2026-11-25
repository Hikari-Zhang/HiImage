"""Run real model routes against a temporary registry and loopback weight source."""

import hashlib
import os
from pathlib import Path
import shutil
import sys
from tempfile import TemporaryDirectory

from fastapi import FastAPI
from fastapi.responses import Response
import uvicorn


def serve(root: Path) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
    os.environ.update({
        "HIIMAGE_PROJECT_ROOT": str(root),
        "HIIMAGE_CACHE": str(root / "cache"),
        "HIIMAGE_MODELS_DIR": str(root / "models"),
        "HIIMAGE_CONFIG_DIR": str(root / "config"),
    })
    from core import model_registry

    weight = b"HiImage browser test fixture"
    models = [
        {
            "id": model_id, "name": name, "provider": "e2e-fixture", "tags": [],
            "local_path": "models/shared-fixture.pth",
            "download_url": "http://127.0.0.1:18787/api/e2e-fixture/weight",
            "sha256": hashlib.sha256(weight).hexdigest(),
        }
        for model_id, name in (("e2e-primary", "主测试模型"), ("e2e-alias", "共享测试模型"))
    ]
    model_registry.MODELS[:] = models
    model_registry.MODEL_BY_ID.clear()
    model_registry.MODEL_BY_ID.update({model["id"]: model for model in models})
    from app.routers.models import router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.add_api_route("/api/health", lambda: {"status": "ok"})
    app.add_api_route("/api/settings", lambda: {"device": "cpu"})
    app.add_api_route("/api/devices", lambda: {"devices": []})
    app.add_api_route("/api/e2e-fixture", lambda: {"weight_exists": (root / "models/shared-fixture.pth").is_file()})
    app.add_api_route("/api/e2e-fixture/weight", lambda: Response(weight, media_type="application/octet-stream"))

    @app.delete("/api/e2e-fixture", status_code=204)
    def reset_fixture():
        # Playwright 强制停止 Windows WebServer 时不会运行 TemporaryDirectory 清理。
        if root.exists():
            shutil.rmtree(root)
        return Response(status_code=204)

    uvicorn.run(app, host="127.0.0.1", port=18787)


if __name__ == "__main__":
    with TemporaryDirectory(prefix="hiimage-model-browser-") as directory:
        serve(Path(directory))
