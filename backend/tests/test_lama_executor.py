import base64
import hashlib
import io
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
from PIL import Image
import pytest

from core.model_executor import ModelExecutorFactory
from core.model_registry import get_model
from core.paths import resolve_model_cache_path


def test_roi_and_dilation_are_combined_once(monkeypatch):
    executor = ModelExecutorFactory.create_executor(get_model("wm_lama"), "cuda")
    image = np.zeros((25, 31, 3), np.uint8)
    loads = []
    monkeypatch.setattr(executor, "load_model", lambda: loads.append(1))
    monkeypatch.setattr(executor, "_repair", lambda crop, _: np.full_like(crop, 255))

    result = executor.execute(image, rois=[[9, 7, 16, 14]], dilation=2)
    assert executor.device == "cpu"
    assert loads == [1]
    assert result[10, 12, 0] == 255
    assert result[10, 7, 0] > 0
    assert result[10, 6, 0] == 0
    assert not image.any()


def test_empty_selection_does_not_load_weights(monkeypatch):
    executor = ModelExecutorFactory.create_executor(get_model("wm_lama"), "cpu")
    monkeypatch.setattr(executor, "load_model", lambda: pytest.fail("空遮罩不应加载权重"))
    image = np.full((9, 11, 3), 100, np.uint8)
    assert np.array_equal(executor.execute(image, mask=np.zeros(image.shape[:2], np.uint8)), image)


def test_bad_weights_are_rejected_before_torchscript_load(monkeypatch, tmp_path):
    weight = tmp_path / "big-lama.pt"
    weight.write_bytes(b"invalid")
    monkeypatch.setattr("core.lama_executor.resolve_model_cache_path", lambda _: weight)
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(jit=SimpleNamespace(
        load=lambda *_args, **_kwargs: pytest.fail("校验失败时不能加载权重"),
    )))
    executor = ModelExecutorFactory.create_executor(get_model("wm_lama"), "cpu")
    with pytest.raises(ValueError, match="权重校验失败"):
        executor.load_model()


@pytest.mark.parametrize("valid", [True, False])
def test_lama_download_validates_before_replacing_existing_weight(monkeypatch, tmp_path, valid):
    from core import downloaders

    weight = tmp_path / "big-lama.pt"
    weight.write_bytes(b"existing")

    class Response(io.BytesIO):
        headers = {"Content-Length": "3"}

    monkeypatch.setattr(downloaders, "resolve_model_cache_path", lambda _: weight)
    monkeypatch.setattr(downloaders.urllib.request, "urlopen", lambda *_args, **_kwargs: Response(b"new"))
    config = {**get_model("wm_lama"), "sha256": hashlib.sha256(b"new" if valid else b"wrong").hexdigest()}
    if valid:
        downloaders.download_direct(config)
        assert weight.read_bytes() == b"new"
    else:
        with pytest.raises(ValueError, match="SHA-256"):
            downloaders.download_direct(config)
        assert weight.read_bytes() == b"existing"
    assert list(tmp_path.iterdir()) == [weight]


def test_real_lama_repairs_photo_through_roi_api():
    if not resolve_model_cache_path(get_model("wm_lama")).is_file():
        pytest.skip("需要本地 Big LaMa 权重")
    from fastapi.testclient import TestClient
    from app.main import app

    source = Path(__file__).parent / "fixtures" / "watermark-residual.jpg"
    with Image.open(source) as file:
        original = np.array(file.convert("RGB"))
    height, width = original.shape[:2]
    x1, y1, x2, y2 = [int(value * side) for value, side in (
        (0.35, width), (0.76, height), (0.49, width), (0.89, height),
    )]
    selected = np.zeros((height, width), dtype=bool)
    selected[y1:y2, x1:x2] = True

    # TestClient 的生命周期会关闭全局调度器。这里复用测试隔离的请求处理，
    # 保持与其他 API 测试共用的调度器可用。
    client = TestClient(app)
    try:
        response = client.post("/api/inpaint", json={
            "image": base64.b64encode(source.read_bytes()).decode(),
            "rois": [[x1, y1, x2, y2]],
            "device": "cpu", "dilation": 0, "task_id": "lama-photo-regression",
        })
    finally:
        client.close()
    assert response.status_code == 200, response.text[:500]
    with Image.open(io.BytesIO(base64.b64decode(response.json()["image"]))) as file:
        result = np.array(file)
    assert result.shape == original.shape
    assert np.array_equal(result[~selected], original[~selected])
    assert np.any(result[selected] != original[selected])
