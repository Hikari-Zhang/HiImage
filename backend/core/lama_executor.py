"""CPU Big LaMa 修复，直接加载发布的 TorchScript 权重。"""
import hashlib

import numpy as np

from .inpaint_regions import prepare_mask, repair_regions
from .model_executor import BaseModelExecutor
from .paths import resolve_model_cache_path


class LamaExecutor(BaseModelExecutor):
    def __init__(self, model_config: dict, device: str):
        super().__init__(model_config, "cpu")
        self._model = None

    def load_model(self):
        if self._model is not None:
            return

        import torch

        path = resolve_model_cache_path(self.model_config)
        with path.open("rb") as weights:
            digest = hashlib.sha256()
            for chunk in iter(lambda: weights.read(1024 * 1024), b""):
                digest.update(chunk)
            if digest.hexdigest() != self.model_config["sha256"]:
                raise ValueError("LaMa 权重校验失败，请删除损坏的权重并重新下载")
            weights.seek(0)
            self._model = torch.jit.load(weights, map_location=self.device).eval()

    def _repair(self, image, mask):
        import torch

        height, width = mask.shape
        pad = ((0, -height % 8), (0, -width % 8))
        pixels = np.pad(image, (*pad, (0, 0)), mode="reflect")
        selection = np.pad(mask > 0, pad, mode="reflect")
        pixels = torch.from_numpy(pixels).permute(2, 0, 1)[None].float().div_(255)
        selection = torch.from_numpy(selection)[None, None].float()
        with torch.inference_mode():
            repaired = self._model(pixels, selection)
        if repaired.shape != pixels.shape:
            raise ValueError("LaMa 返回了无效的图像尺寸")
        result = repaired[0, :, :height, :width].permute(1, 2, 0).cpu().numpy()
        if not np.isfinite(result).all():
            raise ValueError("LaMa 返回了无效的像素")
        return np.rint(result.clip(0, 1) * 255).astype(np.uint8)

    def execute(self, image, *, mask=None, rois=None, dilation=0, disable_nsfw=False,
                progress_callback=None):
        if image.ndim != 3 or image.shape[2] != 3 or image.dtype != np.uint8 or not image.size:
            raise ValueError("需要非空 RGB uint8 图像")
        selection = prepare_mask(image.shape, mask, rois, dilation)
        if not selection.any():
            return image.copy()
        if progress_callback:
            progress_callback(25, "正在加载 LaMa 权重")
        self.load_model()
        if progress_callback:
            progress_callback(45, "正在修复选区")
        result = repair_regions(image, selection, self._repair)
        if progress_callback:
            progress_callback(90, "选区修复完成")
        return result

    def unload_model(self):
        self._model = None

    def supports_mask(self):
        return True
