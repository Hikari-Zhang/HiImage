import base64
import io
import sys
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from app.routers import upscale
from core.upscaler import Upscaler


@pytest.mark.parametrize("device", ["cpu", "cuda", "mps"])
def test_upsampler_uses_requested_torch_device(monkeypatch, device):
    monkeypatch.setitem(sys.modules, "realesrgan", SimpleNamespace(RealESRGANer=lambda **options: options))
    monkeypatch.setitem(sys.modules, "basicsr.archs.rrdbnet_arch", SimpleNamespace(RRDBNet=lambda **options: object()))

    options = Upscaler(device=device)._build_upsampler("weights.pth", 384)

    assert options["device"].type == device
    assert options["half"] is (device == "cuda")
    assert options["tile"] == 384


def test_automatic_tile_applies_to_preloaded_upsampler():
    upscaler = Upscaler(device="mps")
    tiles = []

    def enhance(image, outscale):
        tiles.append(upscaler._upsampler.tile_size)
        return image, None

    upscaler._upsampler = SimpleNamespace(tile_size=0, enhance=enhance)
    upscaler.upscale(np.zeros((1024, 768, 3), dtype=np.uint8))
    upscaler.upscale(np.zeros((32, 32, 3), dtype=np.uint8))

    assert tiles == [384, 0]


@pytest.mark.parametrize(("path", "image_field"), [("/upscale", "image"), ("/upscale/batch", "images")])
def test_missing_dependencies_return_setup_guidance(monkeypatch, isolated_task_registry, path, image_field):
    def unavailable(*args, **kwargs):
        raise ImportError("No module named 'realesrgan'")

    monkeypatch.setattr(upscale.image_processing, "upscale_image", unavailable)
    monkeypatch.setattr(upscale.image_processing, "create_upscaler", lambda **kwargs: SimpleNamespace(
        _ensure_model_loaded=unavailable, close=lambda: None,
    ))
    buffer = io.BytesIO()
    Image.new("RGB", (32, 32)).save(buffer, "PNG")
    image = base64.b64encode(buffer.getvalue()).decode()
    payload = {image_field: image if image_field == "image" else [image], "task_id": "missing-upscale-library"}

    app = FastAPI()
    app.include_router(upscale.router, prefix="/api")
    with TestClient(app) as client:
        response = client.post("/api" + path, json=payload)

    assert response.status_code == 503
    assert response.json()["detail"] == upscale.UPSCALE_DEPENDENCY_MESSAGE
    assert isolated_task_registry.get(payload["task_id"])["status"] == "failed"
