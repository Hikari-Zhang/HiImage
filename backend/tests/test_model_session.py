"""确保资源清理覆盖成功、失败和清理本身失败的情况。"""
import asyncio
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from core.model_session import model_session


@pytest.mark.parametrize("error", [None, RuntimeError("inference"), asyncio.CancelledError()])
def test_session_always_cleans_once(error):
    calls = []
    model = SimpleNamespace(unload_model=lambda: calls.append("unload"))
    if error is None:
        with model_session(model):
            pass
    else:
        with pytest.raises(type(error)) as caught:
            with model_session(model):
                raise error
        assert caught.value is error
    assert calls == ["unload"]


def test_cleanup_failure_preserves_original_inference_error():
    def fail_cleanup():
        raise RuntimeError("cleanup")
    with pytest.raises(ValueError, match="inference"):
        with model_session(SimpleNamespace(unload_model=fail_cleanup)):
            raise ValueError("inference")


def test_cleanup_failure_after_success_is_reported():
    def fail_cleanup():
        raise RuntimeError("cleanup")
    with pytest.raises(RuntimeError, match="cleanup"):
        with model_session(SimpleNamespace(unload_model=fail_cleanup)):
            pass


@pytest.mark.parametrize("stage", ["success", "inpaint", "upscale"])
def test_pipeline_releases_each_created_model(monkeypatch, stage):
    from core.model_executor import ModelExecutorFactory
    from core.pipeline import Pipeline, PipelineConfig, UpscaleStep
    calls = []
    class Executor:
        def execute(self, image, **kwargs):
            if stage == "inpaint":
                raise ValueError("inpaint failed")
            return image
        def unload_model(self):
            calls.append("inpaint.unload")
    class Upscaler:
        def __init__(self, **kwargs):
            pass
        def upscale(self, image):
            if stage == "upscale":
                raise ValueError("upscale failed")
            return image
        def close(self):
            calls.append("upscale.close")
    monkeypatch.setattr(ModelExecutorFactory, "create_executor", lambda *_args: Executor())
    monkeypatch.setitem(sys.modules, "core.upscaler", SimpleNamespace(Upscaler=Upscaler))
    pipeline = Pipeline(PipelineConfig(upscale=UpscaleStep(enabled=True)))
    image = np.zeros((4, 4, 3), np.uint8)
    if stage == "success":
        pipeline.run(image, mask=np.zeros((4, 4), np.uint8))
    else:
        with pytest.raises(ValueError, match="failed"):
            pipeline.run(image, mask=np.zeros((4, 4), np.uint8))
    assert calls == (["inpaint.unload"] if stage == "inpaint" else ["inpaint.unload", "upscale.close"])


@pytest.mark.parametrize("name", ["inpaint_with_rois", "inpaint_with_mask", "run_cutout"])
@pytest.mark.parametrize("fail", [False, True])
def test_web_inference_releases_executor(monkeypatch, name, fail):
    from app.routers import inpaint, cutout
    from core.model_executor import ModelExecutorFactory
    module = cutout if name == "run_cutout" else inpaint
    calls = []
    class Executor:
        def supports_mask(self):
            return True
        def execute(self, image, **kwargs):
            if fail:
                raise ValueError("inference failed")
            channels = 4 if name == "run_cutout" else 3
            return np.zeros((4, 4, channels), np.uint8)
        def unload_model(self):
            calls.append("unload")
    async def ready(*args, **kwargs):
        return True, ""
    async def run_blocking(fn, *args, **kwargs):
        return fn(*args, **kwargs)
    monkeypatch.setattr(module, "ensure_model_ready_with_progress", ready)
    monkeypatch.setattr(module.inference_scheduler, "run_blocking", run_blocking)
    monkeypatch.setattr(module, "decode_image", lambda value: np.zeros((4, 4, 3), np.uint8))
    if name == "inpaint_with_mask":
        monkeypatch.setattr(module, "decode_mask", lambda value: np.zeros((4, 4), np.uint8))
    monkeypatch.setattr(ModelExecutorFactory, "create_executor", lambda *_args: Executor())
    request_type, payload = {
        "inpaint_with_rois": (inpaint.InpaintRequest, {"rois": [[0, 0, 2, 2]]}),
        "inpaint_with_mask": (inpaint.InpaintWithMaskRequest, {"mask": "mask"}),
        "run_cutout": (cutout.CutoutRequest, {}),
    }[name]
    request = request_type(image="image", task_id="cleanup-test", device="cpu", **payload)
    result = asyncio.run(getattr(module, name)(request))
    assert calls == ["unload"]
    if fail:
        assert result.status_code == 422
    else:
        assert result["image"]


def test_realesrgan_executor_explicitly_closes_upscaler():
    from core.realesrgan_executor import RealESRGANExecutor
    calls = []
    executor = RealESRGANExecutor({"id": "test"}, "cpu")
    executor._upscaler = SimpleNamespace(close=lambda: calls.append("close"))
    executor.unload_model()
    executor.unload_model()
    assert executor._upscaler is None
    assert calls == ["close"]


def test_synthesis_releases_executor_after_failed_inpaint(monkeypatch):
    from core.synthesizer import Synthesizer
    from core.model_executor import ModelExecutorFactory
    calls = []
    class Executor:
        def execute(self, *args, **kwargs):
            raise ValueError("synthesis failed")
        def unload_model(self):
            calls.append("unload")
    monkeypatch.setattr(ModelExecutorFactory, "create_executor", lambda *_args: Executor())
    synth = Synthesizer("outfit_swap", "wm_lama", "cpu")
    with pytest.raises(ValueError, match="synthesis failed"):
        synth._inpaint_region(np.zeros((4, 4, 3), np.uint8), [(0, 0, 2, 2)], None)
    assert calls == ["unload"]
