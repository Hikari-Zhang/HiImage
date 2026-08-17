"""Upscaler 必须能解析 denoise-*（realesrgan），并拒绝非 realesrgan ID。"""
import pytest


def test_upscaler_accepts_denoise_general_outscale_1():
    from core.upscaler import Upscaler

    u = Upscaler(model_name="denoise-general", device="cpu")
    assert u.model_name == "denoise-general"
    assert u.scale == 4
    assert u.outscale == 1


def test_upscaler_accepts_denoise_anime():
    from core.upscaler import Upscaler

    u = Upscaler(model_name="denoise-anime", device="cpu")
    assert u.outscale == 1
    assert u.scale == 4


def test_upscaler_explicit_outscale_overrides_yaml():
    from core.upscaler import Upscaler

    u = Upscaler(model_name="denoise-general", device="cpu", outscale=4)
    assert u.outscale == 4


def test_upscaler_still_accepts_x4plus():
    from core.upscaler import Upscaler

    u = Upscaler(model_name="RealESRGAN_x4plus", device="cpu")
    assert u.scale == 4
    assert u.outscale == 4


def test_upscaler_rejects_non_realesrgan():
    from core.upscaler import Upscaler

    with pytest.raises(ValueError, match="realesrgan"):
        Upscaler(model_name="wm_lama", device="cpu")
