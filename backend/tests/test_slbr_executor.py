import asyncio
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi import HTTPException

from app.routers.models import queue_download_single
from core.constants import ModelStatus
from core.inpaint_regions import prepare_mask
from core.model_checker import ModelCheckResult
from core.model_executor import ModelExecutorFactory
from core.model_registry import get_model


class ResidualSession:
    def run(self, outputs, inputs):
        assert outputs == ["residual"]
        tensor = inputs["image"]
        assert tensor.shape == (1, 3, 512, 512) and tensor.dtype == np.float32
        return [np.full_like(tensor, 0.2)]


@pytest.mark.parametrize("shape", [(384, 512), (240, 850)])
def test_slbr_limits_inference_to_expanded_mask(shape):
    executor = ModelExecutorFactory.create_executor(get_model("wm_slbr"), "cuda")
    executor._session = ResidualSession()
    assert executor.supports_mask() and executor.device == "cpu"
    image = np.full((*shape, 3), 20, dtype=np.uint8)
    rois = [[7, 9, shape[1] - 7, 50]]
    mask = prepare_mask(image.shape, rois=rois, dilation=1) > 0

    result = executor.execute(image, rois=rois, dilation=1)

    assert result.shape == image.shape
    assert np.array_equal(result[~mask], image[~mask])
    assert np.all(result[mask] == 71)
    executor.unload_model()
    assert executor._session is None
    assert np.array_equal(executor.execute(image, rois=[]), image)


def test_missing_local_slbr_returns_setup_instructions_without_downloading(monkeypatch):
    from app.routers import models
    from core import download_queue
    from core import model_download_helper

    def missing(self, model_id, *, use_cache):
        assert not use_cache
        return ModelCheckResult(model_id, "SLBR", "slbr", ModelStatus.MISSING, "missing")

    def unexpected_download():
        pytest.fail("Local-only SLBR must not start a download")

    monkeypatch.setattr(model_download_helper.ModelChecker, "check_model", missing)
    monkeypatch.setattr(model_download_helper, "get_download_queue", unexpected_download)
    ready, message = asyncio.run(model_download_helper.ensure_model_ready("wm_slbr"))
    assert not ready and "scripts/export_slbr.py" in message

    with pytest.raises(HTTPException) as response:
        asyncio.run(queue_download_single("wm_slbr"))
    assert response.value.status_code == 422 and response.value.detail == message

    def bulk_submit(ids):
        assert ids == []
        return []

    monkeypatch.setattr(models, "MODELS", [get_model("wm_slbr")])
    monkeypatch.setattr(models.ModelChecker, "check_all", lambda self: [missing(self, "wm_slbr", use_cache=False)])
    monkeypatch.setattr(download_queue, "get_download_queue", lambda: SimpleNamespace(bulk_submit=bulk_submit))
    assert asyncio.run(models.queue_download_bulk()) == {"submitted": 0, "tasks": []}
