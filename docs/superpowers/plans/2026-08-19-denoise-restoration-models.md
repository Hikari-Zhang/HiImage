# 降噪专用模型（SCUNet / NAFNet / SwinIR）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 降噪页增加 SCUNet / NAFNet-SIDD / SwinIR-JPEG，按噪声类型分组标注；默认 SCUNet-psnr；清晰化两条 Real-ESRGAN 保留。

**Architecture:** 新模型 `provider: restoration`，`RestorationExecutor` 用 spandrel 加载官方 `.pth` 并 tile 推理。清晰化仍走 `Upscaler`。路由用 `create_denoise_backend()` 按 provider 分发。不手写网络，不修 Restormer。

**Tech Stack:** FastAPI、spandrel + spandrel-extra-arches、PyTorch、现有 models.yaml / 下载队列 / Electron 降噪页。

**Spec:** `docs/superpowers/specs/2026-08-19-denoise-restoration-models-design.md`

## Global Constraints

- 默认模型 ID：`denoise-scunet-psnr`。
- NAFNet 只用 SIDD width32；SwinIR 只用 color CAR jpeg40。
- 不修复、不启用 `restormer_model.py` / `image_restoration`。
- 不引入降噪强度滑块；不抽超分/降噪共用页。
- 不跑真实权重推理测试。
- 命令前缀 `rtk`（见仓库 `CLAUDE.md`）。
- Python：spandrel 需要 ≥3.10（与 README 推荐 3.11 一致）。

## File Structure

新建：

- `backend/core/restoration_executor.py` — spandrel 执行器
- `backend/tests/test_restoration_executor.py` — Factory / 路径 / 缺权重

修改：

- `backend/tests/test_denoise_registry.py`
- `backend/core/models.yaml`
- `backend/core/constants.py`
- `backend/core/paths.py`
- `backend/core/model_executor.py`
- `backend/requirements.txt`
- `backend/app/routers/denoise.py`
- `backend/tests/test_denoise_router_helpers.py`
- `frontend/src/renderer/types/models.ts`
- `frontend/src/renderer/components/ModelSelect/ModelSelect.tsx`
- `frontend/src/renderer/stores/useSettingsStore.ts`
- `frontend/src/renderer/stores/useModelStore.ts`
- `docs/PRD.md` / `docs/SYSTEM_ARCHITECTURE.md` / `README.md`

---

### Task 1: 注册 restoration 模型与默认值

**Files:**
- Test: `backend/tests/test_denoise_registry.py`
- Modify: `backend/core/models.yaml`

**Interfaces:**
- Consumes: `get_models_for_mode("denoise")`、`get_default_model("denoise")`、`get_model(id)`
- Produces: 六条 denoise 模型，YAML 顺序见 Step 1；`get_default_model("denoise") == "denoise-scunet-psnr"`

- [ ] **Step 1: 改失败测试**

把 `backend/tests/test_denoise_registry.py` 整文件换成：

```python
"""denoise 模式必须包含专用复原模型与清晰化条目。"""


def test_denoise_mode_has_six_models_in_order():
    from core.model_registry import get_models_for_mode

    models = get_models_for_mode("denoise")
    ids = [m["id"] for m in models]
    assert ids == [
        "denoise-scunet-psnr",
        "denoise-scunet-gan",
        "denoise-nafnet-sidd",
        "denoise-swinir-jpeg",
        "denoise-general",
        "denoise-anime",
    ]


def test_denoise_default_model():
    from core.model_registry import get_default_model

    assert get_default_model("denoise") == "denoise-scunet-psnr"


def test_restoration_denoise_models():
    from core.model_registry import get_model

    expected = {
        "denoise-scunet-psnr": ("照片 / 截图", "盲去噪（压缩颗粒 + 一般照片噪声，保纹理）"),
        "denoise-scunet-gan": ("观感更干净", "更干净，可能略抹纹理"),
        "denoise-nafnet-sidd": ("相机 ISO", "手机 / 相机传感器噪声"),
        "denoise-swinir-jpeg": ("去压缩伪影", "JPEG 色块 / 振铃（按常见网图质量 Q≈40）"),
    }
    for model_id, (group, suitable) in expected.items():
        cfg = get_model(model_id)
        assert cfg["provider"] == "restoration"
        assert cfg.get("display_group") == group
        assert cfg.get("suitable_for") == suitable
        assert "denoise" in cfg.get("tags", [])
        assert cfg.get("supports_custom_outscale") is False


def test_clarity_denoise_models_remain_realesrgan():
    from core.model_registry import get_model

    for model_id in ("denoise-general", "denoise-anime"):
        cfg = get_model(model_id)
        assert cfg["provider"] == "realesrgan"
        assert cfg.get("outscale") == 1
        assert cfg.get("display_group") == "快速清晰化"
        assert cfg.get("suitable_for")
        assert "denoise" in cfg.get("tags", [])


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

Expected: FAIL，`assert ids == [...]` 或默认模型仍是 `denoise-general`。

- [ ] **Step 3: 写入 YAML**

把 `backend/core/models.yaml` 里从 `# ── 图片降噪（denoise）` 到 `denoise-anime` 条目结束（Restormer 注释块之前）整段换成：

```yaml
  # ── 图片降噪（denoise）──────────────────────────────────────────────────
  # restoration：spandrel 加载官方 .pth，同分辨率
  # realesrgan：outscale 1，4x 再缩回，快速清晰化

  - id: denoise-scunet-psnr
    name: SCUNet 盲去噪（推荐）
    provider: restoration
    tags: [denoise]
    description: 保持原始分辨率，盲去噪，适合照片与截图的压缩颗粒｜SCUNet real-psnr（~72 MB）
    recommended_for: 照片 / 截图盲去噪
    suitable_for: 盲去噪（压缩颗粒 + 一般照片噪声，保纹理）
    requires_reference: false
    size_mb: 72
    badge: 推荐
    weight_filename: scunet_color_real_psnr.pth
    local_path: models/restoration/scunet_color_real_psnr.pth
    display_group: 照片 / 截图
    download_url: https://github.com/cszn/KAIR/releases/download/v1.0/scunet_color_real_psnr.pth
    supported_params: {}
    supports_custom_outscale: false
    features: [denoise]

  - id: denoise-scunet-gan
    name: SCUNet 观感去噪
    provider: restoration
    tags: [denoise]
    description: 保持原始分辨率，观感更干净，可能略抹纹理｜SCUNet real-gan（~72 MB）
    recommended_for: 观感去噪
    suitable_for: 更干净，可能略抹纹理
    requires_reference: false
    size_mb: 72
    badge: ""
    weight_filename: scunet_color_real_gan.pth
    local_path: models/restoration/scunet_color_real_gan.pth
    display_group: 观感更干净
    download_url: https://github.com/cszn/KAIR/releases/download/v1.0/scunet_color_real_gan.pth
    supported_params: {}
    supports_custom_outscale: false
    features: [denoise]

  - id: denoise-nafnet-sidd
    name: NAFNet 相机降噪
    provider: restoration
    tags: [denoise]
    description: 保持原始分辨率，针对手机 / 相机传感器噪声｜NAFNet-SIDD width32（~117 MB）
    recommended_for: 相机 ISO
    suitable_for: 手机 / 相机传感器噪声
    requires_reference: false
    size_mb: 117
    badge: ""
    weight_filename: NAFNet-SIDD-width32.pth
    local_path: models/restoration/NAFNet-SIDD-width32.pth
    display_group: 相机 ISO
    download_url: https://huggingface.co/nyanko7/nafnet-models/resolve/main/NAFNet-SIDD-width32.pth
    supported_params: {}
    supports_custom_outscale: false
    features: [denoise]

  - id: denoise-swinir-jpeg
    name: SwinIR 去压缩
    provider: restoration
    tags: [denoise]
    description: 保持原始分辨率，去除 JPEG 色块与振铃｜SwinIR color CAR jpeg40（~98 MB）
    recommended_for: 去压缩伪影
    suitable_for: JPEG 色块 / 振铃（按常见网图质量 Q≈40）
    requires_reference: false
    size_mb: 98
    badge: ""
    weight_filename: 006_colorCAR_DFWB_s126w7_SwinIR-M_jpeg40.pth
    local_path: models/restoration/006_colorCAR_DFWB_s126w7_SwinIR-M_jpeg40.pth
    display_group: 去压缩伪影
    download_url: https://github.com/JingyunLiang/SwinIR/releases/download/v0.0/006_colorCAR_DFWB_s126w7_SwinIR-M_jpeg40.pth
    supported_params: {}
    supports_custom_outscale: false
    features: [denoise]

  - id: denoise-general
    name: 快速清晰化·通用
    provider: realesrgan
    tags: [denoise]
    description: 超分权重复用（4x 再缩回），偏锐化，不是专用去噪｜复用 realesr-general-x4v3（~5 MB）
    recommended_for: 快速清晰化（已有权重）
    suitable_for: 要锐化或不想再下大模型
    requires_reference: false
    size_mb: 5
    badge: ""
    scale: 4
    outscale: 1
    weight_filename: realesr-general-x4v3.pth
    local_path: models/realesrgan/realesr-general-x4v3.pth
    arch: SRVGGNetCompact
    num_conv: 32
    display_group: 快速清晰化
    download_url: https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesr-general-x4v3.pth
    supported_params: {}
    supports_custom_outscale: false
    features: [denoise]

  - id: denoise-anime
    name: 快速清晰化·动漫
    provider: realesrgan
    tags: [denoise]
    description: 超分权重复用（4x 再缩回），线条更锐，不是专用去噪｜复用 anime 6B（~18 MB）
    recommended_for: 快速清晰化（动漫）
    suitable_for: 动漫线稿锐化
    requires_reference: false
    size_mb: 18
    badge: 动漫
    scale: 4
    outscale: 1
    weight_filename: RealESRGAN_x4plus_anime_6B.pth
    local_path: models/realesrgan/RealESRGAN_x4plus_anime_6B.pth
    arch: RRDBNet
    num_block: 6
    display_group: 快速清晰化
    download_url: https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.2.4/RealESRGAN_x4plus_anime_6B.pth
    supported_params: {}
    supports_custom_outscale: false
    features: [denoise]
```

把 `mode_groups` 里 denoise 的 `default_model: denoise-general` 改成 `denoise-scunet-psnr`。Restormer 注释块一行都不要动。`clarity-general` / `clarity-anime` 一行都不要改。

- [ ] **Step 4: 跑测试确认通过**

Run:

```bash
cd /Users/hikari/Documents/Git/HiImage/backend && rtk pytest tests/test_denoise_registry.py -v
```

Expected: PASS（7 passed）。

- [ ] **Step 5: Commit**

```bash
rtk git add backend/tests/test_denoise_registry.py backend/core/models.yaml
rtk git commit -m "$(cat <<'EOF'
feat: register SCUNet NAFNet SwinIR denoise models

EOF
)"
```

---

### Task 2: restoration 路径与常量

**Files:**
- Create: `backend/tests/test_restoration_paths.py`
- Modify: `backend/core/constants.py`
- Modify: `backend/core/paths.py`

**Interfaces:**
- Consumes: `resolve_model_cache_path(cfg: dict) -> Path`、`MODELS_CACHE_DIR`
- Produces: `Provider.RESTORATION == "restoration"`；`provider=="restoration"` 时路径为 `MODELS_CACHE_DIR / "restoration" / weight_filename`

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_restoration_paths.py`：

```python
from pathlib import Path


def test_provider_restoration_constant():
    from core.constants import Provider

    assert Provider.RESTORATION == "restoration"


def test_resolve_restoration_cache_path():
    from core.paths import MODELS_CACHE_DIR, resolve_model_cache_path

    path = resolve_model_cache_path({
        "provider": "restoration",
        "weight_filename": "scunet_color_real_psnr.pth",
    })
    assert path == MODELS_CACHE_DIR / "restoration" / "scunet_color_real_psnr.pth"
    assert isinstance(path, Path)
```

- [ ] **Step 2: 跑测试确认失败**

Run:

```bash
cd /Users/hikari/Documents/Git/HiImage/backend && rtk pytest tests/test_restoration_paths.py -v
```

Expected: FAIL，`AttributeError: RESTORATION`。

- [ ] **Step 3: 改常量和路径**

在 `backend/core/constants.py` 的 `Provider` 类里 `NAFNET` 那一行之后增加：

```python
    RESTORATION: ClassVar[str] = "restoration"
```

在 `backend/core/paths.py` 的 `resolve_model_cache_path` 里，`elif provider == "restormer":` 之后、`elif provider == "gfpgan":` 之前插入：

```python
    elif provider == "restoration":
        return MODELS_CACHE_DIR / "restoration" / weight_filename
```

把该函数 docstring 的目录列表补上：`restoration → MODELS_CACHE_DIR / "restoration" / weight_filename`。

- [ ] **Step 4: 跑测试确认通过**

Run:

```bash
cd /Users/hikari/Documents/Git/HiImage/backend && rtk pytest tests/test_restoration_paths.py tests/test_denoise_registry.py -v
```

Expected: PASS。

- [ ] **Step 5: Commit**

```bash
rtk git add backend/tests/test_restoration_paths.py backend/core/constants.py backend/core/paths.py
rtk git commit -m "$(cat <<'EOF'
feat: add restoration provider cache path

EOF
)"
```

---

### Task 3: RestorationExecutor 与 Factory

**Files:**
- Create: `backend/tests/test_restoration_executor.py`
- Create: `backend/core/restoration_executor.py`
- Modify: `backend/core/model_executor.py`
- Modify: `backend/requirements.txt`

**Interfaces:**
- Consumes: Task 1/2 的 YAML 与 `resolve_model_cache_path`；`BaseModelExecutor`
- Produces:
  - `class RestorationExecutor(BaseModelExecutor)`：`load_model() -> None`、`execute(image: np.ndarray, **kwargs) -> np.ndarray`（kwargs 含 `tile: int`）、`unload_model() -> None`、`supports_mask() -> False`
  - `ModelExecutorFactory.create_executor(cfg, device)` 在 `provider=="restoration"` 时返回 `RestorationExecutor`
  - `get_supported_providers()` 含 `"restoration"`

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/test_restoration_executor.py`：

```python
from pathlib import Path

import pytest


def test_factory_creates_restoration_executor():
    from core.model_executor import ModelExecutorFactory
    from core.restoration_executor import RestorationExecutor

    cfg = {
        "id": "denoise-scunet-psnr",
        "provider": "restoration",
        "weight_filename": "scunet_color_real_psnr.pth",
    }
    exe = ModelExecutorFactory.create_executor(cfg, "cpu")
    assert isinstance(exe, RestorationExecutor)
    assert exe.supports_mask() is False


def test_supported_providers_includes_restoration():
    from core.model_executor import ModelExecutorFactory

    assert "restoration" in ModelExecutorFactory.get_supported_providers()


def test_load_model_missing_weight_raises(tmp_path, monkeypatch):
    from core import restoration_executor as mod
    from core.restoration_executor import RestorationExecutor

    missing = tmp_path / "missing.pth"
    monkeypatch.setattr(mod, "resolve_model_cache_path", lambda cfg: missing)

    exe = RestorationExecutor(
        {"id": "denoise-scunet-psnr", "provider": "restoration", "weight_filename": "missing.pth"},
        "cpu",
    )
    with pytest.raises(FileNotFoundError, match="请先在模型管理中下载"):
        exe.load_model()


def test_execute_without_load_uses_missing_weight(tmp_path, monkeypatch):
    from core import restoration_executor as mod
    from core.restoration_executor import RestorationExecutor
    import numpy as np

    missing = tmp_path / "missing.pth"
    monkeypatch.setattr(mod, "resolve_model_cache_path", lambda cfg: missing)
    exe = RestorationExecutor(
        {"id": "denoise-scunet-psnr", "provider": "restoration", "weight_filename": "missing.pth"},
        "cpu",
    )
    with pytest.raises(FileNotFoundError):
        exe.execute(np.zeros((8, 8, 3), dtype=np.uint8), tile=0)
```

- [ ] **Step 2: 跑测试确认失败**

Run:

```bash
cd /Users/hikari/Documents/Git/HiImage/backend && rtk pytest tests/test_restoration_executor.py -v
```

Expected: FAIL，`ModuleNotFoundError: No module named 'core.restoration_executor'` 或 `未知的 provider`。

- [ ] **Step 3: 实现执行器、Factory、依赖**

在 `backend/requirements.txt` 的 `# ---- AI models` 段、`facexlib>=0.3.0` 之后增加：

```
spandrel>=0.4.0
spandrel-extra-arches>=0.2.0
```

创建 `backend/core/restoration_executor.py`：

```python
"""同分辨率图像复原执行器 —— 用 spandrel 加载官方 .pth（SCUNet / NAFNet / SwinIR）。"""
from __future__ import annotations

from typing import Any

import numpy as np
import torch

from core.model_executor import BaseModelExecutor
from core.paths import resolve_model_cache_path

_EXTRA_ARCHES_INSTALLED = False


def _ensure_extra_arches() -> None:
    global _EXTRA_ARCHES_INSTALLED
    if _EXTRA_ARCHES_INSTALLED:
        return
    import spandrel_extra_arches

    spandrel_extra_arches.install()
    _EXTRA_ARCHES_INSTALLED = True


def _torch_device(device: str) -> torch.device:
    if device == "cuda" and torch.cuda.is_available():
        return torch.device("cuda")
    if device == "mps" and torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


class RestorationExecutor(BaseModelExecutor):
    """spandrel ImageModelDescriptor 包装。输入/输出均为 RGB uint8，倍率 1。"""

    DEFAULT_TILE_PAD = 16

    def __init__(self, model_config: dict[str, Any], device: str):
        super().__init__(model_config, device)
        self._descriptor = None
        self._tile = 0

    def supports_mask(self) -> bool:
        return False

    def load_model(self) -> None:
        from spandrel import ModelLoader

        weight_path = resolve_model_cache_path(self.model_config)
        if not weight_path.exists():
            raise FileNotFoundError(
                f"模型权重文件不存在: {weight_path}\n请先在模型管理中下载"
            )
        _ensure_extra_arches()
        self._descriptor = ModelLoader().load_from_file(str(weight_path))
        torch_dev = _torch_device(self.device)
        self._descriptor.eval().to(torch_dev)

    def unload_model(self) -> None:
        self._descriptor = None
        if self.device == "cuda" and torch.cuda.is_available():
            torch.cuda.empty_cache()

    def execute(self, image: np.ndarray, **kwargs) -> np.ndarray:
        if image is None or image.size == 0:
            raise ValueError("输入图像为空")
        tile = kwargs.get("tile", 0)
        if tile is None:
            tile = 0
        if self._descriptor is None:
            self.load_model()

        from core.upscaler import Upscaler

        if tile == 0:
            tile = Upscaler._auto_tile(image, 1, self.device)
        self._tile = tile

        try:
            return self._infer(image, tile)
        except RuntimeError as e:
            msg = str(e).lower()
            if ("cuda" in str(e) or "mps" in str(e) or "alloc" in msg) and tile > 256:
                return self._infer(image, 256)
            raise RuntimeError(f"复原推理失败（显存不足或设备错误）: {e}") from e

    def _infer(self, image: np.ndarray, tile: int) -> np.ndarray:
        if tile <= 0:
            return self._infer_full(image)
        return self._infer_tiled(image, tile)

    def _infer_full(self, image: np.ndarray) -> np.ndarray:
        tensor = self._to_tensor(image)
        with torch.inference_mode():
            out = self._descriptor(tensor)
        return self._to_numpy(out, image.shape[:2])

    def _infer_tiled(self, image: np.ndarray, tile: int) -> np.ndarray:
        h, w = image.shape[:2]
        pad = self.DEFAULT_TILE_PAD
        result = np.zeros_like(image)
        for y in range(0, h, tile):
            for x in range(0, w, tile):
                y0, x0 = y, x
                y1, x1 = min(y + tile, h), min(x + tile, w)
                py0, px0 = max(0, y0 - pad), max(0, x0 - pad)
                py1, px1 = min(h, y1 + pad), min(w, x1 + pad)
                crop = image[py0:py1, px0:px1]
                restored = self._infer_full(crop)
                cy0, cx0 = y0 - py0, x0 - px0
                cy1, cx1 = cy0 + (y1 - y0), cx0 + (x1 - x0)
                result[y0:y1, x0:x1] = restored[cy0:cy1, cx0:cx1]
        return result

    def _to_tensor(self, image: np.ndarray) -> torch.Tensor:
        t = torch.from_numpy(np.ascontiguousarray(image)).float().div_(255.0)
        t = t.permute(2, 0, 1).unsqueeze(0)
        return t.to(_torch_device(self.device))

    def _to_numpy(self, tensor: torch.Tensor, hw: tuple[int, int]) -> np.ndarray:
        t = tensor.detach().float().clamp_(0, 1).squeeze(0).permute(1, 2, 0).cpu().numpy()
        out = np.clip(np.rint(t * 255.0), 0, 255).astype(np.uint8)
        if out.shape[0] != hw[0] or out.shape[1] != hw[1]:
            import cv2

            out = cv2.resize(out, (hw[1], hw[0]), interpolation=cv2.INTER_AREA)
        return out
```

在 `backend/core/model_executor.py` 的 `create_executor` 里，`elif provider == "realesrgan":` 之前插入：

```python
        elif provider == "restoration":
            from .restoration_executor import RestorationExecutor
            return RestorationExecutor(model_config, device)
```

在 `get_supported_providers()` 的列表末尾（`facexlib` 之后）加上 `"restoration"`。

- [ ] **Step 4: 跑测试确认通过**

Run:

```bash
cd /Users/hikari/Documents/Git/HiImage/backend && rtk pytest tests/test_restoration_executor.py tests/test_restoration_paths.py tests/test_denoise_registry.py -v
```

Expected: PASS。不要调用真实 `ModelLoader` 加载权重。

- [ ] **Step 5: Commit**

```bash
rtk git add backend/core/restoration_executor.py backend/core/model_executor.py backend/requirements.txt backend/tests/test_restoration_executor.py
rtk git commit -m "$(cat <<'EOF'
feat: add spandrel RestorationExecutor

EOF
)"
```

---

### Task 4: 降噪路由按 provider 分发

**Files:**
- Modify: `backend/app/routers/denoise.py`
- Modify: `backend/tests/test_denoise_router_helpers.py`

**Interfaces:**
- Consumes: `RestorationExecutor`、`Upscaler`、`get_model`、`get_models_for_mode`
- Produces:
  - `_DENOISE_PROVIDERS = frozenset({"restoration", "realesrgan"})`
  - `_require_denoise_model(model_id: str) -> dict`：tags 含 `denoise` 且 provider ∈ 上面集合
  - `list_denoise_models() -> dict`：每条含 `suitable_for`
  - `create_denoise_backend(model: str, device: str, tile: int) -> DenoiseBackend`，协议：`load() -> None`、`run(image: np.ndarray) -> np.ndarray`、`close() -> None`
  - `DenoiseRequest.model` / `BatchDenoiseRequest.model` 默认 `"denoise-scunet-psnr"`

- [ ] **Step 1: 改失败测试**

在 `backend/tests/test_denoise_router_helpers.py` 里：

1. 把 `test_list_denoise_models_groups` 换成：

```python
def test_list_denoise_models_groups():
    from app.routers.denoise import list_denoise_models

    data = list_denoise_models()
    labels = [g["label"] for g in data["groups"]]
    assert labels == [
        "照片 / 截图",
        "观感更干净",
        "相机 ISO",
        "去压缩伪影",
        "快速清晰化",
    ]
    ids = [m["id"] for g in data["groups"] for m in g["models"]]
    assert ids == [
        "denoise-scunet-psnr",
        "denoise-scunet-gan",
        "denoise-nafnet-sidd",
        "denoise-swinir-jpeg",
        "denoise-general",
        "denoise-anime",
    ]
    first = data["groups"][0]["models"][0]
    assert first["suitable_for"] == "盲去噪（压缩颗粒 + 一般照片噪声，保纹理）"
    assert "scale" not in first
    assert "outscale" not in first
```

2. 在 `test_require_denoise_model_ok` 之后增加：

```python
def test_require_denoise_model_accepts_scunet():
    from app.routers.denoise import _require_denoise_model

    cfg = _require_denoise_model("denoise-scunet-psnr")
    assert cfg["id"] == "denoise-scunet-psnr"
    assert cfg["provider"] == "restoration"
```

3. 把 `test_batch_auto_tile_does_not_preload_before_first_image` 和 `test_batch_imwrite_false_marks_item_error`、`test_single_imwrite_false_raises_original_error` 的 `BatchDenoiseRequest` / `DenoiseRequest` 都加上 `model="denoise-general"`，避免默认改成 scunet 后误走 restoration。

4. 文件末尾增加：

```python
def test_create_denoise_backend_restoration(monkeypatch):
    from app.routers import denoise
    from core.restoration_executor import RestorationExecutor

    backend = denoise.create_denoise_backend(
        "denoise-scunet-psnr", device="cpu", tile=0
    )
    assert isinstance(backend._executor, RestorationExecutor)


def test_create_denoise_backend_realesrgan():
    from app.routers.denoise import create_denoise_backend
    from core.upscaler import Upscaler

    backend = create_denoise_backend("denoise-general", device="cpu", tile=0)
    assert isinstance(backend._upscaler, Upscaler)
```

- [ ] **Step 2: 跑测试确认失败**

Run:

```bash
cd /Users/hikari/Documents/Git/HiImage/backend && rtk pytest tests/test_denoise_router_helpers.py -v
```

Expected: FAIL，分组仍是「通用降噪」或 `_require_denoise_model` 拒绝 scunet，或没有 `create_denoise_backend`。

- [ ] **Step 3: 改路由**

在 `backend/app/routers/denoise.py`：

把模块 docstring 改成：`图片降噪路由：restoration（spandrel）或 realesrgan（outscale 由 YAML 锁定）。`

把 `list_denoise_models` 里 `append` 的字典增加 `"suitable_for": m.get("suitable_for", "")`。

把 `_require_denoise_model` 里 provider 判断换成：

```python
    provider = cfg.get("provider")
    if provider not in ("restoration", "realesrgan") or "denoise" not in cfg.get("tags", []):
        raise HTTPException(
            status_code=400,
            detail=f"模型 {model_id} 不属于 denoise 模式",
        )
```

把两个 Request 的 `model: str = "denoise-general"` 改成 `model: str = "denoise-scunet-psnr"`。

在 `_run_denoise` 之前插入：

```python
class DenoiseBackend:
    """统一 load / run / close，供单张与批量共用。"""

    def __init__(self, upscaler=None, executor=None, tile: int = 0):
        self._upscaler = upscaler
        self._executor = executor
        self._tile = tile

    def load(self) -> None:
        if self._upscaler is not None:
            if self._tile > 0:
                self._upscaler._current_tile = self._tile
            self._upscaler._ensure_model_loaded()
        elif self._executor is not None:
            self._executor.load_model()

    def run(self, image: np.ndarray) -> np.ndarray:
        if self._upscaler is not None:
            return _match_input_size(self._upscaler.upscale(image), image)
        return _match_input_size(
            self._executor.execute(image, tile=self._tile), image
        )

    def close(self) -> None:
        if self._executor is not None:
            self._executor.unload_model()
        self._upscaler = None
        self._executor = None


def create_denoise_backend(model: str, device: str, tile: int) -> DenoiseBackend:
    from core.model_registry import get_model
    from core.restoration_executor import RestorationExecutor
    from core.upscaler import Upscaler

    cfg = get_model(model)
    if cfg.get("provider") == "realesrgan":
        return DenoiseBackend(
            upscaler=Upscaler(model_name=model, device=device, tile=tile),
            tile=tile,
        )
    return DenoiseBackend(
        executor=RestorationExecutor(cfg, device),
        tile=tile,
    )
```

把 `_run_denoise` 整段换成：

```python
def _run_denoise(image: np.ndarray, model: str, device: str, tile: int) -> np.ndarray:
    backend = create_denoise_backend(model, device, tile)
    try:
        return backend.run(image)
    finally:
        backend.close()
        if device == "cuda":
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
```

把 `denoise_image_batch` 里 `from core.upscaler import Upscaler` 和 `shared = Upscaler(...)` / `shared._ensure_model_loaded()` 换成：

```python
    shared = create_denoise_backend(req.model, req.device, tile)
    if tile > 0:
        try:
            shared.load()
        except Exception as e:
            log_manager.error(f"批量降噪模型加载失败: {e}", source="denoise_batch")
            raise RuntimeError(f"模型加载失败: {e}") from e
```

把 `_process` 换成：

```python
        def _process(backend, img):
            return backend.run(img)
```

`run_in_executor` 仍传 `shared`。循环结束后 `del shared` 之前加 `shared.close()`。

`tile==0` 时不要在循环前 `load()`（保持现有「第一张图再加载」行为；`RestorationExecutor.execute` 会懒加载）。

- [ ] **Step 4: 跑测试确认通过**

Run:

```bash
cd /Users/hikari/Documents/Git/HiImage/backend && rtk pytest tests/test_denoise_router_helpers.py tests/test_denoise_registry.py tests/test_restoration_executor.py tests/test_upscaler_lookup.py -v
```

Expected: PASS。

- [ ] **Step 5: Commit**

```bash
rtk git add backend/app/routers/denoise.py backend/tests/test_denoise_router_helpers.py
rtk git commit -m "$(cat <<'EOF'
feat: dispatch denoise by restoration or realesrgan

EOF
)"
```

---

### Task 5: 前端分组与类型标注

**Files:**
- Modify: `frontend/src/renderer/types/models.ts`
- Modify: `frontend/src/renderer/components/ModelSelect/ModelSelect.tsx`
- Modify: `frontend/src/renderer/stores/useSettingsStore.ts`
- Modify: `frontend/src/renderer/stores/useModelStore.ts`

**Interfaces:**
- Consumes: `GET /api/models/denoise` 的 `suitable_for`；`defaults.denoise`
- Produces: `ModelOption.suitableFor?: string`；`denoiseModel` 默认 `'denoise-scunet-psnr'`；下拉第二行显示类型

本仓库前端无单测。以 TypeScript 能编译、字段与 Task 4 一致为验收。

- [ ] **Step 1: 改类型与 ModelSelect**

`frontend/src/renderer/types/models.ts` 的 `ModelOption` 增加：

```typescript
  /** 适合的噪声 / 场景类型（降噪等） */
  suitableFor?: string
```

`frontend/src/renderer/components/ModelSelect/ModelSelect.tsx` 的本地 `ModelOption` 同样增加 `suitableFor?: string`。

在 `renderOption` 里，把名称那一行：

```tsx
        <span className="flex-1 truncate">{opt.label}</span>
```

换成：

```tsx
        <span className="flex-1 min-w-0">
          <span className="block truncate">{opt.label}</span>
          {(opt.suitableFor || opt.description) && (
            <span className="block truncate text-[10px] text-fg-secondary">
              {opt.suitableFor || opt.description}
            </span>
          )}
        </span>
```

选项行 `py-1.5` 保持不变。

- [ ] **Step 2: 改 store 默认值**

`useSettingsStore.ts`：`denoiseModel: 'denoise-general'` 改成 `'denoise-scunet-psnr'`；`loadDefaults` 里 `defaults.denoise ?? 'denoise-general'` 改成 `'denoise-scunet-psnr'`。

`useModelStore.ts`：把 `DEFAULT_DENOISE_GROUPS` 换成：

```typescript
const DEFAULT_DENOISE_GROUPS: ModelGroup[] = [
  {
    label: '照片 / 截图',
    options: [
      { value: 'denoise-scunet-psnr', label: 'SCUNet 盲去噪（推荐）', description: '盲去噪，适合照片与截图', suitableFor: '盲去噪（压缩颗粒 + 一般照片噪声，保纹理）' },
    ],
  },
  {
    label: '观感更干净',
    options: [
      { value: 'denoise-scunet-gan', label: 'SCUNet 观感去噪', description: '更干净，可能略抹纹理', suitableFor: '更干净，可能略抹纹理' },
    ],
  },
  {
    label: '相机 ISO',
    options: [
      { value: 'denoise-nafnet-sidd', label: 'NAFNet 相机降噪', description: '手机 / 相机传感器噪声', suitableFor: '手机 / 相机传感器噪声' },
    ],
  },
  {
    label: '去压缩伪影',
    options: [
      { value: 'denoise-swinir-jpeg', label: 'SwinIR 去压缩', description: 'JPEG 色块与振铃', suitableFor: 'JPEG 色块 / 振铃（按常见网图质量 Q≈40）' },
    ],
  },
  {
    label: '快速清晰化',
    options: [
      { value: 'denoise-general', label: '快速清晰化·通用', description: '超分权重复用，偏锐化', suitableFor: '要锐化或不想再下大模型' },
      { value: 'denoise-anime', label: '快速清晰化·动漫', description: '超分权重复用，线条更锐', suitableFor: '动漫线稿锐化' },
    ],
  },
]
```

`loadModels` 解析 denoise 时把类型改成含 `suitable_for`，并写入 `suitableFor`：

```typescript
        denoiseGroups = denoiseData.groups.map(
          (g: { label: string; models: Array<{ id: string; name: string; description: string; suitable_for?: string }> }) => ({
            label: stripDecorators(g.label),
            options: g.models.map((m) => ({
              value: m.id,
              label: m.name,
              description: m.description,
              suitableFor: m.suitable_for,
            })),
          })
        )
```

- [ ] **Step 3: 确认字段**

检查：`denoiseModel` 默认 scunet-psnr；`DEFAULT_DENOISE_GROUPS` 五组；`ModelSelect` 展示 `suitableFor`。不要改 `useBackendAPI` 的路径或 `outscale`。

- [ ] **Step 4: Commit**

```bash
rtk git add frontend/src/renderer/types/models.ts frontend/src/renderer/components/ModelSelect/ModelSelect.tsx frontend/src/renderer/stores/useSettingsStore.ts frontend/src/renderer/stores/useModelStore.ts
rtk git commit -m "$(cat <<'EOF'
feat: show denoise model type labels in selector

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
- Consumes: 已落地的六条模型与 RestorationExecutor
- Produces: PRD 模型表、架构推理层、README 使用说明与功能一览

- [ ] **Step 1: 改 PRD**

`docs/PRD.md`：

1. 「2.4 图片降噪」功能描述里「底层复用 Real-ESRGAN…不新增权重文件」改成：「默认 SCUNet 盲去噪；另有 NAFNet（相机）、SwinIR（JPEG）、以及 Real-ESRGAN 快速清晰化。」
2. D2 描述改成：`按噪声类型分组：盲去噪 / 观感 / 相机 ISO / 去压缩 / 快速清晰化`
3. 「支持的模型」表换成：

```markdown
| 类别 | 模型 ID | 适合 | 大小 |
|------|---------|------|------|
| 照片 / 截图 | denoise-scunet-psnr（推荐） | 盲去噪，保纹理 | 72MB |
| 观感更干净 | denoise-scunet-gan | 更干净，可能抹纹理 | 72MB |
| 相机 ISO | denoise-nafnet-sidd | 手机 / 相机噪声 | 117MB |
| 去压缩伪影 | denoise-swinir-jpeg | JPEG 色块 / 振铃 | 98MB |
| 快速清晰化 | denoise-general / denoise-anime | 超分权重复用，偏锐化 | 5MB / 18MB |
```

4. 术语表「图片降噪」行改成：`保持分辨率的 AI 去噪；默认 SCUNet，清晰化档复用 Real-ESRGAN outscale=1`
5. 里程碑 M9 后增加：`| M10 | 降噪专用模型 — SCUNet / NAFNet / SwinIR + 类型标注 | ⏳ 本计划 |`

- [ ] **Step 2: 改架构文档与 README**

`docs/SYSTEM_ARCHITECTURE.md`：

- `core/` 树在 `upscaler.py` 下增加：`├── restoration_executor.py  # spandrel（SCUNet / NAFNet / SwinIR）`
- 4.1 分发增加：`降噪：restoration → RestorationExecutor；realesrgan → Upscaler`
- 技术栈「推理」一行补上 `spandrel`

`README.md`：

- 功能一览降噪行改成：`保持分辨率去噪；默认 SCUNet，可按类型选相机 / JPEG / 快速清晰化`
- 「图片降噪」使用指南第 2 步改成：

```markdown
2. 按噪声类型选模型（默认 SCUNet 盲去噪；首次约 72 MB。快速清晰化可复用已有超分权重）
```

- 环境要求 Python 行保持「3.9+（推荐 3.11）」；在降噪节末加一句：`专用复原模型依赖 spandrel，需要 Python 3.10+。`

- [ ] **Step 3: 对照规格扫一遍**

Restormer 注释仍在；超分清晰化 tags 仍是 `[upscale]`；本任务只改文档。

- [ ] **Step 4: Commit**

```bash
rtk git add docs/PRD.md docs/SYSTEM_ARCHITECTURE.md README.md
rtk git commit -m "$(cat <<'EOF'
docs: document restoration denoise models

EOF
)"
```

---

## 手动验收（全部 Task 完成后）

1. `cd backend && rtk pytest tests/test_denoise_registry.py tests/test_restoration_paths.py tests/test_restoration_executor.py tests/test_denoise_router_helpers.py tests/test_upscaler_lookup.py -v` 全绿。
2. 降噪页下拉五组六条，每条第二行有适合类型；默认 SCUNet 盲去噪。
3. 超分页仍有「清晰化增强」，没有 `denoise-*`。
4. 未下载 SCUNet 时按钮走下载队列，不能空跑。
5. 选「快速清晰化·通用」且已有 `realesr-general-x4v3.pth` 时应显示已下载。
6. 不修 Restormer，不加载 NAFNet width64。
