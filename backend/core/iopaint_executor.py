"""
IOPaint 模型执行器 —— 封装现有 Inpainter 类

向后兼容：
- 内部复用 Inpainter，所有现有功能不受影响
- 实现 BaseModelExecutor 接口，使 API 层可以统一调用
"""
import numpy as np
from typing import Dict, Any, Optional

from .model_executor import BaseModelExecutor


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
        """
        IOPaint 采用懒加载，在 execute() 中初始化 Inpainter。
        此方法保留以符合接口规范，实际不执行操作。
        """
        pass  # 懒加载，不在此处初始化

    def execute(self, image: np.ndarray, **kwargs) -> np.ndarray:
        """
        执行 IOPaint 推理

        :param image: 输入图像（RGB，uint8）
        :param kwargs: 支持以下参数：
                      - mask: np.ndarray（必需，0/255 灰度图）
                      - rois: list（可选，与 mask 二选一，格式 [(x1,y1,x2,y2), ...]）
                      - prompt: str（SD 类模型可选）
                      - negative_prompt: str（可选）
                      - sd_steps: int（可选）
                      - sd_guidance_scale: float（可选）
                      - sd_seed: int（可选）
                      - progress_callback: callable（可选，进度回调）
        :return: 修复后的图像（RGB，uint8）
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

    def unload_model(self) -> None:
        """卸载模型，释放显存（Server 模式需要停止保活进程）"""
        if self._inpainter is not None:
            # Server 模式：停止 IOPaint HTTP Server
            if self._iopaint_mode == "server":
                try:
                    from .model_server import get_server
                    get_server().stop()
                except Exception:
                    pass
            self._inpainter = None

    def supports_mask(self) -> bool:
        """IOPaint 模型需要 mask 或 rois"""
        return True

    def _init_inpainter(self) -> None:
        """初始化 Inpainter（懒加载）"""
        from .inpainter import Inpainter

        # 从 kwargs 或 model_config 获取参数
        progress_callback = None  # 可以从 kwargs 传入

        self._inpainter = Inpainter(
            model_name=self.model_config["id"],
            device=self.device,
            dilation=self.model_config.get("dilation", 10),
            disable_nsfw=self.model_config.get("disable_nsfw", False),
            iopaint_path=self.model_config.get("iopaint_path"),
            prompt=self.model_config.get("prompt", ""),
            negative_prompt=self.model_config.get("negative_prompt", ""),
            sd_steps=self.model_config.get("sd_steps", 50),
            sd_guidance_scale=self.model_config.get("sd_guidance_scale", 7.5),
            sd_seed=self.model_config.get("sd_seed", 42),
            progress_callback=progress_callback,
        )
