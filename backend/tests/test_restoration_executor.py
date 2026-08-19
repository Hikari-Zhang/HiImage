from pathlib import Path

import pytest


def test_factory_creates_restoration_executor():
    from core.model_executor import ModelExecutorFactory
    from core.restoration_executor import RestorationExecutor

    cfg = {
        "id": "denoise-scunet-psnr",
        "provider": "restoration",
        "weight_filename": "scunet_color_real_psnr.pth",
    }
    exe = ModelExecutorFactory.create_executor(cfg, "cpu")
    assert isinstance(exe, RestorationExecutor)
    assert exe.supports_mask() is False


def test_supported_providers_includes_restoration():
    from core.model_executor import ModelExecutorFactory

    assert "restoration" in ModelExecutorFactory.get_supported_providers()


def test_load_model_missing_weight_raises(tmp_path, monkeypatch):
    from core import restoration_executor as mod
    from core.restoration_executor import RestorationExecutor

    missing = tmp_path / "missing.pth"
    monkeypatch.setattr(mod, "resolve_model_cache_path", lambda cfg: missing)

    exe = RestorationExecutor(
        {"id": "denoise-scunet-psnr", "provider": "restoration", "weight_filename": "missing.pth"},
        "cpu",
    )
    with pytest.raises(FileNotFoundError, match="请先在模型管理中下载"):
        exe.load_model()


def test_execute_without_load_uses_missing_weight(tmp_path, monkeypatch):
    from core import restoration_executor as mod
    from core.restoration_executor import RestorationExecutor
    import numpy as np

    missing = tmp_path / "missing.pth"
    monkeypatch.setattr(mod, "resolve_model_cache_path", lambda cfg: missing)
    exe = RestorationExecutor(
        {"id": "denoise-scunet-psnr", "provider": "restoration", "weight_filename": "missing.pth"},
        "cpu",
    )
    with pytest.raises(FileNotFoundError):
        exe.execute(np.zeros((8, 8, 3), dtype=np.uint8), tile=0)
