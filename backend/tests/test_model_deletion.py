"""模型删除回归：所有文件均位于临时缓存，不接触真实权重。"""
import asyncio
import threading
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from app.routers import models
from core import model_registry, paths, model_checker, download_queue


@pytest.fixture
def cache(monkeypatch, tmp_path):
    for name in ("CACHE_ROOT", "HF_HOME", "U2NET_HOME", "MODELS_DIR"):
        directory = tmp_path / name
        directory.mkdir()
        monkeypatch.setattr(paths, name, directory)
    monkeypatch.setattr(paths, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(model_registry, "MODEL_BY_ID", {})
    monkeypatch.setattr(download_queue, "get_download_queue", lambda: SimpleNamespace(list_active=lambda: []))
    model_checker.invalidate_model_cache()
    yield tmp_path
    model_checker.invalidate_model_cache()


def register(**entries):
    model_registry.MODEL_BY_ID.update({key: {"id": key, "name": key, **cfg} for key, cfg in entries.items()})


def weight(repo="owner/shared"):
    directory = paths.HF_HOME / "manual" / repo.replace("/", "--")
    directory.mkdir(parents=True)
    result = directory / "model.bin"
    result.write_bytes(b"temporary model")
    return result


async def delete(model_id="first"):
    app = FastAPI()
    app.include_router(models.router, prefix="/api")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        return await client.delete(f"/api/models/{model_id}/files")


@pytest.mark.parametrize("other", [
    {"hf_model_id": "owner/shared"},
    {"hf_models": [{"id": "owner/shared"}]},
    {"dependencies": ["owner/shared"]},
])
def test_shared_repository_cannot_be_deleted(cache, other):
    register(first={"hf_model_id": "owner/shared"}, second=other)
    file = weight()
    response = asyncio.run(delete())
    assert response.status_code == 409
    assert "second" in response.text
    assert file.exists()


def test_download_in_progress_prevents_deletion(cache, monkeypatch):
    register(first={"hf_model_id": "owner/shared"})
    file = weight()
    monkeypatch.setattr(download_queue, "get_download_queue", lambda: SimpleNamespace(
        list_active=lambda: [SimpleNamespace(model_id="first", status="downloading")],
    ))
    response = asyncio.run(delete())
    assert response.status_code == 409
    assert file.exists()


def test_idle_unique_repository_deleted_and_check_cache_invalidated(cache):
    register(first={"hf_model_id": "owner/shared"})
    file = weight()
    model_checker._model_check_cache["first"] = (object(), 0)
    response = asyncio.run(delete())
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert not file.exists()
    assert "first" not in model_checker._model_check_cache


def test_inference_in_progress_prevents_deletion(cache):
    from app.shared_executor import InferenceScheduler

    register(first={"hf_model_id": "owner/shared"})
    file = weight()

    async def scenario():
        scheduler = InferenceScheduler()
        try:
            async with scheduler.admission("using weights"):
                response = await delete()
                assert response.status_code == 409
                assert file.exists()
        finally:
            await scheduler.shutdown()
    asyncio.run(scenario())


def test_shared_local_file_is_protected(cache):
    file = paths.CACHE_ROOT / "shared.pth"
    file.write_bytes(b"temporary")
    register(first={"local_path": str(file)}, second={"local_path": str(file)})
    response = asyncio.run(delete())
    assert response.status_code == 409
    assert file.exists()


def test_rembg_deletes_actual_onnx_filename(cache):
    register(first={"provider": "rembg", "rembg_model_name": "display-name", "onnx_filename": "actual.onnx"})
    file = paths.U2NET_HOME / "actual.onnx"
    file.write_bytes(b"temporary")
    response = asyncio.run(delete())
    assert response.status_code == 200
    assert response.json()["ok"]
    assert not file.exists()


def test_both_manual_and_standard_cache_are_removed(cache):
    register(first={"hf_model_id": "owner/shared"})
    file = weight()
    standard = paths.get_hf_hub_dir() / "models--owner--shared"
    standard.mkdir(parents=True)
    (standard / "model.bin").write_bytes(b"temporary")
    response = asyncio.run(delete())
    assert response.status_code == 200
    assert len(response.json()["deleted"]) == 2
    assert not file.exists()
    assert not standard.exists()


def test_invalid_target_is_rejected_before_any_file_is_removed(cache):
    file = paths.U2NET_HOME / "actual.onnx"
    file.write_bytes(b"temporary")
    outside = cache / "outside.pth"
    outside.write_bytes(b"keep")
    register(first={"provider": "rembg", "onnx_filename": "actual.onnx", "local_path": str(outside)})
    response = asyncio.run(delete())
    assert response.status_code == 400
    assert file.exists()
    assert outside.read_bytes() == b"keep"


def test_delete_error_invalidates_cache_and_releases_guard(cache, monkeypatch):
    import shutil
    register(first={"hf_model_id": "owner/shared"})
    file = weight()
    original = shutil.rmtree
    with monkeypatch.context() as patch:
        def fail(*args, **kwargs):
            raise PermissionError("file busy")
        patch.setattr(shutil, "rmtree", fail)
        model_checker._model_check_cache["first"] = (object(), 0)
        response = asyncio.run(delete())
        assert response.status_code == 500
        assert "first" not in model_checker._model_check_cache
        assert file.exists()
    assert shutil.rmtree is original
    assert asyncio.run(delete()).json()["ok"]


def test_unknown_and_missing_model_do_not_report_deleted_weights(cache):
    assert asyncio.run(delete("unknown")).status_code == 404
    register(first={"hf_model_id": "owner/missing"})
    response = asyncio.run(delete())
    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert response.json()["deleted"] == []


def test_real_registry_sharing_includes_watermark_and_synthesis_models():
    from core.model_files import shared_model_ids
    shared = shared_model_ids("flux_fill", model_registry.MODEL_BY_ID)
    assert "flux_fill_prompt" in shared
    assert "grounded_sam_flux" in shared


def test_cancelled_download_thread_keeps_deletion_protected(cache, monkeypatch):
    from core import downloaders, model_setup

    register(first={"hf_model_id": "owner/shared"})
    file = weight()
    started, release, finished = threading.Event(), threading.Event(), threading.Event()

    def download(*args, **kwargs):
        started.set()
        try:
            assert release.wait(3)
        finally:
            finished.set()

    monkeypatch.setattr(downloaders, "download_hf", download)
    monkeypatch.setattr(model_setup, "ensure_iopaint_hub_links", lambda *_: None)

    async def scenario():
        queue = download_queue.DownloadQueue()
        task = queue.submit("first")
        runner = task._runner
        try:
            assert await asyncio.to_thread(started.wait, 2)
            assert queue.cancel("first")
            assert not runner.done()
            assert task in queue.list_active()
            # UI 已显示 cancelled，但线程及槽位都应保留到真实退出。
            response = await delete()
            assert response.status_code == 409
            assert file.exists()
        finally:
            release.set()
            await runner
            assert await asyncio.to_thread(finished.wait, 2)
        for _ in range(100):
            response = await delete()
            if response.status_code != 409:
                break
            await asyncio.sleep(0.01)
        assert response.status_code == 200
        assert response.json()["ok"]

    asyncio.run(scenario())


def test_delete_and_actual_inference_worker_are_mutually_exclusive(cache, monkeypatch):
    """删除期间新推理在线程中等待；推理失败后仍能再次删除。"""
    from app.shared_executor import InferenceScheduler
    from core import model_files

    register(first={"hf_model_id": "owner/shared"})
    weight()
    deletion_started, allow_delete, inference_started = threading.Event(), threading.Event(), threading.Event()
    original = model_files.delete_files

    def slow_delete(cfg):
        deletion_started.set()
        assert allow_delete.wait(3)
        assert not inference_started.is_set()
        return original(cfg)

    monkeypatch.setattr(model_files, "delete_files", slow_delete)

    async def scenario():
        scheduler = InferenceScheduler()
        def work():
            inference_started.set()
            raise RuntimeError("inference failed")
        deleting = asyncio.create_task(asyncio.to_thread(lambda: asyncio.run(delete())))
        inference = None
        try:
            assert await asyncio.to_thread(deletion_started.wait, 2)
            assert (await delete()).status_code == 409
            inference = asyncio.create_task(scheduler.run_blocking(work))
            await asyncio.sleep(0.03)
            assert not inference_started.is_set()
        finally:
            allow_delete.set()
            response = await deleting
            if inference is not None:
                with pytest.raises(RuntimeError, match="inference failed"):
                    await inference
            await scheduler.shutdown()
        assert response.status_code == 200
        assert inference_started.is_set()
        monkeypatch.setattr(model_files, "delete_files", original)
        weight()
        assert (await delete()).json()["ok"]
    asyncio.run(scenario())
