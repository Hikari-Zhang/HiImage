"""
Facexlib 执行器（GFPGAN 人脸增强）

包装 facexlib 库，用于 GFPGAN 人脸修复与增强。
"""
import numpy as np
from typing import Dict, Any
from .model_executor import BaseModelExecutor

# 使用统一路径管理器
from .paths import GFPGAN_HOME as _GFPGAN_HOME, resolve_model_path as _resolve


class FacexlibExecutor(BaseModelExecutor):
    """
    Facexlib 执行器（GFPGAN 人脸增强）

    主要用于换脸后的人脸细节增强。
    """

    def __init__(self, model_config: Dict[str, Any], device: str):
        super().__init__(model_config, device)
        self._enhancer = None

    def load_model(self) -> None:
        """加载 GFPGAN 模型"""
        try:
            from gfpgan import GFPGANer
            import torch

            model_path = self._get_model_path()
            self._enhancer = GFPGANer(
                model_path=model_path,
                upscale=self.model_config.get("upscale", 1),
                arch=self.model_config.get("arch", "clean"),
                channel_multiplier=self.model_config.get("channel_multiplier", 2),
                bg_upsampler=None,
            )
        except ImportError:
            raise RuntimeError("gfpgan 库未安装，请运行：pip install gfpgan")

    def execute(self, image: np.ndarray, **kwargs) -> np.ndarray:
        """
        执行 GFPGAN 人脸增强

        :param image: 输入图像（RGB, uint8）
        :param kwargs: 支持以下参数：
                      - rois: list（人脸区域列表，可选）
        :return: 增强后的图像（RGB, uint8）
        """
        if self._enhancer is None:
            self.load_model()

        import cv2

        # RGB → BGR
        image_bgr = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)

        # GFPGAN 增强
        _, _, enhanced = self._enhancer.enhance(
            image_bgr,
            has_aligned=False,
            only_center_face=False,
            paste_back=True,
        )

        if enhanced is None:
            # 未检测到人脸，返回原图
            return image

        # BGR → RGB
        return cv2.cvtColor(enhanced, cv2.COLOR_BGR2RGB)

    def unload_model(self) -> None:
        """卸载模型，释放显存"""
        if self._enhancer is not None:
            del self._enhancer
            self._enhancer = None

            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            elif hasattr(torch, "mps") and torch.backends.mps.is_available():
                torch.mps.empty_cache()

    def supports_mask(self) -> bool:
        """GFPGAN 不需要 mask，直接对人脸区域增强"""
        return False

    def _get_model_path(self) -> str:
        """获取模型权重路径"""
        import os
        # 优先使用配置中的 local_path
        local_path = self.model_config.get("local_path", "")
        if local_path:
            # 使用 paths.py 统一解析路径
            return str(_resolve(local_path))
        # 否则使用默认缓存路径
        weight_file = "GFPGANv1.4.pth"
        return os.path.join(str(_GFPGAN_HOME), weight_file)
