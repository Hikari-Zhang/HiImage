"""
Real-ESRGAN 超分辨率执行器

包装现有 core/upscaler.py 中的 Upscaler 类。
"""
import numpy as np
from typing import Dict, Any
from .model_executor import BaseModelExecutor


class RealESRGANExecutor(BaseModelExecutor):
    """
    Real-ESRGAN 超分辨率执行器

    支持 2x/4x 放大，多种场景优化。
    """

    def __init__(self, model_config: Dict[str, Any], device: str):
        super().__init__(model_config, device)
        self._upscaler = None

    def load_model(self) -> None:
        """加载 Real-ESRGAN 模型"""
        from .upscaler import Upscaler

        model_name = self.model_config.get("iopaint_model_id", self.model_config["id"])
        self._upscaler = Upscaler(model_name=model_name, device=self.device)

    def execute(self, image: np.ndarray, **kwargs) -> np.ndarray:
        """
        执行超分辨率推理（不需要 mask）

        :param image: 输入图像（RGB, uint8）
        :return: 超分辨率后的图像（RGB, uint8）
        """
        if self._upscaler is None:
            self.load_model()

        return self._upscaler.upscale(image)

    def unload_model(self) -> None:
        """卸载模型，释放显存"""
        if self._upscaler is not None:
            # Upscaler 内部会处理模型卸载
            self._upscaler = None

    def supports_mask(self) -> bool:
        """Real-ESRGAN 不需要 mask"""
        return False
