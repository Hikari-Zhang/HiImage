"""推理任务 ID 与进度隔离契约测试。"""
import asyncio
import inspect

import pytest
import httpx
from fastapi import FastAPI
from fastapi.routing import APIRoute
from pydantic import BaseModel
from pydantic import ValidationError

from app.routers.cutout import CompositeRequest, CutoutRequest, RefineRequest
from app.routers.inpaint import DetectRequest, InpaintRequest, InpaintWithMaskRequest
from app.routers.postprocess import PipelineRequest, PostprocessRequest
from app.routers.synthesis import SynthesisRequest
from app.routers.upscale import BatchUpscaleRequest, UpscaleRequest
from app.websocket.progress import ProgressManager
from app.routers import cutout, inpaint, postprocess, synthesis, upscale


REQUEST_MODELS = (
    DetectRequest,
    InpaintRequest,
    InpaintWithMaskRequest,
    PipelineRequest,
    UpscaleRequest,
    BatchUpscaleRequest,
    CutoutRequest,
    RefineRequest,
    CompositeRequest,
    SynthesisRequest,
    PostprocessRequest,
)

INFERENCE_ROUTERS = (inpaint.router, postprocess.router, upscale.router, cutout.router, synthesis.router)
EXPECTED_ROUTE_MODELS = {
    "/detect": DetectRequest,
    "/inpaint": InpaintRequest,
    "/inpaint/with-mask": InpaintWithMaskRequest,
    "/pipeline/run": PipelineRequest,
    "/upscale": UpscaleRequest,
    "/upscale/batch": BatchUpscaleRequest,
    "/cutout": CutoutRequest,
    "/cutout/refine": RefineRequest,
    "/cutout/composite": CompositeRequest,
    "/synthesis/run": SynthesisRequest,
    "/postprocess/fix": PostprocessRequest,
}


def _declared_request_models() -> set[type[BaseModel]]:
    modules = (inpaint, postprocess, upscale, cutout, synthesis)
    return {
        candidate
        for module in modules
        for _, candidate in inspect.getmembers(module, inspect.isclass)
        if issubclass(candidate, BaseModel)
        and candidate is not BaseModel
        and candidate.__module__ == module.__name__
    }


def _post_route_models() -> dict[str, type[BaseModel]]:
    result = {}
    for router in INFERENCE_ROUTERS:
        for route in router.routes:
            if not isinstance(route, APIRoute) or "POST" not in route.methods:
                continue
            body_models = {
                field.type_
                for field in route.dependant.body_params
                if inspect.isclass(field.type_) and issubclass(field.type_, BaseModel)
            }
            assert len(body_models) == 1, route.path
            result[route.path] = body_models.pop()
    return result


def test_inference_route_and_request_model_matrix_is_complete():
    assert _post_route_models() == EXPECTED_ROUTE_MODELS
    assert _declared_request_models() == set(REQUEST_MODELS)


@pytest.mark.parametrize("model", REQUEST_MODELS)
@pytest.mark.parametrize("task_id", [None, "", "   ", "x" * 129])
def test_inference_request_models_require_valid_task_id(model, task_id):
    field = model.model_fields["task_id"]
    payload = {
        field_name: "x"
        for field_name, info in model.model_fields.items()
        if info.is_required() and field_name != "task_id"
    }
    if task_id is not None:
        payload["task_id"] = task_id

    with pytest.raises(ValidationError):
        model(**payload)


class FakeWebSocket:
    def __init__(self):
        self.messages: list[dict] = []
        self.accepted = False

    async def accept(self):
        self.accepted = True

    async def send_json(self, data: dict):
        self.messages.append(data)


def test_progress_manager_isolates_tasks_and_rejects_empty_ids():
    async def run():
        manager = ProgressManager()
        first = FakeWebSocket()
        second = FakeWebSocket()
        await manager.connect(first, "task-a")
        await manager.connect(second, "task-b")

        await manager.send_progress(25, "only a", task_id="task-a")
        assert first.messages == [{"type": "progress", "percent": 25, "message": "only a"}]
        assert second.messages == []

        with pytest.raises(ValueError, match="task_id"):
            await manager.send_progress(50, "invalid", task_id="")
        assert second.messages == []

    asyncio.run(run())


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/api/detect", {"image": "x"}),
        ("/api/inpaint", {"image": "x", "rois": []}),
        ("/api/inpaint/with-mask", {"image": "x", "mask": "x"}),
        ("/api/pipeline/run", {"image": "x"}),
        ("/api/upscale", {"image": "x"}),
        ("/api/upscale/batch", {"images": []}),
        ("/api/cutout", {"image": "x"}),
        ("/api/cutout/refine", {"mask": "x", "original_image": "x"}),
        ("/api/cutout/composite", {"image": "x"}),
        ("/api/synthesis/run", {"source_image": "x"}),
        (
            "/api/postprocess/fix",
            {"original_image": "x", "inpainted_image": "x", "mask": "x"},
        ),
    ],
)
def test_inference_http_requests_missing_task_id_return_422(path, payload):
    async def request():
        app = FastAPI()
        for router in (inpaint.router, postprocess.router, upscale.router, cutout.router, synthesis.router):
            app.include_router(router, prefix="/api")
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(path, json=payload)

    response = asyncio.run(request())
    assert response.status_code == 422
    assert any(error["loc"][-1] == "task_id" for error in response.json()["detail"])
