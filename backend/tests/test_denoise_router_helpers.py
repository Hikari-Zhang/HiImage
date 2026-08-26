import asyncio
import os

import numpy as np
import pytest
from fastapi import HTTPException


def test_denoised_output_path_adds_suffix_on_collision(tmp_path):
    from app.routers.denoise import _denoised_output_path

    first = tmp_path / "1_denoised.png"
    second = tmp_path / "1_denoised_2.png"
    third = tmp_path / "1_denoised_3.png"

    assert _denoised_output_path(str(tmp_path), "1.png") == os.fspath(first)
    first.touch()

    assert _denoised_output_path(str(tmp_path), "1.png") == os.fspath(second)
    second.touch()

    assert _denoised_output_path(str(tmp_path), "1.png") == os.fspath(third)


def test_list_denoise_models_groups():
    from app.routers.denoise import list_denoise_models

    data = list_denoise_models()
    labels = [g["label"] for g in data["groups"]]
    assert labels == [
        "照片 / 截图",
        "观感更干净",
        "相机 ISO",
        "去压缩伪影",
        "快速清晰化",
    ]
    ids = [m["id"] for g in data["groups"] for m in g["models"]]
    assert ids == [
        "denoise-scunet-psnr",
        "denoise-scunet-gan",
        "denoise-nafnet-sidd",
        "denoise-swinir-jpeg",
        "denoise-general",
        "denoise-anime",
    ]
    first = data["groups"][0]["models"][0]
    assert first["suitable_for"] == "盲去噪（压缩颗粒 + 一般照片噪声，保纹理）"
    assert "scale" not in first
    assert "outscale" not in first


def test_require_denoise_model_ok():
    from app.routers.denoise import _require_denoise_model

    cfg = _require_denoise_model("denoise-general")
    assert cfg["id"] == "denoise-general"


def test_require_denoise_model_accepts_scunet():
    from app.routers.denoise import _require_denoise_model

    cfg = _require_denoise_model("denoise-scunet-psnr")
    assert cfg["id"] == "denoise-scunet-psnr"
    assert cfg["provider"] == "restoration"


def test_require_denoise_model_rejects_upscale_id():
    from app.routers.denoise import _require_denoise_model

    with pytest.raises(HTTPException) as ei:
        _require_denoise_model("RealESRGAN_x4plus")
    assert ei.value.status_code == 400


def test_require_denoise_model_unknown():
    from app.routers.denoise import _require_denoise_model

    with pytest.raises(HTTPException) as ei:
        _require_denoise_model("not-a-model")
    assert ei.value.status_code == 400


def test_match_input_size_resizes_mismatch():
    from app.routers.denoise import _match_input_size

    src = np.zeros((10, 20, 3), dtype=np.uint8)
    out = np.zeros((11, 21, 3), dtype=np.uint8)
    got = _match_input_size(out, src)
    assert got.shape[:2] == (10, 20)


def test_match_input_size_keeps_same():
    from app.routers.denoise import _match_input_size

    src = np.zeros((8, 8, 3), dtype=np.uint8)
    got = _match_input_size(src.copy(), src)
    assert got.shape == src.shape


def test_batch_auto_tile_does_not_preload_before_first_image(monkeypatch):
    from app.routers import denoise
    from core import upscaler

    events = []

    class FakeUpscaler:
        def __init__(self, model_name, device, tile):
            self._tile = tile

        def _ensure_model_loaded(self):
            events.append("load")

        def upscale(self, image):
            events.append("upscale")
            return image.copy()

    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr(upscaler, "Upscaler", FakeUpscaler)
    monkeypatch.setattr(denoise, "_require_denoise_model", lambda model: {})
    monkeypatch.setattr(
        denoise, "decode_image", lambda data: np.zeros((8, 8, 3), dtype=np.uint8)
    )
    monkeypatch.setattr(denoise.progress_manager, "send_progress", noop)
    monkeypatch.setattr(denoise.progress_manager, "send_complete", noop)

    request = denoise.BatchDenoiseRequest(
        images=["encoded"], tile=None, model="denoise-general"
    )
    response = asyncio.run(denoise.denoise_image_batch(request))

    assert response["summary"]["success"] == 1
    assert events == ["upscale"]


def test_batch_imwrite_false_marks_item_error(monkeypatch, tmp_path):
    from app.routers import denoise
    from core import upscaler

    class FakeUpscaler:
        def __init__(self, model_name, device, tile):
            self._tile = tile
            self._current_tile = 0

        def _ensure_model_loaded(self):
            return None

        def upscale(self, image):
            return image.copy()

    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr(upscaler, "Upscaler", FakeUpscaler)
    monkeypatch.setattr(denoise, "_require_denoise_model", lambda model: {})
    monkeypatch.setattr(
        denoise, "decode_image", lambda data: np.zeros((8, 8, 3), dtype=np.uint8)
    )
    monkeypatch.setattr(denoise.cv2, "imwrite", lambda path, image: False)
    monkeypatch.setattr(denoise.progress_manager, "send_progress", noop)
    monkeypatch.setattr(denoise.progress_manager, "send_complete", noop)

    request = denoise.BatchDenoiseRequest(
        images=["encoded"], tile=128, output_dir=str(tmp_path), model="denoise-general"
    )
    response = asyncio.run(denoise.denoise_image_batch(request))

    assert response["summary"] == {"total": 1, "success": 0, "failed": 1}
    assert response["results"][0]["status"] == "error"
    assert "写入降噪图片失败" in response["results"][0]["error"]


def test_single_imwrite_false_raises_original_error(monkeypatch, tmp_path):
    from app.routers import denoise

    async def noop(*args, **kwargs):
        return None

    image = np.zeros((8, 8, 3), dtype=np.uint8)
    monkeypatch.setattr(denoise, "_require_denoise_model", lambda model: {})
    monkeypatch.setattr(denoise, "decode_image", lambda data: image.copy())
    monkeypatch.setattr(denoise, "_run_denoise", lambda *args: image.copy())
    monkeypatch.setattr(denoise.cv2, "imwrite", lambda path, output: False)
    monkeypatch.setattr(denoise.progress_manager, "send_progress", noop)
    monkeypatch.setattr(denoise.progress_manager, "send_complete", noop)
    monkeypatch.setattr(denoise.progress_manager, "send_error", noop)

    request = denoise.DenoiseRequest(
        image="encoded",
        output_path=str(tmp_path / "denoised.png"),
        model="denoise-general",
    )
    with pytest.raises(RuntimeError, match="写入降噪图片失败"):
        asyncio.run(denoise.denoise_image(request))


def test_create_denoise_backend_restoration():
    from app.routers import denoise
    from core.restoration_executor import RestorationExecutor

    backend = denoise.create_denoise_backend(
        "denoise-scunet-psnr", device="cpu", tile=0
    )
    assert isinstance(backend._executor, RestorationExecutor)
    assert backend._tile == 0


def test_create_denoise_backend_restoration_ignores_client_tile():
    from app.routers import denoise
    from core.restoration_executor import RestorationExecutor

    backend = denoise.create_denoise_backend(
        "denoise-nafnet-sidd", device="cpu", tile=384
    )
    assert isinstance(backend._executor, RestorationExecutor)
    assert backend._tile == 0


def test_create_denoise_backend_realesrgan():
    from app.routers.denoise import create_denoise_backend
    from core.upscaler import Upscaler

    backend = create_denoise_backend("denoise-general", device="cpu", tile=0)
    assert isinstance(backend._upscaler, Upscaler)
