"""根据注册表声明加载并调用 Diffusers 管线。"""
import math

import cv2
import numpy as np
from PIL import Image

from .model_executor import BaseModelExecutor
from .inpaint_regions import prepare_mask, repair_regions


FILL_MAX_EDGE = 1024
FILL_MAX_PIXELS = 768 * 768


_PIPELINE_CLASSES = {
    "flux_fill": "FluxFillPipeline",
    "flux_img2img": "FluxImg2ImgPipeline",
    "sdxl_img2img": "StableDiffusionXLImg2ImgPipeline",
    "instruct_pix2pix": "StableDiffusionInstructPix2PixPipeline",
}


class DiffusersExecutor(BaseModelExecutor):
    def __init__(self, model_config: dict, device: str):
        super().__init__(model_config, device)
        self.pipeline_id = model_config.get("pipeline")
        if self.pipeline_id not in _PIPELINE_CLASSES:
            raise ValueError(f"不支持的 Diffusers 管线: {self.pipeline_id!r}")
        self._pipeline = None

    def load_model(self) -> None:
        if self._pipeline is not None:
            return

        import diffusers
        import torch
        from .model_checker import resolve_hf_model_path

        if self.device not in ("cpu", "cuda", "mps"):
            raise ValueError(f"未知推理设备: {self.device!r}")
        if self.device == "cuda" and not torch.cuda.is_available():
            raise ValueError("CUDA 不可用")
        if self.device == "mps" and not torch.backends.mps.is_available():
            raise ValueError("MPS 不可用")

        is_flux = self.pipeline_id in ("flux_fill", "flux_img2img")
        dtype = torch.float32
        if self.device == "cuda":
            dtype = torch.bfloat16 if is_flux and torch.cuda.is_bf16_supported() else torch.float16
        pipeline_class = getattr(diffusers, _PIPELINE_CLASSES[self.pipeline_id])
        model_path = resolve_hf_model_path(self.model_config["hf_model_id"])
        load_options = {"torch_dtype": dtype}
        if self.pipeline_id == "flux_fill":
            load_options.update(local_files_only=True, use_safetensors=True)
        pipe = pipeline_class.from_pretrained(model_path, **load_options)
        if self.pipeline_id == "flux_fill" and self.device == "cuda":
            pipe.enable_sequential_cpu_offload()
        elif is_flux and self.device == "cuda":
            pipe.enable_model_cpu_offload()
        else:
            pipe.to(self.device)
        self._pipeline = pipe

    def _parameters(self, supplied: dict) -> dict:
        specs = self.model_config["supported_params"]
        unknown = supplied.keys() - specs.keys()
        if unknown:
            raise ValueError(f"{self.pipeline_id} 不支持参数: {', '.join(sorted(unknown))}")
        params = {name: spec["default"] for name, spec in specs.items()}
        params.update(supplied)
        for name, value in params.items():
            spec = specs[name]
            kind = spec["type"]
            if kind == "int":
                valid = type(value) is int
            elif kind == "float":
                valid = type(value) in (int, float) and math.isfinite(value)
            else:
                valid = isinstance(value, str)
            bounds = spec.get("range")
            if not valid or (bounds is not None and not bounds[0] <= value <= bounds[1]):
                raise ValueError(f"参数 {name} 的类型或范围无效")
        return params

    def execute(self, image: np.ndarray, *, mask=None, rois=None, dilation=0,
                disable_nsfw=False, progress_callback=None, **kwargs) -> np.ndarray:
        params = self._parameters(kwargs)
        if image.ndim != 3 or image.shape[2] != 3 or image.dtype != np.uint8 or not image.size:
            raise ValueError("需要非空 RGB uint8 图像")
        if self.supports_mask():
            if mask is None and not rois:
                raise ValueError("局部重绘需要 ROI 或遮罩")
            if mask is not None and (mask.shape != image.shape[:2] or mask.dtype != np.uint8):
                raise ValueError("需要与原图同尺寸的 uint8 遮罩")
            selection = prepare_mask(image.shape, mask, rois, dilation)
            if not selection.any():
                return image.copy()
            if progress_callback:
                progress_callback(25, "正在加载 FLUX Fill 权重")
            self.load_model()
            if progress_callback:
                progress_callback(45, "正在重绘选区")
            result = repair_regions(
                image, selection, lambda crop, region: self._infer(crop, params.copy(), region),
            )
            if progress_callback:
                progress_callback(90, "选区重绘完成")
            return result
        if mask is not None or rois is not None or dilation:
            raise ValueError(f"{self.pipeline_id} 不支持遮罩")

        self.load_model()
        return self._infer(image, params)

    def _infer(self, image, params, mask=None):
        import torch

        height, width = image.shape[:2]
        original_size = (width, height)
        pil_image = Image.fromarray(image)
        if mask is not None:
            scale = min(1, FILL_MAX_EDGE / max(width, height),
                        math.sqrt(FILL_MAX_PIXELS / (width * height)))
            size = tuple(max(16, int(side * scale) // 16 * 16) for side in original_size)
            pil_image = pil_image.resize(size, Image.Resampling.LANCZOS)
            coverage = cv2.resize((mask > 0).astype(np.float32), size, interpolation=cv2.INTER_AREA)
            params["mask_image"] = Image.fromarray((coverage > 0).astype(np.uint8) * 255)
            params.update(width=size[0], height=size[1])
        params["generator"] = torch.Generator(device="cpu").manual_seed(params.pop("seed", 42))
        result = self._pipeline(image=pil_image, **params).images[0].convert("RGB")
        if result.size != original_size:
            result = result.resize(original_size, Image.Resampling.LANCZOS)
        return np.array(result)

    def unload_model(self) -> None:
        if self._pipeline is None:
            return
        self._pipeline = None

        import torch
        if self.device == "cuda":
            torch.cuda.empty_cache()
        elif self.device == "mps":
            torch.mps.empty_cache()

    def supports_mask(self) -> bool:
        return self.pipeline_id == "flux_fill"
