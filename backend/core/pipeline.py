"""
Pipeline 处理链 - 串联 inpaint → postprocess → upscale

用法示例：
    from core.pipeline import Pipeline, PipelineConfig, InpaintStep, PostprocessStep, UpscaleStep

    config = PipelineConfig(
        inpaint=InpaintStep(model="wm_lama", device="mps", dilation=10),
        postprocess=PostprocessStep(method="poisson"),
        upscale=UpscaleStep(model="RealESRGAN_x4plus", enabled=False),
    )
    pipeline = Pipeline(config)
    result = pipeline.run(original_image, rois=[(x1,y1,x2,y2)])
"""
from __future__ import annotations

import numpy as np
from dataclasses import dataclass, field, fields
from typing import List, Optional, Tuple

from core.model_registry import get_default_model


# ──────────────────────────────────────────────────────────────
# Step 配置数据类
# ──────────────────────────────────────────────────────────────

@dataclass
class InpaintStep:
    model: str = field(default_factory=lambda: get_default_model("watermark_removal"))
    device: str = "mps"
    dilation: int = 10
    disable_nsfw: bool = False


@dataclass
class PostprocessStep:
    method: str = "none"       # none / poisson / gfpgan / lama_refine
    device: str = "mps"
    enabled: bool = True


@dataclass
class UpscaleStep:
    model: str = "RealESRGAN_x4plus"
    device: str = "mps"
    enabled: bool = False


@dataclass
class PipelineConfig:
    inpaint: InpaintStep = field(default_factory=InpaintStep)
    postprocess: PostprocessStep = field(default_factory=PostprocessStep)
    upscale: UpscaleStep = field(default_factory=UpscaleStep)


# ──────────────────────────────────────────────────────────────
# Pipeline 执行器
# ──────────────────────────────────────────────────────────────

class Pipeline:
    """
    串联处理链：inpaint → postprocess → upscale
    每个阶段输入/输出均为 RGB numpy array，可以任意跳过。
    """

    def __init__(self, config: PipelineConfig):
        self.config = config

    def run(
        self,
        original_rgb: np.ndarray,
        rois: Optional[List[Tuple[int, int, int, int]]] = None,
        mask: Optional[np.ndarray] = None,
        progress_callback=None,
    ) -> np.ndarray:
        """
        执行完整处理链。

        :param original_rgb:     原始图像（RGB numpy array）
        :param rois:             水印区域列表 [(x1,y1,x2,y2), ...]（与 mask 二选一）
        :param mask:             水印掩码（0/255 灰度图），与 rois 二选一；若同时提供，优先使用 mask
        :param progress_callback: 可选进度回调 callback(step_name: str, percent: int)
        :return:                 处理后的 RGB numpy array
        """
        cfg = self.config

        def _progress(name, pct):
            if progress_callback:
                progress_callback(name, pct)

        # ── Step 1: Inpainting ──────────────────────────────────
        _progress("inpaint", 10)
        print(f"[Pipeline] Step 1/3 - Inpaint: model={cfg.inpaint.model}")

        from core.model_registry import get_model
        from core.model_executor import ModelExecutorFactory
        
        model_config = get_model(cfg.inpaint.model)
        executor = ModelExecutorFactory.create_executor(model_config, cfg.inpaint.device)

        if mask is not None:
            # 使用外部传入的 mask
            inpaint_mask = mask
            inpainted = executor.execute(original_rgb, mask=inpaint_mask, 
                                       dilation=cfg.inpaint.dilation, 
                                       disable_nsfw=cfg.inpaint.disable_nsfw)
        elif rois:
            # 从 ROI 生成 mask
            h, w = original_rgb.shape[:2]
            inpaint_mask = np.zeros((h, w), dtype=np.uint8)
            for (x1, y1, x2, y2) in rois:
                inpaint_mask[y1:y2, x1:x2] = 255
            inpainted = executor.execute(original_rgb, mask=inpaint_mask)
        else:
            executor.unload_model()
            raise ValueError("[Pipeline] 必须提供 rois 或 mask 之一")

        executor.unload_model()
        _progress("inpaint", 50)

        # ── Step 2: 后处理 ───────────────────────────────────────
        result = inpainted
        post_method = cfg.postprocess.method if cfg.postprocess.enabled else "none"

        if post_method and post_method != "none":
            _progress("postprocess", 55)
            print(f"[Pipeline] Step 2/3 - Postprocess: method={post_method}")

            from core.background_fixer import fix_background
            result = fix_background(
                original_rgb=original_rgb,
                inpainted_rgb=inpainted,
                mask=inpaint_mask,
                method=post_method,
                device=cfg.postprocess.device,
            )
            _progress("postprocess", 75)
        else:
            print("[Pipeline] Step 2/3 - Postprocess: 跳过")

        # ── Step 3: 超分辨率 ─────────────────────────────────────
        if cfg.upscale.enabled:
            _progress("upscale", 80)
            print(f"[Pipeline] Step 3/3 - Upscale: model={cfg.upscale.model}")

            from core.upscaler import Upscaler
            upscaler = Upscaler(model_name=cfg.upscale.model, device=cfg.upscale.device)
            result = upscaler.upscale(result)
            _progress("upscale", 95)
        else:
            print("[Pipeline] Step 3/3 - Upscale: 跳过")

        _progress("done", 100)
        print("[Pipeline] 处理完成")
        return result
