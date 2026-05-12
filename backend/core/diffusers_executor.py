"""
Diffusers 执行器

包装 diffusers 库，用于 FLUX.1、SDXL、InstructPix2Pix 等模型。
"""
import numpy as np
from typing import Dict, Any
from .model_executor import BaseModelExecutor


class DiffusersExecutor(BaseModelExecutor):
    """
    Diffusers 模型执行器

    支持模型：
    - FLUX.1-dev（图生图）
    - FLUX.1-Fill-dev（Inpainting）
    - SDXL Img2Img
    - InstructPix2Pix
    """

    def __init__(self, model_config: Dict[str, Any], device: str):
        super().__init__(model_config, device)
        self._pipeline = None
        self._task_type = model_config.get("task_type", "img2img")  # img2img or inpainting

    def load_model(self) -> None:
        """加载 diffusers 模型"""
        import torch
        from diffusers import (
            FluxPipeline, FluxInpaintPipeline,
            StableDiffusionXLPipeline, StableDiffusionXLImg2ImgPipeline,
            StableDiffusionImg2ImgPipeline,
        )

        hf_model_id = self.model_config.get("hf_model_id")
        if not hf_model_id:
            raise ValueError(f"模型 {self.model_config.get('id')} 缺少 hf_model_id 配置")

        # 根据模型 ID 选择对应的 Pipeline
        if "flux" in hf_model_id.lower():
            if self._task_type == "inpaint":
                self._pipeline = FluxInpaintPipeline.from_pretrained(
                    hf_model_id,
                    torch_dtype=torch.bfloat16 if self.device != "cpu" else torch.float32,
                )
            else:
                self._pipeline = FluxPipeline.from_pretrained(
                    hf_model_id,
                    torch_dtype=torch.bfloat16 if self.device != "cpu" else torch.float32,
                )
        elif "sdxl" in hf_model_id.lower() or "stable-diffusion-xl" in hf_model_id.lower():
            if self._task_type == "img2img":
                self._pipeline = StableDiffusionXLImg2ImgPipeline.from_pretrained(
                    hf_model_id,
                    torch_dtype=torch.float16 if self.device != "cpu" else torch.float32,
                    use_safetensors=True,
                )
            else:
                # SDXL Inpainting
                from diffusers import StableDiffusionXLInpaintPipeline
                self._pipeline = StableDiffusionXLInpaintPipeline.from_pretrained(
                    hf_model_id,
                    torch_dtype=torch.float16 if self.device != "cpu" else torch.float32,
                    use_safetensors=True,
                )
        else:
            # SD 1.5 Img2Img (InstructPix2Pix)
            self._pipeline = StableDiffusionImg2ImgPipeline.from_pretrained(
                hf_model_id,
                torch_dtype=torch.float16 if self.device != "cpu" else torch.float32,
                use_safetensors=True,
            )

        # 移动到设备
        if self.device == "cuda" and torch.cuda.is_available():
            self._pipeline = self._pipeline.cuda()
        elif self.device == "mps" and hasattr(torch, "mps") and torch.backends.mps.is_available():
            self._pipeline = self._pipeline.to("mps")

        self._pipeline.eval()

    def execute(self, image: np.ndarray, **kwargs) -> np.ndarray:
        """
        执行 diffusers 推理

        :param image: 输入图像（RGB, uint8）
        :param kwargs: 支持以下参数：
                      - prompt: str（必需）
                      - negative_prompt: str（可选）
                      - num_inference_steps: int（可选）
                      - guidance_scale: float（可选）
                      - strength: float（可选，图生图强度）
                      - mask: np.ndarray（Inpainting 需要）
        :return: 生成的图像（RGB, uint8）
        """
        if self._pipeline is None:
            self.load_model()

        import torch
        import cv2

        # 转换输入图像
        image_bgr = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
        image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
        input_image = torch.from_numpy(image_rgb).float() / 255.0
        input_image = input_image.permute(2, 0, 1).unsqueeze(0)

        # 移动到设备
        if self.device == "cuda" and torch.cuda.is_available():
            input_image = input_image.cuda()
        elif self.device == "mps" and hasattr(torch, "mps") and torch.backends.mps.is_available():
            input_image = input_image.to("mps")

        # 准备参数
        prompt = kwargs.get("prompt", "")
        negative_prompt = kwargs.get("negative_prompt", "")
        num_inference_steps = kwargs.get("num_inference_steps", 50)
        guidance_scale = kwargs.get("guidance_scale", 7.5)
        strength = kwargs.get("strength", 0.75)

        # 执行推理
        with torch.no_grad():
            if self._task_type == "inpaint":
                mask = kwargs.get("mask")
                if mask is None:
                    raise ValueError("Inpainting 需要 mask 参数")
                # 转换 mask
                mask_tensor = torch.from_numpy(mask).float() / 255.0
                mask_tensor = mask_tensor.unsqueeze(0).unsqueeze(0)  # (B, 1, H, W)
                if self.device == "cuda" and torch.cuda.is_available():
                    mask_tensor = mask_tensor.cuda()
                elif self.device == "mps" and hasattr(torch, "mps") and torch.backends.mps.is_available():
                    mask_tensor = mask_tensor.to("mps")

                result = self._pipeline(
                    prompt=prompt,
                    image=input_image,
                    mask_image=mask_tensor,
                    num_inference_steps=num_inference_steps,
                    guidance_scale=guidance_scale,
                    strength=strength,
                ).images[0]
            else:
                result = self._pipeline(
                    prompt=prompt,
                    image=input_image,
                    num_inference_steps=num_inference_steps,
                    guidance_scale=guidance_scale,
                    strength=strength,
                ).images[0]

        # 转换结果
        result_np = np.array(result)
        result_rgb = cv2.cvtColor(result_np, cv2.COLOR_RGB2BGR)
        result_rgb = cv2.cvtColor(result_rgb, cv2.COLOR_BGR2RGB)
        return result_rgb

    def unload_model(self) -> None:
        """卸载模型，释放显存"""
        if self._pipeline is not None:
            del self._pipeline
            self._pipeline = None
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            elif hasattr(torch, "mps") and torch.backends.mps.is_available():
                torch.mps.empty_cache()

    def supports_mask(self) -> bool:
        """Inpainting 任务需要 mask，img2img 不需要"""
        return self._task_type == "inpaint"
