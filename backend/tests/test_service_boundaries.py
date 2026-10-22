"""验证服务提取后的调用契约、资源清理和跨线程进度。"""
import ast
import asyncio
import base64
import io
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import httpx
import numpy as np
import pytest
from fastapi import FastAPI
from PIL import Image

from app.services import image_processing, model_catalog, model_readiness, progress


def test_http_routes_do_not_import_other_routes_and_services_do_not_import_routes():
    backend = Path(__file__).resolve().parents[1]
    for folder in (backend / "app/routers", backend / "app/services"):
        for path in folder.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    assert not (node.module or "").startswith("app.routers"), path
    assert "from app." not in (backend / "core/utils.py").read_text(encoding="utf-8")


@pytest.mark.parametrize("kind", ["inpaint", "upscale", "cutout"])
def test_aggregate_and_independent_model_catalogs_are_identical(kind):
    from app.routers import cutout, inpaint, models, upscale
    handlers = {"inpaint": inpaint.get_inpaint_models, "upscale": upscale.get_upscale_models, "cutout": cutout.get_cutout_models}
    async def scenario():
        aggregate = await models.get_model_groups()
        assert aggregate[kind] == await handlers[kind]()
    asyncio.run(scenario())


def test_model_catalog_preserves_order_scale_capabilities_and_cutout_default(monkeypatch):
    monkeypatch.setattr(model_catalog, "get_models_for_mode", lambda mode: [
        {"id": "one", "display_group": "B", "scale": 2, "supports_custom_outscale": True},
        {"id": "two", "display_group": "A", "scale": 4, "outscale": 1},
    ])
    monkeypatch.setattr(model_catalog, "get_default_model", lambda mode: "two")
    groups = model_catalog.model_groups("upscale")["groups"]
    assert [group["label"] for group in groups] == ["B", "A"]
    assert groups[0]["models"][0]["supports_custom_outscale"] is True
    assert groups[1]["models"][0]["outscale"] == 1
    assert model_catalog.model_groups("cutout")["default_model"] == "two"


@pytest.mark.parametrize("use_mask", [False, True])
def test_inpaint_service_forwards_masks_rois_and_callback_and_cleans_up(monkeypatch, use_mask):
    calls = []
    image = np.zeros((2, 2, 3), np.uint8)
    mask = np.zeros((2, 2), np.uint8)
    callback = lambda percent, message: None
    class Executor:
        def supports_mask(self):
            return True
        def execute(self, value, **kwargs):
            calls.append((value, kwargs))
            return value
        def unload_model(self):
            calls.append("unload")
    monkeypatch.setattr(image_processing.ModelExecutorFactory, "create_executor", lambda *_args: Executor())
    image_processing.inpaint_image(image, "wm_lama", "cpu", mask=mask if use_mask else None,
                                  rois=[[0, 0, 1, 1]], progress_callback=callback)
    assert calls[0][0] is image
    assert calls[0][1]["progress_callback"] is callback
    if use_mask:
        assert calls[0][1]["mask"] is mask
        assert "rois" not in calls[0][1]
    else:
        assert calls[0][1]["rois"] == [[0, 0, 1, 1]]
    assert calls[-1] == "unload"


@pytest.mark.parametrize("color,expected", [("#FFFFFF", [255, 255, 255]), ("#123456", [18, 52, 86])])
def test_composite_service_preserves_rgb_order_and_alpha(color, expected):
    rgba = np.array([[[200, 100, 50, 0], [200, 100, 50, 255]]], np.uint8)
    result = image_processing.composite_cutout(rgba, color)
    assert result[0, 0].tolist() == expected
    assert result[0, 1].tolist() == [200, 100, 50]


def test_refinement_service_uses_supplied_brushes():
    mask = np.array([[0, 255]], np.uint8)
    original = np.zeros((1, 2, 3), np.uint8)
    rgba, refined = image_processing.refine_cutout(mask, original, np.array([[255, 0]], np.uint8),
                                                  np.array([[0, 255]], np.uint8), 0, 0)
    assert refined.tolist() == [[255, 0]]
    assert rgba[0, :, 3].tolist() == [255, 0]


@pytest.mark.parametrize("failure", [False, True])
def test_progress_adapter_delivers_from_worker_and_observes_async_errors(monkeypatch, failure):
    events = []
    errors = []
    async def send(percent, message, *, task_id):
        events.append((task_id, percent, message, threading.get_ident()))
        if failure:
            raise RuntimeError("socket failed")
    monkeypatch.setattr(progress, "progress_manager", SimpleNamespace(send_progress=send))
    monkeypatch.setattr(progress.log_manager, "error", lambda message, **kwargs: errors.append(message))
    async def scenario():
        callback = progress.make_progress_callback("owned-task", asyncio.get_running_loop())
        await asyncio.to_thread(callback, 40, "working")
        # Let the thread-safe future's completion callback observe the send result.
        for _ in range(5):
            await asyncio.sleep(0)
        assert events == [("owned-task", 40, "working", threading.get_ident())]
    asyncio.run(scenario())
    assert bool(errors) is failure


def test_progress_adapter_handles_closed_loop_without_leaking_coroutine(monkeypatch):
    errors = []
    monkeypatch.setattr(progress.log_manager, "error", lambda message, **kwargs: errors.append(message))
    loop = asyncio.new_event_loop()
    loop.close()
    progress.make_progress_callback("closed-loop", loop)(10, "initializing")
    assert len(errors) == 1
    assert "closed" in errors[0]


@pytest.mark.parametrize("ready", [False, True])
def test_model_readiness_keeps_download_updates_on_the_requested_task(monkeypatch, ready):
    events = []
    async def send_progress(percent, message, *, task_id):
        events.append((task_id, percent))
    async def send_error(message, *, task_id):
        events.append((task_id, "error"))
    async def ensure_model_ready(*, model_id, progress_callback):
        await progress_callback(90, "downloading")
        if not ready:
            await progress_callback(-1, "failed")
        return ready, "" if ready else "failed"
    monkeypatch.setitem(sys.modules, "core.model_download_helper", SimpleNamespace(ensure_model_ready=ensure_model_ready))
    monkeypatch.setattr(model_readiness, "progress_manager", SimpleNamespace(send_progress=send_progress, send_error=send_error))
    result = asyncio.run(model_readiness.ensure_model_ready_with_progress("model", "处理", "owned-task"))
    assert result[0] is ready
    assert events == ([("owned-task", 30)] if ready else [("owned-task", 30), ("owned-task", "error")])


def test_pipeline_http_delivers_real_stage_callbacks_on_its_task_channel(monkeypatch):
    from app.routers import postprocess
    from app.shared_executor import InferenceScheduler
    events = []
    threads = []
    class Executor:
        def execute(self, image, **kwargs):
            threads.append(threading.get_ident())
            return image
        def unload_model(self):
            threads.append(threading.get_ident())
    class Progress:
        async def send_progress(self, percent, message, *, task_id):
            events.append((task_id, percent, message))
        async def send_complete(self, message, *, task_id):
            events.append((task_id, 100, message))
    monkeypatch.setattr(image_processing.ModelExecutorFactory, "create_executor", lambda *_args: Executor())
    manager = Progress()
    monkeypatch.setattr(postprocess, "progress_manager", manager)
    monkeypatch.setattr(progress, "progress_manager", manager)
    data = io.BytesIO()
    Image.new("RGB", (4, 4)).save(data, format="PNG")
    encoded = base64.b64encode(data.getvalue()).decode()
    async def scenario():
        scheduler = InferenceScheduler(max_pending=0)
        monkeypatch.setattr(postprocess, "inference_scheduler", scheduler)
        app = FastAPI()
        app.include_router(postprocess.router)
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post("/pipeline/run", json={
                    "image": encoded, "mask": encoded, "device": "cpu", "task_id": "pipeline-service",
                })
            for _ in range(5):
                await asyncio.sleep(0)
            assert response.status_code == 200
            assert response.json()["width"] == 4
            assert all(thread != threading.get_ident() for thread in threads)
        finally:
            await scheduler.shutdown()
    asyncio.run(scenario())
    assert all(task_id == "pipeline-service" for task_id, _, _ in events)
    assert ("pipeline-service", 50, "水印修复") in events
    assert ("pipeline-service", 100, "处理完成") in events


def test_single_upscale_encoding_failure_keeps_original_error_after_model_cleanup(monkeypatch):
    from app.routers import upscale
    calls = []
    class Upscaler:
        def __init__(self, **kwargs):
            pass
        def upscale(self, image):
            return image
        def close(self):
            calls.append("closed")
    class Progress:
        async def send_progress(self, *args, **kwargs):
            pass
        async def send_complete(self, *args, **kwargs):
            pass
        async def send_error(self, message, **kwargs):
            calls.append(message)
    async def run_blocking(function, *args, **kwargs):
        return function(*args, **kwargs)
    def fail_encoding(image):
        raise RuntimeError("encode failed")
    monkeypatch.setitem(sys.modules, "core.upscaler", SimpleNamespace(Upscaler=Upscaler))
    monkeypatch.setattr(upscale, "decode_image", lambda value: np.zeros((2, 2, 3), np.uint8))
    monkeypatch.setattr(upscale, "encode_image", fail_encoding)
    monkeypatch.setattr(upscale, "progress_manager", Progress())
    monkeypatch.setattr(upscale.inference_scheduler, "run_blocking", run_blocking)
    request = upscale.UpscaleRequest(image="image", device="cpu", outscale=1, task_id="encoding-failure")
    with pytest.raises(RuntimeError, match="encode failed"):
        asyncio.run(upscale.upscale_image(request))
    assert calls == ["closed", "encode failed"]
