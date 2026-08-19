"""同分辨率图像复原执行器 —— 用 spandrel 加载官方 .pth（SCUNet / NAFNet / SwinIR）。"""
from __future__ import annotations

from typing import Any

import numpy as np
import torch

from core.model_executor import BaseModelExecutor
from core.paths import resolve_model_cache_path

_EXTRA_ARCHES_INSTALLED = False


def _ensure_extra_arches() -> None:
    global _EXTRA_ARCHES_INSTALLED
    if _EXTRA_ARCHES_INSTALLED:
        return
    import spandrel_extra_arches

    spandrel_extra_arches.install()
    _EXTRA_ARCHES_INSTALLED = True


def _torch_device(device: str) -> torch.device:
    if device == "cuda" and torch.cuda.is_available():
        return torch.device("cuda")
    if device == "mps" and torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


class RestorationExecutor(BaseModelExecutor):
    """spandrel ImageModelDescriptor 包装。输入/输出均为 RGB uint8，倍率 1。"""

    DEFAULT_TILE_PAD = 16

    def __init__(self, model_config: dict[str, Any], device: str):
        super().__init__(model_config, device)
        self._descriptor = None
        self._tile = 0

    def supports_mask(self) -> bool:
        return False

    def load_model(self) -> None:
        weight_path = resolve_model_cache_path(self.model_config)
        if not weight_path.exists():
            raise FileNotFoundError(
                f"模型权重文件不存在: {weight_path}\n请先在模型管理中下载"
            )
        from spandrel import ModelLoader

        _ensure_extra_arches()
        self._descriptor = ModelLoader().load_from_file(str(weight_path))
        torch_dev = _torch_device(self.device)
        self._descriptor.eval().to(torch_dev)

    def unload_model(self) -> None:
        self._descriptor = None
        if self.device == "cuda" and torch.cuda.is_available():
            torch.cuda.empty_cache()

    def execute(self, image: np.ndarray, **kwargs) -> np.ndarray:
        if image is None or image.size == 0:
            raise ValueError("输入图像为空")
        tile = kwargs.get("tile", 0)
        if tile is None:
            tile = 0
        if self._descriptor is None:
            self.load_model()

        from core.upscaler import Upscaler

        if tile == 0:
            tile = Upscaler._auto_tile(image, 1, self.device)
        self._tile = tile

        try:
            return self._infer(image, tile)
        except RuntimeError as e:
            msg = str(e).lower()
            if ("cuda" in str(e) or "mps" in str(e) or "alloc" in msg) and tile > 256:
                return self._infer(image, 256)
            raise RuntimeError(f"复原推理失败（显存不足或设备错误）: {e}") from e

    def _infer(self, image: np.ndarray, tile: int) -> np.ndarray:
        if tile <= 0:
            return self._infer_full(image)
        return self._infer_tiled(image, tile)

    def _infer_full(self, image: np.ndarray) -> np.ndarray:
        tensor = self._to_tensor(image)
        with torch.inference_mode():
            out = self._descriptor(tensor)
        return self._to_numpy(out, image.shape[:2])

    def _infer_tiled(self, image: np.ndarray, tile: int) -> np.ndarray:
        h, w = image.shape[:2]
        pad = self.DEFAULT_TILE_PAD
        result = np.zeros_like(image)
        for y in range(0, h, tile):
            for x in range(0, w, tile):
                y0, x0 = y, x
                y1, x1 = min(y + tile, h), min(x + tile, w)
                py0, px0 = max(0, y0 - pad), max(0, x0 - pad)
                py1, px1 = min(h, y1 + pad), min(w, x1 + pad)
                crop = image[py0:py1, px0:px1]
                restored = self._infer_full(crop)
                cy0, cx0 = y0 - py0, x0 - px0
                cy1, cx1 = cy0 + (y1 - y0), cx0 + (x1 - x0)
                result[y0:y1, x0:x1] = restored[cy0:cy1, cx0:cx1]
        return result

    def _to_tensor(self, image: np.ndarray) -> torch.Tensor:
        t = torch.from_numpy(np.ascontiguousarray(image)).float().div_(255.0)
        t = t.permute(2, 0, 1).unsqueeze(0)
        return t.to(_torch_device(self.device))

    def _to_numpy(self, tensor: torch.Tensor, hw: tuple[int, int]) -> np.ndarray:
        t = tensor.detach().float().clamp_(0, 1).squeeze(0).permute(1, 2, 0).cpu().numpy()
        out = np.clip(np.rint(t * 255.0), 0, 255).astype(np.uint8)
        if out.shape[0] != hw[0] or out.shape[1] != hw[1]:
            import cv2

            out = cv2.resize(out, (hw[1], hw[0]), interpolation=cv2.INTER_AREA)
        return out
