"""验证真实执行器路由，使用小图和替身管线避免下载权重。"""
import sys
from types import SimpleNamespace

import numpy as np
from PIL import Image
import pytest

from core.model_executor import ModelExecutorFactory
from core.model_registry import get_model
from core.model_session import model_session


@pytest.fixture
def pipelines(monkeypatch):
    events = []

    class Generator:
        def __init__(self, *, device):
            assert device == "cpu"

        def manual_seed(self, seed):
            return seed

    class Pipe:
        def to(self, device):
            events.append(("device", device))
            return self

        def __call__(self, **params):
            events.append(("run", params))
            return SimpleNamespace(images=[Image.new("RGB", params["image"].size, "red")])

    def pipeline_class(name):
        def load(path, **kwargs):
            events.append(("load", name, path))
            return Pipe()
        return SimpleNamespace(from_pretrained=load)

    monkeypatch.setitem(sys.modules, "diffusers", SimpleNamespace(
        FluxFillPipeline=pipeline_class("FluxFillPipeline")))
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(
        float32="float32", Generator=Generator,
        cuda=SimpleNamespace(is_available=lambda: False),
        backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: False)),
    ))
    monkeypatch.setattr("core.model_checker.resolve_hf_model_path", lambda _: "resolved-model")
    return events


def test_explicit_pipeline_dispatch_and_result(pipelines):
    config = {**get_model("flux_fill"), "provider": "display-only", "hf_model_id": "test/neutral"}
    executor = ModelExecutorFactory.create_executor(config, "cpu")
    image = np.zeros((24, 32, 3), dtype=np.uint8)
    mask = np.zeros(image.shape[:2], dtype=np.uint8)
    mask[:, :8] = 255
    with model_session(executor):
        result = executor.execute(image, mask=mask, prompt="edit", seed=17, num_inference_steps=3)
    assert result.shape == image.shape
    assert tuple(result[0, 0]) == (255, 0, 0)
    assert pipelines[0] == ("load", "FluxFillPipeline", "resolved-model")
    params = pipelines[-1][1]
    assert params["prompt"] == "edit"
    assert params["generator"] == 17
    assert params["num_inference_steps"] == 3
    assert params["mask_image"].size == params["image"].size
    assert set(np.unique(params["mask_image"])) == {0, 255}
    assert "strength" not in params
    assert executor._pipeline is None


@pytest.mark.parametrize("params", [
    {"strength": 0.5}, {"negative_prompt": "blur"}, {"seed": True},
    {"num_inference_steps": 0}, {"guidance_scale": float("nan")},
])
def test_bad_fill_parameters_fail_before_loading(pipelines, params):
    executor = ModelExecutorFactory.create_executor(get_model("flux_fill"), "cpu")
    with pytest.raises(ValueError, match="参数"):
        executor.execute(np.zeros((2, 2, 3), np.uint8), mask=np.ones((2, 2), np.uint8), **params)
    assert pipelines == []


def test_invalid_masks_and_unavailable_device_fail_before_loading(pipelines):
    image = np.zeros((2, 2, 3), np.uint8)
    executor = ModelExecutorFactory.create_executor(get_model("flux_fill"), "cpu")
    with pytest.raises(ValueError, match="遮罩"):
        executor.execute(image, mask=np.zeros((3, 2), np.uint8))
    executor = ModelExecutorFactory.create_executor(get_model("flux_fill"), "cuda")
    with pytest.raises(ValueError, match="CUDA"):
        executor.execute(image, mask=np.ones((2, 2), np.uint8))
    assert pipelines == []


def test_pipeline_failure_releases_loaded_reference(pipelines, monkeypatch):
    executor = ModelExecutorFactory.create_executor(get_model("flux_fill"), "cpu")
    executor.load_model()

    def fail(*args, **kwargs):
        raise RuntimeError("inference failed")

    monkeypatch.setattr(type(executor._pipeline), "__call__", fail)
    with pytest.raises(RuntimeError, match="inference failed"), model_session(executor):
        executor.execute(np.zeros((2, 2, 3), np.uint8), mask=np.ones((2, 2), np.uint8))
    assert executor._pipeline is None


@pytest.mark.parametrize("shape", [(32, 2400), (1400, 1800)])
def test_flux_fill_budgets_and_preserves_pixels_outside_selection(pipelines, shape):
    from core.diffusers_executor import FILL_MAX_EDGE, FILL_MAX_PIXELS

    executor = ModelExecutorFactory.create_executor(get_model("flux_fill"), "cpu")
    image = np.full((*shape, 3), 13, np.uint8)
    selection = np.zeros(shape, np.uint8)
    if shape[0] == 32:
        selection[shape[0] // 2, 10:-10] = 255
    else:
        selection[100:-100, 100:-100] = 255
    result = executor.execute(image, mask=selection, prompt="rebuild texture", seed=89)
    params = pipelines[-1][1]
    width, height = params["image"].size
    assert max(width, height) <= FILL_MAX_EDGE
    assert width * height <= FILL_MAX_PIXELS
    assert width % 16 == height % 16 == 0
    assert np.asarray(params["mask_image"]).any()
    assert params["generator"] == 89
    assert params["prompt"] == "rebuild texture"
    assert result.shape == image.shape
    assert np.array_equal(result[selection == 0], image[selection == 0])
    assert np.any(result[selection > 0] != image[selection > 0])


def test_flux_fill_crops_distant_rois_and_loads_once(pipelines):
    executor = ModelExecutorFactory.create_executor(get_model("flux_fill"), "cpu")
    image = np.zeros((240, 1500, 3), np.uint8)
    result = executor.execute(image, rois=[[12, 12, 32, 32], [1400, 12, 1420, 32]], dilation=2)
    calls = [event[1] for event in pipelines if event[0] == "run"]
    assert len(calls) == 2
    assert all(call["image"].width < 300 for call in calls)
    assert [event[0] for event in pipelines].count("load") == 1
    assert result[20, 20, 0] == 255
    assert result[20, 1410, 0] == 255
    assert result[20, 750, 0] == 0
