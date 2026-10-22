import asyncio
import threading

import httpx
import pytest
from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel

from app.services.task_registry import TaskRegistry, TaskConflict, TaskCapacityFull
from app.task_route import TaskRoute
from app.task_id import TaskId
from app.shared_executor import inference_admission, InferenceScheduler, install_inference_queue_handler
from app.websocket.progress import ProgressManager
from app.routers import tasks


def test_registry_retains_terminal_state_and_returns_detached_snapshots():
    registry = TaskRegistry()
    registry.register("one", "/upscale", queued=True)
    assert registry.get("one")["status"] == "queued"
    registry.mark_running("one")
    registry.progress("one", 40, "working")
    snapshot = registry.get("one")
    snapshot["message"] = "mutated"
    assert registry.get("one")["message"] == "working"
    registry.finish("one", "succeeded", 200)
    terminal = registry.get("one")
    registry.mark_running("one")
    registry.progress("one", 1, "late")
    registry.finish("one", "failed", 500)
    assert registry.get("one") == terminal
    assert terminal["progress"] == 100
    assert terminal["http_status"] == 200


def test_registry_ttl_does_not_expire_active_work():
    now = [100.0]
    registry = TaskRegistry(ttl_seconds=10, clock=lambda: now[0])
    registry.register("active", "/upscale", queued=True)
    registry.register("done", "/upscale", queued=False)
    registry.finish("done", "failed", 422)
    now[0] += 10
    assert registry.get("done") is None
    assert registry.get("active")["status"] == "queued"
    registry.register("done", "/upscale", queued=False)


def test_registry_evicts_oldest_terminal_record_but_never_active_work():
    now = [100.0]
    registry = TaskRegistry(max_records=3, clock=lambda: now[0])
    registry.register("active", "/upscale", queued=True)
    for key in ("older", "newer"):
        registry.register(key, "/upscale", queued=False)
        registry.finish(key, "succeeded", 200)
        now[0] += 1
    registry.register("latest", "/upscale", queued=True)
    assert registry.get("older") is None
    assert registry.get("newer")["status"] == "succeeded"
    assert registry.get("active")["status"] == "queued"


def test_registry_rejects_duplicate_ids_and_full_active_capacity():
    registry = TaskRegistry(max_records=1)
    registry.register("one", "/upscale", queued=False)
    with pytest.raises(TaskConflict):
        registry.register("one", "/cutout", queued=False)
    with pytest.raises(TaskCapacityFull):
        registry.register("two", "/upscale", queued=False)
    assert registry.get("one")["operation"] == "/upscale"


@pytest.mark.parametrize("percent", [-1, 101, float("nan"), float("inf")])
def test_registry_ignores_invalid_progress(percent):
    registry = TaskRegistry()
    registry.register("one", "/upscale", queued=False)
    registry.progress("one", percent, "invalid")
    assert registry.get("one")["progress"] == 0


class JobRequest(BaseModel):
    task_id: TaskId
    mode: str = "ok"


def create_test_app(monkeypatch, *, max_pending=1, blocking_release=None):
    from app import shared_executor
    scheduler = InferenceScheduler(max_pending=max_pending)
    monkeypatch.setattr(shared_executor, "inference_scheduler", scheduler)
    app = FastAPI()
    install_inference_queue_handler(app)
    app.include_router(tasks.router, prefix="/api")
    router = APIRouter(route_class=TaskRoute)
    entered = asyncio.Event()
    release = asyncio.Event()
    executed = []

    @router.post("/job", dependencies=[Depends(inference_admission)])
    async def job(req: JobRequest):
        executed.append(req.task_id)
        if req.mode == "wait":
            entered.set()
            await release.wait()
        if req.mode == "blocking":
            loop = asyncio.get_running_loop()
            def work():
                loop.call_soon_threadsafe(entered.set)
                blocking_release.wait(2)
            await scheduler.run_blocking(work)
        if req.mode == "http-error":
            raise HTTPException(422, "private request data")
        if req.mode == "response-error":
            return JSONResponse({"detail": "private error text"}, status_code=422)
        if req.mode == "exception":
            raise RuntimeError("private exception data")
        return {"image": "private-image-result"}

    @router.post("/cpu")
    async def cpu(req: JobRequest):
        assert tasks.task_registry.get(req.task_id)["status"] == "running"
        return {"ok": True}

    app.include_router(router, prefix="/api")
    return app, scheduler, entered, release, executed


def test_query_observes_queue_running_and_success_and_duplicate_does_not_execute(monkeypatch):
    async def scenario():
        app, scheduler, entered, release, executed = create_test_app(monkeypatch)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            first = asyncio.create_task(client.post("/api/job", json={"task_id": "one", "mode": "wait"}))
            await asyncio.wait_for(entered.wait(), 1)
            second = asyncio.create_task(client.post("/api/job", json={"task_id": "two"}))
            for _ in range(100):
                if tasks.task_registry.get("two"):
                    break
                await asyncio.sleep(0)
            assert (await client.get("/api/tasks/one")).json()["status"] == "running"
            assert (await client.get("/api/tasks/two")).json()["status"] == "queued"
            duplicate = await client.post("/api/job", json={"task_id": " one "})
            assert duplicate.status_code == 409
            assert duplicate.json()["code"] == "TASK_ID_CONFLICT"
            release.set()
            assert (await first).status_code == (await second).status_code == 200
            state = await client.get("/api/tasks/one")
            assert state.json()["status"] == "succeeded"
            assert state.json()["http_status"] == 200
            assert state.headers["cache-control"] == "no-store"
            assert (await client.get("/api/tasks/%20one%20")).json()["task_id"] == "one"
            assert "private-image-result" not in state.text
            assert (await client.post("/api/job", json={"task_id": "one"})).status_code == 409
            assert executed == ["one", "two"]
            assert (await client.post("/api/cpu", json={"task_id": "cpu"})).status_code == 200
            assert (await client.get("/api/tasks/cpu")).json()["status"] == "succeeded"
        await scheduler.shutdown()
    asyncio.run(scenario())


@pytest.mark.parametrize("mode,code", [("http-error", 422), ("response-error", 422), ("exception", 500)])
def test_terminal_failure_uses_actual_http_outcome_without_exposing_error_body(monkeypatch, mode, code):
    async def scenario():
        app, scheduler, *_ = create_test_app(monkeypatch)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test") as client:
            response = await client.post("/api/job", json={"task_id": "failed", "mode": mode})
            assert response.status_code == code
            state = await client.get("/api/tasks/failed")
            assert state.json()["status"] == "failed"
            assert state.json()["http_status"] == code
            assert "private" not in state.text
        await scheduler.shutdown()
    asyncio.run(scenario())


def test_queue_rejection_leaves_id_reusable_and_cancellation_is_interrupted(monkeypatch):
    async def scenario():
        app, scheduler, entered, release, _ = create_test_app(monkeypatch, max_pending=0)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            first = asyncio.create_task(client.post("/api/job", json={"task_id": "one", "mode": "wait"}))
            await asyncio.wait_for(entered.wait(), 1)
            assert (await client.post("/api/job", json={"task_id": "rejected"})).status_code == 429
            assert (await client.get("/api/tasks/rejected")).status_code == 404
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            assert (await client.get("/api/tasks/one")).json()["status"] == "interrupted"
            assert scheduler.snapshot()["running"] == 0
            assert (await client.post("/api/job", json={"task_id": "rejected"})).status_code == 200
        await scheduler.shutdown()
    asyncio.run(scenario())


def test_progress_without_a_websocket_is_queryable_but_complete_is_not_terminal(isolated_task_registry):
    registry = isolated_task_registry
    registry.register("one", "/upscale", queued=False)
    async def scenario():
        manager = ProgressManager()
        await manager.send_progress(40, "working", task_id="one")
        assert registry.get("one")["progress"] == 40
        await manager.send_complete(task_id="one")
        assert registry.get("one")["status"] == "running"
        registry.finish("one", "failed", 500)
        await manager.send_progress(90, "late", task_id="one")
        assert registry.get("one")["status"] == "failed"
        assert registry.get("one")["message"] != "late"
    asyncio.run(scenario())


def test_all_image_posts_have_tracking_and_main_exposes_query():
    from app.main import app
    paths = {route.path for route in app.routes if isinstance(route, APIRoute) and isinstance(route, TaskRoute) and "POST" in route.methods}
    assert paths == {"/api/detect", "/api/inpaint", "/api/inpaint/with-mask", "/api/pipeline/run",
                     "/api/upscale", "/api/upscale/batch", "/api/cutout", "/api/cutout/refine",
                     "/api/cutout/composite", "/api/synthesis/run", "/api/postprocess/fix"}
    assert any(route.path == "/api/tasks/{task_id}" for route in app.routes)


def test_cancelled_blocking_work_remains_running_until_the_worker_exits(monkeypatch):
    async def scenario():
        release = threading.Event()
        app, scheduler, entered, *_ = create_test_app(monkeypatch, blocking_release=release)
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                first = asyncio.create_task(client.post("/api/job", json={"task_id": "one", "mode": "blocking"}))
                await asyncio.wait_for(entered.wait(), 1)
                first.cancel()
                await asyncio.sleep(0)
                assert (await client.get("/api/tasks/one")).json()["status"] == "running"
                assert scheduler.snapshot()["running"] == 1
                release.set()
                with pytest.raises(asyncio.CancelledError):
                    await first
                assert (await client.get("/api/tasks/one")).json()["status"] == "interrupted"
        finally:
            release.set()
            await scheduler.shutdown()
    asyncio.run(scenario())


def test_invalid_payload_and_unknown_query_keep_http_validation_contract(monkeypatch):
    async def scenario():
        app, scheduler, _, _, executed = create_test_app(monkeypatch)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            unknown = await client.get("/api/tasks/unknown")
            assert unknown.status_code == 404
            assert unknown.headers["cache-control"] == "no-store"
            for body in ({}, {"task_id": " "}, {"task_id": "x" * 129}, []):
                assert (await client.post("/api/job", json=body)).status_code == 422
            assert (await client.post("/api/job", content="not-json", headers={"Content-Type": "application/json"})).status_code == 422
            assert (await client.get("/api/tasks/" + "x" * 129)).status_code == 422
            assert (await client.post("/api/job", json={"task_id": "invalid-fields", "mode": {"secret": "private"}})).status_code == 422
            state = await client.get("/api/tasks/invalid-fields")
            assert state.json()["status"] == "failed"
            assert state.json()["http_status"] == 422
            assert "private" not in state.text
            assert executed == []
        await scheduler.shutdown()
    asyncio.run(scenario())


def test_full_tracking_capacity_returns_retryable_503_without_executing(monkeypatch):
    from app import task_route
    registry = TaskRegistry(max_records=1)
    registry.register("active", "/upscale", queued=False)
    monkeypatch.setattr(task_route, "task_registry", registry)
    async def scenario():
        app, scheduler, _, _, executed = create_test_app(monkeypatch)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/job", json={"task_id": "new"})
            assert response.status_code == 503
            assert response.json()["code"] == "TASK_REGISTRY_FULL"
            assert response.json()["retryable"] is True
            assert response.headers["retry-after"] == "1"
            assert executed == []
        await scheduler.shutdown()
    asyncio.run(scenario())


def test_real_upscale_route_records_encoding_failure_after_stage_complete(monkeypatch, isolated_task_registry):
    import numpy as np
    from app.routers import upscale

    async def scenario():
        app, scheduler, *_ = create_test_app(monkeypatch)
        app.include_router(upscale.router, prefix="/api")
        monkeypatch.setattr(upscale, "inference_scheduler", scheduler)
        image = np.zeros((2, 2, 3), dtype=np.uint8)
        monkeypatch.setattr(upscale, "check_image_size", lambda _value: None)
        monkeypatch.setattr(upscale, "decode_image", lambda _value: image)
        monkeypatch.setattr(upscale, "enforce_output_pixel_budget", lambda *_args: 0)
        monkeypatch.setattr(upscale.image_processing, "upscale_image", lambda *_args: image)
        def broken_encode(_image):
            state = isolated_task_registry.get("encoding-failed")
            assert state["status"] == "running"
            assert state["progress"] == 100
            raise RuntimeError("private encoder failure")
        monkeypatch.setattr(upscale, "encode_image", broken_encode)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test") as client:
            response = await client.post("/api/upscale", json={"task_id": "encoding-failed", "image": "private-input", "device": "cpu"})
            assert response.status_code == 500
            state = await client.get("/api/tasks/encoding-failed")
            assert state.json()["status"] == "failed"
            assert state.json()["http_status"] == 500
            assert "private" not in state.text
        await scheduler.shutdown()
    asyncio.run(scenario())
