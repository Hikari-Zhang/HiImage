import asyncio
import threading
import time

import httpx
import pytest
from fastapi import Depends, FastAPI
from fastapi.routing import APIRoute


def run(coro):
    return asyncio.run(coro)


def test_capacity_is_one_running_plus_four_pending_and_sixth_is_rejected():
    from app.shared_executor import InferenceQueueFull, InferenceScheduler

    async def scenario():
        scheduler = InferenceScheduler(max_pending=4)
        release = threading.Event()
        started = threading.Event()

        async def job(name):
            async with scheduler.admission(name):
                return await scheduler.run_blocking(
                    lambda: (started.set(), release.wait(2), name)[-1]
                )

        first = asyncio.create_task(job("first"))
        await asyncio.to_thread(started.wait, 1)
        queued = [asyncio.create_task(job(f"queued-{index}")) for index in range(4)]
        await asyncio.sleep(0)

        assert scheduler.snapshot() == {
            "running": 1,
            "pending": 4,
            "capacity": 5,
            "accepting": True,
        }
        with pytest.raises(InferenceQueueFull):
            async with scheduler.admission("sixth"):
                pass

        release.set()
        assert await first == "first"
        assert await asyncio.gather(*queued) == [f"queued-{index}" for index in range(4)]
        assert scheduler.snapshot()["running"] == 0
        assert scheduler.snapshot()["pending"] == 0
        await scheduler.shutdown()

    run(scenario())


def test_fifo_and_exception_release_capacity():
    from app.shared_executor import InferenceScheduler

    async def scenario():
        scheduler = InferenceScheduler(max_pending=2)
        order = []

        async def job(name, fail=False):
            async with scheduler.admission(name):
                def blocking():
                    order.append(name)
                    if fail:
                        raise RuntimeError(name)
                    return name

                return await scheduler.run_blocking(blocking)

        tasks = [
            asyncio.create_task(job("first")),
            asyncio.create_task(job("second", fail=True)),
            asyncio.create_task(job("third")),
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        assert order == ["first", "second", "third"]
        assert isinstance(results[1], RuntimeError)
        assert scheduler.snapshot()["running"] == 0
        assert scheduler.snapshot()["pending"] == 0
        await scheduler.shutdown()

    run(scenario())


def test_cancellation_does_not_release_until_blocking_call_finishes():
    from app.shared_executor import InferenceScheduler

    async def scenario():
        scheduler = InferenceScheduler(max_pending=0)
        started = threading.Event()
        release = threading.Event()

        async def job():
            async with scheduler.admission("cancelled"):
                await scheduler.run_blocking(
                    lambda: (started.set(), release.wait(2))[1]
                )

        task = asyncio.create_task(job())
        await asyncio.to_thread(started.wait, 1)
        task.cancel()
        await asyncio.sleep(0.02)
        assert scheduler.snapshot()["running"] == 1

        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert scheduler.snapshot()["running"] == 0
        await scheduler.shutdown()

    run(scenario())


@pytest.mark.parametrize("cancel_count", [2, 5])
def test_repeated_cancellation_waits_for_blocking_call_before_releasing(
    cancel_count,
):
    from app.shared_executor import InferenceQueueFull, InferenceScheduler

    async def scenario():
        scheduler = InferenceScheduler(max_pending=0)
        started = threading.Event()
        release = threading.Event()
        executed = []

        async def job():
            async with scheduler.admission("repeated-cancel"):
                await scheduler.run_blocking(
                    lambda: (started.set(), release.wait(2))[1]
                )

        task = asyncio.create_task(job())
        await asyncio.to_thread(started.wait, 1)
        for _ in range(cancel_count):
            task.cancel()
            await asyncio.sleep(0)

        assert not task.done()
        assert scheduler.snapshot()["running"] == 1
        with pytest.raises(InferenceQueueFull):
            async with scheduler.admission("must-not-start"):
                executed.append("started")
        assert executed == []

        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert scheduler.snapshot()["running"] == 0
        await scheduler.shutdown()

    run(scenario())


def test_shutdown_rejects_new_work_and_waits_for_running_work():
    from app.shared_executor import InferenceQueueFull, InferenceScheduler

    async def scenario():
        scheduler = InferenceScheduler(max_pending=1)
        started = threading.Event()
        release = threading.Event()

        async def job():
            async with scheduler.admission("running"):
                await scheduler.run_blocking(
                    lambda: (started.set(), release.wait(2))[1]
                )

        task = asyncio.create_task(job())
        await asyncio.to_thread(started.wait, 1)
        shutdown = asyncio.create_task(scheduler.shutdown())
        await asyncio.sleep(0)
        assert scheduler.snapshot()["accepting"] is False
        assert not shutdown.done()
        with pytest.raises(InferenceQueueFull):
            async with scheduler.admission("late"):
                pass

        release.set()
        await task
        await shutdown

    run(scenario())


def test_scheduler_shutdown_joins_executor_without_blocking_event_loop(
    monkeypatch,
):
    from app.shared_executor import InferenceScheduler

    async def scenario():
        scheduler = InferenceScheduler(max_pending=0)
        close_started = threading.Event()
        allow_close = threading.Event()
        original_close = scheduler.close_executor

        def slow_close():
            close_started.set()
            allow_close.wait(1)
            original_close()

        monkeypatch.setattr(scheduler, "close_executor", slow_close)
        timer = threading.Timer(0.2, allow_close.set)
        timer.start()
        try:
            shutdown = asyncio.create_task(scheduler.shutdown())
            await asyncio.sleep(0.02)
            assert close_started.is_set()
            assert not allow_close.is_set()
            assert not shutdown.done()
            allow_close.set()
            await shutdown
        finally:
            allow_close.set()
            timer.join()

    run(scenario())


@pytest.mark.parametrize("cancel_count", [1, 3])
def test_lifespan_cancellation_waits_for_drain_executor(
    monkeypatch,
    cancel_count,
):
    import sys
    from types import SimpleNamespace

    import app.main as main
    from app.shared_executor import InferenceQueueFull, InferenceScheduler

    async def scenario():
        scheduler = InferenceScheduler(max_pending=0)
        started = threading.Event()
        release = threading.Event()
        executor_finished = threading.Event()
        original_close = scheduler.close_executor

        def observed_close():
            original_close()
            executor_finished.set()

        monkeypatch.setattr(scheduler, "close_executor", observed_close)
        monkeypatch.setattr(main, "inference_scheduler", scheduler)
        monkeypatch.setattr(main, "setup_logging", lambda: None)
        monkeypatch.setattr(main.log_manager, "set_loop", lambda _loop: None)
        monkeypatch.setattr(main, "get", lambda _key, default=None: default)
        monkeypatch.setattr(main, "apply_default_env_vars", lambda: None)

        context = main.lifespan(main.app)
        await context.__aenter__()

        async def model_work():
            async with scheduler.admission("native model"):
                await scheduler.run_blocking(
                    lambda: (started.set(), release.wait(2))[1]
                )

        work = asyncio.create_task(model_work())
        await asyncio.to_thread(started.wait, 1)
        shutdown = asyncio.create_task(context.__aexit__(None, None, None))
        await asyncio.sleep(0)
        assert scheduler.snapshot()["accepting"] is False

        for _ in range(cancel_count):
            shutdown.cancel()
            await asyncio.sleep(0)

        assert scheduler.snapshot()["running"] == 1
        with pytest.raises(InferenceQueueFull):
            async with scheduler.admission("late"):
                pass

        release.set()
        await work
        with pytest.raises(asyncio.CancelledError):
            await shutdown

        assert executor_finished.is_set()
        assert scheduler.snapshot() == {"running": 0, "pending": 0, "capacity": 1, "accepting": False}


    run(scenario())


def test_lifespan_drains_model_work_before_stopping_server(
    monkeypatch,
):
    import sys
    from types import SimpleNamespace

    import app.main as main
    from app.shared_executor import InferenceQueueFull, InferenceScheduler

    async def scenario():
        scheduler = InferenceScheduler(max_pending=0)
        started = threading.Event()
        release = threading.Event()

        monkeypatch.setattr(main, "inference_scheduler", scheduler)
        monkeypatch.setattr(main, "setup_logging", lambda: None)
        monkeypatch.setattr(main.log_manager, "set_loop", lambda _loop: None)
        monkeypatch.setattr(main, "get", lambda _key, default=None: default)
        monkeypatch.setattr(main, "apply_default_env_vars", lambda: None)

        context = main.lifespan(main.app)
        await context.__aenter__()

        async def model_work():
            async with scheduler.admission("native model"):
                await scheduler.run_blocking(
                    lambda: (started.set(), release.wait(2))[1]
                )

        work = asyncio.create_task(model_work())
        await asyncio.to_thread(started.wait, 1)
        shutdown = asyncio.create_task(context.__aexit__(None, None, None))
        await asyncio.sleep(0)

        assert scheduler.snapshot()["accepting"] is False
        with pytest.raises(InferenceQueueFull):
            async with scheduler.admission("late"):
                pass
        assert scheduler.snapshot()["running"] == 1

        release.set()
        await work
        await shutdown
        assert scheduler.snapshot() == {"running": 0, "pending": 0, "capacity": 1, "accepting": False}


    run(scenario())


def test_lifespan_closes_executor_when_scheduler_shutdown_fails(
    monkeypatch,
):
    import sys
    from types import SimpleNamespace

    import app.main as main

    async def scenario():
        calls = []

        class FailingScheduler:
            async def shutdown(self):
                calls.append("scheduler.shutdown")
                raise RuntimeError("drain failed")

            def close_executor(self):
                calls.append("scheduler.close_executor")

        errors = []
        monkeypatch.setattr(main, "inference_scheduler", FailingScheduler())
        monkeypatch.setattr(main, "setup_logging", lambda: None)
        monkeypatch.setattr(main.log_manager, "set_loop", lambda _loop: None)
        monkeypatch.setattr(
            main.log_manager,
            "error",
            lambda message, **_kwargs: errors.append(message),
        )
        monkeypatch.setattr(main, "get", lambda _key, default=None: default)
        monkeypatch.setattr(main, "apply_default_env_vars", lambda: None)

        async with main.lifespan(main.app):
            pass

        assert calls == [
            "scheduler.shutdown",
            "scheduler.close_executor",
        ]
        assert any("drain failed" in message for message in errors)

    run(scenario())


def test_queue_full_handler_returns_structured_429_and_health_stays_available():
    from app.shared_executor import (
        InferenceScheduler,
        install_inference_queue_handler,
    )

    async def scenario():
        scheduler = InferenceScheduler(max_pending=0)
        started = threading.Event()
        release = threading.Event()
        app = FastAPI()
        install_inference_queue_handler(app)

        async def admission():
            async with scheduler.admission("POST /busy"):
                yield

        @app.get("/health")
        async def health():
            return {"status": "ok"}

        @app.post("/busy", dependencies=[Depends(admission)])
        async def busy():
            await scheduler.run_blocking(
                lambda: (started.set(), release.wait(2))[1]
            )
            return {"status": "done"}

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            first = asyncio.create_task(client.post("/busy"))
            await asyncio.to_thread(started.wait, 1)
            rejected = await client.post("/busy")
            healthy = await client.get("/health")
            release.set()
            completed = await first
        await scheduler.shutdown()

        assert rejected.status_code == 429
        assert rejected.headers["Retry-After"] == "1"
        assert rejected.json() == {
            "code": "INFERENCE_QUEUE_FULL",
            "message": "服务器任务队列已满，请稍后重试",
            "retryable": True,
        }
        assert healthy.status_code == 200
        assert healthy.json() == {"status": "ok"}
        assert completed.status_code == 200

    run(scenario())


def test_invalid_pending_configuration_falls_back_safely():
    from app.shared_executor import DEFAULT_MAX_PENDING, max_pending_from_env

    assert max_pending_from_env({}) == DEFAULT_MAX_PENDING == 4
    assert max_pending_from_env({"HIIMAGE_MAX_PENDING_INFERENCE": "invalid"}) == 4
    assert max_pending_from_env({"HIIMAGE_MAX_PENDING_INFERENCE": "-1"}) == 4
    assert max_pending_from_env({"HIIMAGE_MAX_PENDING_INFERENCE": "33"}) == 4
    assert max_pending_from_env({"HIIMAGE_MAX_PENDING_INFERENCE": "0"}) == 0
    assert max_pending_from_env({"HIIMAGE_MAX_PENDING_INFERENCE": "32"}) == 32


def test_all_inference_posts_use_the_shared_admission_dependency():
    from app.routers import cutout, inpaint, postprocess, synthesis, upscale
    from app.shared_executor import inference_admission

    expected = {
        "/detect": True,
        "/inpaint": True,
        "/inpaint/with-mask": True,
        "/upscale": True,
        "/upscale/batch": True,
        "/cutout": True,
        "/cutout/refine": False,
        "/cutout/composite": False,
        "/synthesis/run": True,
        "/postprocess/fix": True,
        "/pipeline/run": True,
    }
    routes = {
        route.path: route
        for router in (
            inpaint.router,
            upscale.router,
            cutout.router,
            synthesis.router,
            postprocess.router,
        )
        for route in router.routes
        if isinstance(route, APIRoute) and "POST" in route.methods
    }

    assert routes.keys() == expected.keys()
    for path, requires_admission in expected.items():
        has_admission = any(
            dependency.call is inference_admission
            for dependency in routes[path].dependant.dependencies
        )
        assert has_admission is requires_admission, path

def test_ready_includes_queue_snapshot_and_is_not_ready_when_not_accepting(
    tmp_path,
    monkeypatch,
):
    import app.routers.system as system

    monkeypatch.setattr(system, "get", lambda key, default=None: "cpu")
    monkeypatch.setattr(
        system,
        "get_runtime_paths",
        lambda: {
            "config": tmp_path / "config",
            "cache": tmp_path / "cache",
            "tmp": tmp_path / "tmp",
            "output": tmp_path / "output",
        },
    )
    monkeypatch.setattr(
        system.inference_scheduler,
        "snapshot",
        lambda: {
            "running": 0,
            "pending": 0,
            "capacity": 5,
            "accepting": False,
        },
    )

    response = run(system.readiness_check())
    body = response.body.decode()

    assert response.status_code == 503
    assert '"status":"not_ready"' in body
    assert (
        '"inference_queue":{"running":0,"pending":0,"capacity":5,"accepting":false}'
        in body
    )


def test_routers_do_not_call_run_in_executor_directly():
    from pathlib import Path

    router_dir = Path(__file__).parents[1] / "app" / "routers"
    inference_routers = [
        "inpaint.py",
        "upscale.py",
        "cutout.py",
        "synthesis.py",
        "postprocess.py",
    ]
    for filename in inference_routers:
        assert "run_in_executor" not in (router_dir / filename).read_text(
            encoding="utf-8"
        ), filename


def test_batch_upscaler_creation_and_preload_run_on_inference_thread(
    monkeypatch,
):
    import sys
    from types import SimpleNamespace

    import numpy as np

    import app.routers.upscale as upscale
    from app.shared_executor import InferenceScheduler

    async def scenario():
        scheduler = InferenceScheduler(max_pending=0)
        main_thread = threading.get_ident()
        calls = []

        class FakeUpscaler:
            def __init__(self, **_kwargs):
                calls.append(("create", threading.get_ident()))

            def _ensure_model_loaded(self):
                calls.append(("preload", threading.get_ident()))

            def upscale(self, image):
                calls.append(("upscale", threading.get_ident()))
                return image

            def close(self):
                calls.append(("close", threading.get_ident()))

        class Progress:
            async def send_progress(self, *_args, **_kwargs):
                return None

            async def send_complete(self, *_args, **_kwargs):
                return None

        monkeypatch.setitem(
            sys.modules,
            "core.upscaler",
            SimpleNamespace(Upscaler=FakeUpscaler),
        )
        monkeypatch.setattr(upscale, "inference_scheduler", scheduler)
        monkeypatch.setattr(upscale, "progress_manager", Progress())
        monkeypatch.setattr(upscale, "check_image_size", lambda _value: None)
        monkeypatch.setattr(
            upscale,
            "decode_image",
            lambda _value: np.zeros((2, 2, 3), dtype=np.uint8),
        )
        monkeypatch.setattr(upscale, "encode_image", lambda _value: "encoded")
        monkeypatch.setattr(upscale, "get", lambda _key, default=None: 256)

        request = upscale.BatchUpscaleRequest(
            images=["image"],
            device="cpu",
            outscale=1,
            task_id="thread-check",
        )
        async with scheduler.admission("batch"):
            result = await upscale.upscale_image_batch(request)
        await scheduler.shutdown()

        assert result["summary"]["success"] == 1
        assert [name for name, _thread_id in calls[:3]] == [
            "create",
            "preload",
            "upscale",
        ]
        assert all(thread_id != main_thread for _name, thread_id in calls[:3])
        assert len({thread_id for _name, thread_id in calls[:3]}) == 1

    run(scenario())


@pytest.mark.parametrize(
    "failure_point",
    ["preload", "progress", "encode", "close", "preload_close"],
)
def test_batch_upscale_closes_once_on_failure_in_inference_thread(
    monkeypatch,
    failure_point,
):
    import sys
    from types import SimpleNamespace

    import numpy as np

    import app.routers.upscale as upscale
    from app.shared_executor import InferenceScheduler

    async def scenario():
        scheduler = InferenceScheduler(max_pending=0)
        main_thread = threading.get_ident()
        close_threads = []

        class FakeUpscaler:
            def __init__(self, **_kwargs):
                return None

            def _ensure_model_loaded(self):
                if failure_point in {"preload", "preload_close"}:
                    raise RuntimeError("preload failed")

            def upscale(self, image):
                return image

            def close(self):
                close_threads.append(threading.get_ident())
                if failure_point in {"close", "preload_close"}:
                    raise RuntimeError("close failed")

        class Progress:
            async def send_progress(self, *_args, **_kwargs):
                if failure_point == "progress":
                    raise RuntimeError("progress failed")

            async def send_complete(self, *_args, **_kwargs):
                return None

        monkeypatch.setitem(
            sys.modules,
            "core.upscaler",
            SimpleNamespace(Upscaler=FakeUpscaler),
        )
        monkeypatch.setattr(upscale, "inference_scheduler", scheduler)
        monkeypatch.setattr(upscale, "progress_manager", Progress())
        monkeypatch.setattr(upscale, "check_image_size", lambda _value: None)
        monkeypatch.setattr(
            upscale,
            "decode_image",
            lambda _value: np.zeros((2, 2, 3), dtype=np.uint8),
        )

        def encode(_value):
            if failure_point == "encode":
                raise RuntimeError("encode failed")
            return "encoded"

        monkeypatch.setattr(upscale, "encode_image", encode)
        monkeypatch.setattr(upscale, "get", lambda _key, default=None: 256)

        request = upscale.BatchUpscaleRequest(
            images=["image"],
            device="cpu",
            outscale=1,
            task_id=f"{failure_point}-cleanup",
        )
        async with scheduler.admission("batch"):
            expected_error = {
                "preload": "preload",
                "progress": "progress",
                "close": "close",
                "preload_close": "preload",
            }.get(failure_point)
            if expected_error is not None:
                with pytest.raises(RuntimeError, match=expected_error):
                    await upscale.upscale_image_batch(request)
            else:
                await upscale.upscale_image_batch(request)
        await scheduler.shutdown()

        assert len(close_threads) == 1
        assert close_threads[0] != main_thread

    run(scenario())


def test_batch_upscale_cancellation_closes_once_before_admission_releases(
    monkeypatch,
):
    import sys
    from types import SimpleNamespace

    import numpy as np

    import app.routers.upscale as upscale
    from app.shared_executor import InferenceScheduler

    async def scenario():
        scheduler = InferenceScheduler(max_pending=0)
        inference_started = threading.Event()
        release_inference = threading.Event()
        close_threads = []

        class FakeUpscaler:
            def __init__(self, **_kwargs):
                return None

            def _ensure_model_loaded(self):
                return None

            def upscale(self, image):
                inference_started.set()
                release_inference.wait(2)
                return image

            def close(self):
                close_threads.append(threading.get_ident())

        class Progress:
            async def send_progress(self, *_args, **_kwargs):
                return None

            async def send_complete(self, *_args, **_kwargs):
                return None

        monkeypatch.setitem(
            sys.modules,
            "core.upscaler",
            SimpleNamespace(Upscaler=FakeUpscaler),
        )
        monkeypatch.setattr(upscale, "inference_scheduler", scheduler)
        monkeypatch.setattr(upscale, "progress_manager", Progress())
        monkeypatch.setattr(upscale, "check_image_size", lambda _value: None)
        monkeypatch.setattr(
            upscale,
            "decode_image",
            lambda _value: np.zeros((2, 2, 3), dtype=np.uint8),
        )
        monkeypatch.setattr(upscale, "encode_image", lambda _value: "encoded")
        monkeypatch.setattr(upscale, "get", lambda _key, default=None: 256)
        request = upscale.BatchUpscaleRequest(
            images=["image"],
            device="cpu",
            outscale=1,
            task_id="cancel-cleanup",
        )

        async def batch():
            async with scheduler.admission("batch"):
                await upscale.upscale_image_batch(request)

        task = asyncio.create_task(batch())
        await asyncio.to_thread(inference_started.wait, 1)
        task.cancel()
        task.cancel()
        await asyncio.sleep(0)
        assert scheduler.snapshot()["running"] == 1
        assert close_threads == []

        release_inference.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert len(close_threads) == 1
        assert scheduler.snapshot()["running"] == 0
        await scheduler.shutdown()

    run(scenario())


def test_batch_upscale_cancellation_during_creation_closes_created_instance(
    monkeypatch,
):
    import sys
    from types import SimpleNamespace

    import numpy as np

    import app.routers.upscale as upscale
    from app.shared_executor import InferenceScheduler

    async def scenario():
        scheduler = InferenceScheduler(max_pending=0)
        creation_started = threading.Event()
        release_creation = threading.Event()
        close_threads = []

        class FakeUpscaler:
            def __init__(self, **_kwargs):
                creation_started.set()
                release_creation.wait(2)

            def _ensure_model_loaded(self):
                return None

            def close(self):
                close_threads.append(threading.get_ident())

        class Progress:
            async def send_progress(self, *_args, **_kwargs):
                return None

            async def send_complete(self, *_args, **_kwargs):
                return None

        monkeypatch.setitem(
            sys.modules,
            "core.upscaler",
            SimpleNamespace(Upscaler=FakeUpscaler),
        )
        monkeypatch.setattr(upscale, "inference_scheduler", scheduler)
        monkeypatch.setattr(upscale, "progress_manager", Progress())
        monkeypatch.setattr(upscale, "check_image_size", lambda _value: None)
        monkeypatch.setattr(
            upscale,
            "decode_image",
            lambda _value: np.zeros((2, 2, 3), dtype=np.uint8),
        )
        monkeypatch.setattr(upscale, "get", lambda _key, default=None: 256)
        request = upscale.BatchUpscaleRequest(
            images=["image"],
            device="cpu",
            outscale=1,
            task_id="cancel-create-cleanup",
        )

        async def batch():
            async with scheduler.admission("batch"):
                await upscale.upscale_image_batch(request)

        task = asyncio.create_task(batch())
        await asyncio.to_thread(creation_started.wait, 1)
        task.cancel()
        task.cancel()
        release_creation.set()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert len(close_threads) == 1
        assert scheduler.snapshot()["running"] == 0
        await scheduler.shutdown()

    run(scenario())


def test_single_upscale_closes_once_when_inference_fails_after_model_load(
    monkeypatch,
):
    import sys
    from types import SimpleNamespace

    import numpy as np

    import app.routers.upscale as upscale
    from app.shared_executor import InferenceScheduler

    async def scenario():
        scheduler = InferenceScheduler(max_pending=0)
        events = []

        class FakeUpscaler:
            def __init__(self, **_kwargs):
                events.append("created")

            def upscale(self, _image):
                events.append("model-loaded")
                raise RuntimeError("inference failed")

            def close(self):
                events.append("closed")
                raise RuntimeError("cleanup failed")

        class Progress:
            async def send_progress(self, *_args, **_kwargs):
                return None

            async def send_complete(self, *_args, **_kwargs):
                return None

            async def send_error(self, *_args, **_kwargs):
                return None

        monkeypatch.setitem(
            sys.modules,
            "core.upscaler",
            SimpleNamespace(Upscaler=FakeUpscaler),
        )
        monkeypatch.setattr(upscale, "inference_scheduler", scheduler)
        monkeypatch.setattr(upscale, "progress_manager", Progress())
        monkeypatch.setattr(upscale, "check_image_size", lambda _value: None)
        monkeypatch.setattr(
            upscale,
            "decode_image",
            lambda _value: np.zeros((2, 2, 3), dtype=np.uint8),
        )
        monkeypatch.setattr(upscale, "get", lambda _key, default=None: 256)

        request = upscale.UpscaleRequest(
            image="image",
            device="cpu",
            outscale=1,
            task_id="single-cleanup",
        )
        async with scheduler.admission("single"):
            with pytest.raises(RuntimeError, match="inference failed"):
                await upscale.upscale_image(request)
        await scheduler.shutdown()

        assert events == ["created", "model-loaded", "closed"]

    run(scenario())


def test_upscaler_close_releases_model_references_and_cuda_cache(monkeypatch):
    import sys
    from types import SimpleNamespace

    from core.upscaler import Upscaler

    cache_clears = []
    fake_upsampler = SimpleNamespace(model=object())
    upscaler = Upscaler.__new__(Upscaler)
    upscaler.device = "cuda"
    upscaler._upsampler = fake_upsampler
    monkeypatch.setitem(
        sys.modules,
        "torch",
        SimpleNamespace(
            cuda=SimpleNamespace(
                is_available=lambda: True,
                empty_cache=lambda: cache_clears.append(True),
            )
        ),
    )

    upscaler.close()

    assert upscaler._upsampler is None
    assert fake_upsampler.model is None
    assert cache_clears == [True]


def test_cutout_cpu_endpoints_run_while_gpu_queue_is_full_without_changing_it(
    monkeypatch,
):
    import base64
    import cv2
    import numpy as np

    import app.routers.cutout as cutout
    from app.shared_executor import InferenceScheduler

    async def scenario():
        scheduler = InferenceScheduler(max_pending=0)
        started = threading.Event()
        release = threading.Event()
        monkeypatch.setattr(cutout, "inference_scheduler", scheduler)
        monkeypatch.setattr(
            cutout,
            "decode_mask",
            lambda _value: np.full((2, 2), 255, dtype=np.uint8),
        )
        monkeypatch.setattr(
            cutout,
            "decode_image",
            lambda _value: np.zeros((2, 2, 3), dtype=np.uint8),
        )
        monkeypatch.setattr(cutout, "encode_rgba_image", lambda _value: "rgba")
        monkeypatch.setattr(cutout, "encode_mask", lambda _value: "mask")
        monkeypatch.setattr(cutout, "encode_image", lambda _value: "rgb")

        rgba = np.zeros((2, 2, 4), dtype=np.uint8)
        ok, encoded = cv2.imencode(".png", rgba)
        assert ok
        composite_image = base64.b64encode(encoded).decode()

        async def occupy_gpu():
            async with scheduler.admission("gpu"):
                await scheduler.run_blocking(
                    lambda: (started.set(), release.wait(2))[1]
                )

        blocker = asyncio.create_task(occupy_gpu())
        await asyncio.to_thread(started.wait, 1)
        before = scheduler.snapshot()
        refine = asyncio.create_task(cutout.refine_mask(cutout.RefineRequest(
            mask="mask",
            original_image="image",
            task_id="cpu-refine",
        )))
        composite = asyncio.create_task(cutout.composite_cutout(
            cutout.CompositeRequest(
                image=composite_image,
                task_id="cpu-composite",
            )
        ))
        try:
            await asyncio.sleep(0.05)
            assert refine.done()
            assert composite.done()
            assert scheduler.snapshot() == before
        finally:
            release.set()
            await blocker
            await asyncio.gather(refine, composite, return_exceptions=True)
            await scheduler.shutdown()

    run(scenario())
