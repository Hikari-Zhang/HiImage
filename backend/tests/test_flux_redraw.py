import asyncio
import os
from pathlib import Path
from types import SimpleNamespace
import sys

import numpy as np
import pytest

from app.routers import inpaint, postprocess
from app.redraw_options import RedrawOptions
from core.model_checker import ModelChecker
from core.model_registry import get_model


def test_watermark_catalog_exposes_default_and_repair_pipelines():
    from app.services.model_catalog import model_groups

    catalog = model_groups("watermark_removal")
    models = {model["id"]: model for group in catalog["groups"] for model in group["models"]}
    assert catalog["default_model"] == "wm_lama"
    assert catalog["available"] is True
    assert catalog["unavailable_reason"] == ""
    assert models["wm_lama"]["device_override"] == "cpu"
    assert models["flux_fill"]["pipeline"] == "flux_fill"
    for model_id in models:
        assert models[model_id]["watermark_defaults"] == get_model(model_id)["watermark_defaults"]


def test_redraw_cpu_rejected_before_decoding_or_downloading(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("不支持的设备不能进入解码或下载")

    monkeypatch.setattr(inpaint, "decode_image", fail)
    monkeypatch.setattr(inpaint, "ensure_model_ready_with_progress", fail)
    response = asyncio.run(inpaint.inpaint_with_rois(inpaint.InpaintRequest(
        image="invalid", rois=[], model="flux_fill", device="cpu", task_id="redraw-cpu",
    )))
    assert response.status_code == 422
    assert b"REDRAW_CUDA_REQUIRED" in response.body


@pytest.mark.parametrize("route", ["mask", "roi", "pipeline"])
def test_watermark_preset_and_explicit_options_are_forwarded_to_worker(monkeypatch, route):
    image = np.zeros((4, 6, 3), np.uint8)
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(
        cuda=SimpleNamespace(is_available=lambda: True)))
    module = postprocess if route == "pipeline" else inpaint
    monkeypatch.setattr(module, "decode_image", lambda _: image)
    monkeypatch.setattr(module, "decode_mask", lambda _: image[:, :, 0])
    defaults = get_model("flux_fill")["watermark_defaults"]

    async def ready(*_):
        return True, ""

    async def run(_scheduler, operation, pixels, *, options, mask=None, **kwargs):
        assert operation == ("pipeline" if route == "pipeline" else "inpaint")
        assert pixels is image
        if route != "roi":
            assert mask.shape == image.shape[:2]
        assert options.get("model_id", options.get("inpaint_model")) == "flux_fill"
        assert options["dilation"] == (0 if route == "roi" else defaults["dilation"])
        assert options["redraw"] == {**defaults["redraw"], "prompt": "brick wall", "seed": 7}
        return image

    monkeypatch.setattr(module, "ensure_model_ready_with_progress", ready)
    monkeypatch.setattr(module, "run_repair", run)
    options = dict(image="test", device="cuda", task_id="redraw-options", redraw=RedrawOptions(prompt="brick wall", seed=7))
    if route == "pipeline":
        result = postprocess.run_pipeline(postprocess.PipelineRequest(mask="test", inpaint_model="flux_fill", **options))
    elif route == "roi":
        result = inpaint.inpaint_with_rois(inpaint.InpaintRequest(rois=[], model="flux_fill", dilation=0, **options))
    else:
        result = inpaint.inpaint_with_mask(inpaint.InpaintWithMaskRequest(mask="test", model="flux_fill", **options))
    response = asyncio.run(result)
    assert response["image"]


def test_required_hf_files_distinguish_partial_corrupted_and_complete(monkeypatch, tmp_path):
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    index = snapshot / "model_index.json"
    index.write_bytes(b"{}")
    monkeypatch.setattr("core.paths.find_hf_snapshot", lambda _: snapshot)
    checker = ModelChecker()
    config = {**get_model("flux_fill"), "hf_required_files": {"model_index.json": 2, "weights.safetensors": 4}}
    assert checker._check_hf_cache(config).status == "partial"
    weight = snapshot / "weights.safetensors"
    weight.write_bytes(b"bad")
    assert checker._check_hf_cache(config).status == "corrupted"
    weight.write_bytes(b"good")
    assert checker._check_hf_cache(config).status == "ok"


def test_download_fetches_only_declared_diffusers_files(monkeypatch, tmp_path):
    import huggingface_hub
    from core.downloaders import download_hf

    files = {"model_index.json": 2, "transformer/model.safetensors": 4}
    fetched = []
    weight = tmp_path / "weight"

    def download(**kwargs):
        name = kwargs["filename"]
        fetched.append(name)
        weight.write_bytes(b"x" * files[name])
        return str(weight)

    monkeypatch.setattr(huggingface_hub, "list_repo_files", lambda *a, **kw: [*files, "original.safetensors"])
    monkeypatch.setattr(huggingface_hub, "try_to_load_from_cache", lambda *a, **kw: None)
    monkeypatch.setattr(huggingface_hub, "hf_hub_download", download)
    download_hf({**get_model("flux_fill"), "hf_required_files": files})
    assert fetched == list(files)


@pytest.mark.skipif(os.environ.get("HIIMAGE_TEST_FLUX") != "1", reason="需完整 FLUX Fill 权重和 CUDA")
def test_real_flux_redraw_through_worker():
    import base64
    import io
    import time
    from fastapi.testclient import TestClient
    from PIL import Image
    from app.main import app

    assert ModelChecker().check_model("flux_fill").status == "ok"
    source = Path(__file__).parent / "fixtures" / "watermark-residual.jpg"
    with Image.open(source) as photo:
        image = np.array(photo.convert("RGB"))
    mask = np.zeros(image.shape[:2], np.uint8)
    mask[795:885, 540:755] = 255

    def png(array):
        stream = io.BytesIO()
        Image.fromarray(array).save(stream, format="PNG")
        return base64.b64encode(stream.getvalue()).decode()

    started = time.monotonic()
    client = TestClient(app)
    try:
        response = client.post("/api/inpaint/with-mask", json={
            "image": png(image), "mask": png(mask), "model": "flux_fill",
            "device": "cuda", "dilation": 0, "task_id": "flux-real-worker",
            "redraw": {"prompt": "Wet cobblestone pavement with warm reflections", "num_inference_steps": 2, "seed": 42},
        })
    finally:
        client.close()
    assert response.status_code == 200, response.text[:700]
    with Image.open(io.BytesIO(base64.b64decode(response.json()["image"]))) as output:
        result = np.array(output)
    assert result.shape == image.shape
    assert np.array_equal(result[mask == 0], image[mask == 0])
    assert np.any(result[mask > 0] != image[mask > 0])
    print(f"FLUX Fill CUDA worker smoke: {time.monotonic() - started:.2f}s, shape={result.shape}")
