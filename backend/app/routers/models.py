"""
模型完整性健康检查端点

GET /api/models/health              → 检测所有注册模型的完整性
GET /api/models/health/{model_id}   → 检测单个模型的完整性
GET /api/models/list                → 返回所有注册模型的元数据（含当前状态）
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from core.model_checker import ModelChecker
from core.model_registry import MODELS, MODE_GROUPS

router = APIRouter(prefix="/models", tags=["models"])


def _result_to_dict(r) -> dict:
    """将 ModelCheckResult 转为可序列化字典。"""
    return {
        "id":               r.model_id,
        "name":             r.name,
        "provider":         r.provider,
        "status":           r.status,
        "message":          r.message,
        "file_path":        r.file_path,
        "size_mb":          round(r.size_bytes / (1024 * 1024), 1) if r.size_bytes else None,
        "expected_size_mb": r.expected_size_mb,
    }


@router.get("/health")
async def models_health():
    """
    检测所有注册模型的文件完整性。

    返回格式：
    ```json
    {
      "models": [
        {"id": "birefnet", "status": "ok", "message": "100 MB", ...},
        {"id": "flux_fill", "status": "missing", "message": "未在 HF 缓存中找到 ...", ...}
      ],
      "summary": {"ok": 18, "missing": 6, "corrupted": 0, "unknown": 2, "total": 26}
    }
    ```
    """
    checker = ModelChecker()
    results = checker.check_all()
    return {
        "models": [_result_to_dict(r) for r in results],
        "summary": {
            "ok":       sum(1 for r in results if r.status == "ok"),
            "missing":  sum(1 for r in results if r.status in ("missing", "partial")),
            "corrupted": sum(1 for r in results if r.status == "corrupted"),
            "unknown":  sum(1 for r in results if r.status == "unknown"),
            "total":    len(results),
        },
    }


@router.get("/health/{model_id}")
async def model_health(model_id: str):
    """
    检测单个模型的文件完整性。

    返回格式：
    ```json
    {"id": "birefnet", "status": "ok", "message": "100 MB", ...}
    ```
    """
    checker = ModelChecker()
    result = checker.check_model(model_id)
    if result.status == "unknown" and result.message.startswith("模型 ID"):
        raise HTTPException(status_code=404, detail=f"未知模型 ID: {model_id!r}")
    return _result_to_dict(result)


@router.get("/list")
async def models_list():
    """
    返回所有注册模型的元数据（来自 models.yaml），
    附带实时完整性状态。
    """
    checker = ModelChecker()
    check_map = {r.model_id: r for r in checker.check_all()}

    result = []
    for m in MODELS:
        mid = m["id"]
        r = check_map.get(mid)
        result.append({
            **{k: v for k, v in m.items() if k not in ("supported_params",)},
            "status":  r.status if r else "unknown",
            "message": r.message if r else "",
        })

    return {
        "models": result,
        "mode_groups": MODE_GROUPS,
    }
