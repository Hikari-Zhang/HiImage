"""
HiImage 复合模型执行器

用于 HiImage provider（组合多个子模型的复合任务）。
例如：GroundingDINO + SAM + FLUX.1-Fill
"""
import numpy as np
from typing import Dict, Any
from .model_executor import BaseModelExecutor


class HiImageExecutor(BaseModelExecutor):
    """
    HiImage 复合模型执行器

    支持复合任务：
    - GDINO + SAM + FLUX.1-Fill（智能定位 + 高质量修复）
    - GDINO + SAM + SDXL（智能定位 + 高分辨率修复）
    - SegFormer + HSV 换色（快速换色）
    - SegFormer + SD 1.5（风格/纹理替换）
    """

    def __init__(self, model_config: Dict[str, Any], device: str):
        super().__init__(model_config, device)
        self._segmenter = None
        self._inpainter = None

    def load_model(self) -> None:
        """加载复合模型（按需加载子模型）"""
        # 根据模型 ID 决定加载哪些子模型
        model_id = self.model_config.get("id", "")

        if "grounded_sam" in model_id:
            # 加载 GroundingDINO + SAM
            from .grounded_segmenter import GroundedSegmenter
            self._segmenter = GroundedSegmenter()
            # 加载修复模型（FLUX 或 SDXL）
            if "flux" in model_id:
                from .flux_filler import FluxFiller
                self._inpainter = FluxFiller(device=self.device)
            elif "sdxl" in model_id:
                from .inpainter import Inpainter
                self._inpainter = Inpainter(
                    model_name="sdxl",
                    device=self.device,
                )
        elif "auto_segment" in model_id:
            # 加载 SegFormer
            from .human_parser import HumanParser
            self._segmenter = HumanParser()
            # 加载 SD 1.5（如果需要）
            if "sd15" in model_id:
                from .inpainter import Inpainter
                self._inpainter = Inpainter(
                    model_name="sd15",
                    device=self.device,
                )

    def execute(self, image: np.ndarray, **kwargs) -> np.ndarray:
        """
        执行复合推理

        :param image: 输入图像（RGB, uint8）
        :param kwargs: 支持以下参数：
                      - prompt: str（编辑指令）
                      - part: str（目标部位，如 "upper_clothes"）
        :return: 编辑后的图像（RGB, uint8）
        """
        if self._segmenter is None:
            self.load_model()

        prompt = kwargs.get("prompt", "")
        part = kwargs.get("part", "upper_clothes")

        # Step 1: 分割目标部位
        mask = self._segmenter.segment(image, part)

        if mask is None or mask.max() == 0:
            raise RuntimeError(f"未能在图像中检测到「{part}」区域")

        # Step 2: 修复（如果有 inpainter）
        if self._inpainter is not None:
            if hasattr(self._inpainter, 'inpaint'):
                # FluxFiller
                return self._inpainter.inpaint(
                    image_rgb=image,
                    mask=mask,
                    prompt=prompt,
                )
            else:
                # Inpainter
                return self._inpainter.remove_watermark_with_mask(image, mask)
        else:
            # 只有分割，没有修复（HSV 换色场景）
            from .color_replacer import replace_color_in_mask
            intent = kwargs.get("intent")
            if intent and hasattr(intent, 'value'):
                return replace_color_in_mask(image, mask, intent.value)
            else:
                raise ValueError("需要提供 intent 参数用于换色")

    def unload_model(self) -> None:
        """卸载模型，释放显存"""
        if self._segmenter is not None:
            del self._segmenter
            self._segmenter = None
        if self._inpainter is not None:
            if hasattr(self._inpainter, 'offload'):
                self._inpainter.offload()
            del self._inpainter
            self._inpainter = None

        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        elif hasattr(torch, "mps") and torch.backends.mps.is_available():
            torch.mps.empty_cache()

    def supports_mask(self) -> bool:
        """复合模型需要 mask（由分割生成）"""
        return True
