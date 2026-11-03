"""删除完成后的 API 和注册表契约。"""
import asyncio
from pathlib import Path
import pytest
from core.model_registry import MODELS, MODE_GROUPS, get_models_for_mode
from core.model_executor import ModelExecutorFactory
from app.routers.inpaint import InpaintRequest, inpaint_with_rois
from app.routers.postprocess import PipelineRequest, run_pipeline
from app.routers.synthesis import SynthesisRequest, run_synthesis


def test_registry_has_no_legacy_provider_or_dangling_defaults():
    ids={model["id"] for model in MODELS}
    assert len(ids)==23
    for model in MODELS:
        assert "iopaint" not in str(model).lower()
    for mode in MODE_GROUPS:
        assert not mode.get("default_model") or mode["default_model"] in ids
    assert {m["id"] for m in get_models_for_mode("watermark_removal")} == {"wm_lama", "flux_fill"}
    assert "IOPaint" not in ModelExecutorFactory.get_supported_providers()


@pytest.mark.parametrize("fn,req",[
    (inpaint_with_rois,InpaintRequest(image="invalid",rois=[],model="wm_migan",task_id="removed-inpaint")),
    (run_pipeline,PipelineRequest(image="invalid",inpaint_model="wm_migan",task_id="removed-pipeline")),
    (run_synthesis,SynthesisRequest(source_image="invalid",mode="outfit_swap",model_id="powerpaint",task_id="removed-synthesis")),
    (run_synthesis,SynthesisRequest(source_image="invalid",mode="auto_segment_edit",model_id="auto_segment_sd15",task_id="removed-auto")),
])
def test_unavailable_before_decode_or_download(fn,req):
    response=asyncio.run(fn(req))
    assert response.status_code==503
    assert b"FEATURE_UNAVAILABLE" in response.body


def test_postprocess_does_not_silently_accept_retired_method():
    from core.background_fixer import fix_background, AVAILABLE_METHODS
    assert "lama_refine" not in AVAILABLE_METHODS
    with pytest.raises(ValueError,match="不支持"):
        fix_background(None,None,None,method="lama_refine",device="cpu")


def test_no_runtime_imports_of_removed_modules():
    root=Path(__file__).resolve().parents[1]
    for directory in [root/"core",root/"app"]:
        for file in directory.rglob("*.py"):
            text=file.read_text(encoding="utf-8")
            assert not __import__("re").search(r"(?:from|import)\s+[^\n]*iopaint", text.lower()), str(file)
            assert "core.model_server" not in text, str(file)


def test_http_unavailable_marks_failed_task_and_keeps_catalogs_working(isolated_task_registry):
    import httpx
    from fastapi import FastAPI
    from app.routers import inpaint, models
    task_registry = isolated_task_registry
    app=FastAPI()
    app.include_router(inpaint.router,prefix="/api")
    app.include_router(models.router,prefix="/api")
    task_id="removed-engine-http"
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test") as client:
            catalog=(await client.get("/api/models/inpaint")).json()
            assert catalog["available"] is True
            assert {m["id"] for group in catalog["groups"] for m in group["models"]} == {"wm_lama", "flux_fill"}
            response=await client.post("/api/inpaint",json={"task_id":task_id,"image":"invalid","rois":[],"model":"wm_migan"})
            assert response.status_code == 503
            assert response.json()["code"] == "FEATURE_UNAVAILABLE"
            defaults=(await client.get("/api/models/defaults")).json()
            assert defaults["watermark_removal"] == "wm_lama"
            assert defaults["cutout"] == "birefnet"
    try:
        asyncio.run(scenario())
        assert task_registry.get(task_id)["status"] == "failed"
    finally:
        task_registry.discard(task_id)


def test_saving_old_settings_removes_only_retired_keys(monkeypatch):
    from app.routers import settings
    saved=[]
    monkeypatch.setattr(settings,"get_all",lambda:{"server.port":51821,"server.low_mem":True,
        "inpaint.iopaint_path":"old", "inpaint.default_device":"cpu","network.hf_endpoint":"https://huggingface.co"})
    monkeypatch.setattr(settings,"save",saved.append)
    asyncio.run(settings.update_settings(settings.SettingsUpdate(default_dilation=12)))
    assert not any(key.startswith("server.") or "iopaint" in key for key in saved[0])
    assert saved[0]["inpaint.default_device"] == "cpu"
    assert saved[0]["inpaint.default_dilation"] == 12
    assert saved[0]["network.hf_endpoint"] == "https://huggingface.co"


def test_requirements_use_one_graph_without_legacy_distribution():
    root=Path(__file__).resolve().parents[2]
    main=(root/"backend/requirements.txt").read_text(encoding="utf-8")
    assert "-r requirements-inference.txt" in main
    for filename in ["requirements.txt","requirements-inference.txt","requirements-locked.txt"]:
        text=(root/"backend"/filename).read_text(encoding="utf-8").lower()
        assert "iopaint" not in text and "gradio" not in text and "--no-deps" not in text


def test_direct_synthesizer_cannot_report_success_for_removed_mode_with_empty_rois():
    import numpy as np
    from core.capabilities import FeatureUnavailable
    from core.synthesizer import Synthesizer
    with pytest.raises(FeatureUnavailable):
        Synthesizer("outfit_swap","powerpaint","cpu").run(np.zeros((4,4,3),np.uint8),rois=[])
