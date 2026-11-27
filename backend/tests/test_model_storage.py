"""模型库的配置顺序必须在下载、检测和实际加载时保持一致。"""
import hashlib
import sys
from types import SimpleNamespace

import pytest

from app import config
from app.routers import models
from core import paths
from core.model_checker import ModelChecker
from core.model_files import delete_files
from core.model_registry import get_model
from core.model_resources import model_resources


@pytest.fixture
def libraries(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "CACHE_ROOT", tmp_path / "cache")
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path / "config")
    monkeypatch.setattr(config, "CONFIG_PATH", config.CONFIG_DIR / "settings.json")
    monkeypatch.setattr(config, "_cache", {})
    monkeypatch.setattr(config, "_loaded", False)
    return tmp_path / "first", tmp_path / "second"


def test_ordered_reads_and_first_directory_downloads(libraries, tmp_path, monkeypatch):
    first, second = libraries
    fallback = paths.default_model_directory()
    assert fallback == tmp_path / "cache" / "HiImage"
    cfg = get_model("RealESRGAN_x2plus")
    from core.upscaler import _get_weight_path
    default_file = paths.resolve_model_cache_path(cfg)
    assert default_file.is_relative_to(fallback)
    relative = default_file.relative_to(fallback)
    monkeypatch.setattr(paths, "REALESRGAN_HOME", tmp_path / "legacy")
    for directory in (first, second, fallback):
        path = directory / relative
        path.parent.mkdir(parents=True)
        path.write_bytes(b"fixture")
    config.save({"storage.model_directories": [str(second), str(first)]})
    assert paths.resolve_model_cache_path(cfg) == second / relative
    for directory in (second, first, fallback):
        expected = directory / relative
        assert ModelChecker().check_config({**cfg, "size_mb": None}).file_path == str(expected)
        assert _get_weight_path(cfg["id"]) == str(expected)
        assert delete_files(cfg)["deleted"] == [str(expected)]
    assert paths.resolve_model_cache_path(cfg) == second / relative


def test_default_library_receives_new_download(libraries, tmp_path):
    from core.downloaders import download_direct

    source = tmp_path / "weight.bin"
    source.write_bytes(b"temporary weight")
    cfg = {**get_model("wm_lama"), "download_url": source.as_uri(),
           "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
    download_direct(cfg)
    installed = paths.default_model_directory() / "lama/big-lama.pt"
    assert installed.read_bytes() == source.read_bytes()
    assert paths.find_local_model_path(cfg) == installed


def test_hf_manual_library_precedes_lower_hub_and_prompt_weights_share_order(libraries):
    from core.prompt_edit_windows import weight_locations
    first, second = libraries
    config.save({"storage.model_directories": [str(first), str(second)]})
    assert paths.get_hf_hub_dir() == first / "huggingface/hub"
    manual = first / "huggingface/manual/demo--ordered"
    manual.mkdir(parents=True)
    repo = second / "huggingface/hub/models--demo--ordered"
    revision = "a" * 40
    lower = repo / "snapshots" / revision
    lower.mkdir(parents=True)
    (repo / "refs").mkdir()
    (repo / "refs/main").write_text(revision)
    assert paths.find_hf_snapshot("demo/ordered") == manual
    manual.rmdir()
    assert paths.find_hf_snapshot("demo/ordered") == lower
    assert weight_locations()[:2] == tuple(p / "prompt-edit-windows/weights" for p in libraries)


def test_rembg_session_loads_exact_weight_from_second_directory(libraries, monkeypatch):
    from core.rembg_executor import RembgExecutor
    first, second = libraries
    config.save({"storage.model_directories": [str(first), str(second)]})
    cfg = {**get_model("birefnet"), "md5": hashlib.md5(b"fixture").hexdigest()}
    file = second / "rembg" / cfg["onnx_filename"]
    file.parent.mkdir(parents=True)
    file.write_bytes(b"fixture")

    class Session:
        @classmethod
        def name(cls):
            return "birefnet-general"

        def __init__(self, name, options):
            self.file = self.download_models()

    monkeypatch.setitem(sys.modules, "rembg.sessions", SimpleNamespace(sessions_class=[Session]))
    monkeypatch.setitem(sys.modules, "onnxruntime", SimpleNamespace(SessionOptions=SimpleNamespace))
    executor = RembgExecutor(cfg, "cpu")
    executor.load_model()
    assert executor._session.file == str(file)
    assert paths.rembg_model_path(cfg) == first / "rembg" / file.name


def test_invalid_or_busy_edits_preserve_order(libraries):
    import asyncio
    from fastapi import HTTPException
    from starlette.requests import Request
    first, second = libraries
    request = Request({"type": "http", "scheme": "http", "path": "/", "headers": [],
                       "server": ("localhost", 8787), "client": ("127.0.0.1", 1234), "query_string": b""})

    def save(*directories):
        data = models.ModelStorageUpdate(directories=[str(path) for path in directories])
        return asyncio.run(models.update_model_storage(data, request))

    save(second, first, second)
    config.reload()
    assert paths.model_storage_dirs() == (second.resolve(), first.resolve(), paths.default_model_directory())
    with pytest.raises(HTTPException) as invalid:
        save("relative")
    assert invalid.value.status_code == 422
    with model_resources.use(), pytest.raises(HTTPException) as busy:
        save(first)
    assert busy.value.status_code == 409
    assert paths.model_storage_dirs()[0] == second.resolve()
    save()
    assert paths.model_storage_dirs() == (paths.default_model_directory(),)
