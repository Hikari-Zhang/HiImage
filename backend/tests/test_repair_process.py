import asyncio
import json
import sys
import threading
import time

import numpy as np
import pytest

from app import shared_executor
from app.services import repair_process
from core.model_resources import ModelResourceGuard, ModelResourcesBusy
from core.process_runner import ProcessCleanupError


IMAGE = np.zeros((4, 6, 3), np.uint8)


def command(monkeypatch, code):
    directories = []

    def build(directory):
        directories.append(directory)
        return [sys.executable, "-u", "-c", code, str(directory)]

    monkeypatch.setattr(repair_process, "_worker_command", build)
    return directories


def test_worker_transfers_image_mask_options_and_progress(monkeypatch):
    directories = command(monkeypatch, """
from pathlib import Path
import sys
from app.services import image_processing
from app.repair_worker import main

def repair(image, *, mask, model_id, progress_callback):
    assert mask.shape == image.shape[:2]
    assert model_id == "example"
    progress_callback(35, "worker ready")
    return image + 12

image_processing.inpaint_image = repair
main(Path(sys.argv[1]))
""")
    updates = []
    result = repair_process._run_process(
        "inpaint", IMAGE, IMAGE[:, :, 0], None, {"model_id": "example"},
        20, threading.Event(), lambda *args: updates.append(args),
    )
    assert np.array_equal(result, IMAGE + 12)
    assert updates == [(35, "worker ready")]
    assert all(not path.exists() for path in directories)


@pytest.mark.parametrize("noisy", [False, True])
def test_hard_deadline_handles_silence_and_continuous_output(monkeypatch, noisy):
    directories = command(monkeypatch, "import time\nwhile True:\n "
                          + ("print('working')\n " if noisy else "") + "time.sleep(.01)")
    started = time.monotonic()
    with pytest.raises(repair_process.RepairTimeout) as error:
        repair_process._run_process("inpaint", IMAGE, None, None, {}, 0.5, threading.Event(), None)
    assert time.monotonic() - started < 6
    response = repair_process.error_response(error.value)
    assert response.status_code == 504
    assert json.loads(response.body)["code"] == "INFERENCE_TIMEOUT"
    assert all(not path.exists() for path in directories)


@pytest.mark.parametrize("disconnect", [False, True])
def test_cancellation_waits_for_worker_and_unblocks_next_task(monkeypatch, tmp_path, disconnect):
    started = tmp_path / "started"
    directories = command(monkeypatch, f"""
from pathlib import Path
import time
Path({str(started)!r}).touch()
time.sleep(60)
""")

    class Request:
        async def is_disconnected(self):
            return disconnect and started.exists()

    async def scenario():
        scheduler = shared_executor.InferenceScheduler()

        async def first():
            async with scheduler.admission("first"):
                await repair_process.run_repair(
                    scheduler, "inpaint", IMAGE, options={"model_id": "flux_fill"},
                    request=Request() if disconnect else None,
                )

        task = asyncio.create_task(first())
        try:
            async def wait_for_start():
                while not started.exists():
                    await asyncio.sleep(0.02)
            await asyncio.wait_for(wait_for_start(), timeout=10)
            if not disconnect:
                task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=10)
            assert all(not path.exists() for path in directories)
            async with scheduler.admission("next"):
                assert await scheduler.run_blocking(lambda: 42) == 42
        finally:
            await scheduler.shutdown()

    asyncio.run(scenario())


def test_failed_cleanup_blocks_inference_and_weight_deletion(monkeypatch):
    guard = ModelResourceGuard()
    monkeypatch.setattr(shared_executor, "model_resources", guard)

    def fail(*_args):
        raise ProcessCleanupError("未能确认进程退出")

    monkeypatch.setattr(repair_process, "_run_process", fail)

    async def scenario():
        scheduler = shared_executor.InferenceScheduler()
        try:
            with pytest.raises(ProcessCleanupError) as error:
                async with scheduler.admission("repair"):
                    await repair_process.run_repair(scheduler, "inpaint", IMAGE, options={"model_id": "flux_fill"})
            assert repair_process.error_response(error.value).status_code == 503
            with pytest.raises(ProcessCleanupError):
                async with scheduler.admission("next"):
                    pytest.fail("回收失败后不能继续推理")
            with pytest.raises(ModelResourcesBusy):
                with guard.deleting():
                    pytest.fail("回收失败后不能删除权重")
        finally:
            await scheduler.shutdown()

    asyncio.run(scenario())


def test_worker_oom_maps_to_recoverable_device_error(monkeypatch):
    directories = command(monkeypatch, """
import sys
from pathlib import Path
(Path(sys.argv[1]) / "out_of_memory").touch()
raise SystemExit(1)
""")
    with pytest.raises(repair_process.RepairOutOfMemory) as error:
        repair_process._run_process("inpaint", IMAGE, None, None, {}, 10, threading.Event(), None)
    response = repair_process.error_response(error.value)
    assert response.status_code == 503
    assert json.loads(response.body)["code"] == "INFERENCE_OUT_OF_MEMORY"
    assert all(not path.exists() for path in directories)


def test_mask_route_reports_timeout_and_keeps_scheduler_usable(monkeypatch, isolated_task_registry):
    import httpx
    from fastapi import FastAPI
    from app.routers import inpaint

    command(monkeypatch, "import time; time.sleep(60)")
    original = repair_process._run_process

    def short_deadline(operation, image, mask, rois, options, _timeout, stop, report):
        return original(operation, image, mask, rois, options, 0.5, stop, report)

    async def ready(*_args):
        return True, ""

    monkeypatch.setattr(repair_process, "_run_process", short_deadline)
    monkeypatch.setattr(inpaint, "unavailable_response", lambda *_: None)
    monkeypatch.setattr(inpaint, "ensure_model_ready_with_progress", ready)
    monkeypatch.setattr(inpaint, "decode_image", lambda _: IMAGE)
    monkeypatch.setattr(inpaint, "decode_mask", lambda _: IMAGE[:, :, 0])

    async def scenario():
        scheduler = shared_executor.InferenceScheduler()
        monkeypatch.setattr(inpaint, "inference_scheduler", scheduler)
        monkeypatch.setattr(shared_executor, "inference_scheduler", scheduler)
        app = FastAPI()
        app.include_router(inpaint.router)
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post("/inpaint/with-mask", json={
                    "image": "test", "mask": "test", "model": "wm_lama",
                    "device": "cpu", "task_id": "repair-timeout",
                })
            assert response.status_code == 504
            assert response.json()["code"] == "INFERENCE_TIMEOUT"
            assert isolated_task_registry.get("repair-timeout")["status"] == "failed"
            async with scheduler.admission("next"):
                assert await scheduler.run_blocking(lambda: 42) == 42
        finally:
            await scheduler.shutdown()

    asyncio.run(scenario())
