import numpy as np
import pytest
from fastapi import HTTPException


def test_list_denoise_models_groups():
    from app.routers.denoise import list_denoise_models

    data = list_denoise_models()
    labels = [g["label"] for g in data["groups"]]
    assert labels == ["通用降噪", "动漫/插画"]
    ids = [m["id"] for g in data["groups"] for m in g["models"]]
    assert ids == ["denoise-general", "denoise-anime"]
    assert "scale" not in data["groups"][0]["models"][0]
    assert "outscale" not in data["groups"][0]["models"][0]


def test_require_denoise_model_ok():
    from app.routers.denoise import _require_denoise_model

    cfg = _require_denoise_model("denoise-general")
    assert cfg["id"] == "denoise-general"


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
