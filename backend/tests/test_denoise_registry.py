"""denoise 模式必须包含专用复原模型与清晰化条目。"""


def test_denoise_mode_has_six_models_in_order():
    from core.model_registry import get_models_for_mode

    models = get_models_for_mode("denoise")
    ids = [m["id"] for m in models]
    assert ids == [
        "denoise-scunet-psnr",
        "denoise-scunet-gan",
        "denoise-nafnet-sidd",
        "denoise-swinir-jpeg",
        "denoise-general",
        "denoise-anime",
    ]


def test_denoise_default_model():
    from core.model_registry import get_default_model

    assert get_default_model("denoise") == "denoise-scunet-psnr"


def test_restoration_denoise_models():
    from core.model_registry import get_model

    expected = {
        "denoise-scunet-psnr": ("照片 / 截图", "盲去噪（压缩颗粒 + 一般照片噪声，保纹理）"),
        "denoise-scunet-gan": ("观感更干净", "更干净，可能略抹纹理"),
        "denoise-nafnet-sidd": ("相机 ISO", "手机 / 相机传感器噪声"),
        "denoise-swinir-jpeg": ("去压缩伪影", "JPEG 色块 / 振铃（按常见网图质量 Q≈40）"),
    }
    for model_id, (group, suitable) in expected.items():
        cfg = get_model(model_id)
        assert cfg["provider"] == "restoration"
        assert cfg.get("display_group") == group
        assert cfg.get("suitable_for") == suitable
        assert "denoise" in cfg.get("tags", [])
        assert cfg.get("supports_custom_outscale") is False


def test_clarity_denoise_models_remain_realesrgan():
    from core.model_registry import get_model

    for model_id in ("denoise-general", "denoise-anime"):
        cfg = get_model(model_id)
        assert cfg["provider"] == "realesrgan"
        assert cfg.get("outscale") == 1
        assert cfg.get("display_group") == "快速清晰化"
        assert cfg.get("suitable_for")
        assert "denoise" in cfg.get("tags", [])


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
