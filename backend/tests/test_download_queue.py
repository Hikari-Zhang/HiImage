"""下载竞争回归：使用受控线程，不联网、不写真实模型缓存。"""
import asyncio
import threading
import time

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


@pytest.mark.parametrize("status", ["done", "error", "cancelled"])
def test_subscribe_immediately_replays_terminal_snapshot_and_closes(harness, status):
    async def scenario():
        q = harness.queue
        task = download_queue.DownloadTask("a", "A", status=status, message="terminal detail")
        q._tasks["a"] = task
        stream = q.subscribe("a")
        try:
            event = await asyncio.wait_for(anext(stream), 0.1)
            assert event == task.to_dict()
            with pytest.raises(StopAsyncIteration):
                await asyncio.wait_for(anext(stream), 0.1)
            assert not q._subscribers
        finally:
            await stream.aclose()
    asyncio.run(scenario())


def test_start_failure_is_replayed_when_client_connects_late(harness):
    async def scenario():
        q = harness.queue
        model_registry.MODEL_BY_ID["bad"] = {"id": "bad", "name": "Bad"}
        task = q.submit("bad")
        await task._runner
        assert task.status == "error"
        stream = q.subscribe("bad")
        try:
            event = await asyncio.wait_for(anext(stream), 0.1)
            assert event["status"] == "error"
            assert "无下载来源" in event["message"]
        finally:
            await stream.aclose()
    asyncio.run(scenario())


@pytest.mark.parametrize("status", ["done", "error", "cancelled"])
def test_http_sse_returns_terminal_snapshot_without_waiting_for_heartbeat(harness, monkeypatch, status):
    import httpx
    from fastapi import FastAPI
    from app.routers import models
    monkeypatch.setattr(download_queue, "get_download_queue", lambda: harness.queue)
    task = download_queue.DownloadTask("a", "A", status=status)
    harness.queue._tasks["a"] = task

    async def scenario():
        app = FastAPI()
        app.include_router(models.router, prefix="/api")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await asyncio.wait_for(client.get("/api/models/subscribe/a"), 0.1)
            assert response.status_code == 200
            assert "text/event-stream" in response.headers["content-type"]
            assert response.text.count("event: status") == 1
            assert task.download_id in response.text
            assert f'"status": "{status}"' in response.text
            assert not harness.queue._subscribers
    asyncio.run(scenario())


def test_start_failure_broadcasts_to_existing_subscriber_and_closes(harness):
    async def scenario():
        q = harness.queue
        task = download_queue.DownloadTask("bad", "Bad", status="downloading", _config={"id": "bad"})
        q._tasks["bad"] = task
        q._active["bad"] = task
        stream = q.subscribe("bad")
        try:
            assert (await anext(stream))["status"] == "downloading"
            await q._run_download(task)
            assert (await asyncio.wait_for(anext(stream), 0.1))["status"] == "error"
            with pytest.raises(StopAsyncIteration):
                await asyncio.wait_for(anext(stream), 0.1)
            assert not q._subscribers
            assert not q.list_active()
        finally:
            await stream.aclose()
    asyncio.run(scenario())


def test_missing_task_subscription_returns_404_instead_of_hanging(harness, monkeypatch):
    import httpx
    from fastapi import FastAPI
    from app.routers import models
    monkeypatch.setattr(download_queue, "get_download_queue", lambda: harness.queue)

    async def scenario():
        app = FastAPI()
        app.include_router(models.router, prefix="/api")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await asyncio.wait_for(client.get("/api/models/subscribe/missing"), 0.1)
            assert response.status_code == 404
            assert not harness.queue._subscribers
    asyncio.run(scenario())


@pytest.fixture
def dependencies(harness, monkeypatch):
    import huggingface_hub

    class Dependencies:
        started = threading.Event()
        release = threading.Event()
        calls = []
        threads = []
        error = None

        def snapshot(self, repo_id, **_):
            self.calls.append(repo_id)
            self.threads.append(threading.get_ident())
            if repo_id == "test/dep-one":
                self.started.set()
                assert self.release.wait(2), "dependency worker was not released"
            if self.error:
                raise self.error
            return "unused-test-cache"

    state = Dependencies()
    model_registry.MODEL_BY_ID["a"]["dependencies"] = ["test/dep-one", "test/dep-two"]
    monkeypatch.setattr(huggingface_hub, "snapshot_download", state.snapshot)
    yield state
    state.release.set()


def test_dependency_download_keeps_health_api_and_sse_heartbeat_responsive(harness, dependencies, monkeypatch):
    import httpx
    from fastapi import FastAPI
    from app.routers import models
    from core.model_checker import ModelCheckResult
    monkeypatch.setattr(download_queue, "get_download_queue", lambda: harness.queue)
    monkeypatch.setattr(models.ModelChecker, "check_model", lambda *_: ModelCheckResult("a", "A", "test", "ok", "test"))
    monkeypatch.setattr(download_queue, "SSE_HEARTBEAT_SECONDS", 0.02, raising=False)

    async def scenario():
        q = harness.queue
        watchdog = threading.Timer(0.6, dependencies.release.set)
        watchdog.start()
        stream = None
        started_at = time.monotonic()
        q.submit("a")
        try:
            assert await asyncio.to_thread(dependencies.started.wait, 1)
            assert not dependencies.release.is_set(), "dependency blocked the event loop until the watchdog released it"
            app = FastAPI()
            app.include_router(models.router, prefix="/api")
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                assert (await client.get("/api/models/health/a")).status_code == 200
                assert (await client.get("/api/models/queue")).status_code == 200
            stream = q.subscribe("a")
            assert (await anext(stream))["status"] == "downloading"
            async def heartbeat():
                async for event in stream:
                    if event.get("heartbeat"):
                        return event
            assert (await asyncio.wait_for(heartbeat(), 0.2))["heartbeat"]
            assert time.monotonic() - started_at < 0.5
            assert dependencies.threads[0] != threading.get_ident()
        finally:
            watchdog.cancel()
            dependencies.release.set()
            if stream:
                await stream.aclose()
            await harness.cleanup()
            watchdog.join()
    asyncio.run(scenario())


def test_required_dependency_failure_stops_main_download_and_releases_slot(harness, dependencies):
    dependencies.release.set()
    dependencies.error = OSError("dependency unavailable")

    async def scenario():
        q = harness.queue
        task = q.submit("a")
        try:
            await until(lambda: dependencies.calls)
            await asyncio.sleep(0.02)
            assert not harness.started, "main download started despite a missing required dependency"
            await task._runner
            assert task.status == "error"
            assert "test/dep-one" in task.message
            assert "dependency unavailable" in task.message
            assert dependencies.calls == ["test/dep-one"]
            assert not q.list_active()
        finally:
            await harness.cleanup()
    asyncio.run(scenario())


def test_cancel_during_dependency_wait_stops_later_dependencies_and_main(harness, dependencies):
    async def scenario():
        q = harness.queue
        watchdog = threading.Timer(0.5, dependencies.release.set)
        watchdog.start()
        task = q.submit("a")
        try:
            assert await asyncio.to_thread(dependencies.started.wait, 1)
            assert q.cancel("a", task.download_id)
            retry = q.submit("a")
            assert retry.status == "queued"
            assert task in q.list_active()
            assert dependencies.calls == ["test/dep-one"]
            # 取消排队重试，单独验证旧工作不会继续后续阶段。
            assert q.cancel("a", retry.download_id)
            dependencies.release.set()
            await task._runner
            assert dependencies.calls == ["test/dep-one"]
            assert not harness.started
            assert not q.list_active()
        finally:
            watchdog.cancel()
            dependencies.release.set()
            await harness.cleanup()
            watchdog.join()
    asyncio.run(scenario())


def test_dependencies_main_and_cache_bridge_run_in_order_on_worker(harness, dependencies, monkeypatch):
    import huggingface_hub
    events = []
    threads = []
    snapshot = dependencies.snapshot
    def dependency(repo_id, **kwargs):
        events.append(repo_id)
        threads.append(threading.get_ident())
        return snapshot(repo_id, **kwargs)
    def main(*_, **__):
        events.append("main")
        threads.append(threading.get_ident())
    def bridge(*_):
        events.append("bridge")
        threads.append(threading.get_ident())
    monkeypatch.setattr(huggingface_hub, "snapshot_download", dependency)
    monkeypatch.setattr(downloaders, "download_hf", main)
    monkeypatch.setattr(model_setup, "ensure_iopaint_hub_links", bridge)
    dependencies.release.set()

    async def scenario():
        task = harness.queue.submit("a")
        await task._runner
        assert task.status == "done"
        assert events == ["test/dep-one", "test/dep-two", "main", "bridge"]
        assert len(set(threads)) == 1
        assert threads[0] != threading.get_ident()
        assert not harness.queue.list_active()
    asyncio.run(scenario())


def test_dependency_failure_reaches_subscribers_and_next_task_can_run(harness, dependencies):
    dependencies.error = OSError("required weights missing")

    async def scenario():
        q = harness.queue
        task = q.submit("a")
        stream = q.subscribe("a")
        watchdog = threading.Timer(0.5, dependencies.release.set)
        watchdog.start()
        try:
            await anext(stream)
            assert await asyncio.to_thread(dependencies.started.wait, 1)
            dependencies.release.set()
            events = [event async for event in stream]
            await task._runner
            assert events[-1]["status"] == "error"
            assert "test/dep-one" in events[-1]["message"]
            assert not harness.started
            late = q.subscribe("a")
            try:
                assert (await anext(late))["status"] == "error"
            finally:
                await late.aclose()
            q.submit("c")
            await until(lambda: harness.started == ["c"])
        finally:
            watchdog.cancel()
            dependencies.release.set()
            await stream.aclose()
            await harness.cleanup()
            watchdog.join()
    asyncio.run(scenario())


def test_dependency_error_after_cancel_remains_cancelled(harness, dependencies):
    async def scenario():
        q = harness.queue
        task = q.submit("a")
        try:
            assert await asyncio.to_thread(dependencies.started.wait, 1)
            q.cancel("a", task.download_id)
            dependencies.error = OSError("transport aborted during cancellation")
            dependencies.release.set()
            await task._runner
            assert task.status == "cancelled"
            assert task.message == "已取消"
            assert dependencies.calls == ["test/dep-one"]
            assert not harness.started
        finally:
            dependencies.release.set()
            await harness.cleanup()
    asyncio.run(scenario())


def test_coroutine_cancel_during_dependency_keeps_slot_and_delete_guard(harness, dependencies):
    from core.model_resources import model_resources, ModelResourcesBusy

    async def scenario():
        q = harness.queue
        task = q.submit("a")
        try:
            assert await asyncio.to_thread(dependencies.started.wait, 1)
            for _ in range(3):
                task._runner.cancel()
                await asyncio.sleep(0)
            assert not task._runner.done()
            assert task in q.list_active()
            assert not task._future.cancelled()
            with pytest.raises(ModelResourcesBusy):
                with model_resources.deleting():
                    pass
            dependencies.release.set()
            with pytest.raises(asyncio.CancelledError):
                await task._runner
            assert dependencies.calls == ["test/dep-one"]
            assert not harness.started
            assert not q.list_active()
            with model_resources.deleting():
                pass
        finally:
            dependencies.release.set()
            await harness.cleanup()
    asyncio.run(scenario())


def test_shared_dependency_stays_locked_until_cancelled_worker_exits(harness, dependencies):
    harness.limit = 2
    model_registry.MODEL_BY_ID["b"].update({"hf_model_id": "test/main-b", "dependencies": ["test/dep-one"]})

    async def scenario():
        q = harness.queue
        old = q.submit("a")
        try:
            assert await asyncio.to_thread(dependencies.started.wait, 1)
            assert q.cancel("a", old.download_id)
            blocked = q.submit("b")
            assert blocked.status == "queued"
            q.submit("c")
            await until(lambda: harness.started == ["c"])
            assert dependencies.calls == ["test/dep-one"]
            dependencies.release.set()
            await until(lambda: "b" in harness.started)
            assert "a" not in harness.started
            assert dependencies.calls == ["test/dep-one", "test/dep-one"]
            assert harness.peak == 2
        finally:
            dependencies.release.set()
            await harness.cleanup()
    asyncio.run(scenario())
