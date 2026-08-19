from pathlib import Path


def test_provider_restoration_constant():
    from core.constants import Provider

    assert Provider.RESTORATION == "restoration"


def test_resolve_restoration_cache_path():
    from core.paths import MODELS_CACHE_DIR, resolve_model_cache_path

    path = resolve_model_cache_path({
        "provider": "restoration",
        "weight_filename": "scunet_color_real_psnr.pth",
    })
    assert path == MODELS_CACHE_DIR / "restoration" / "scunet_color_real_psnr.pth"
    assert isinstance(path, Path)
