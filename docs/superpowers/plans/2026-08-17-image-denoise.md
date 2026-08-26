# 图片降噪（单张 + 批量）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增独立「图片降噪」页与 `/api/denoise`、`/api/denoise/batch`，复用已有 Real-ESRGAN 清晰化权重做同分辨率去噪，支持单张与批量。

**Architecture:** `models.yaml` 增加 `denoise` 模式与 `denoise-general` / `denoise-anime`（权重复用 `realesr-general-x4v3.pth` 与 `RealESRGAN_x4plus_anime_6B.pth`）。`Upscaler` 改为按 `get_model(id)` 解析任意 `provider: realesrgan` 模型。降噪路由不传 `outscale`，YAML 默认 1；批量在循环外加载一次模型。前端 `/denoise` 对齐超分页交互，但无倍率选择，批量走 `denoiseBatch` 以保证模型只加载一次。

**Tech Stack:** FastAPI、Real-ESRGAN (`core/upscaler.py`)、Electron + React + Zustand、`models.yaml` 注册表。

**Spec:** `docs/superpowers/specs/2026-08-17-image-denoise-design.md`

## Global Constraints

- Restormer / `image_restoration` / NAFNet：不修复、不启用、不新增。
- `clarity-general` / `clarity-anime` 的 `tags` 保持 `[upscale]`，超分页「清晰化增强」不变。
- 降噪请求体不含 `outscale`；输出倍率锁为 YAML `outscale: 1`。
- 不新增权重文件；`denoise-general` 与 `realesr-general-x4v3` / `clarity-general` 同一 `weight_filename`。
- 不抽超分/降噪共用 Enhance 页面；不新增降噪强度滑块。
- 批量输出文件名：`{原主文件名}_denoised.png`。
- tile 复用设置项 `upscaleTile`，不新增独立 denoise tile 配置。
- 降噪加入共享 `useWorkspaceStore`（与超分相同）；抠图仍独立。
- UI 文案中文；描述必须写清「保持原始分辨率」。
- 命令前缀 `rtk`（见仓库 `CLAUDE.md`）。

## File Structure

新建：

- `backend/tests/test_denoise_registry.py` — 注册表断言
- `backend/tests/test_upscaler_lookup.py` — Upscaler 按 ID 解析
- `backend/tests/test_denoise_router_helpers.py` — 模型列表与尺寸对齐
- `backend/app/routers/denoise.py` — GET 模型列表 + 单张/批量推理
- `frontend/src/renderer/pages/Denoise.tsx` — 降噪页

修改：

- `backend/core/models.yaml` — 两条模型 + `denoise` mode_group
- `backend/core/upscaler.py` — `get_model` 查找，不再只用 upscale 模式字典做准入
- `backend/app/main.py` — 注册 denoise router
- `frontend/src/renderer/stores/useSettingsStore.ts` — `denoiseModel`
- `frontend/src/renderer/stores/useModelStore.ts` — `denoiseGroups`
- `frontend/src/renderer/hooks/useBackendAPI.ts` — `denoise` / `denoiseBatch`
- `frontend/src/renderer/App.tsx` / `MainLayout.tsx` / `Sidebar.tsx` — 路由与入口
- `docs/PRD.md` / `docs/SYSTEM_ARCHITECTURE.md` / `README.md`

---

### Task 1: 注册 denoise 模型与模式

**Files:**
- Create: `backend/tests/test_denoise_registry.py`
- Modify: `backend/core/models.yaml`（清晰化增强条目之后、Restormer 注释块之前插入模型；`mode_groups` 中 `upscale` 之后插入 `denoise`）

**Interfaces:**
- Consumes: `get_models_for_mode(mode_id: str) -> list[dict]`、`get_default_model(mode_id: str) -> str`、`get_model(model_id: str) -> dict`
- Produces: 模型 ID `denoise-general`、`denoise-anime`；模式 ID `denoise`；`get_default_model("denoise") == "denoise-general"`；两条均为 `provider: "realesrgan"`、`outscale: 1`、`supports_custom_outscale: false`；`denoise-general` 的 `weight_filename` 与 `realesr-general-x4v3` 相同

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_denoise_registry.py`：

```python
"""denoise 模式必须出现在 models.yaml 注册表中。"""


def test_denoise_mode_has_two_models_in_order():
    from core.model_registry import get_models_for_mode

    models = get_models_for_mode("denoise")
    ids = [m["id"] for m in models]
    assert ids == ["denoise-general", "denoise-anime"]


def test_denoise_default_model():
    from core.model_registry import get_default_model

    assert get_default_model("denoise") == "denoise-general"


def test_denoise_models_are_realesrgan_outscale_1():
    from core.model_registry import get_model

    for model_id in ("denoise-general", "denoise-anime"):
        cfg = get_model(model_id)
        assert cfg["provider"] == "realesrgan"
        assert cfg.get("outscale") == 1
        assert cfg.get("supports_custom_outscale") is False
        assert "denoise" in cfg.get("tags", [])
        assert "upscale" not in cfg.get("tags", [])


def test_denoise_general_reuses_general_v3_weight():
    from core.model_registry import get_model

    denoise = get_model("denoise-general")
    upscale = get_model("realesr-general-x4v3")
    assert denoise["weight_filename"] == upscale["weight_filename"] == "realesr-general-x4v3.pth"


def test_denoise_anime_reuses_anime_6b_weight():
    from core.model_registry import get_model

    denoise = get_model("denoise-anime")
    upscale = get_model("RealESRGAN_x4plus_anime_6B")
    assert denoise["weight_filename"] == upscale["weight_filename"] == "RealESRGAN_x4plus_anime_6B.pth"


def test_clarity_models_remain_upscale_only():
    from core.model_registry import get_model

    for model_id in ("clarity-general", "clarity-anime"):
        cfg = get_model(model_id)
        assert cfg.get("tags") == ["upscale"]
```

- [ ] **Step 2: 跑测试确认失败**

Run:

```bash
cd /Users/hikari/Documents/Git/HiImage/backend && rtk pytest tests/test_denoise_registry.py -v
```

Expected: FAIL，`KeyError` 或 `get_models_for_mode("denoise")` 返回空列表 / `get_default_model` 抛错。

- [ ] **Step 3: 写入 YAML**

在 `backend/core/models.yaml` 中，`clarity-anime` 条目结束之后、`# ── 图像复原（image_restoration）` 注释之前插入：

```yaml
  # ── 图片降噪（denoise）──────────────────────────────────────────────────
  # outscale: 1 — 与清晰化增强相同：网络先 4x 再缩回，保持分辨率
  # 权重复用已有 Real-ESRGAN 文件，不新增下载

  - id: denoise-general
    name: 通用降噪（推荐）
    provider: realesrgan
    tags: [denoise]
    description: 保持原始分辨率，去除照片/截图的压缩噪声与颗粒｜复用 realesr-general-x4v3 权重（~5 MB）
    recommended_for: 图片降噪（照片/截图，不放大）
    requires_reference: false
    size_mb: 5
    badge: 推荐
    scale: 4
    outscale: 1
    weight_filename: realesr-general-x4v3.pth
    local_path: models/realesrgan/realesr-general-x4v3.pth
    arch: SRVGGNetCompact
    num_conv: 32
    display_group: 通用降噪
    download_url: https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesr-general-x4v3.pth
    supported_params: {}
    supports_custom_outscale: false
    features: [denoise]

  - id: denoise-anime
    name: 动漫/插画降噪
    provider: realesrgan
    tags: [denoise]
    description: 保持原始分辨率，针对动漫/插画去噪，线条更干净｜复用 RealESRGAN_x4plus_anime_6B 权重（~18 MB）
    recommended_for: 图片降噪（动漫/插画，不放大）
    requires_reference: false
    size_mb: 18
    badge: 动漫
    scale: 4
    outscale: 1
    weight_filename: RealESRGAN_x4plus_anime_6B.pth
    local_path: models/realesrgan/RealESRGAN_x4plus_anime_6B.pth
    arch: RRDBNet
    num_block: 6
    display_group: 动漫/插画
    download_url: https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.2.4/RealESRGAN_x4plus_anime_6B.pth
    supported_params: {}
    supports_custom_outscale: false
    features: [denoise]
```

在 `mode_groups` 里 `upscale` 块之后插入（不要取消注释 Restormer 的 `image_restoration`）：

```yaml
  - id: denoise
    name: 图片降噪
    icon: droplet
    icon_name: Droplets
    description: 保持分辨率，去除压缩噪声与颗粒，支持单张与批量
    default_model: denoise-general
    needs_reference: false
    needs_roi: false
    needs_prompt: false
```

`clarity-general` / `clarity-anime` 一行都不要改。`model_registry` 在 import 时读 YAML；测试进程会重新 import，无需改 Python。

- [ ] **Step 4: 跑测试确认通过**

Run:

```bash
cd /Users/hikari/Documents/Git/HiImage/backend && rtk pytest tests/test_denoise_registry.py -v
```

Expected: PASS（6 passed）

- [ ] **Step 5: Commit**

```bash
rtk git add backend/tests/test_denoise_registry.py backend/core/models.yaml
rtk git commit -m "$(cat <<'EOF'
feat: register denoise models in models.yaml

EOF
)"
```

---

### Task 2: Upscaler 按注册表 ID 解析 realesrgan 模型

**Files:**
- Create: `backend/tests/test_upscaler_lookup.py`
- Modify: `backend/core/upscaler.py`（`_get_weight_path`、`_download_weight`、`Upscaler.__init__`、`_build_upsampler`）

**Interfaces:**
- Consumes: Task 1 的 `denoise-general` / `denoise-anime` YAML；`get_model(model_id: str) -> dict`
- Produces: `Upscaler(model_name: str, device: str = "cpu", outscale: int | None = None, tile: int = 0)` 对任意 `provider == "realesrgan"` 的 ID 有效；未传 `outscale` 时使用 YAML `outscale`（降噪为 1）；`provider` 不是 realesrgan 时 `ValueError`

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_upscaler_lookup.py`：

```python
"""Upscaler 必须能解析 denoise-*（realesrgan），并拒绝非 realesrgan ID。"""
import pytest


def test_upscaler_accepts_denoise_general_outscale_1():
    from core.upscaler import Upscaler

    u = Upscaler(model_name="denoise-general", device="cpu")
    assert u.model_name == "denoise-general"
    assert u.scale == 4
    assert u.outscale == 1


def test_upscaler_accepts_denoise_anime():
    from core.upscaler import Upscaler

    u = Upscaler(model_name="denoise-anime", device="cpu")
    assert u.outscale == 1
    assert u.scale == 4


def test_upscaler_explicit_outscale_overrides_yaml():
    from core.upscaler import Upscaler

    u = Upscaler(model_name="denoise-general", device="cpu", outscale=4)
    assert u.outscale == 4


def test_upscaler_still_accepts_x4plus():
    from core.upscaler import Upscaler

    u = Upscaler(model_name="RealESRGAN_x4plus", device="cpu")
    assert u.scale == 4
    assert u.outscale == 4


def test_upscaler_rejects_non_realesrgan():
    from core.upscaler import Upscaler

    with pytest.raises(ValueError, match="realesrgan"):
        Upscaler(model_name="wm_lama", device="cpu")
```

- [ ] **Step 2: 跑测试确认失败**

Run:

```bash
cd /Users/hikari/Documents/Git/HiImage/backend && rtk pytest tests/test_upscaler_lookup.py -v
```

Expected: FAIL，`ValueError: 不支持的模型: denoise-general`（当前用 `AVAILABLE_UPSCALE_MODELS` 校验）。

- [ ] **Step 3: 改 Upscaler 查找**

在 `backend/core/upscaler.py` 的 `_build_upscale_structures` 之后、`class Upscaler` 之前，新增：

```python
def _get_realesrgan_config(model_name: str) -> dict:
    """按模型 ID 读取 YAML；仅允许 provider=realesrgan。"""
    from core.model_registry import get_model

    try:
        cfg = get_model(model_name)
    except KeyError as e:
        raise ValueError(f"未知模型: {model_name!r}") from e
    if cfg.get("provider") != "realesrgan":
        raise ValueError(
            f"不支持的模型: {model_name}（provider={cfg.get('provider')!r}，需要 realesrgan）"
        )
    return cfg
```

把 `_get_weight_path` 整段替换为：

```python
def _get_weight_path(model_name: str) -> str:
    """获取模型权重路径（MODELS_CACHE_DIR / realesrgan / weight_filename）。"""
    from .paths import resolve_model_cache_path

    cfg = _get_realesrgan_config(model_name)
    return str(resolve_model_cache_path({
        "provider": "realesrgan",
        "weight_filename": cfg.get("weight_filename", f"{model_name}.pth"),
    }))
```

把 `_download_weight` 里 `url = _MODEL_URL[model_name]` 换成：

```python
    cfg = _get_realesrgan_config(model_name)
    url = cfg.get("download_url", "")
    if not url:
        raise RuntimeError(f"模型 {model_name} 未配置 download_url")
    dest = _get_weight_path(model_name)
```

把 `Upscaler.__init__` 整段替换为：

```python
    def __init__(self, model_name: str = 'RealESRGAN_x4plus', device: str = 'cpu', outscale: int | None = None, tile: int = 0):
        cfg = _get_realesrgan_config(model_name)
        self.model_name = model_name
        self.device = device
        self._cfg = cfg
        self.scale = cfg.get("scale", 4)
        if outscale is not None:
            self.outscale = outscale
        else:
            self.outscale = cfg.get("outscale", self.scale)
        self._upsampler = None
        self._current_tile = 0
        self._tile = tile
```

把 `_build_upsampler` 里这三行：

```python
        arch      = _MODEL_ARCH.get(self.model_name, "RRDBNet")
        num_block = _MODEL_NUM_BLOCK.get(self.model_name, 23)
        num_conv  = _MODEL_NUM_CONV.get(self.model_name, 32)
```

换成：

```python
        arch      = self._cfg.get("arch", "RRDBNet")
        num_block = self._cfg.get("num_block", 23)
        num_conv  = self._cfg.get("num_conv", 32)
```

保留模块级 `UPSCALE_MODEL_LIST` / `AVAILABLE_UPSCALE_MODELS`（超分页列表仍用 `get_models_for_mode("upscale")` 构建）。不要在 `__init__` 里再用 `AVAILABLE_UPSCALE_MODELS` 做准入。

- [ ] **Step 4: 跑测试确认通过**

Run:

```bash
cd /Users/hikari/Documents/Git/HiImage/backend && rtk pytest tests/test_upscaler_lookup.py tests/test_denoise_registry.py -v
```

Expected: PASS（全部通过）。不要调用 `upscale()`，本任务不加载权重。

- [ ] **Step 5: Commit**

```bash
rtk git add backend/tests/test_upscaler_lookup.py backend/core/upscaler.py
rtk git commit -m "$(cat <<'EOF'
feat: resolve Real-ESRGAN models by registry id

EOF
)"
```

---

### Task 3: 降噪 REST API

**Files:**
- Create: `backend/tests/test_denoise_router_helpers.py`
- Create: `backend/app/routers/denoise.py`
- Modify: `backend/app/main.py`（import 与 `include_router`）

**Interfaces:**
- Consumes: `Upscaler(model_name, device, tile)`（不传 outscale）；`get_models_for_mode("denoise")`；`decode_image` / `encode_image`
- Produces:
  - `list_denoise_models() -> dict`：`{"groups": [{"label": str, "models": [{"id","name","description","badge"}]}]}`
  - `_match_input_size(result: np.ndarray, src: np.ndarray) -> np.ndarray`
  - `_require_denoise_model(model_id: str) -> dict`：非法 ID 抛 `HTTPException 400`
  - `GET /api/models/denoise`
  - `POST /api/denoise` 请求 `DenoiseRequest(image: str, model: str = "denoise-general", device: str = "mps", tile: int | None = None, output_path: str | None = None)`
  - `POST /api/denoise/batch` 请求 `BatchDenoiseRequest(images: list[str], model: str = "denoise-general", device: str = "mps", tile: int | None = None, output_dir: str | None = None, is_file_path: bool = False)`
  - 批量成功文件：`{stem}_denoised.png`

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_denoise_router_helpers.py`：

```python
import numpy as np
import pytest
from fastapi import HTTPException


def test_list_denoise_models_groups():
    from app.routers.denoise import list_denoise_models

    data = list_denoise_models()
    labels = [g["label"] for g in data["groups"]]
    assert labels == ["通用降噪", "动漫/插画"]
    ids = [m["id"] for g in data["groups"] for m in g["models"]]
    assert ids == ["denoise-general", "denoise-anime"]
    assert "scale" not in data["groups"][0]["models"][0]
    assert "outscale" not in data["groups"][0]["models"][0]


def test_require_denoise_model_ok():
    from app.routers.denoise import _require_denoise_model

    cfg = _require_denoise_model("denoise-general")
    assert cfg["id"] == "denoise-general"


def test_require_denoise_model_rejects_upscale_id():
    from app.routers.denoise import _require_denoise_model

    with pytest.raises(HTTPException) as ei:
        _require_denoise_model("RealESRGAN_x4plus")
    assert ei.value.status_code == 400


def test_require_denoise_model_unknown():
    from app.routers.denoise import _require_denoise_model

    with pytest.raises(HTTPException) as ei:
        _require_denoise_model("not-a-model")
    assert ei.value.status_code == 400


def test_match_input_size_resizes_mismatch():
    from app.routers.denoise import _match_input_size

    src = np.zeros((10, 20, 3), dtype=np.uint8)
    out = np.zeros((11, 21, 3), dtype=np.uint8)
    got = _match_input_size(out, src)
    assert got.shape[:2] == (10, 20)


def test_match_input_size_keeps_same():
    from app.routers.denoise import _match_input_size

    src = np.zeros((8, 8, 3), dtype=np.uint8)
    got = _match_input_size(src.copy(), src)
    assert got.shape == src.shape
```

- [ ] **Step 2: 跑测试确认失败**

Run:

```bash
cd /Users/hikari/Documents/Git/HiImage/backend && rtk pytest tests/test_denoise_router_helpers.py -v
```

Expected: FAIL，`ModuleNotFoundError: No module named 'app.routers.denoise'`。

- [ ] **Step 3: 实现路由**

创建 `backend/app/routers/denoise.py`：

```python
"""图片降噪路由：同分辨率 Real-ESRGAN（outscale 由 YAML 锁定为 1）。"""
import asyncio
import os
from concurrent.futures import ThreadPoolExecutor

import cv2
import numpy as np
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.routers.inpaint import decode_image, encode_image
from app.websocket.progress import progress_manager
from app.logging_manager import log_manager

router = APIRouter()
executor = ThreadPoolExecutor(max_workers=1)


def list_denoise_models() -> dict:
    from core.model_registry import get_models_for_mode

    models = get_models_for_mode("denoise")
    groups: dict[str, list] = {}
    for m in models:
        label = m.get("display_group", "其他")
        if label not in groups:
            groups[label] = []
        groups[label].append({
            "id": m["id"],
            "name": m.get("name", m["id"]),
            "description": m.get("description", ""),
            "badge": m.get("badge", ""),
        })
    return {
        "groups": [
            {"label": label, "models": model_list}
            for label, model_list in groups.items()
        ]
    }


def _require_denoise_model(model_id: str) -> dict:
    from core.model_registry import get_model, get_models_for_mode

    try:
        cfg = get_model(model_id)
    except KeyError:
        allowed = [m["id"] for m in get_models_for_mode("denoise")]
        raise HTTPException(
            status_code=400,
            detail=f"未知降噪模型: {model_id!r}，可选: {allowed}",
        )
    if cfg.get("provider") != "realesrgan" or "denoise" not in cfg.get("tags", []):
        raise HTTPException(
            status_code=400,
            detail=f"模型 {model_id} 不属于 denoise 模式",
        )
    return cfg


def _match_input_size(result: np.ndarray, src: np.ndarray) -> np.ndarray:
    h, w = src.shape[:2]
    if result.shape[0] != h or result.shape[1] != w:
        result = cv2.resize(result, (w, h), interpolation=cv2.INTER_AREA)
    return result


@router.get("/models/denoise")
async def get_denoise_models():
    return list_denoise_models()


class DenoiseRequest(BaseModel):
    image: str
    model: str = "denoise-general"
    device: str = "mps"
    tile: int | None = None
    output_path: str | None = None


class BatchDenoiseRequest(BaseModel):
    images: list[str]
    model: str = "denoise-general"
    device: str = "mps"
    tile: int | None = None
    output_dir: str | None = None
    is_file_path: bool = False


def _run_denoise(image: np.ndarray, model: str, device: str, tile: int) -> np.ndarray:
    from core.upscaler import Upscaler

    upscaler = Upscaler(model_name=model, device=device, tile=tile)
    result = upscaler.upscale(image)
    del upscaler
    if device == "cuda":
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    return _match_input_size(result, image)


@router.post("/denoise/batch")
async def denoise_image_batch(req: BatchDenoiseRequest):
    from core.upscaler import Upscaler

    _require_denoise_model(req.model)
    total = len(req.images)
    if total == 0:
        return {"results": [], "summary": {"total": 0, "success": 0, "failed": 0}}

    log_manager.info(
        f"开始批量降噪: 共 {total} 张, model={req.model}, device={req.device}",
        source="denoise_batch",
    )
    if req.output_dir:
        os.makedirs(req.output_dir, exist_ok=True)

    tile = req.tile if req.tile is not None else 0
    shared = Upscaler(model_name=req.model, device=req.device, tile=tile)
    try:
        shared._ensure_model_loaded()
    except Exception as e:
        log_manager.error(f"批量降噪模型加载失败: {e}", source="denoise_batch")
        raise RuntimeError(f"模型加载失败: {e}") from e

    results = []
    success_count = 0
    failed_count = 0

    for idx, img_data in enumerate(req.images):
        current = idx + 1
        file_name = f"image_{current}.png"
        try:
            if req.is_file_path:
                file_path = img_data
                file_name = os.path.basename(file_path)
                img_bgr = cv2.imread(file_path)
                if img_bgr is None:
                    raise ValueError(f"无法读取图片: {file_path}")
                image = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
            else:
                image = decode_image(img_data)
        except Exception as e:
            failed_count += 1
            results.append({
                "index": idx,
                "status": "error",
                "error": f"读取图片失败: {str(e)}",
                "fileName": file_name,
            })
            continue

        h_in, w_in = image.shape[:2]
        msg = f"正在处理第 {current}/{total} 张: {file_name} ({w_in}x{h_in})"
        progress = int((idx / total) * 100)
        await progress_manager.send_progress(progress, msg)
        log_manager.info(msg, source="denoise_batch")

        def _process(upscaler_instance, img):
            out = upscaler_instance.upscale(img)
            return _match_input_size(out, img)

        loop = asyncio.get_event_loop()
        try:
            result_rgb = await loop.run_in_executor(executor, _process, shared, image)
            del image
            h_out, w_out = result_rgb.shape[:2]
            if req.output_dir:
                base_name = os.path.splitext(file_name)[0]
                out_path = os.path.join(req.output_dir, f"{base_name}_denoised.png")
                result_bgr = cv2.cvtColor(result_rgb, cv2.COLOR_RGB2BGR)
                cv2.imwrite(out_path, result_bgr)
                del result_rgb
                results.append({
                    "index": idx,
                    "status": "success",
                    "outputPath": out_path,
                    "width": w_out,
                    "height": h_out,
                    "fileName": file_name,
                })
            else:
                results.append({
                    "index": idx,
                    "status": "success",
                    "image": encode_image(result_rgb),
                    "width": w_out,
                    "height": h_out,
                    "fileName": file_name,
                })
                del result_rgb
            success_count += 1
        except Exception as e:
            failed_count += 1
            results.append({
                "index": idx,
                "status": "error",
                "error": str(e),
                "fileName": file_name,
            })
            if "image" in locals():
                del image
            log_manager.error(
                f"批量降噪失败（第 {current}/{total} 张）: {str(e)}",
                source="denoise_batch",
            )

    await progress_manager.send_progress(
        100, f"批量处理完成：成功 {success_count} 张，失败 {failed_count} 张"
    )
    log_manager.info(f"批量降噪完成: 成功 {success_count}/{total}", source="denoise_batch")
    await progress_manager.send_complete(f"批量处理完成：{success_count} 成功，{failed_count} 失败")

    del shared
    if req.device == "cuda":
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    return {
        "results": results,
        "summary": {"total": total, "success": success_count, "failed": failed_count},
    }


@router.post("/denoise")
async def denoise_image(req: DenoiseRequest):
    _require_denoise_model(req.model)
    image = decode_image(req.image)
    h_in, w_in = image.shape[:2]
    log_manager.info(
        f"开始降噪: model={req.model}, device={req.device}, input={w_in}x{h_in}",
        source="denoise",
    )
    await progress_manager.send_progress(10, "正在加载降噪模型...")
    tile = req.tile if req.tile is not None else 0

    def _process(img):
        return _run_denoise(img, req.model, req.device, tile)

    loop = asyncio.get_event_loop()
    await progress_manager.send_progress(30, "正在降噪...")
    result = None
    try:
        result = await loop.run_in_executor(executor, _process, image)
        del image
        await progress_manager.send_complete("降噪处理完成")
        h, w = result.shape[:2]
        log_manager.info(f"降噪完成: output={w}x{h}", source="denoise")
        if req.output_path:
            out_dir = os.path.dirname(req.output_path) or "."
            os.makedirs(out_dir, exist_ok=True)
            result_bgr = cv2.cvtColor(result, cv2.COLOR_RGB2BGR)
            cv2.imwrite(req.output_path, result_bgr)
            del result
            if req.device == "cuda":
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            return {"outputPath": req.output_path, "width": w, "height": h}
        encoded = encode_image(result)
        del result
        if req.device == "cuda":
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        return {"image": encoded, "width": w, "height": h}
    except Exception as e:
        if image is not None:
            del image
        if result is not None:
            del result
        if req.device == "cuda":
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        await progress_manager.send_error(str(e))
        log_manager.error(f"降噪失败: {str(e)}", source="denoise")
        raise
```

在 `backend/app/main.py` 把：

```python
from app.routers import system, inpaint, upscale, settings, logs, postprocess, synthesis, cutout, models as models_router_module
```

改为：

```python
from app.routers import system, inpaint, upscale, settings, logs, postprocess, synthesis, cutout, denoise, models as models_router_module
```

在 `app.include_router(cutout.router, ...)` 之后增加：

```python
app.include_router(denoise.router, prefix="/api", tags=["denoise"])
```

- [ ] **Step 4: 跑测试确认通过**

Run:

```bash
cd /Users/hikari/Documents/Git/HiImage/backend && rtk pytest tests/test_denoise_router_helpers.py tests/test_denoise_registry.py tests/test_upscaler_lookup.py -v
```

Expected: PASS。不要对 POST `/api/denoise` 做真实推理测试。

- [ ] **Step 5: Commit**

```bash
rtk git add backend/app/routers/denoise.py backend/app/main.py backend/tests/test_denoise_router_helpers.py
rtk git commit -m "$(cat <<'EOF'
feat: add denoise REST API

EOF
)"
```

---

### Task 4: 前端 store 与 API 客户端

**Files:**
- Modify: `frontend/src/renderer/stores/useSettingsStore.ts`
- Modify: `frontend/src/renderer/stores/useModelStore.ts`
- Modify: `frontend/src/renderer/hooks/useBackendAPI.ts`

**Interfaces:**
- Consumes: `GET /api/models/denoise`、`POST /api/denoise`、`POST /api/denoise/batch`、`GET /api/models/defaults` 的 `denoise` 字段
- Produces:
  - `useSettingsStore`: `denoiseModel: string`（默认 `'denoise-general'`）、`setDenoiseModel: (model: string) => void`；`loadDefaults` 写入 `denoiseModel: defaults.denoise ?? 'denoise-general'`
  - `useModelStore`: `denoiseGroups: ModelGroup[]`；`loadModels` 并行请求 `/api/models/denoise`
  - `useBackendAPI().denoise(params: { image: string; model: string; device: string; tile?: number; output_path?: string })`
  - `useBackendAPI().denoiseBatch(params: { images: string[]; model: string; device: string; tile?: number; output_dir?: string; is_file_path?: boolean })`
  - 返回类型与超分对应函数相同，但无 `outscale`

本仓库前端没有单元测试。本任务以 TypeScript 能编译、字段名与 Task 3 一致为验收。

- [ ] **Step 1: 改 useSettingsStore**

在 `SettingsState` 的 `upscaleTile` 后增加：

```typescript
  denoiseModel: string
```

在 Actions 的 `setUpscaleTile` 后增加：

```typescript
  setDenoiseModel: (model: string) => void
```

在 create 初始值 `upscaleTile: 0` 后增加：

```typescript
  denoiseModel: 'denoise-general',
```

在 `setUpscaleTile` 实现后增加：

```typescript
  setDenoiseModel: (model) => set({ denoiseModel: model }),
```

把 `loadDefaults` 的 `set({...})` 改为：

```typescript
      set({
        inpaintModel: defaults.watermark_removal ?? 'wm_lama',
        cutoutModel: defaults.cutout ?? 'birefnet',
        denoiseModel: defaults.denoise ?? 'denoise-general',
        defaultsLoaded: true,
      })
```

不要把 `denoiseModel` 写入 `saveSettings`（与 `upscaleModel` 一致）。

- [ ] **Step 2: 改 useModelStore**

在 `DEFAULT_CUTOUT_GROUPS` 之后增加：

```typescript
const DEFAULT_DENOISE_GROUPS: ModelGroup[] = [
  {
    label: '通用降噪',
    options: [
      { value: 'denoise-general', label: '通用降噪（推荐）', description: '保持原始分辨率，去除照片/截图压缩噪声与颗粒' },
    ],
  },
  {
    label: '动漫/插画',
    options: [
      { value: 'denoise-anime', label: '动漫/插画降噪', description: '保持原始分辨率，针对动漫/插画去噪' },
    ],
  },
]
```

`ModelStoreState` 增加：

```typescript
  denoiseGroups: ModelGroup[]
```

初始值 `cutoutGroups: DEFAULT_CUTOUT_GROUPS` 后增加 `denoiseGroups: DEFAULT_DENOISE_GROUPS`。

`loadModels` 里把并行 fetch 改为：

```typescript
      const [inpaintRes, upscaleRes, cutoutRes, denoiseRes] = await Promise.all([
        fetch(`${backendURL}/api/models/inpaint`),
        fetch(`${backendURL}/api/models/upscale`),
        fetch(`${backendURL}/api/cutout/models`),
        fetch(`${backendURL}/api/models/denoise`),
      ])
```

`inpaintRes.ok || upscaleRes.ok` 判断保持不变（降噪列表失败时用 DEFAULT）。解析 cutout 之后增加：

```typescript
      const denoiseData = denoiseRes.ok ? await denoiseRes.json() : null
      let denoiseGroups: ModelGroup[] = DEFAULT_DENOISE_GROUPS
      if (denoiseData?.groups) {
        denoiseGroups = denoiseData.groups.map(
          (g: { label: string; models: Array<{ id: string; name: string; description: string }> }) => ({
            label: stripDecorators(g.label),
            options: g.models.map((m) => ({
              value: m.id,
              label: m.name,
              description: m.description,
            })),
          })
        )
      }
```

`set({ inpaintGroups, upscaleGroups, cutoutGroups, upscaleModelMeta, ...})` 增加 `denoiseGroups`。

- [ ] **Step 3: 改 useBackendAPI**

在 `upscaleBatch` 之后、`getInpaintModels` 之前插入：

```typescript
  const denoise = async (params: {
    image: string
    model: string
    device: string
    tile?: number
    output_path?: string
  }) => {
    const url = await getURL()
    const res = await fetch(`${url}/api/denoise`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        image: params.image,
        model: params.model,
        device: params.device,
        tile: params.tile,
        output_path: params.output_path || undefined,
      }),
    })
    if (!res.ok) {
      const err = await res.text()
      throw new Error(`降噪失败: ${err}`)
    }
    return res.json() as Promise<{
      image: string
      width: number
      height: number
      outputPath?: string
    }>
  }

  const denoiseBatch = async (params: {
    images: string[]
    model: string
    device: string
    tile?: number
    output_dir?: string
    is_file_path?: boolean
  }) => {
    const url = await getURL()
    const res = await fetch(`${url}/api/denoise/batch`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(params),
    })
    if (!res.ok) {
      const err = await res.text()
      throw new Error(`批量降噪失败: ${err}`)
    }
    return res.json() as Promise<{
      results: { index: number; status: 'success' | 'error'; image?: string; outputPath?: string; width?: number; height?: number; error?: string; fileName?: string }[]
      summary: { total: number; success: number; failed: number }
    }>
  }
```

`return { ... }` 增加 `denoise, denoiseBatch`。不要传 `outscale`。

- [ ] **Step 4: 确认无类型错误**

检查这三个文件：`denoiseModel` / `setDenoiseModel` / `denoiseGroups` / `denoise` / `denoiseBatch` 已导出；`denoise()` 参数无 `outscale`。

- [ ] **Step 5: Commit**

```bash
rtk git add frontend/src/renderer/stores/useSettingsStore.ts frontend/src/renderer/stores/useModelStore.ts frontend/src/renderer/hooks/useBackendAPI.ts
rtk git commit -m "$(cat <<'EOF'
feat: add denoise frontend stores and API client

EOF
)"
```

---

### Task 5: 降噪页面、路由与侧边栏

**Files:**
- Create: `frontend/src/renderer/pages/Denoise.tsx`
- Modify: `frontend/src/renderer/App.tsx`
- Modify: `frontend/src/renderer/components/layout/MainLayout.tsx`
- Modify: `frontend/src/renderer/components/layout/Sidebar.tsx`

**Interfaces:**
- Consumes: `denoise` / `denoiseBatch`；`denoiseModel` / `setDenoiseModel`；`denoiseGroups`；`upscaleTile`；`selectWorkingImage` / `setHandoffResult`
- Produces: 路由 `/denoise`；侧边栏「图片降噪」（在「超分辨率」之上，图标 `Droplets`）；单张输出 `{base}_denoised.png`；批量走一次 `denoiseBatch`（Electron 优先 `is_file_path` + `output_dir`）

- [ ] **Step 1: 接路由与侧边栏**

`App.tsx` 的 Routes 在 `path="cutout"` 与 `path="upscale"` 之间增加：

```tsx
          <Route path="denoise"   element={null} />
```

`MainLayout.tsx` import：

```tsx
import Denoise from '../../pages/Denoise'
```

`PAGES` 在 cutout 与 upscale 之间增加：

```tsx
  { path: '/denoise',    element: <Denoise /> },
```

`Sidebar.tsx`：从 `lucide-react` 增加 `Droplets`。`navItems` 在超分辨率之上插入：

```tsx
  { path: '/denoise', label: '图片降噪', icon: Droplets },
```

- [ ] **Step 2: 实现 Denoise.tsx**

创建 `frontend/src/renderer/pages/Denoise.tsx`（无倍率 UI；批量调用 `denoiseBatch` 而不是逐张 `denoise`，保证模型只加载一次）：

```tsx
import { useState, useRef, useCallback, useEffect } from 'react'
import { Upload, Download, Images } from 'lucide-react'
import { Button, Select, Progress, showToast } from '../components/ui'
import ImageCanvas from '../components/ImageCanvas'
import ImageCompare from '../components/ImageCompare'
import PageHeader from '../components/layout/PageHeader'
import ModelSelect from '../components/ModelSelect/ModelSelect'
import BatchImageQueue, { type ImageQueueItem } from '../components/BatchImageQueue'
import BatchProgress from '../components/BatchProgress'
import WorkspaceInputBar from '../components/WorkspaceInputBar'
import {
  useWorkspaceStore,
  selectWorkingImage,
} from '../stores/useWorkspaceStore'
import { useProcessStore } from '../stores/useProcessStore'
import { useSettingsStore } from '../stores/useSettingsStore'
import { useModelStore } from '../stores/useModelStore'
import { useBackendAPI } from '../hooks/useBackendAPI'
import { useDeviceOptions } from '../hooks/useDeviceOptions'
import { useDownloadStore } from '../stores/useDownloadStore'
import { useDownloadManager } from '../hooks/useDownloadManager'
import { DownloadStatus, ModelStatus } from '../constants'

async function readItemImage(item: ImageQueueItem): Promise<string> {
  if (item.filePath && window.electronAPI) {
    return window.electronAPI.readImageFile(item.filePath)
  }
  if (item.dataUrl) return item.dataUrl
  if (item.file) {
    return new Promise<string>((resolve, reject) => {
      const reader = new FileReader()
      reader.onload = () => resolve(reader.result as string)
      reader.onerror = () => reject(reader.error)
      reader.readAsDataURL(item.file!)
    })
  }
  throw new Error('无图片数据')
}

export default function Denoise() {
  const sourceImage = useWorkspaceStore((s) => s.sourceImage)
  const sourceFilePath = useWorkspaceStore((s) => s.sourceFilePath)
  const loadImage = useWorkspaceStore((s) => s.loadImage)
  const setHandoffResult = useWorkspaceStore((s) => s.setHandoffResult)
  const workingImage = useWorkspaceStore(selectWorkingImage)
  const { isProcessing, progress, statusMessage, startProcess, finishProcess, setError, reset } = useProcessStore()
  const { denoise, denoiseBatch } = useBackendAPI()
  const { device, denoiseModel, upscaleTile, setDevice, setDenoiseModel } = useSettingsStore()
  const { denoiseGroups } = useModelStore()
  const { options: deviceOptions } = useDeviceOptions()
  const downloadTasks = useDownloadStore((s) => s.tasks)
  const { startDownload } = useDownloadManager()

  const [mode, setMode] = useState<'single' | 'batch'>('single')
  const [batchItems, setBatchItems] = useState<ImageQueueItem[]>([])
  const [batchCurrentIndex, setBatchCurrentIndex] = useState<number | null>(null)
  const [isBatchProcessing, setIsBatchProcessing] = useState(false)
  const [resultImage, setResultImage] = useState<string | null>(null)
  const [compareBefore, setCompareBefore] = useState<string | null>(null)
  const [inputSize, setInputSize] = useState({ w: 0, h: 0 })
  const [outputSize, setOutputSize] = useState({ w: 0, h: 0 })
  const [tile, setTile] = useState<number | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const loadSource = (dataUrl: string, filePath?: string) => {
    if (mode === 'batch') {
      const img = new Image()
      img.onload = () => {
        const newItem: ImageQueueItem = {
          id: `img_${Date.now()}_${Math.random().toString(36).substr(1, 5)}`,
          fileName: filePath ? filePath.split(/[\\/]/).pop() || 'unknown.png' : `image_${batchItems.length + 1}.png`,
          filePath: filePath,
          dataUrl: dataUrl,
          status: 'pending',
        }
        setBatchItems(prev => [...prev, newItem])
        showToast('success', `已添加到队列: ${newItem.fileName}`)
      }
      img.src = dataUrl
      return
    }
    setResultImage(null)
    setCompareBefore(null)
    reset()
    loadImage(dataUrl, filePath)
    const img = new Image()
    img.onload = () => {
      setInputSize({ w: img.naturalWidth, h: img.naturalHeight })
      setOutputSize({ w: img.naturalWidth, h: img.naturalHeight })
    }
    img.src = dataUrl
  }

  useEffect(() => {
    if (!workingImage || mode === 'batch' || resultImage) return
    const img = new Image()
    img.onload = () => {
      setInputSize({ w: img.naturalWidth, h: img.naturalHeight })
      setOutputSize({ w: img.naturalWidth, h: img.naturalHeight })
    }
    img.src = workingImage
  }, [workingImage, mode, resultImage])

  const handleOpenFile = async () => {
    if (window.electronAPI) {
      const handler = mode === 'batch' ? window.electronAPI.openFiles?.() : window.electronAPI.openFile()
      const result = await handler
      if (result) {
        if (mode === 'batch') {
          const paths = Array.isArray(result) ? result : [result]
          const newItems: ImageQueueItem[] = paths.map(filePath => ({
            id: `img_${Date.now()}_${Math.random().toString(36).substr(1, 5)}`,
            fileName: filePath.split(/[\\/]/).pop() || 'unknown.png',
            filePath,
            status: 'pending' as const,
          }))
          setBatchItems(prev => [...prev, ...newItems])
          showToast('success', `已添加 ${newItems.length} 张图片到队列`)
        } else {
          const filePath = Array.isArray(result) ? result[0] : result
          try {
            const dataUrl = await window.electronAPI.readImageFile(filePath)
            loadSource(dataUrl, filePath)
          } catch (err: any) {
            showToast('error', `读取图片失败: ${err.message}`)
          }
        }
      }
    } else {
      fileInputRef.current?.click()
    }
  }

  const handleFileInput = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files
    if (!files || files.length === 0) return
    e.target.value = ''
    if (mode === 'batch') {
      const newItems: ImageQueueItem[] = Array.from(files).map(file => ({
        id: `img_${Date.now()}_${Math.random().toString(36).substr(1, 5)}`,
        fileName: file.name,
        file,
        status: 'pending' as const,
      }))
      setBatchItems(prev => [...prev, ...newItems])
      showToast('success', `已添加 ${newItems.length} 张图片到队列`)
    } else {
      const file = files[0]
      const electronFile = file as File & { path?: string }
      if (window.electronAPI && electronFile.path) {
        window.electronAPI.readImageFile(electronFile.path)
          .then((dataUrl) => loadSource(dataUrl, electronFile.path))
          .catch((err: any) => showToast('error', `读取图片失败: ${err.message}`))
        return
      }
      const reader = new FileReader()
      reader.onload = () => loadSource(reader.result as string)
      reader.readAsDataURL(file)
    }
  }

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    const files = e.dataTransfer.files
    if (!files || files.length === 0) return
    if (mode === 'batch') {
      const newItems: ImageQueueItem[] = Array.from(files)
        .filter(file => file.type.startsWith('image/'))
        .map(file => ({
          id: `img_${Date.now()}_${Math.random().toString(36).substr(1, 5)}`,
          fileName: file.name,
          file,
          status: 'pending' as const,
        }))
      if (newItems.length > 0) {
        setBatchItems(prev => [...prev, ...newItems])
        showToast('success', `已添加 ${newItems.length} 张图片到队列`)
      }
    } else {
      const file = files[0]
      if (!file || !file.type.startsWith('image/')) return
      const electronFile = file as File & { path?: string }
      if (window.electronAPI && electronFile.path) {
        window.electronAPI.readImageFile(electronFile.path)
          .then((dataUrl) => loadSource(dataUrl, electronFile.path))
          .catch((err: any) => showToast('error', `读取图片失败: ${err.message}`))
        return
      }
      const reader = new FileReader()
      reader.onload = () => loadSource(reader.result as string)
      reader.readAsDataURL(file)
    }
  }

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault()
    e.dataTransfer.dropEffect = 'copy'
  }

  const ensureModelReady = () => {
    const task = downloadTasks[denoiseModel]
    if (task?.status === DownloadStatus.DOWNLOADING || task?.status === DownloadStatus.QUEUED) {
      showToast('info', '模型下载中，请稍候...')
      return false
    }
    if (task?.status === ModelStatus.MISSING || task?.status === DownloadStatus.ERROR) {
      showToast('info', '模型未下载，已自动加入下载队列，完成后可继续操作')
      startDownload(denoiseModel)
      return false
    }
    return true
  }

  const handleBatchDenoise = async () => {
    if (batchItems.length === 0) return
    if (!ensureModelReady()) return

    setIsBatchProcessing(true)
    const isElectron = !!window.electronAPI
    const items = batchItems.map(item => ({ ...item, status: 'pending' as ImageQueueItem['status'] })) as ImageQueueItem[]
    setBatchItems(items)
    setBatchCurrentIndex(0)

    let outputDir: string | undefined
    if (isElectron) {
      const dir = await window.electronAPI.openDirectory?.()
      if (!dir) {
        setIsBatchProcessing(false)
        setBatchCurrentIndex(null)
        return
      }
      outputDir = dir
    }

    const useFilePath = isElectron && items.every(i => !!i.filePath)
    try {
      startProcess(denoiseModel)
      setBatchItems(items.map(i => ({ ...i, status: 'processing' as const })))
      let images: string[]
      if (useFilePath) {
        images = items.map(i => i.filePath as string)
      } else {
        images = await Promise.all(items.map(readItemImage))
      }
      const result = await denoiseBatch({
        images,
        model: denoiseModel,
        device,
        tile: tile ?? (upscaleTile || 0),
        output_dir: outputDir,
        is_file_path: useFilePath,
      })
      const next = items.map((item, idx) => {
        const r = result.results.find(x => x.index === idx)
        if (r?.status === 'success') {
          return {
            ...item,
            status: 'success' as const,
            resultPath: r.outputPath,
            resultDataUrl: r.image ? `data:image/png;base64,${r.image}` : undefined,
          }
        }
        return { ...item, status: 'error' as const, error: r?.error || '处理失败' }
      })
      setBatchItems(next.map(({ dataUrl, resultDataUrl, ...rest }) => {
        if (rest.resultPath) return rest as ImageQueueItem
        return { ...rest, resultDataUrl } as ImageQueueItem
      }))
      const { success, failed } = result.summary
      finishProcess('批量降噪完成')
      showToast('success', `批量降噪完成: ${success} 成功${failed > 0 ? `, ${failed} 失败` : ''}`)
    } catch (err: any) {
      setError(err.message)
      showToast('error', err.message)
      setBatchItems(items)
    } finally {
      setIsBatchProcessing(false)
      setBatchCurrentIndex(null)
    }
  }

  const handleDenoise = async () => {
    if (!workingImage) return
    if (!ensureModelReady()) return

    let outputPath: string | undefined
    if (window.electronAPI && sourceFilePath) {
      outputPath = sourceFilePath.replace(/\.[^.]+$/, '') + '_denoised.png'
    }

    try {
      startProcess(denoiseModel)
      setCompareBefore(workingImage)
      const result = await denoise({
        image: workingImage,
        model: denoiseModel,
        device,
        tile: tile ?? (upscaleTile || 0),
        output_path: outputPath,
      })
      if (result.outputPath) {
        const dataUrl = await window.electronAPI.readImageFile(result.outputPath)
        setResultImage(dataUrl)
        setHandoffResult(dataUrl, { width: result.width, height: result.height })
      } else {
        const dataUrl = `data:image/png;base64,${result.image}`
        setResultImage(dataUrl)
        setHandoffResult(dataUrl, { width: result.width, height: result.height })
      }
      setOutputSize({ w: result.width, h: result.height })
      finishProcess('降噪处理完成')
      showToast('success', `降噪完成: ${result.width}x${result.height}`)
    } catch (err: any) {
      setError(err.message)
      showToast('error', err.message)
    }
  }

  const handleSave = async () => {
    if (!resultImage) return
    const baseName = sourceFilePath
      ? sourceFilePath.split(/[\\/]/).pop()?.replace(/\.[^.]+$/, '') || 'image'
      : 'image'
    const defaultName = `${baseName}_denoised.png`
    if (window.electronAPI) {
      const filePath = await window.electronAPI.saveFile(defaultName)
      if (filePath) {
        const result = await window.electronAPI.saveImageFile(filePath, resultImage)
        if (result.success) {
          showToast('success', `已保存至: ${filePath}`)
        } else {
          showToast('error', `保存失败: ${result.error}`)
        }
      }
    } else {
      const a = document.createElement('a')
      a.href = resultImage
      a.download = defaultName
      a.click()
    }
  }

  const handleUseResultAsSource = () => {
    if (!resultImage) return
    loadSource(resultImage, sourceFilePath ?? undefined)
    showToast('success', '已将处理结果设为原图')
  }

  const handleRemoveBatchItem = (id: string) => {
    setBatchItems(prev => prev.filter(item => item.id !== id))
  }

  const handleClearBatch = () => {
    setBatchItems([])
    setBatchCurrentIndex(null)
  }

  const handleAddToBatch = (files: FileList | string[]) => {
    if (Array.isArray(files)) {
      const newItems: ImageQueueItem[] = (files as string[]).map(filePath => ({
        id: `img_${Date.now()}_${Math.random().toString(36).substr(1, 5)}`,
        fileName: filePath.split(/[\\/]/).pop() || 'unknown.png',
        filePath,
        status: 'pending' as const,
      }))
      setBatchItems(prev => [...prev, ...newItems])
      showToast('success', `已添加 ${newItems.length} 张图片到队列`)
    } else {
      const newItems: ImageQueueItem[] = Array.from(files as FileList).map(file => ({
        id: `img_${Date.now()}_${Math.random().toString(36).substr(1, 5)}`,
        fileName: file.name,
        file,
        status: 'pending' as const,
      }))
      setBatchItems(prev => [...prev, ...newItems])
      showToast('success', `已添加 ${newItems.length} 张图片到队列`)
    }
  }

  const loadBatchThumbnail = useCallback(async (id: string) => {
    const item = batchItems.find(i => i.id === id)
    if (!item || item.dataUrl) return
    try {
      const dataUrl = await readItemImage(item)
      setBatchItems(prev => prev.map(i => i.id === id ? { ...i, dataUrl } : i))
    } catch (err: any) {
      console.error('缩略图加载失败:', err)
    }
  }, [batchItems])

  return (
    <div className="flex-1 flex flex-col min-h-0">
      <PageHeader
        title="图片降噪"
        subtitle={
          mode === 'batch'
            ? `${batchItems.length} 张图片 | 保持原分辨率 | ${denoiseModel}`
            : inputSize.w > 0 ? `${inputSize.w}x${inputSize.h}（保持分辨率）` : undefined
        }
        right={
          <div className="flex items-center gap-2">
            {mode === 'single' && (
              <WorkspaceInputBar
                onChange={() => {
                  setResultImage(null)
                  setCompareBefore(null)
                }}
              />
            )}
            <div className="flex items-center gap-1">
            <button
              onClick={() => setMode('single')}
              className={`px-2 py-1 rounded text-xs ${
                mode === 'single'
                  ? 'bg-blue-500 text-white'
                  : 'bg-bg-primary text-fg-secondary hover:text-fg-primary'
              }`}
            >
              <Upload size={12} className="inline mr-1" />
              单图
            </button>
            <button
              onClick={() => setMode('batch')}
              className={`px-2 py-1 rounded text-xs ${
                mode === 'batch'
                  ? 'bg-blue-500 text-white'
                  : 'bg-bg-primary text-fg-secondary hover:text-fg-primary'
              }`}
            >
              <Images size={12} className="inline mr-1" />
              批量
            </button>
            </div>
          </div>
        }
      />

      <div className="flex-1 flex overflow-hidden min-h-0">
        <div
          className="flex-1 bg-[#1a1a1a] flex items-center justify-center relative"
          onDrop={handleDrop}
          onDragOver={handleDragOver}
        >
          {mode === 'batch' ? (
            <div className="w-full h-full p-4">
              <BatchImageQueue
                items={batchItems}
                currentIndex={batchCurrentIndex}
                onRemove={handleRemoveBatchItem}
                onClear={handleClearBatch}
                onAdd={handleAddToBatch}
                onLoadThumbnail={loadBatchThumbnail}
                disabled={isBatchProcessing}
              />
            </div>
          ) : !sourceImage ? (
            <div className="flex flex-col items-center gap-4 text-fg-secondary">
              <Upload size={48} strokeWidth={1} />
              <p className="text-sm">拖拽图片到此处，或点击打开文件</p>
              <Button onClick={handleOpenFile}>打开图片</Button>
              <input ref={fileInputRef} type="file" accept="image/*" className="hidden" onChange={handleFileInput} />
            </div>
          ) : resultImage ? (
            <div className="w-full h-full p-4">
              <ImageCompare
                beforeSrc={compareBefore || workingImage || sourceImage}
                afterSrc={resultImage}
                beforeLabel={`输入 (${inputSize.w}x${inputSize.h})`}
                afterLabel={`降噪 (${outputSize.w}x${outputSize.h})`}
              />
            </div>
          ) : (
            <ImageCanvas
              imageSrc={workingImage}
              rois={[]}
              selectedROIs={[]}
              tool="pan"
              onROIDrawn={() => {}}
              onROISelect={() => {}}
              onImageLoad={(w, h) => {
                setInputSize({ w, h })
                setOutputSize({ w, h })
              }}
            />
          )}
          <input ref={fileInputRef} type="file" accept="image/*" multiple={mode === 'batch'} className="hidden" onChange={handleFileInput} />
        </div>

        <div className="w-[240px] bg-bg-secondary border-l border-border-subtle p-3 flex flex-col gap-4 overflow-y-auto min-h-0">
          <ModelSelect
            label="模型"
            value={denoiseModel}
            onChange={(v) => setDenoiseModel(v)}
            size="sm"
            groups={denoiseGroups}
          />
          <Select
            label="设备"
            value={device}
            onChange={(e) => setDevice(e.target.value)}
            size="sm"
            options={deviceOptions}
          />
          <div>
            <label className="block text-xs font-medium text-fg-secondary mb-1">
              分块大小 (Tile)
              <span className="ml-1 text-[10px] text-fg-tertiary">（0 = 自动）</span>
            </label>
            <input
              type="number"
              min={0}
              max={2048}
              step={64}
              value={tile ?? upscaleTile ?? 0}
              onChange={(e) => setTile(e.target.value ? Number(e.target.value) : null)}
              className="w-full bg-bg-primary border border-border-subtle text-fg-primary text-xs px-2 py-1.5 rounded focus:border-border-focus focus:outline-none"
            />
            <p className="text-[11px] text-fg-tertiary mt-1">
              分块处理大小：0 = 自动计算；建议值：256 / 512 / 1024。降低此值可节省显存。
            </p>
          </div>
          {mode === 'single' && (
            <section>
              <h3 className="text-xs uppercase tracking-wider text-fg-secondary mb-2">图像信息</h3>
              <div className="bg-bg-primary rounded border border-border-subtle p-2.5 space-y-1.5">
                <div className="flex justify-between text-xs">
                  <span className="text-fg-secondary">输入尺寸</span>
                  <span className="text-fg-primary">{inputSize.w > 0 ? `${inputSize.w} x ${inputSize.h}` : '-'}</span>
                </div>
                <div className="flex justify-between text-xs">
                  <span className="text-fg-secondary">输出尺寸</span>
                  <span className="text-status-success">{outputSize.w > 0 ? `${outputSize.w} x ${outputSize.h}` : '-'}</span>
                </div>
                <div className="flex justify-between text-xs">
                  <span className="text-fg-secondary">分辨率</span>
                  <span className="text-fg-accent">保持原尺寸</span>
                </div>
              </div>
            </section>
          )}
          {mode === 'batch' && isBatchProcessing && (
            <BatchProgress
              current={batchCurrentIndex || 0}
              total={batchItems.length}
            />
          )}
          <div className="mt-auto pt-2 space-y-2">
            {mode === 'batch' ? (
              <>
                <Button
                  onClick={handleBatchDenoise}
                  disabled={batchItems.length === 0 || isBatchProcessing}
                  loading={isBatchProcessing}
                  className="w-full"
                  size="lg"
                >
                  {isBatchProcessing ? '处理中...' : `开始批量降噪 (${batchItems.length} 张)`}
                </Button>
              </>
            ) : (
              <>
                {(() => {
                  const task = downloadTasks[denoiseModel]
                  const isModelBusy = task?.status === DownloadStatus.DOWNLOADING || task?.status === DownloadStatus.QUEUED
                  return (
                    <Button
                      onClick={handleDenoise}
                      disabled={!workingImage || isProcessing || isModelBusy}
                      loading={isProcessing}
                      className="w-full"
                      size="lg"
                    >
                      {isProcessing
                        ? statusMessage
                        : (() => {
                            const t = downloadTasks[denoiseModel]
                            if (t?.status === DownloadStatus.DOWNLOADING) return '等待下载...'
                            if (t?.status === DownloadStatus.QUEUED) return `排队中 #${t.position}`
                            if (t?.status === ModelStatus.MISSING || t?.status === DownloadStatus.ERROR) return '点击下载并处理'
                            return '开始降噪'
                          })()}
                    </Button>
                  )
                })()}
                {isProcessing && (
                  <Progress value={progress} label={`${statusMessage} ${progress > 0 ? progress + '%' : ''}`} />
                )}
                {resultImage && (
                  <Button variant="ghost" onClick={handleSave} icon={<Download size={14} />} className="w-full" size="sm">
                    保存结果
                  </Button>
                )}
                {resultImage && (
                  <Button variant="ghost" onClick={handleUseResultAsSource} icon={<Upload size={14} />} className="w-full" size="sm">
                    将结果设为原图
                  </Button>
                )}
                {sourceImage && !resultImage && (
                  <Button variant="ghost" onClick={handleOpenFile} icon={<Upload size={14} />} className="w-full" size="sm">
                    更换图片
                  </Button>
                )}
              </>
            )}
            {mode === 'batch' && (
              <Button
                variant="ghost"
                onClick={handleOpenFile}
                disabled={isBatchProcessing}
                icon={<Images size={14} />}
                className="w-full"
                size="sm"
              >
                选择图片
              </Button>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
```

- [ ] **Step 3: 确认页面能挂上**

侧边栏第三项为「图片降噪」；Hash 路由 `#/denoise` 显示该页；超分页模型列表仍无 `denoise-*`。

- [ ] **Step 4: Commit**

```bash
rtk git add frontend/src/renderer/pages/Denoise.tsx frontend/src/renderer/App.tsx frontend/src/renderer/components/layout/MainLayout.tsx frontend/src/renderer/components/layout/Sidebar.tsx
rtk git commit -m "$(cat <<'EOF'
feat: add denoise page with single and batch modes

EOF
)"
```

---

### Task 6: 文档同步

**Files:**
- Modify: `docs/PRD.md`
- Modify: `docs/SYSTEM_ARCHITECTURE.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: 已落地的 `/denoise`、模型 ID、API
- Produces: PRD 将图片降噪从 Phase 3 挪到功能需求；架构文档页面表含 `/denoise`；README 功能一览含降噪

- [ ] **Step 1: 改 PRD**

`docs/PRD.md`：

1. 1.1 产品定位「去水印、抠图、超分辨率、智能合成」改为包含「图片降噪」。
2. 2.1 核心功能模块 ASCII 图增加「图片降噪」列。
3. 「去水印、超分辨率、智能合成共享同一工作图」改为「去水印、超分辨率、图片降噪、智能合成共享同一工作图」。
4. 在 2.3 超分辨率与 2.4 抠图之间插入新节「2.4 图片降噪」，其后章节序号顺延（原 2.4 抠图 → 2.5，原 2.5 智能合成 → 2.6）。新节正文：

```markdown
### 2.4 图片降噪 (Denoise)

#### 功能描述

用户加载带压缩噪声或颗粒的图片，选择降噪模型，生成**与输入相同分辨率**的结果。支持单张与批量。底层复用 Real-ESRGAN 清晰化权重（`outscale=1`），不新增权重文件。

#### 功能点

| # | 功能 | 优先级 | 描述 |
|---|------|--------|------|
| D1 | 图片加载 | P0 | 同超分：拖拽 / 文件对话框；单张写入共享工作区 |
| D2 | 模型选择 | P0 | 通用降噪 / 动漫插画降噪 |
| D3 | 单张处理 | P0 | 进度显示；输出尺寸等于输入 |
| D4 | 批量处理 | P0 | 导入多张，一次请求逐张处理，模型只加载一次 |
| D5 | 对比预览 | P1 | 拖拽滑块对比处理前后 |
| D6 | 保存结果 | P0 | 单张 `{原名}_denoised.png` / 批量输出到指定目录 |

#### 支持的模型

| 类别 | 模型 ID | 特点 | 大小 |
|------|---------|------|------|
| 通用降噪 | denoise-general（推荐） | 保持分辨率，照片/截图去噪，复用 realesr-general-x4v3 | 5MB |
| 动漫/插画 | denoise-anime | 保持分辨率，复用 anime 6B | 18MB |
```

5. API 设计表增加：

```
| GET | /api/models/denoise | 降噪模型列表（按 display_group 分组） |
| POST | /api/denoise | 单张降噪 |
| POST | /api/denoise/batch | 批量降噪（循环外加载模型） |
```

6. 里程碑增加一行：`| M9 | 图片降噪 — 单张 + 批量，复用 Real-ESRGAN 清晰化权重 | ⏳ 本计划 |`

7. Phase 3 表格删除「图片降噪」行。

8. 附录术语表增加：`图片降噪 | 保持分辨率的 AI 去噪；本产品用 Real-ESRGAN outscale=1`

- [ ] **Step 2: 改 SYSTEM_ARCHITECTURE 与 README**

`docs/SYSTEM_ARCHITECTURE.md`：

- 概述「核心功能」加上图片降噪。
- 3.2 页面表在 `/cutout` 与 `/upscale` 之间插入：`| `/denoise` | 图片降噪 | 工作区 working image（原图或上一页结果） |`
- 3.3：`inputSource` 说明改为超分、**降噪**、合成默认用结果。
- 「最近一次去水印 / 超分 / 合成」改为包含降噪。

`README.md` 功能一览表在超分辨率后增加：

```
| **图片降噪** | 保持分辨率去除压缩噪声与颗粒，支持单张与批量（复用 Real-ESRGAN 清晰化权重） |
```

「跨页流水线」改为「去水印 / 降噪 / 超分 / 合成共享工作图」。

在「超分辨率」使用指南之后增加：

```markdown
### 图片降噪

1. 打开图片（可与去水印结果衔接）
2. 选择通用或动漫模型（不放大）
3. 单张点击「开始降噪」，或切换批量后选择输出目录
4. 对比预览并保存为 `{原名}_denoised.png`
```

- [ ] **Step 3: 对照规格扫一遍**

确认 Restormer 注释仍在；超分清晰化条目未改 tags；本任务只改文档。

- [ ] **Step 4: Commit**

```bash
rtk git add docs/PRD.md docs/SYSTEM_ARCHITECTURE.md README.md
rtk git commit -m "$(cat <<'EOF'
docs: document image denoise feature

EOF
)"
```

---

## 手动验收（全部 Task 完成后）

1. `cd backend && rtk pytest tests/test_denoise_registry.py tests/test_upscaler_lookup.py tests/test_denoise_router_helpers.py -v` 全绿。
2. 超分页模型列表仍有「清晰化增强」，没有 `denoise-general`。
3. 降噪页只有两条模型，没有输出倍率下拉。
4. 若本机已有 `realesr-general-x4v3.pth`，通用降噪应显示已下载。
5. 单张：输出宽高等于输入；对比滑块可用；结果可切到超分页继续处理。
6. 批量：选输出目录，生成 `*_denoised.png`；故意放一个坏路径，其余仍成功。
