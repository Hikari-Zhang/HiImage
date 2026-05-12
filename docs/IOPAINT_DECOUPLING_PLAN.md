# IOPaint 解耦与通用模型执行器框架设计方案

> **文档状态**：设计阶段  
> **创建时间**：2026-05-12  
> **目标分支**：`feat/decouple-iopaint`  
> **问题来源**：Restormer / NAFNet 等模型无法通过现有 IOPaint 硬编码框架调用

---

## 一、问题诊断

### 1.1 现状架构（IOPaint 强耦合）

```
API Layer (routers/inpaint.py, routers/synthesis.py, ...)
   │
   ├── 直接 import Inpainter / model_server
   │
   └── Inpainter 只认识两种模式：
         ├── CLI 模式（lama / migan / zits ...）
         └── Server 模式（SD / AnyText / PowerPaint ...）
```

**结论**：整个 `core/inpainter.py` + `core/model_server.py` 完全围绕 IOPaint 设计，
任何非 IOPaint 模型（Restormer / NAFNet / 自研模型）都无法接入。

---

### 1.2 为什么 Restormer / NAFNet "用不了"

| 限制 | 说明 |
|------|------|
| **强制要求 mask** | `Inpainter.remove_watermark()` 必须传入 ROI 或 mask，但 Restormer / NAFNet 是 Image-to-Image 复原模型，**不需要 mask** |
| **只调用 IOPaint** | `Inpainter._run_cli()` / `model_server.inpaint_via_server()` 硬编码调用 `iopaint` 子进程 |
| **models.yaml 无法驱动** | 即使添加了 Restormer 配置，也没有对应的执行器去加载和运行它 |
| **provider 未定义** | `constants.Provider` 没有 `restormer` / `nafnet`，分发逻辑无法识别 |

---

### 1.3 已有的"绕过"案例（散落各处，不可扩展）

| 模型 | 调用方式 | 位置 |
|------|-----------|------|
| FLUX.1-Fill | 直接调用 diffusers | `core/flux_filler.py` |
| InstructPix2Pix | 直接调用 diffusers | `core/instruction_editor.py` |
| Real-ESRGAN | 直接调用 realesrgan 库 | `core/upscaler.py` |

这些都是**特例**，没有统一抽象，新增模型需要改多处代码。

---

## 二、设计目标

1. **解耦**：API 层不再依赖 IOPaint，模型调用通过统一接口
2. **可扩展**：新增模型只需实现执行器接口 + 在 Factory 注册
3. **向后兼容**：现有 IOPaint 模型（LaMa / SD / AnyText 等）功能不受影响
4. **统一语义**：所有模型执行器遵循相同的输入输出规范（RGB numpy array in → RGB numpy array out）

---

## 三、架构设计

### 3.1 目标架构（解耦后）

```
┌─────────────────────────────────────────┐
│          API Layer (routers/)          │
│   不再直接 import Inpainter          │
│   改为调用 ModelExecutorFactory       │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────────┐
│      ModelExecutorFactory              │
│   根据 provider 分发到对应执行器      │
└──────────────┬───────────────────────┘
               │
    ┌──────────┼──────────┐
    ▼          ▼          ▼
┌────────┐ ┌────────┐ ┌────────────┐
│IOPaint │ │Diffuse│ │Custom     │
│Executo│ │rExecu-│ │Executor   │
│r       │ │tor    │ │(Restormer)│
│        │ │       │ │(NAFNet)   │
└────────┘ └────────┘ └────────────┘
```

### 3.2 执行器接口设计

```python
# core/model_executor.py

from abc import ABC, abstractmethod
import numpy as np
from typing import Optional, Dict, Any

class BaseModelExecutor(ABC):
    """
    所有模型执行器的抽象基类。
    
    设计约定：
    - 输入：RGB numpy array（uint8）
    - 输出：RGB numpy array（uint8）
    - 如需 mask：通过 **kwargs 透传，由具体执行器决定是否使用
    """

    def __init__(self, model_config: Dict[str, Any], device: str):
        self.model_config = model_config
        self.device = device

    @abstractmethod
    def load_model(self) -> None:
        """加载模型到内存（懒加载，首次执行时调用）"""
        pass

    @abstractmethod
    def execute(self, image: np.ndarray, **kwargs) -> np.ndarray:
        """
        执行模型推理
        
        :param image: 输入图像（RGB numpy array, uint8）
        :param kwargs: 模型特定参数，例如：
                      - mask: np.ndarray（Inpainting 模型需要）
                      - prompt: str（SD 类模型需要）
                      - roi: list（区域限定）
        :return: 处理后的图像（RGB numpy array, uint8）
        """
        pass

    @abstractmethod
    def unload_model(self) -> None:
        """从内存卸载模型，释放显存"""
        pass

    def supports_mask(self) -> bool:
        """该执行器是否需要 mask（供 API 层判断参数合法性）"""
        return False
```

---

## 四、实施步骤

### Step 1：创建 `core/model_executor.py`（框架核心）

**文件**：`backend/core/model_executor.py`

**内容**：
- 定义 `BaseModelExecutor` 抽象基类
- 实现 `ModelExecutorFactory` 工厂类
- 支持从 `models.yaml` 的 `provider` 字段自动分发

**伪代码**：

```python
# core/model_executor.py

from typing import Dict, Any
import numpy as np

class ModelExecutorFactory:
    """根据 provider 创建对应的执行器"""

    @staticmethod
    def create_executor(model_config: Dict[str, Any], device: str):
        provider = model_config.get("provider")

        if provider == "IOPaint":
            from .iopaint_executor import IOPaintExecutor
            return IOPaintExecutor(model_config, device)

        elif provider == "diffusers":
            from .diffusers_executor import DiffusersExecutor
            return DiffusersExecutor(model_config, device)

        elif provider == "HiImage":
            from .hiimage_executor import HiImageExecutor
            return HiImageExecutor(model_config, device)

        elif provider == "restormer":
            from .restormer_executor import RestormerExecutor
            return RestormerExecutor(model_config, device)

        elif provider == "nafnet":
            from .nafnet_executor import NAFNetExecutor
            return NAFNetExecutor(model_config, device)

        elif provider == "realesrgan":
            from .upscaler import Upscaler  # 复用现有实现
            return RealESRGANExecutor(model_config, device)

        else:
            raise ValueError(f"未知的 provider: {provider}")
```

---

### Step 2：封装 IOPaint 执行器（向后兼容）

**文件**：`backend/core/iopaint_executor.py`

**设计要点**：
- 内部复用现有 `Inpainter` 类，确保现有功能零改动
- 实现 `BaseModelExecutor` 接口，使 API 层可以统一调用

**伪代码**：

```python
# core/iopaint_executor.py

import numpy as np
from .model_executor import BaseModelExecutor
from typing import Optional, Dict, Any

class IOPaintExecutor(BaseModelExecutor):
    """
    IOPaint 模型执行器（封装原有 Inpainter）
    
    支持两种子模式：
    - CLI 模式（lama / migan / zits / mat / fcf / manga / cv2）
    - Server 模式（SD / AnyText / PowerPaint / SDXL）
    """

    def __init__(self, model_config: Dict[str, Any], device: str):
        super().__init__(model_config, device)
        self._inpainter = None
        self._iopaint_mode = model_config.get("iopaint_mode", "cli")

    def load_model(self) -> None:
        """IOPaint 采用懒加载，在 execute() 中初始化 Inpainter"""
        pass  # 懒加载，不在此处初始化

    def execute(self, image: np.ndarray, **kwargs) -> np.ndarray:
        """
        执行 IOPaint 推理
        
        :param image: 输入图像（RGB）
        :param kwargs: 支持以下参数：
                      - mask: np.ndarray（必需）
                      - rois: list（可选，与 mask 二选一）
                      - prompt: str（SD 类模型可选）
                      - negative_prompt: str（可选）
                      - sd_steps: int（可选）
                      - sd_guidance_scale: float（可选）
                      - sd_seed: int（可选）
        :return: 修复后的图像（RGB）
        """
        if self._inpainter is None:
            self._init_inpainter()

        mask = kwargs.get("mask")
        rois = kwargs.get("rois")

        if mask is not None:
            return self._inpainter.remove_watermark_with_mask(image, mask)
        elif rois is not None:
            return self._inpainter.remove_watermark(image, rois)
        else:
            raise ValueError("IOPaint 模型需要 mask 或 rois 参数")

    def _init_inpainter(self):
        """初始化 Inpainter（懒加载）"""
        from .inpainter import Inpainter

        self._inpainter = Inpainter(
            model_name=self.model_config["id"],
            device=self.device,
            dilation=self.model_config.get("dilation", 10),
            disable_nsfw=self.model_config.get("disable_nsfw", False),
        )

    def unload_model(self) -> None:
        """IOPaint Server 模式需要停止保活进程"""
        if self._inpainter is not None:
            # Server 模式：停止 IOPaint HTTP Server
            if hasattr(self._inpainter, '_server'):
                from .model_server import get_server
                get_server().stop()
            self._inpainter = None

    def supports_mask(self) -> bool:
        return True
```

---

### Step 3：实现 Restormer 执行器

**文件**：`backend/core/restormer_executor.py`

**说明**：
- Restormer 是 **Image-to-Image 复原模型**，**不需要 mask**
- 支持任务：去噪（denoise）、去模糊（deblur）、去雨（derain）、去雾（dehaze）
- 输入：RGB 图像；输出：RGB 图像

**伪代码**：

```python
# core/restormer_executor.py

import numpy as np
import torch
from .model_executor import BaseModelExecutor
from typing import Dict, Any

class RestormerExecutor(BaseModelExecutor):
    """
    Restormer 图像复原执行器
    
    支持任务类型（通过 models.yaml 的 task_type 参数配置）：
    - denoise：去噪
    - deblur：去模糊
    - derain：去雨滴
    - dehaze：去雾
    """

    def __init__(self, model_config: Dict[str, Any], device: str):
        super().__init__(model_config, device)
        self._pipeline = None
        self._task_type = model_config.get("task_type", "denoise")

    def load_model(self) -> None:
        """加载 Restormer 模型权重"""
        from .restormer_model import RestormerPipeline

        model_path = self._get_model_path()
        self._pipeline = RestormerPipeline.from_pretrained(
            model_path,
            device=self.device,
            task_type=self._task_type,
        )
        self._pipeline.eval()

    def execute(self, image: np.ndarray, **kwargs) -> np.ndarray:
        """
        执行 Restormer 推理（不需要 mask）
        
        :param image: 输入图像（RGB, uint8）
        :param kwargs: 支持以下参数：
                      - task_type: str（可选，覆盖默认任务类型）
        :return: 复原后的图像（RGB, uint8）
        """
        if self._pipeline is None:
            self.load_model()

        # 转换输入格式
        input_tensor = self._numpy_to_tensor(image)

        with torch.no_grad():
            output_tensor = self._pipeline(input_tensor)

        return self._tensor_to_numpy(output_tensor)

    def unload_model(self) -> None:
        """卸载模型，释放显存"""
        if self._pipeline is not None:
            del self._pipeline
            self._pipeline = None
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

    def supports_mask(self) -> bool:
        """Restormer 不需要 mask"""
        return False

    def _get_model_path(self) -> str:
        """获取模型权重路径"""
        import os
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(project_root, self.model_config.get("local_path", "models/restormer"))

    def _numpy_to_tensor(self, image: np.ndarray):
        """RGB numpy array → PyTorch tensor"""
        import torch
        image_bgr = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
        tensor = torch.from_numpy(image_bgr).float() / 255.0
        tensor = tensor.permute(2, 0, 1).unsqueeze(0)
        return tensor.to(self.device)

    def _tensor_to_numpy(self, tensor):
        """PyTorch tensor → RGB numpy array"""
        import numpy as np
        tensor = tensor.squeeze(0).permute(1, 2, 0).cpu()
        image = (tensor.numpy() * 255.0).clip(0, 255).astype(np.uint8)
        return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
```

---

### Step 4：实现 NAFNet 执行器

**文件**：`backend/core/nafnet_executor.py`

**说明**：
- NAFNet（Nonlinear Activation Free Network）是极快的图像去模糊模型
- 同样不需要 mask，Image-to-Image 复原

**伪代码**：

```python
# core/nafnet_executor.py

import numpy as np
import torch
from .model_executor import BaseModelExecutor
from typing import Dict, Any

class NAFNetExecutor(BaseModelExecutor):
    """
    NAFNet 图像去模糊执行器
    
    特点：
    - 极快速度（无非线性激活函数）
    - 适合实时预览场景
    """

    def __init__(self, model_config: Dict[str, Any], device: str):
        super().__init__(model_config, device)
        self._model = None

    def load_model(self) -> None:
        """加载 NAFNet 模型权重"""
        from .nafnet_model import NAFNetPipeline

        model_path = self._get_model_path()
        self._model = NAFNetPipeline.from_pretrained(
            model_path,
            device=self.device,
        )
        self._model.eval()

    def execute(self, image: np.ndarray, **kwargs) -> np.ndarray:
        """执行 NAFNet 推理（不需要 mask）"""
        if self._model is None:
            self.load_model()

        input_tensor = self._numpy_to_tensor(image)

        with torch.no_grad():
            output_tensor = self._model(input_tensor)

        return self._tensor_to_numpy(output_tensor)

    def unload_model(self) -> None:
        """卸载模型，释放显存"""
        if self._model is not None:
            del self._model
            self._model = None
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

    def supports_mask(self) -> bool:
        return False

    def _get_model_path(self) -> str:
        """获取模型权重路径"""
        import os
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(project_root, self.model_config.get("local_path", "models/nafnet"))

    def _numpy_to_tensor(self, image: np.ndarray):
        """RGB numpy array → PyTorch tensor"""
        import torch
        image_bgr = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
        tensor = torch.from_numpy(image_bgr).float() / 255.0
        tensor = tensor.permute(2, 0, 1).unsqueeze(0)
        return tensor.to(self.device)

    def _tensor_to_numpy(self, tensor):
        """PyTorch tensor → RGB numpy array"""
        import numpy as np
        tensor = tensor.squeeze(0).permute(1, 2, 0).cpu()
        image = (tensor.numpy() * 255.0).clip(0, 255).astype(np.uint8)
        return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
```

---

### Step 5：修改 API 路由层（使用新框架）

**目标文件**：
- `backend/app/routers/inpaint.py`
- `backend/app/routers/synthesis.py`
- `backend/app/routers/postprocess.py`

**修改模式**（以 `inpaint.py` 为例）：

**修改前**：
```python
@router.post("/inpaint")
async def inpaint_with_rois(req: InpaintRequest):
    from core.inpainter import Inpainter  # 硬编码
    
    inpainter = Inpainter(
        model_name=req.model,
        device=req.device,
        dilation=req.dilation,
    )
    return inpainter.remove_watermark(image, req.rois)
```

**修改后**：
```python
@router.post("/inpaint")
async def inpaint_with_rois(req: InpaintRequest):
    from core.model_registry import get_model
    from core.model_executor import ModelExecutorFactory
    
    # 根据 model_id 获取配置
    model_config = get_model(req.model)
    
    # 创建对应的执行器（自动根据 provider 分发）
    executor = ModelExecutorFactory.create_executor(
        model_config,
        req.device
    )
    
    # 统一调用接口
    image = decode_image(req.image)
    
    # 根据执行器类型，准备不同参数
    if executor.supports_mask():
        # IOPaint 类模型：需要 mask 或 rois
        mask = create_mask_from_rois(image.shape, req.rois)
        result = executor.execute(image, mask=mask)
    else:
        # Restormer / NAFNet 类模型：直接处理
        result = executor.execute(image)
    
    return {"image": encode_image(result)}
```

---

### Step 6：更新 `models.yaml`（添加 Restormer / NAFNet 配置）

在 `backend/core/models.yaml` 的 `models:` 列表末尾添加：

```yaml
  # ── 图像复原（image_restoration）────────────────────────────────────
  - id: restormer_denoise
    name: Restormer（去噪）
    provider: restormer  # 新增 provider
    tags: [image_restoration]
    description: Transformer-based 图像去噪，保留细节效果最佳
    recommended_for: 图像复原（去噪）
    requires_reference: false
    size_mb: 89
    badge: 推荐
    local_path: models/restormer/denoise.pth
    download_url: https://github.com/swz30/Restormer/releases/download/v1.0/denoise.pth
    supported_params:
      task_type:
        type: str
        default: denoise
        desc: 任务类型（denoise/deblur/derain/dehaze）
    features: [image_restoration]

  - id: restormer_deblur
    name: Restormer（去模糊）
    provider: restormer
    tags: [image_restoration]
    description: 图像去模糊，适合失焦/运动模糊场景
    recommended_for: 图像复原（去模糊）
    requires_reference: false
    size_mb: 89
    badge: ""
    local_path: models/restormer/deblur.pth
    download_url: https://github.com/swz30/Restormer/releases/download/v1.0/deblur.pth
    supported_params:
      task_type:
        type: str
        default: deblur
        desc: 任务类型
    features: [image_restoration]

  - id: nafnet_deblur
    name: NAFNet（去模糊·快速）
    provider: nafnet  # 新增 provider
    tags: [image_restoration]
    description: Nonlinear Activation Free Network，极快速度的去模糊
    recommended_for: 图像复原（去模糊·快速预览）
    requires_reference: false
    size_mb: 26
    badge: 快速
    local_path: models/nafnet/nafnet_l_8f8_g5.pth
    download_url: https://github.com/megvii-model/NAFNet/releases/download/v1.0/nafnet_l_8f8_g5.pth
    supported_params: {}
    features: [image_restoration]
```

同时在 `mode_groups:` 中添加图像复原模式：

```yaml
  - id: image_restoration
    name: 图像复原
    icon: droplet
    icon_name: Droplet
    description: 使用 AI 模型修复图像质量（去噪/去模糊/去雨/去雾）
    default_model: restormer_denoise
    needs_reference: false
    needs_roi: false
    needs_prompt: false
```

---

### Step 7：更新 `core/constants.py`（添加新 provider）

在 `Provider` 类中添加：

```python
class Provider:
    """models.yaml 中 provider 字段的合法值。"""
    REMBG:      ClassVar[str] = "rembg"
    IOPAINT:    ClassVar[str] = "IOPaint"
    DIFFUSERS:  ClassVar[str] = "diffusers"
    FACEXLIB:   ClassVar[str] = "facexlib"
    REALESRGAN: ClassVar[str] = "realesrgan"
    HIIMAGE:    ClassVar[str] = "HiImage"
    # 新增
    RESTORMER:  ClassVar[str] = "restormer"
    NAFNET:     ClassVar[str] = "nafnet"
```

---

## 五、文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `backend/core/model_executor.py` | **新增** | 框架核心：定义 `BaseModelExecutor` + `ModelExecutorFactory` |
| `backend/core/iopaint_executor.py` | **新增** | 封装现有 `Inpainter`，向后兼容 |
| `backend/core/restormer_executor.py` | **新增** | Restormer 执行器 |
| `backend/core/nafnet_executor.py` | **新增** | NAFNet 执行器 |
| `backend/core/restormer_model.py` | **新增** | Restormer 模型架构代码（内联） |
| `backend/core/nafnet_model.py` | **新增** | NAFNet 模型架构代码（内联） |
| `backend/core/constants.py` | **修改** | 添加 `RESTORMER` / `NAFNET` provider |
| `backend/core/models.yaml` | **修改** | 添加 Restormer / NAFNet 配置 + `image_restoration` 模式 |
| `backend/app/routers/inpaint.py` | **修改** | 使用 `ModelExecutorFactory` 替代直接 `import Inpainter` |
| `backend/app/routers/synthesis.py` | **修改** | 同上（如涉及模型调用） |
| `backend/app/routers/postprocess.py` | **修改** | 同上（如涉及模型调用） |
| `backend/core/inpainter.py` | **保留** | 作为 `IOPaintExecutor` 的内部实现，暂不删除 |

---

## 六、向后兼容性保证

1. **现有 IOPaint 模型完全不受影响**：
   - `IOPaintExecutor` 内部复用 `Inpainter`，所有参数透传
   - `models.yaml` 中现有配置的 `provider: IOPaint` 自动路由到 `IOPaintExecutor`

2. **API 响应格式不变**：
   - 所有执行器统一返回 RGB numpy array
   - API 层编码为 base64 返回，格式与之前完全一致

3. **前端无需改动**（除非要新增图像复原功能）：
   - 现有去水印、换装、合成等功能的 model_id 不变
   - 新增 `image_restoration` 模式需要前端配合添加入口

---

## 七、实施优先级

| 优先级 | 任务 | 说明 |
|--------|------|------|
| **P0** | 创建 `core/model_executor.py` | 框架核心，必须先完成 |
| **P0** | 封装 `IOPaintExecutor` | 确保现有功能不受影响 |
| **P1** | 实现 `RestormerExecutor` | 解决用户最紧急的问题 |
| **P1** | 实现 `NAFNetExecutor` | 提供快速预览选项 |
| **P1** | 修改 API 路由层 | 使用新的执行器框架 |
| **P2** | 更新 `models.yaml` | 添加 Restormer / NAFNet 配置 |
| **P2** | 更新 `constants.py` | 添加新 provider 常量 |
| **P2** | 清理 `inpainter.py` | 逐步迁移到新框架（可选，不急需） |

---

## 八、测试计划

### 8.1 单元测试

| 测试项 | 输入 | 预期输出 |
|--------|------|----------|
| `IOPaintExecutor` + LaMa | 带水印图像 + mask | 水印去除成功 |
| `IOPaintExecutor` + SD | 带水印图像 + mask + prompt | Inpainting 成功 |
| `RestormerExecutor` + 去噪 | 带噪点图像 | 去噪后图像 |
| `NAFNetExecutor` + 去模糊 | 模糊图像 | 去模糊后图像 |
| `ModelExecutorFactory` 分发 | 不同 provider 配置 | 返回正确的执行器实例 |

### 8.2 集成测试

1. **去水印功能**：用 LaMa / SD / AnyText 测试，确保与修改前行为一致
2. **图像复原功能**：用 Restormer / NAFNet 测试，验证可以成功调用
3. **模型热切换**：同一个请求中切换不同 provider 的模型，验证框架正确性

---

## 九、风险与缓解

| 风险 | 缓解措施 |
|------|----------|
| Restormer / NAFNet 模型架构代码复杂 | 参考官方实现，内联关键代码到 `restormer_model.py` / `nafnet_model.py` |
| 现有功能回归 | P0 任务确保 `IOPaintExecutor` 完全兼容，编写单元测试覆盖 |
| 性能下降（额外抽象层） | 执行器创建是轻量级操作，推理本身零开销 |
| 前端需要配合修改 | 新增 `image_restoration` 模式才需要，现有功能无需前端改动 |

---

## 十、后续扩展方向

1. **更多模型接入**：
   - 只需实现 `BaseModelExecutor` 接口，并在 Factory 注册
   - 例如：SwinIR、MPRNet、MIMO-UNet 等

2. **模型链（Pipeline）增强**：
   - 当前 `core/pipeline.py` 已经支持 Inpaint → Postprocess → Upscale
   - 可以扩展为更通用的模型链，例如：Restormer（去噪）→ LaMa（去水印）→ Real-ESRGAN（超分）

3. **模型并行**：
   - 多个独立模型可以同时加载到不同 GPU
   - 执行器框架可以扩展为支持多 GPU 调度

---

## 附录 A：关键设计决策

### A.1 为什么不用 `abc.ABC` 的 `__subclasshook__`？

- 希望显式实现接口，避免隐式契约
- 更符合 Python 社区对 ABC 的使用习惯

### A.2 为什么 `execute()` 不用固定参数，而是 `**kwargs`？

- 不同模型需要的参数差异很大（mask / prompt / task_type 等）
- `**kwargs` 更灵活，避免为每种模型定义不同的 `execute()` 签名

### A.3 为什么保留 `Inpainter` 不删除？

- 现有代码多处直接 `import Inpainter`，删除会破坏兼容性
- 改为由 `IOPaintExecutor` 内部调用，逐步迁移

---

## 附录 B：参考代码位置

| 功能 | 文件 |
|------|------|
| 现有 IOPaint 调用逻辑 | `backend/core/inpainter.py` |
| IOPaint Server 管理 | `backend/core/model_server.py` |
| 模型注册表 | `backend/core/model_registry.py` |
| 模型配置 YAML | `backend/core/models.yaml` |
| 常量定义 | `backend/core/constants.py` |
| API 路由（去水印） | `backend/app/routers/inpaint.py` |
| API 路由（合成） | `backend/app/routers/synthesis.py` |
| 现有 Real-ESRGAN 实现 | `backend/core/upscaler.py` |
| 现有 FLUX 实现 | `backend/core/flux_filler.py` |

---

*文档版本：v1.0*  
*最后更新：2026-05-12*
