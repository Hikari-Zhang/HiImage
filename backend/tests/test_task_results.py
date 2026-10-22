import asyncio

import httpx
import pytest
from fastapi import APIRouter, FastAPI

from app.services.task_results import TaskResults
from app.services.task_registry import TaskRegistry
from app.task_route import TaskRoute
from app.routers import tasks


def test_size_budget_eviction_and_expiration_remove_files(tmp_path):
    now = [100.0]
    results = TaskResults(base_dir=tmp_path, max_item_bytes=8, max_total_bytes=10, ttl_seconds=10, clock=lambda: now[0])
    assert results.put("one", b"123456")
    assert not results.put("oversized", b"123456789")
    assert results.read("one") == b"123456"
    assert results.put("two", b"abcdef")
    assert results.read("one") is None
    assert len(list(tmp_path.rglob("*.json"))) == 1
    now[0] += 10
    assert results.read("two") is None
    assert list(tmp_path.rglob("*.json")) == []


def test_task_ids_never_become_paths_and_registry_expiry_cleans_result(tmp_path):
    now = [0.0]
    results = TaskResults(base_dir=tmp_path, clock=lambda: now[0])
    registry = TaskRegistry(ttl_seconds=10, clock=lambda: now[0], result_store=results)
    registry.register("../../outside", "/upscale", queued=False)
    assert registry.save_result("../../outside", b'{"image":"output"}')
    registry.finish("../../outside", "succeeded", 200)
    assert registry.get("../../outside")["result_available"] is True
    assert all(path.parent.parent == tmp_path for path in tmp_path.rglob("*.json"))
    now[0] = 10
    assert registry.get("../../outside") is None
    assert list(tmp_path.rglob("*.json")) == []


def test_registry_record_eviction_discards_its_result(tmp_path):
    results = TaskResults(base_dir=tmp_path)
    registry = TaskRegistry(max_records=1, result_store=results)
    registry.register("old", "/upscale", queued=False)
    registry.save_result("old", b"output")
    registry.finish("old", "succeeded", 200)
    registry.register("new", "/upscale", queued=False)
    assert results.read("old") is None


def test_unwritable_cache_does_not_report_available(tmp_path):
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory")
    results = TaskResults(base_dir=blocked)
    assert results.put("one", b"output") is False
    assert results.read("one") is None


def test_result_endpoint_round_trips_output_without_reexecuting(monkeypatch, isolated_task_registry):
    async def scenario():
        app = FastAPI()
        app.include_router(tasks.router, prefix="/api")
        router = APIRouter(route_class=TaskRoute)
        calls = []
        @router.post("/output")
        async def output(body: dict):
            calls.append(body["task_id"])
            return {"image": "encoded-result", "mask": "encoded-mask", "width": 2, "height": 2}
        app.include_router(router, prefix="/api")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            result = await client.post("/api/output", json={"task_id": "one", "private_input": "secret"})
            state = await client.get("/api/tasks/one")
            assert state.json()["result_available"] is True
            assert "encoded-result" not in state.text
            recovered = await client.get("/api/tasks/one/result")
            assert recovered.json() == result.json()
            assert "secret" not in recovered.text
            assert recovered.headers["cache-control"] == "no-store"
            assert calls == ["one"]
            isolated_task_registry.results.remove("one")
            assert (await client.get("/api/tasks/one")).json()["result_available"] is False
            assert (await client.get("/api/tasks/one/result")).status_code == 410
    asyncio.run(scenario())


@pytest.mark.parametrize("status,expected", [("running", 409), ("failed", 409), ("interrupted", 409), ("succeeded", 410), ("unknown", 404)])
def test_result_endpoint_handles_unavailable_states(isolated_task_registry, status, expected):
    registry = isolated_task_registry
    if status != "unknown":
        registry.register("one", "/upscale", queued=False)
        if status != "running":
            registry.finish("one", status, 200 if status == "succeeded" else 500)
    async def scenario():
        app = FastAPI()
        app.include_router(tasks.router)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/tasks/one/result")
            assert response.status_code == expected
            assert response.headers["cache-control"] == "no-store"
    asyncio.run(scenario())


def test_cache_failure_preserves_successful_http_response(monkeypatch, isolated_task_registry):
    def fail(*_args):
        raise OSError("disk full")
    monkeypatch.setattr(isolated_task_registry.results, "put", fail)
    async def scenario():
        app = FastAPI()
        router = APIRouter(route_class=TaskRoute)
        @router.post("/output")
        async def output(body: dict):
            return {"image": "result"}
        app.include_router(router)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.post("/output", json={"task_id": "one"})).json() == {"image": "result"}
        state = isolated_task_registry.get("one")
        assert state["status"] == "succeeded"
        assert state["result_available"] is False
    asyncio.run(scenario())
