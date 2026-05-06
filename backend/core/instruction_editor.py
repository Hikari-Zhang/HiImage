"""
自由编辑模块（方案C）

支持四种图生图管道，按画质从高到低：
  - flux          : FLUX.1-dev Img2Img，2024 SOTA，最高画质，需 24GB+ VRAM
  - sdxl_img2img  : SDXL Img2Img，1024px 高分辨率，需 12GB+ VRAM
  - magicbrush    : MagicBrush（InstructPix2Pix 变体），指令跟随更精准，~5GB
  - instruct_pix2pix: 原版 timbrooks/instruct-pix2pix，SD 1.5 底座，~5GB

每个管道惰性加载，offload() 后释放 GPU/MPS 内存。
"""
from __future__ import annotations

import numpy as np
import cv2
from typing import Optional

# 模型 ID 常量
_MODEL_IDS = {
    "instruct_pix2pix": "timbrooks/instruct-pix2pix",
    "magicbrush":       "osunlp/MagicBrush",
    "sdxl_img2img":     "stabilityai/stable-diffusion-xl-base-1.0",
    "flux":             "black-forest-labs/FLUX.1-dev",
}

# 推理分辨率（短边）
_INFER_SIZE = {
    "instruct_pix2pix": 512,
    "magicbrush":       512,
    "sdxl_img2img":     1024,
    "flux":             1024,
}


class InstructionEditor:
    """
    惰性加载的自由编辑器。支持多种图生图后端，按 model_key 选择。

    :param model_key: 使用的后端模型键名（见 _MODEL_IDS）
    :param device:    推理设备（'mps' / 'cuda' / 'cpu'）
    """

    def __init__(self, model_key: str = "instruct_pix2pix", device: str = "mps"):
        if model_key not in _MODEL_IDS:
            raise ValueError(
                f"不支持的模型：{model_key}，可选：{list(_MODEL_IDS.keys())}"
            )
        self.model_key = model_key
        self.device = device
        self._pipe = None

    # ------------------------------------------------------------------
    # 内部加载
    # ------------------------------------------------------------------

    def _load(self):
        if self._pipe is not None:
            return

        import torch

        key = self.model_key
        model_id = _MODEL_IDS[key]
        print(f"[InstructionEditor] 加载 {key} 模型: {model_id} → {self.device}")

        if key in ("instruct_pix2pix", "magicbrush"):
            self._load_ip2p(model_id)
        elif key == "sdxl_img2img":
            self._load_sdxl(model_id)
        elif key == "flux":
            self._load_flux(model_id)

        print(f"[InstructionEditor] {key} 模型就绪")

    def _load_ip2p(self, model_id: str):
        """加载 InstructPix2Pix（IP2P）管道，兼容 IP2P fine-tunes（如 MagicBrush）"""
        import torch
        from diffusers import StableDiffusionInstructPix2PixPipeline

        dtype = torch.float16 if self.device == "cuda" else torch.float32
        pipe = StableDiffusionInstructPix2PixPipeline.from_pretrained(
            model_id,
            torch_dtype=dtype,
            safety_checker=None,
        )
        pipe = pipe.to(self.device)
        if self.device in ("cuda", "mps"):
            pipe.enable_attention_slicing()
        self._pipe = pipe

    def _load_sdxl(self, model_id: str):
        """加载 SDXL Img2Img 管道"""
        import torch
        from diffusers import StableDiffusionXLImg2ImgPipeline

        # SDXL 推荐 fp16（CUDA）；MPS/CPU 降为 fp32
        dtype = torch.float16 if self.device == "cuda" else torch.float32
        pipe = StableDiffusionXLImg2ImgPipeline.from_pretrained(
            model_id,
            torch_dtype=dtype,
            use_safetensors=True,
        )
        pipe = pipe.to(self.device)
        if self.device in ("cuda", "mps"):
            pipe.enable_attention_slicing()
            # CUDA 额外开启显存高效注意力（需 xformers 可选）
            try:
                pipe.enable_xformers_memory_efficient_attention()
            except Exception:
                pass
        self._pipe = pipe

    def _load_flux(self, model_id: str):
        """加载 FLUX.1-dev Img2Img 管道"""
        import torch
        from diffusers import FluxImg2ImgPipeline

        dtype = torch.bfloat16 if self.device == "cuda" else torch.float32
        pipe = FluxImg2ImgPipeline.from_pretrained(
            model_id,
            torch_dtype=dtype,
        )
        # FLUX 模型体积大，优先使用 CPU offload 减少 VRAM 压力
        if self.device == "cuda":
            try:
                pipe.enable_model_cpu_offload()
            except Exception:
                pipe = pipe.to(self.device)
        elif self.device == "mps":
            # MPS 暂不支持 enable_model_cpu_offload，直接加载
            pipe = pipe.to(self.device)
        else:
            pipe = pipe.to("cpu")

        self._pipe = pipe

    # ------------------------------------------------------------------
    # 公共编辑接口
    # ------------------------------------------------------------------

    def edit(
        self,
        image_rgb: np.ndarray,
        instruction: str,
        # IP2P / MagicBrush 专用
        num_inference_steps: int = 50,
        image_guidance_scale: float = 1.5,
        guidance_scale: float = 7.5,
        # SDXL Img2Img 专用
        strength: float = 0.6,
        # FLUX 专用（strength 同样适用）
        seed: int = 42,
    ) -> np.ndarray:
        """
        按照 instruction 对 image_rgb 进行语义编辑。

        :param image_rgb:            输入图像（RGB uint8）
        :param instruction:          编辑指令（中/英均支持）
        :param num_inference_steps:  扩散步数（IP2P 类有效，默认 50）
        :param image_guidance_scale: 原图保留权重（IP2P 类有效，越大越保留原图，默认 1.5）
        :param guidance_scale:       文字引导强度（默认 7.5）
        :param strength:             改变幅度（SDXL/FLUX img2img，0=不改 1=完全重绘，默认 0.6）
        :param seed:                 随机种子
        :return:                     编辑后的 RGB 图像（与输入同分辨率）
        """
        self._load()

        import torch
        from PIL import Image as PILImage

        orig_h, orig_w = image_rgb.shape[:2]
        infer_size = _INFER_SIZE[self.model_key]

        # 缩放到推理尺寸（短边对齐，保持宽高比，8 的倍数）
        scale = infer_size / min(orig_h, orig_w)
        infer_w = int(orig_w * scale) // 8 * 8
        infer_h = int(orig_h * scale) // 8 * 8
        pil_img = PILImage.fromarray(image_rgb).resize(
            (infer_w, infer_h), PILImage.LANCZOS
        )

        # 构建 generator
        generator_device = "cpu" if self.model_key == "flux" else self.device
        generator = torch.Generator(device=generator_device).manual_seed(seed)

        # 分发到对应管道
        if self.model_key in ("instruct_pix2pix", "magicbrush"):
            result_pil = self._pipe(
                prompt=instruction,
                image=pil_img,
                num_inference_steps=num_inference_steps,
                image_guidance_scale=image_guidance_scale,
                guidance_scale=guidance_scale,
                generator=generator,
            ).images[0]

        elif self.model_key == "sdxl_img2img":
            result_pil = self._pipe(
                prompt=instruction,
                image=pil_img,
                strength=strength,
                num_inference_steps=num_inference_steps,
                guidance_scale=guidance_scale,
                generator=generator,
            ).images[0]

        elif self.model_key == "flux":
            result_pil = self._pipe(
                prompt=instruction,
                image=pil_img,
                strength=strength,
                num_inference_steps=num_inference_steps,
                guidance_scale=guidance_scale,
                generator=generator,
            ).images[0]

        else:
            raise RuntimeError(f"未知 model_key: {self.model_key}")

        # 还原到原始分辨率
        result_np = np.array(result_pil)
        if (result_np.shape[0], result_np.shape[1]) != (orig_h, orig_w):
            result_np = cv2.resize(
                result_np, (orig_w, orig_h), interpolation=cv2.INTER_LANCZOS4
            )

        return result_np

    # ------------------------------------------------------------------
    # 释放资源
    # ------------------------------------------------------------------

    def offload(self):
        """推理完毕后释放 GPU/MPS 资源"""
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
            print(f"[InstructionEditor] offload 失败（忽略）: {e}")
        finally:
            self._pipe = None
