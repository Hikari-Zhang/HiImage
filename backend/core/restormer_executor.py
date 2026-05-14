"""
Restormer 图像复原执行器

Restormer 是 Image-to-Image 复原模型，不需要 mask。
支持任务：去噪（denoise）、去模糊（deblur）、去雨（derain）、去雾（dehaze）。

输入：RGB 图像（numpy array, uint8）
输出：RGB 图像（numpy array, uint8）
"""
import numpy as np
import torch
from typing import Dict, Any
from .model_executor import BaseModelExecutor


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

        # 更新任务类型（如果提供）
        task_type = kwargs.get("task_type", self._task_type)
        if task_type != self._task_type:
            self._task_type = task_type
            # 重新加载模型（不同任务可能需要不同权重）
            self.unload_model()
            self.load_model()

        # 转换输入格式
        input_tensor = self._numpy_to_tensor(image)
        
        # 将输入张量移动到模型所在设备
        if self.device == "mps" and torch.backends.mps.is_available():
            input_tensor = input_tensor.to("mps")
        elif self.device == "cuda" and torch.cuda.is_available():
            input_tensor = input_tensor.to("cuda")

        if self._pipeline is None:
            raise RuntimeError("模型未加载，请先调用 load_model()")

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
            elif hasattr(torch, "mps") and torch.backends.mps.is_available():
                torch.mps.empty_cache()

    def supports_mask(self) -> bool:
        """Restormer 不需要 mask"""
        return False

    def _get_model_path(self) -> str:
        """获取模型权重路径（完整文件路径）"""
        import os
        from .paths import resolve_model_cache_path

        # 使用 paths.py 统一解析模型缓存路径
        full_path = str(resolve_model_cache_path(self.model_config))

        # 如果是目录，查找其中的 .pth 文件
        if os.path.isdir(full_path):
            for f in os.listdir(full_path):
                if f.endswith(".pth") or f.endswith(".pt"):
                    return os.path.join(full_path, f)
            raise FileNotFoundError(f"目录中未找到 .pth 文件: {full_path}")

        return full_path

    @staticmethod
    def _numpy_to_tensor(image: np.ndarray) -> torch.Tensor:
        """RGB numpy array → PyTorch tensor (B, C, H, W)"""
        import cv2
        import torch
        # RGB → BGR
        image_bgr = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
        # (H, W, C) → (B, C, H, W)
        tensor = torch.from_numpy(image_bgr).float() / 255.0
        tensor = tensor.permute(2, 0, 1).unsqueeze(0)
        return tensor

    @staticmethod
    def _tensor_to_numpy(tensor: torch.Tensor) -> np.ndarray:
        """PyTorch tensor (B, C, H, W) → RGB numpy array (H, W, C)"""
        import numpy as np
        import cv2
        import torch
        # (B, C, H, W) → (C, H, W)
        tensor = tensor.squeeze(0).permute(1, 2, 0).cpu()
        # 限制到 [0, 1]
        tensor = torch.clamp(tensor, 0, 1)
        # 转换为 numpy
        image = (tensor.numpy() * 255.0).clip(0, 255).astype(np.uint8)
        # BGR → RGB
        return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
