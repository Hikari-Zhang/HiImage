import numpy as np
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


def test_load_model_missing_spandrel_raises(tmp_path, monkeypatch):
    from core import restoration_executor as mod
    from core.restoration_executor import RestorationExecutor
    import builtins

    dummy = tmp_path / "w.pth"
    dummy.write_bytes(b"x")
    monkeypatch.setattr(mod, "resolve_model_cache_path", lambda cfg: dummy)

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "spandrel" or str(name).startswith("spandrel."):
            raise ImportError("No module named 'spandrel'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    exe = RestorationExecutor(
        {"id": "denoise-scunet-psnr", "provider": "restoration", "weight_filename": "w.pth"},
        "cpu",
    )
    with pytest.raises(RuntimeError, match="需要 spandrel"):
        exe.load_model()


class _IdentityDescriptor:
    tiling = type("T", (), {"name": "SUPPORTED"})()

    def __call__(self, tensor):
        return tensor


def test_infer_tiled_identity():
    from core.restoration_executor import RestorationExecutor

    exe = RestorationExecutor(
        {"id": "denoise-scunet-psnr", "provider": "restoration", "weight_filename": "x.pth"},
        "cpu",
    )
    exe._descriptor = _IdentityDescriptor()
    rng = np.random.default_rng(0)
    image = rng.integers(0, 256, size=(40, 55, 3), dtype=np.uint8)
    out = exe._infer_tiled(image, tile=16)
    assert out.shape == image.shape
    np.testing.assert_array_equal(out, image)


def test_discouraged_tiling_prefers_full_image(monkeypatch):
    from core.restoration_executor import RestorationExecutor

    exe = RestorationExecutor(
        {"id": "denoise-scunet-psnr", "provider": "restoration", "weight_filename": "x.pth"},
        "cpu",
    )
    desc = _IdentityDescriptor()
    desc.tiling = type("T", (), {"name": "DISCOURAGED"})()
    exe._descriptor = desc
    called = {"auto": False}

    def fake_auto(*_args, **_kwargs):
        called["auto"] = True
        return 384

    monkeypatch.setattr("core.upscaler.Upscaler._auto_tile", fake_auto)
    image = np.zeros((8, 8, 3), dtype=np.uint8)
    out = exe.execute(image, tile=0)
    assert called["auto"] is False
    np.testing.assert_array_equal(out, image)


def test_supported_tiling_still_prefers_full_image(monkeypatch):
    """NAFNet 在 spandrel 里默认 SUPPORTED；不能因此套用超分的 MPS 384 分块。"""
    from core.restoration_executor import RestorationExecutor

    exe = RestorationExecutor(
        {"id": "denoise-nafnet-sidd", "provider": "restoration", "weight_filename": "x.pth"},
        "mps",
    )
    exe._descriptor = _IdentityDescriptor()
    called = {"auto": False}

    def fake_auto(*_args, **_kwargs):
        called["auto"] = True
        return 384

    monkeypatch.setattr("core.upscaler.Upscaler._auto_tile", fake_auto)
    image = np.zeros((8, 8, 3), dtype=np.uint8)
    out = exe.execute(image, tile=0)
    assert called["auto"] is False
    np.testing.assert_array_equal(out, image)


def test_to_numpy_crops_padding_instead_of_area_resize():
    import torch
    from core.restoration_executor import RestorationExecutor

    exe = RestorationExecutor(
        {"id": "denoise-nafnet-sidd", "provider": "restoration", "weight_filename": "x.pth"},
        "cpu",
    )
    tensor = torch.zeros(1, 3, 6, 6)
    tensor[0, 0, :4, :4] = 1.0
    out = exe._to_numpy(tensor, (4, 4))
    assert out.shape == (4, 4, 3)
    assert int(out[3, 3, 0]) == 255


def test_device_override_wins_over_requested_device():
    from core.restoration_executor import RestorationExecutor

    exe = RestorationExecutor(
        {
            "id": "denoise-scunet-psnr",
            "provider": "restoration",
            "weight_filename": "x.pth",
            "device_override": "cpu",
        },
        "mps",
    )
    assert exe.device == "cpu"


class _MpsUnimplementedDescriptor:
    tiling = type("T", (), {"name": "SUPPORTED"})()

    def __init__(self):
        self.device = "mps"

    def to(self, device):
        self.device = str(device)
        return self

    def __call__(self, tensor):
        if str(self.device) == "mps":
            raise NotImplementedError(
                "convolution_overrideable not implemented. You are likely "
                "triggering this with tensor backend other than CPU/CUDA/MKLDNN"
            )
        return tensor


def test_mps_unimplemented_conv_retries_on_cpu():
    from core.restoration_executor import RestorationExecutor

    exe = RestorationExecutor(
        {"id": "denoise-nafnet-sidd", "provider": "restoration", "weight_filename": "x.pth"},
        "mps",
    )
    exe._descriptor = _MpsUnimplementedDescriptor()
    image = np.zeros((8, 8, 3), dtype=np.uint8)
    out = exe.execute(image, tile=0)
    assert exe.device == "cpu"
    assert out.shape == image.shape
