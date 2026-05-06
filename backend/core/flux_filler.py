"""
FLUX.1-Fill-dev 图像修复管道

使用 black-forest-labs/FLUX.1-Fill-dev 实现文字引导的 Inpainting。
FLUX.1-Fill 是 FLUX.1-dev 系列的 inpainting 专用变体，具备：
  - 卓越的细节还原与语义一致性
  - 强文字理解力（超过 SD/SDXL 系列）
  - 支持任意分辨率（推荐 1024px）

模型大小：~23.8GB（bfloat16）
需要 diffusers>=0.30.0，VRAM 需求约 16-24GB（FP16/BF16），
通过 enable_model_cpu_offload() 可降至 ~8GB。
"""
from __future__ import annotations

import numpy as np
import cv2
from typing import Optional


_MODEL_ID = "black-forest-labs/FLUX.1-Fill-dev"
_INFER_SIZE = 1024    # 推理短边，FLUX 推荐 1024


class FluxFiller:
    """
    惰性加载的 FLUX.1-Fill-dev Inpainting 管道。
    每次调用 offload() 后释放 GPU 资源，可重复实例化。
    """

    def __init__(self, device: str = "cuda"):
        self.device = device
        self._pipe = None

    # ------------------------------------------------------------------

    def _load(self):
        if self._pipe is not None:
            return

        import torch
        from diffusers import FluxFillPipeline

        print(f"[FluxFiller] 加载 FLUX.1-Fill-dev → {self.device}")

        # FLUX 使用 bfloat16（CUDA）或 float32（CPU/MPS）
        dtype = torch.bfloat16 if self.device == "cuda" else torch.float32

        pipe = FluxFillPipeline.from_pretrained(
            _MODEL_ID,
            torch_dtype=dtype,
        )

        if self.device == "cuda":
            # enable_model_cpu_offload 将非活跃层 offload 到 CPU，
            # 可将峰值 VRAM 从 ~24GB 降至 ~16GB
            pipe.enable_model_cpu_offload()
        else:
            pipe = pipe.to(self.device)

        self._pipe = pipe
        print("[FluxFiller] 模型就绪")

    # ------------------------------------------------------------------

    def inpaint(
        self,
        image_rgb: np.ndarray,
        mask: np.ndarray,
        prompt: str,
        negative_prompt: str = "",
        num_inference_steps: int = 50,
        guidance_scale: float = 30.0,   # FLUX.1-Fill 推荐较高 CFG（7-30）
        seed: int = 42,
    ) -> np.ndarray:
        """
        在 mask 覆盖区域内，按 prompt 描述进行高质量 Inpainting。

        :param image_rgb:          原图（RGB uint8）
        :param mask:               掩码（uint8，255=待修复区域，0=保留区域）
        :param prompt:             文字引导描述
        :param negative_prompt:    负向提示（FLUX.1 原生不支持，预留兼容）
        :param num_inference_steps: 扩散步数（默认 50）
        :param guidance_scale:     CFG 强度（FLUX.1-Fill 推荐 7-30，默认 30）
        :param seed:               随机种子
        :return:                   修复后的 RGB 图像（与输入同分辨率）
        """
        self._load()

        import torch
        from PIL import Image as PILImage

        orig_h, orig_w = image_rgb.shape[:2]

        # 缩放到推理尺寸（短边对齐，8 的倍数）
        scale = _INFER_SIZE / min(orig_h, orig_w)
        infer_w = int(orig_w * scale) // 8 * 8
        infer_h = int(orig_h * scale) // 8 * 8

        pil_img  = PILImage.fromarray(image_rgb).resize((infer_w, infer_h), PILImage.LANCZOS)
        pil_mask = PILImage.fromarray(mask).resize((infer_w, infer_h), PILImage.NEAREST)

        # FLUX.1-Fill 不支持 negative_prompt，仅传 prompt 和 masked_image
        generator_device = "cpu"  # FLUX Generator 固定用 CPU 保证跨平台一致
        generator = torch.Generator(device=generator_device).manual_seed(seed)

        result_pil = self._pipe(
            prompt=prompt,
            image=pil_img,
            mask_image=pil_mask,
            num_inference_steps=num_inference_steps,
            guidance_scale=guidance_scale,
            generator=generator,
        ).images[0]

        # 还原到原始分辨率
        result_np = np.array(result_pil)
        if (result_np.shape[0], result_np.shape[1]) != (orig_h, orig_w):
            result_np = cv2.resize(result_np, (orig_w, orig_h), interpolation=cv2.INTER_LANCZOS4)

        return result_np

    # ------------------------------------------------------------------

    def offload(self):
        """推理完毕后释放 GPU 资源"""
        if self._pipe is None:
            return
        try:
            import torch
            self._pipe = self._pipe.to("cpu")
            del self._pipe
            if self.device == "cuda":
                torch.cuda.empty_cache()
            elif self.device == "mps":
                torch.mps.empty_cache()
        except Exception as e:
            print(f"[FluxFiller] offload 失败（忽略）: {e}")
        finally:
            self._pipe = None
