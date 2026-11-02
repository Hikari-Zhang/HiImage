"""下载竞争回归：使用受控线程，不联网、不写真实模型缓存。"""
import asyncio
import threading

import pytest

from core import download_queue, downloaders, model_registry, model_setup


async def until(predicate, timeout=2):
    async def poll():
        while not predicate():
            await asyncio.sleep(0.005)
    await asyncio.wait_for(poll(), timeout)


@pytest.fixture
def harness(monkeypatch):
    class Harness:
        def __init__(self):
            self.queue = download_queue.DownloadQueue()
            self.limit = 1
            self.started = []
            self.gates = []
            self.running = 0
            self.peak = 0
            self.lock = threading.Lock()

        def download(self, cfg, progress_cb=None, cancel_check=None):
            gate = threading.Event()
            with self.lock:
                self.started.append(cfg["id"])
                self.gates.append(gate)
                self.running += 1
                self.peak = max(self.peak, self.running)
            try:
                assert gate.wait(3), "test worker was not released"
                if progress_cb:
                    progress_cb({"message": "late progress from worker"})
            finally:
                with self.lock:
                    self.running -= 1

        async def cleanup(self):
            for _ in range(100):
                for gate in self.gates:
                    gate.set()
                if not self.queue.list_active() and not self.running:
                    return
                await asyncio.sleep(0.01)
            assert not self.running

    state = Harness()
    monkeypatch.setattr(download_queue.DownloadQueue, "max_concurrent", property(lambda _: state.limit))
    monkeypatch.setattr(model_registry, "MODEL_BY_ID", {
        "a": {"id": "a", "name": "A", "hf_model_id": "test/shared"},
        "b": {"id": "b", "name": "B", "hf_model_id": "test/shared"},
        "c": {"id": "c", "name": "C", "hf_model_id": "test/independent"},
    })
    monkeypatch.setattr(downloaders, "download_hf", state.download)
    monkeypatch.setattr(model_setup, "ensure_iopaint_hub_links", lambda *_: None)
    return state


def test_cancel_keeps_slot_until_real_worker_exits(harness):
    async def scenario():
        q = harness.queue
        old = q.submit("a")
        try:
            await until(lambda: len(harness.started) == 1)
            assert q.cancel("a")
            assert old in q.list_active()
            q.submit("c")
            await asyncio.sleep(0.03)
            assert harness.started == ["a"]
            harness.gates[0].set()
            await until(lambda: len(harness.started) == 2)
            assert harness.peak == 1
        finally:
            await harness.cleanup()
    asyncio.run(scenario())


def test_immediate_retry_has_own_identity_and_waits_for_old_thread(harness):
    async def scenario():
        q = harness.queue
        first = q.submit("a")
        try:
            await until(lambda: len(harness.started) == 1)
            assert q.cancel("a")
            retry = q.submit("a")
            assert retry is not first
            assert retry.status == "queued"
            assert retry.to_dict()["downloadId"] != first.to_dict()["downloadId"]
            assert q.submit("a") is retry
            harness.gates[0].set()
            await until(lambda: len(harness.started) == 2)
            assert q.get_task("a") is retry
            assert retry in q.list_active()
            assert retry.status == "downloading"
            assert harness.peak == 1
        finally:
            await harness.cleanup()
    asyncio.run(scenario())


def test_models_sharing_repository_do_not_write_concurrently(harness):
    async def scenario():
        harness.limit = 2
        q = harness.queue
        q.submit("a")
        try:
            await until(lambda: len(harness.started) == 1)
            blocked = q.submit("b")
            assert blocked.status == "queued"
            q.submit("c")
            await until(lambda: "c" in harness.started)
            assert "b" not in harness.started
            harness.gates[0].set()
            await until(lambda: "b" in harness.started)
            assert harness.peak == 2
        finally:
            await harness.cleanup()
    asyncio.run(scenario())


def test_old_attempt_cannot_finish_or_close_retry_subscription(harness):
    async def scenario():
        q = harness.queue
        q.submit("a")
        stream = None
        try:
            await until(lambda: len(harness.started) == 1)
            q.cancel("a")
            retry = q.submit("a")
            stream = q.subscribe("a")
            initial = await anext(stream)
            harness.gates[0].set()
            event = await asyncio.wait_for(anext(stream), 1)
            assert event["status"] == "downloading"
            assert event["downloadId"] == initial["downloadId"] == retry.to_dict()["downloadId"]
            assert retry in q.list_active()
        finally:
            if stream:
                await stream.aclose()
            await harness.cleanup()
    asyncio.run(scenario())


def test_cancel_before_coroutine_starts_does_not_download_old_attempt(harness):
    async def scenario():
        q = harness.queue
        old = q.submit("a")
        assert q.cancel("a")
        retry = q.submit("a")
        try:
            await old._runner
            await until(lambda: len(harness.started) == 1)
            assert old.status == "cancelled"
            assert retry.status == "downloading"
            assert q.get_task("a") is retry
            assert harness.peak == 1
        finally:
            await harness.cleanup()
    asyncio.run(scenario())


def test_cancel_queued_attempt_removes_only_that_attempt(harness):
    async def scenario():
        q = harness.queue
        q.submit("a")
        try:
            await until(lambda: len(harness.started) == 1)
            old = q.submit("c")
            assert old.status == "queued"
            assert q.cancel("c", old.download_id)
            retry = q.submit("c")
            assert retry.position == 1
            assert old not in q.list_active()
            assert q.submit("c") is retry
            harness.gates[0].set()
            await until(lambda: len(harness.started) == 2)
            assert harness.started == ["a", "c"]
            assert retry in q.list_active()
        finally:
            await harness.cleanup()
    asyncio.run(scenario())


def test_delayed_cancel_for_old_download_id_does_not_cancel_retry(harness):
    async def scenario():
        q = harness.queue
        old = q.submit("a")
        try:
            await until(lambda: len(harness.started) == 1)
            q.cancel("a", old.download_id)
            retry = q.submit("a")
            assert not q.cancel("a", old.download_id)
            assert retry.status == "queued"
            assert not retry._cancel_flag
        finally:
            await harness.cleanup()
    asyncio.run(scenario())


@pytest.mark.parametrize("config", [
    {"hf_models": [{"id": "test/shared"}]},
    {"hf_model_id": "test/other", "dependencies": ["test/shared"]},
])
def test_combination_and_dependency_resource_claims(harness, monkeypatch, config):
    model_registry.MODEL_BY_ID["b"] = {"id": "b", "name": "B", **config}
    monkeypatch.setattr(downloaders, "download_hf_multi", harness.download)
    import huggingface_hub
    monkeypatch.setattr(huggingface_hub, "snapshot_download", lambda **_: "unused-test-path")

    async def scenario():
        harness.limit = 2
        q = harness.queue
        q.submit("a")
        try:
            await until(lambda: len(harness.started) == 1)
            assert q.submit("b").status == "queued"
            harness.gates[0].set()
            await until(lambda: "b" in harness.started)
            assert harness.peak == 1
        finally:
            await harness.cleanup()
    asyncio.run(scenario())


def test_repeated_coroutine_cancellation_waits_for_real_thread(harness):
    async def scenario():
        q = harness.queue
        task = q.submit("a")
        try:
            await until(lambda: len(harness.started) == 1)
            for _ in range(3):
                task._runner.cancel()
                await asyncio.sleep(0)
            assert task in q.list_active()
            assert not task._runner.done()
            harness.gates[0].set()
            with pytest.raises(asyncio.CancelledError):
                await task._runner
            assert task not in q.list_active()
            assert harness.running == 0
        finally:
            await harness.cleanup()
    asyncio.run(scenario())


def test_worker_failure_releases_slot_and_starts_next_task(harness, monkeypatch):
    def download(cfg, **kwargs):
        if cfg["id"] == "a":
            raise RuntimeError("test failure")
        harness.download(cfg, **kwargs)
    monkeypatch.setattr(downloaders, "download_hf", download)

    async def scenario():
        q = harness.queue
        failed = q.submit("a")
        next_task = q.submit("c")
        try:
            await until(lambda: harness.started == ["c"])
            assert failed.status == "error"
            assert next_task in q.list_active()
        finally:
            await harness.cleanup()
    asyncio.run(scenario())


def test_local_file_aliases_share_a_download_slot(harness, monkeypatch, tmp_path):
    from core import paths
    monkeypatch.setattr(paths, "CACHE_ROOT", tmp_path)
    for model_id in ("a", "b"):
        model_registry.MODEL_BY_ID[model_id] = {
            "id": model_id, "local_path": str(tmp_path / "shared.pth"),
            "download_url": f"https://unused.invalid/{model_id}",
        }
    monkeypatch.setattr(downloaders, "download_direct", harness.download)

    async def scenario():
        harness.limit = 2
        q = harness.queue
        q.submit("a")
        try:
            await until(lambda: harness.started == ["a"])
            assert q.submit("b").status == "queued"
            harness.gates[0].set()
            await until(lambda: harness.started == ["a", "b"])
            assert harness.peak == 1
        finally:
            await harness.cleanup()
    asyncio.run(scenario())


def test_task_uses_config_snapshot_from_submission(harness, monkeypatch):
    configs = []
    def download(cfg, **kwargs):
        configs.append(cfg["hf_model_id"])
        harness.download(cfg, **kwargs)
    monkeypatch.setattr(downloaders, "download_hf", download)

    async def scenario():
        q = harness.queue
        q.submit("a")
        model_registry.MODEL_BY_ID["a"]["hf_model_id"] = "test/changed"
        try:
            await until(lambda: len(configs) == 1)
            assert configs == ["test/shared"]
        finally:
            await harness.cleanup()
    asyncio.run(scenario())


def test_http_cancel_id_guard_and_retry_response(harness, monkeypatch):
    import httpx
    from fastapi import FastAPI
    from app.routers import models
    monkeypatch.setattr(download_queue, "get_download_queue", lambda: harness.queue)

    async def scenario():
        app = FastAPI()
        app.include_router(models.router, prefix="/api")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            old = (await client.post("/api/models/download/a")).json()
            try:
                await until(lambda: len(harness.started) == 1)
                url = f"/api/models/download/a?download_id={old['downloadId']}"
                assert (await client.delete(url)).status_code == 200
                retry = (await client.post("/api/models/download/a")).json()
                assert retry["downloadId"] != old["downloadId"]
                assert retry["status"] == "queued"
                assert (await client.delete(url)).status_code == 404
                assert harness.queue.get_task("a").status == "queued"
            finally:
                await harness.cleanup()
    asyncio.run(scenario())
