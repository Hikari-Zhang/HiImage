"""验证 Web 输入在像素分配和模型创建之前被拒绝。"""
import asyncio
import base64
import io
import sys
from types import SimpleNamespace

import httpx
import numpy as np
import pytest
from fastapi import FastAPI, HTTPException
from PIL import Image

from app.services import image_codec as codec


def png(width=8, height=6, mode="RGB"):
    buffer = io.BytesIO()
    Image.new(mode, (width, height)).save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


@pytest.mark.parametrize("decoder", [codec.decode_image, codec.decode_mask, codec.decode_rgba_image])
def test_compressed_large_image_rejected_before_opencv_decode(monkeypatch, decoder):
    value = png(600, 600)
    original_get = codec.get
    monkeypatch.setattr(codec, "get", lambda key, default=None:
                        1 if key == "image.max_input_rgba_mb" else original_get(key, default))
    def unexpected_decode(*args):
        pytest.fail("不应为超像素预算图片分配 OpenCV 缓冲区")
    monkeypatch.setattr(codec.cv2, "imdecode", unexpected_decode)
    with pytest.raises(HTTPException) as error:
        decoder(value)
    assert error.value.status_code == 413


@pytest.mark.parametrize("value", ["not-base64!", base64.b64encode(b"not an image").decode(), ""])
def test_invalid_image_returns_client_error(value):
    with pytest.raises(HTTPException) as error:
        codec.decode_image(value)
    assert error.value.status_code == 422


def test_masks_obey_compressed_byte_budget(monkeypatch):
    monkeypatch.setattr(codec, "get", lambda key, default=None: 0 if key == "image.max_decode_mb" else default)
    with pytest.raises(HTTPException) as error:
        codec.decode_mask(png(mode="L"))
    assert error.value.status_code == 413


@pytest.mark.parametrize("mode", ["L", "RGB", "RGBA"])
def test_rgba_decoder_normalizes_channels(mode):
    image = codec.decode_rgba_image(png(mode=mode))
    assert image.shape == (6, 8, 4)
    assert image.dtype == np.uint8


@pytest.mark.parametrize("route,body", [
    ("/inpaint/with-mask", {"image": png(), "mask": png(4, 4), "device": "cpu"}),
    ("/postprocess/fix", {"original_image": png(), "inpainted_image": png(), "mask": png(4, 4)}),
    ("/pipeline/run", {"image": png(), "mask": png(4, 4)}),
    ("/cutout/refine", {"original_image": png(), "mask": png(4, 4)}),
    ("/cutout/refine", {"original_image": png(), "mask": png(mode="L"), "brush_add": png(4, 4)}),
    ("/postprocess/fix", {"original_image": png(), "inpainted_image": png(4, 4), "mask": png(mode="L")}),
])
def test_routes_reject_mismatched_mask_and_image_dimensions(route, body):
    from app.routers import cutout, inpaint, postprocess
    app = FastAPI()
    for router in (cutout.router, inpaint.router, postprocess.router):
        app.include_router(router)
    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await client.post(route, json={**body, "task_id": "validation-test"})
    response = asyncio.run(request())
    assert response.status_code == 422
    assert "尺寸" in response.json()["detail"]


def test_pipeline_checks_upscale_budget_before_running_model(monkeypatch):
    from app.routers import postprocess
    from app.services import upscale_budget
    monkeypatch.setattr(upscale_budget, "output_scale", lambda *_args: 2000)
    async def unexpected_inference(*_args):
        pytest.fail("超出输出预算时不应调用模型")
    monkeypatch.setattr(postprocess.inference_scheduler, "run_blocking", unexpected_inference)
    request = postprocess.PipelineRequest(image=png(), mask=png(mode="L"),
                                        upscale_enabled=True, task_id="pipeline-budget")
    with pytest.raises(HTTPException) as error:
        asyncio.run(postprocess.run_pipeline(request))
    assert error.value.status_code == 413


def test_composite_uses_validated_decoder():
    from app.routers.cutout import CompositeRequest, composite_cutout
    with pytest.raises(HTTPException) as error:
        asyncio.run(composite_cutout(CompositeRequest(image="invalid", task_id="composite-test")))
    assert error.value.status_code == 422


@pytest.mark.parametrize("scale", [0, -1, float("inf"), float("nan")])
def test_invalid_scale_rejected(scale):
    with pytest.raises(HTTPException) as error:
        codec.enforce_rgba_budget(10, 10, scale)
    assert error.value.status_code == 422


def test_batch_checks_aggregate_output_before_creating_model(monkeypatch):
    from app.routers import upscale
    def unexpected_creation(**kwargs):
        pytest.fail("批量输出超预算时不应创建模型")
    monkeypatch.setitem(sys.modules, "core.upscaler", SimpleNamespace(Upscaler=unexpected_creation))
    monkeypatch.setattr(upscale, "get", lambda key, default=None: 1 if key == "image.max_output_mb" else default)
    request = upscale.BatchUpscaleRequest(images=[png(400, 400), png(400, 400)], outscale=1,
                                         device="cpu", task_id="batch-budget")
    with pytest.raises(HTTPException) as error:
        asyncio.run(upscale.upscale_image_batch(request))
    assert error.value.status_code == 413
    assert "批量" in error.value.detail
