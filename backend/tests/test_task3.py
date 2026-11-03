import json
import platform
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import numpy as np
import httpx
from fastapi import FastAPI, HTTPException


def test_paths_are_environment_overridable(tmp_path, monkeypatch):
    from core.paths import paths_from_env

    values = paths_from_env(
        {
            "HIIMAGE_CONFIG_DIR": str(tmp_path / "config"),
            "HIIMAGE_CACHE": str(tmp_path / "cache"),
            "HIIMAGE_TMP_DIR": str(tmp_path / "tmp"),
            "HIIMAGE_OUTPUT_DIR": str(tmp_path / "output"),
        }
    )

    assert values.config_dir == tmp_path / "config"
    assert values.cache_dir == tmp_path / "cache"
    assert values.tmp_dir == tmp_path / "tmp"
    assert values.output_dir == tmp_path / "output"


def test_project_root_override_controls_relative_runtime_paths(tmp_path):
    from core.paths import paths_from_env

    root = tmp_path / "container-app"
    values = paths_from_env({"HIIMAGE_PROJECT_ROOT": str(root)})

    assert values.project_root == root
    assert values.config_dir == root / "config"
    assert values.tmp_dir == root / "tmp"
    assert values.output_dir == root / "output"
    assert values.models_dir == root / "models"


def test_cache_root_controls_all_default_model_caches(tmp_path):
    from core.paths import cache_dirs_from_env

    cache = tmp_path / "cache"
    values = cache_dirs_from_env({"HIIMAGE_CACHE": str(cache)})

    assert values["HF_HOME"] == cache / "huggingface"
    assert values["TORCH_HOME"] == cache / "torch"
    assert values["GFPGAN_HOME"] == cache / "gfpgan"
    assert values["REALESRGAN_HOME"] == cache / "realesrgan"
    assert values["RESTORMER_HOME"] == cache / "restormer"
    assert values["U2NET_HOME"] == cache / "u2net"


def test_specialized_cache_environment_wins(tmp_path):
    from core.paths import cache_dirs_from_env

    cache = tmp_path / "cache"
    custom = tmp_path / "custom-hf"
    values = cache_dirs_from_env({"HIIMAGE_CACHE": str(cache), "HF_HOME": str(custom)})

    assert values["HF_HOME"] == custom
    assert values["TORCH_HOME"] == cache / "torch"


def test_u2net_default_preserves_legacy_home_without_cache_override():
    from core.paths import cache_dirs_from_env

    assert cache_dirs_from_env({})["U2NET_HOME"] == Path.home() / ".u2net"


def test_u2net_uses_cache_root_only_when_cache_is_explicit(tmp_path):
    from core.paths import cache_dirs_from_env

    assert cache_dirs_from_env({"HIIMAGE_CACHE": str(tmp_path)})["U2NET_HOME"] == tmp_path / "u2net"


def test_paths_defaults_are_not_based_on_source_tree():
    from core.paths import paths_from_env

    values = paths_from_env({})
    assert values.config_dir.name == "config"
    assert values.tmp_dir.name == "tmp"
    assert values.output_dir.name == "output"


def test_config_persistence_uses_config_path(tmp_path, monkeypatch):
    import app.config as config

    config_path = tmp_path / "settings.json"
    monkeypatch.setattr(config, "CONFIG_PATH", config_path)
    config._cache = {}
    config._loaded = False

    config.save({"inpaint.default_device": "cpu"})

    assert json.loads(config_path.read_text(encoding="utf-8"))["inpaint"]["default_device"] == "cpu"
    assert config.get("inpaint.default_device") == "cpu"


@pytest.mark.parametrize("value", ["cpu", "cuda", "mps"])
def test_config_default_device_prefers_valid_environment(value, monkeypatch):
    import importlib
    import app.config as config

    monkeypatch.setenv("HIIMAGE_DEFAULT_DEVICE", value)
    reloaded = importlib.reload(config)
    assert reloaded.DEFAULTS["inpaint.default_device"] == value


def test_config_default_device_ignores_invalid_environment(monkeypatch):
    import app.config as config

    monkeypatch.setenv("HIIMAGE_DEFAULT_DEVICE", "gpu")
    assert config.configured_default_device(system="Linux", machine="x86_64") == "cpu"


def test_default_output_pixel_budget_is_256_mb():
    import app.config as config

    assert config.DEFAULTS["image.max_output_mb"] == 256


def test_upscale_rejects_highly_compressed_large_dimensions_before_inference(monkeypatch):
    import app.routers.upscale as upscale

    tiny_compressed_but_large_pixels = np.lib.stride_tricks.as_strided(
        np.zeros((1, 1, 3), dtype=np.uint8),
        shape=(5000, 5000, 3),
        strides=(0, 0, 0),
    )
    monkeypatch.setattr(upscale, "get", lambda key, default=None: 256)

    with pytest.raises(HTTPException) as error:
        upscale.enforce_output_pixel_budget(
            tiny_compressed_but_large_pixels,
            model_name="RealESRGAN_x4plus",
            outscale=2,
        )

    assert error.value.status_code == 413
    assert "输出 RGBA" in error.value.detail


def test_upscale_endpoint_rejects_pixel_budget_before_upscaler_is_created(
    monkeypatch,
):
    import app.routers.upscale as upscale

    created = False

    class UnexpectedUpscaler:
        def __init__(self, *args, **kwargs):
            nonlocal created
            created = True

    image = np.lib.stride_tricks.as_strided(
        np.zeros((1, 1, 3), dtype=np.uint8),
        shape=(5000, 5000, 3),
        strides=(0, 0, 0),
    )
    monkeypatch.setitem(
        sys.modules,
        "core.upscaler",
        SimpleNamespace(Upscaler=UnexpectedUpscaler),
    )
    monkeypatch.setattr(upscale, "check_image_size", lambda _image: None)
    monkeypatch.setattr(upscale, "decode_image", lambda _image: image)
    monkeypatch.setattr(upscale, "get", lambda key, default=None: 256)

    request = upscale.UpscaleRequest(
        image="tiny-compressed",
        outscale=2,
        device="cpu",
        task_id="pixel-budget-test",
    )
    with pytest.raises(HTTPException) as error:
        awaitable_result(upscale.upscale_image(request))

    assert error.value.status_code == 413
    assert created is False


def _install_batch_upscale_test_doubles(monkeypatch, upscale):
    created = []
    processed = []

    class FakeUpscaler:
        def __init__(self, *args, **kwargs):
            created.append(kwargs)

        def _ensure_model_loaded(self):
            return None

        def upscale(self, image):
            processed.append(image.shape)
            return image

        def close(self):
            return None

    class InlineScheduler:
        async def run_blocking(self, function, *args, **kwargs):
            return function(*args, **kwargs)

    class ProgressManager:
        async def send_progress(self, *args, **kwargs):
            return None

        async def send_complete(self, *args, **kwargs):
            return None

    monkeypatch.setitem(
        sys.modules,
        "core.upscaler",
        SimpleNamespace(Upscaler=FakeUpscaler),
    )
    monkeypatch.setattr(upscale, "inference_scheduler", InlineScheduler())
    monkeypatch.setattr(upscale, "progress_manager", ProgressManager())
    monkeypatch.setattr(upscale, "check_image_size", lambda _image: None)
    monkeypatch.setattr(upscale, "encode_image", lambda _image: "encoded")
    monkeypatch.setattr(upscale, "get", lambda key, default=None: 256)
    return created, processed


async def _post_batch_upscale(upscale, payload):
    app = FastAPI()
    app.include_router(upscale.router, prefix="/api")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        return await client.post("/api/upscale/batch", json=payload)


def test_batch_upscale_records_invalid_item_and_processes_valid_item(monkeypatch):
    import app.routers.upscale as upscale

    created, processed = _install_batch_upscale_test_doubles(monkeypatch, upscale)
    valid_image = np.zeros((2, 3, 3), dtype=np.uint8)

    def decode(value):
        if value == "invalid-base64":
            raise ValueError("非法 Base64")
        return valid_image

    monkeypatch.setattr(upscale, "decode_image", decode)
    response = awaitable_result(_post_batch_upscale(upscale, {
        "images": ["invalid-base64", "valid-base64"],
        "device": "cpu",
        "outscale": 1,
        "task_id": "mixed-batch-test",
    }))

    assert response.status_code == 200
    body = response.json()
    assert [item["index"] for item in body["results"]] == [0, 1]
    assert body["results"][0]["status"] == "error"
    assert "非法 Base64" in body["results"][0]["error"]
    assert body["results"][1]["status"] == "success"
    assert body["summary"] == {"total": 2, "success": 1, "failed": 1}
    assert len(created) == 1
    assert processed == [(2, 3, 3)]


def test_batch_upscale_all_invalid_does_not_load_model(monkeypatch):
    import app.routers.upscale as upscale

    created, processed = _install_batch_upscale_test_doubles(monkeypatch, upscale)
    monkeypatch.setattr(
        upscale,
        "decode_image",
        lambda _value: (_ for _ in ()).throw(ValueError("非法 Base64")),
    )
    response = awaitable_result(_post_batch_upscale(upscale, {
        "images": ["bad-1", "bad-2"],
        "device": "cpu",
        "outscale": 1,
        "task_id": "invalid-batch-test",
    }))

    assert response.status_code == 200
    body = response.json()
    assert [item["index"] for item in body["results"]] == [0, 1]
    assert all(item["status"] == "error" for item in body["results"])
    assert body["summary"] == {"total": 2, "success": 0, "failed": 2}
    assert created == []
    assert processed == []


@pytest.mark.parametrize(
    ("system", "machine", "expected"),
    [
        ("Darwin", "arm64", "mps"),
        ("Darwin", "aarch64", "mps"),
        ("Darwin", "x86_64", "cpu"),
        ("Windows", "AMD64", "cpu"),
        ("Linux", "x86_64", "cpu"),
    ],
)
def test_default_device_is_platform_and_architecture_aware(system, machine, expected):
    from core.utils import default_device_for_platform

    assert default_device_for_platform(system, machine) == expected


def test_default_device_reads_machine_when_not_explicit(monkeypatch):
    import core.utils as utils

    monkeypatch.setattr(utils.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(utils.platform, "machine", lambda: "x86_64")

    assert utils.default_device_for_platform() == "cpu"


def test_explicit_device_setting_wins(monkeypatch):
    from core.utils import resolve_default_device

    assert resolve_default_device("cuda", system="Windows") == "cuda"


@pytest.mark.parametrize(
    ("device", "torch_module", "expected", "reason_fragment"),
    [
        ("cpu", None, True, ""),
        (
            "cuda",
            SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False)),
            False,
            "CUDA",
        ),
        (
            "mps",
            SimpleNamespace(
                backends=SimpleNamespace(
                    mps=SimpleNamespace(is_available=lambda: False)
                )
            ),
            False,
            "MPS",
        ),
        ("quantum", None, False, "未知"),
    ],
)
def test_device_availability_checks_requested_device(
    device,
    torch_module,
    expected,
    reason_fragment,
):
    from app.routers.system import device_availability

    available, reason = device_availability(device, torch_module=torch_module)

    assert available is expected
    assert reason_fragment in reason


@pytest.mark.parametrize(
    ("device", "torch_module", "error_message"),
    [
        (
            "cuda",
            SimpleNamespace(
                cuda=SimpleNamespace(
                    is_available=lambda: (_ for _ in ()).throw(
                        RuntimeError("CUDA 驱动初始化失败")
                    )
                )
            ),
            "CUDA 驱动初始化失败",
        ),
        (
            "mps",
            SimpleNamespace(
                backends=SimpleNamespace(
                    mps=SimpleNamespace(
                        is_available=lambda: (_ for _ in ()).throw(
                            OSError("MPS 后端查询失败")
                        )
                    )
                )
            ),
            "MPS 后端查询失败",
        ),
    ],
)
def test_device_availability_converts_probe_exceptions_to_reason(
    device,
    torch_module,
    error_message,
):
    from app.routers.system import device_availability

    available, reason = device_availability(device, torch_module=torch_module)

    assert available is False
    assert device.upper() in reason
    assert error_message in reason


def test_device_availability_converts_torch_import_exception_to_reason(monkeypatch):
    import builtins

    from app.routers.system import device_availability

    real_import = builtins.__import__

    def failing_import(name, *args, **kwargs):
        if name == "torch":
            raise RuntimeError("torch 动态库加载失败")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", failing_import)

    available, reason = device_availability("cuda")

    assert available is False
    assert "torch 动态库加载失败" in reason


def test_device_availability_does_not_swallow_keyboard_interrupt():
    from app.routers.system import device_availability

    torch_module = SimpleNamespace(
        cuda=SimpleNamespace(
            is_available=lambda: (_ for _ in ()).throw(KeyboardInterrupt())
        )
    )

    with pytest.raises(KeyboardInterrupt):
        device_availability("cuda", torch_module=torch_module)


def test_cors_origins_are_parsed_and_defaults_are_local():
    from app.main import cors_middleware_options, parse_cors_origins

    assert parse_cors_origins(None) == [
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    ]
    assert parse_cors_origins(" https://example.test, http://localhost:3000 ") == [
        "https://example.test",
        "http://localhost:3000",
    ]
    assert parse_cors_origins("") == []
    assert cors_middleware_options("*")["allow_credentials"] is False
    assert cors_middleware_options("*")["allow_origins"] == ["*"]
    assert cors_middleware_options("https://example.test")["allow_credentials"] is True


def test_readiness_reports_ready_without_application_lifespan(tmp_path, monkeypatch):
    import app.routers.system as system

    monkeypatch.setattr(system, "get", lambda key, default=None: "cpu")
    monkeypatch.setattr(system, "get_runtime_paths", lambda: {
        "config": tmp_path / "config",
        "cache": tmp_path / "cache",
        "tmp": tmp_path / "tmp",
        "output": tmp_path / "output",
    })
    response = awaitable_result(system.readiness_check())
    assert response.status_code == 200
    body = response.body
    assert b'"status":"ready"' in body
    assert b'"writable":true' in body
    assert b'"output"' in body
    assert b'"configured":"cpu"' in body
    assert b'"available":true' in body


def test_readiness_returns_503_when_directory_is_unwritable(monkeypatch, tmp_path):
    import app.routers.system as system

    monkeypatch.setattr(system, "get", lambda key, default=None: "cpu")
    monkeypatch.setattr(system, "get_runtime_paths", lambda: {
        "config": tmp_path / "config",
        "cache": tmp_path / "cache",
        "tmp": tmp_path / "tmp",
        "output": tmp_path / "output",
    })
    monkeypatch.setattr(system, "_directory_status", lambda path: {
        "path": str(path), "writable": path.name != "tmp"
    })

    response = awaitable_result(system.readiness_check())
    assert response.status_code == 503
    assert b'"status":"not_ready"' in response.body


def test_readiness_returns_503_for_configured_unavailable_cuda(
    monkeypatch,
    tmp_path,
):
    import app.routers.system as system

    monkeypatch.setattr(system.platform, "system", lambda: "Linux")
    monkeypatch.setattr(system.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(system, "get", lambda key, default=None: "cuda")
    monkeypatch.setitem(
        sys.modules,
        "torch",
        SimpleNamespace(
            cuda=SimpleNamespace(
                is_available=lambda: (_ for _ in ()).throw(
                    RuntimeError("CUDA 驱动初始化失败")
                )
            )
        ),
    )
    monkeypatch.setattr(system, "get_runtime_paths", lambda: {
        "config": tmp_path / "config",
        "cache": tmp_path / "cache",
        "tmp": tmp_path / "tmp",
        "output": tmp_path / "output",
    })

    response = awaitable_result(system.readiness_check())
    body = json.loads(response.body)

    assert response.status_code == 503
    assert body["device"] == {
        "configured": "cuda",
        "default": "cpu",
        "available": False,
        "reason": "CUDA 探测失败：CUDA 驱动初始化失败",
    }


def awaitable_result(awaitable):
    import asyncio

    return asyncio.run(awaitable)
