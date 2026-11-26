"""Edit a whole image or a selected region with a text instruction."""
import numpy as np
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, StringConstraints
from typing import Annotated

from app.services import prompt_edit
from app.services.image_codec import check_image_size, encode_image, encode_rgba_image
from app.shared_executor import inference_admission, inference_scheduler
from app.task_id import TaskId
from app.task_route import TaskRoute
from app.websocket.progress import progress_manager
from core.process_runner import ProcessCleanupError

router = APIRouter(route_class=TaskRoute)


class PromptEditRequest(BaseModel):
    image: str
    prompt: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
    model: str | None = None
    mask: str | None = None
    seed: int | None = Field(default=None, strict=True, ge=0, le=2**31 - 1)
    task_id: TaskId


@router.get("/prompt-edit/status")
def get_prompt_edit_status():
    if reason := prompt_edit.platform_unavailable_reason():
        return {"available": False, "reason": reason, "model": None, "default_model": None, "models": []}

    models = []
    for model_id, model in prompt_edit.active_models().items():
        _, _, reason = prompt_edit.runtime(model)
        models.append({
            "id": model_id, "name": model.name, "available": reason is None, "reason": reason,
            "supports_mask": model.supports_mask,
        })
    preferred = next(model for model in models if model["id"] == prompt_edit.DEFAULT_MODEL)
    default = preferred if preferred["available"] else next((model for model in models if model["available"]), preferred)
    return {"available": default["available"], "reason": default["reason"], "model": default["name"],
            "default_model": default["id"], "models": models}


@router.post("/prompt-edit", dependencies=[Depends(inference_admission)])
async def run_prompt_edit(req: PromptEditRequest):
    model_id = req.model if req.model is not None else (get_prompt_edit_status()["default_model"] or prompt_edit.DEFAULT_MODEL)
    model = prompt_edit.active_models().get(model_id)
    if model is None:
        raise HTTPException(422, "不支持该提示词编辑模型。")
    if req.mask is not None and not model.supports_mask:
        raise HTTPException(422, "该模型不支持局部编辑，请选择支持选区的模型。")
    executable, weights, reason = prompt_edit.runtime(model)
    if reason:
        raise HTTPException(503, reason)
    if error := check_image_size(req.image):
        return error
    if req.mask is not None and (error := check_image_size(req.mask)):
        return error
    if "\x00" in req.prompt:
        raise HTTPException(422, "提示词包含无效字符。")

    def process():
        try:
            result = prompt_edit.edit_image(model_id, model, executable, weights, req.image, req.prompt, req.seed, req.mask)
            encode = encode_rgba_image if result.mode == "RGBA" else encode_image
            return {"image": encode(np.asarray(result)), "width": result.width, "height": result.height}
        except ProcessCleanupError:
            inference_scheduler.fail_process_cleanup()
            raise

    await progress_manager.send_progress(10, "正在加载模型并编辑图片", task_id=req.task_id)
    try:
        result = await inference_scheduler.run_blocking(process)
    except HTTPException as error:
        await progress_manager.send_error(str(error.detail), task_id=req.task_id)
        raise
    await progress_manager.send_complete("图片编辑完成", task_id=req.task_id)
    return result
