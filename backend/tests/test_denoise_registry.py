"""denoise 模式必须出现在 models.yaml 注册表中。"""


def test_denoise_mode_has_two_models_in_order():
    from core.model_registry import get_models_for_mode

    models = get_models_for_mode("denoise")
    ids = [m["id"] for m in models]
    assert ids == ["denoise-general", "denoise-anime"]


def test_denoise_default_model():
    from core.model_registry import get_default_model

    assert get_default_model("denoise") == "denoise-general"


def test_denoise_models_are_realesrgan_outscale_1():
    from core.model_registry import get_model

    for model_id in ("denoise-general", "denoise-anime"):
        cfg = get_model(model_id)
        assert cfg["provider"] == "realesrgan"
        assert cfg.get("outscale") == 1
        assert cfg.get("supports_custom_outscale") is False
        assert "denoise" in cfg.get("tags", [])
        assert "upscale" not in cfg.get("tags", [])


def test_denoise_general_reuses_general_v3_weight():
    from core.model_registry import get_model

    denoise = get_model("denoise-general")
    upscale = get_model("realesr-general-x4v3")
    assert denoise["weight_filename"] == upscale["weight_filename"] == "realesr-general-x4v3.pth"


def test_denoise_anime_reuses_anime_6b_weight():
    from core.model_registry import get_model

    denoise = get_model("denoise-anime")
    upscale = get_model("RealESRGAN_x4plus_anime_6B")
    assert denoise["weight_filename"] == upscale["weight_filename"] == "RealESRGAN_x4plus_anime_6B.pth"


def test_clarity_models_remain_upscale_only():
    from core.model_registry import get_model

    for model_id in ("clarity-general", "clarity-anime"):
        cfg = get_model(model_id)
        assert cfg.get("tags") == ["upscale"]
