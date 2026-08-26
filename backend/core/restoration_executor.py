"""同分辨率图像复原执行器 —— 用 spandrel 加载官方 .pth（SCUNet / NAFNet / SwinIR）。"""
from __future__ import annotations

from typing import Any

import numpy as np
import torch

from core.model_executor import BaseModelExecutor
from core.paths import resolve_model_cache_path

_SPANDREL_INSTALL_HINT = (
    "专用降噪模型需要 spandrel（Python 3.10+）。"
    "请执行: pip install 'spandrel>=0.4.0'"
)


def _torch_device(device: str) -> torch.device:
    if device == "cuda" and torch.cuda.is_available():
        return torch.device("cuda")
    if device == "mps" and torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def _effective_device(model_config: dict[str, Any], device: str) -> str:
    override = model_config.get("device_override")
    if override:
        return str(override)
    return device


def _is_unimplemented_backend_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return (
        isinstance(exc, NotImplementedError)
        or "convolution_overrideable" in msg
        or ("not implemented" in msg and "mkldnn" in msg)
    )


class RestorationExecutor(BaseModelExecutor):
    """spandrel ImageModelDescriptor 包装。输入/输出均为 RGB uint8，倍率 1。"""

    DEFAULT_TILE_PAD = 16

    def __init__(self, model_config: dict[str, Any], device: str):
        super().__init__(model_config, _effective_device(model_config, device))
        self._descriptor = None
        self._tile = 0
        if self.device != device:
            print(
                f"[Restoration] 模型 {model_config.get('id')} 不支持 {device}，"
                f"改用 {self.device}"
            )

    def supports_mask(self) -> bool:
        return False

    def load_model(self) -> None:
        weight_path = resolve_model_cache_path(self.model_config)
        if not weight_path.exists():
            raise FileNotFoundError(
                f"模型权重文件不存在: {weight_path}\n请先在模型管理中下载"
            )
        try:
            from spandrel import ModelLoader
        except ImportError as e:
            raise RuntimeError(_SPANDREL_INSTALL_HINT) from e

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

        self._tile = tile
        print(
            f"[Restoration] infer id={self.model_config.get('id')} "
            f"device={self.device} tile={tile} shape={image.shape[:2]}"
        )

        try:
            return self._infer(image, tile)
        except RuntimeError as e:
            if self.device == "mps" and _is_unimplemented_backend_error(e):
                return self._retry_on_cpu(image, tile)
            msg = str(e).lower()
            if ("cuda" in msg or "mps" in msg or "alloc" in msg) and (tile <= 0 or tile > 256):
                return self._infer(image, 256)
            raise RuntimeError(f"复原推理失败（显存不足或设备错误）: {e}") from e

    def _retry_on_cpu(self, image: np.ndarray, tile: int) -> np.ndarray:
        print(
            f"[Restoration] {self.model_config.get('id')} 在 MPS 上缺少算子，改用 CPU"
        )
        self.device = "cpu"
        if self._descriptor is not None:
            self._descriptor.to("cpu")
        return self._infer(image, tile)

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
        oh, ow = out.shape[:2]
        th, tw = hw
        if oh == th and ow == tw:
            return out
        if oh >= th and ow >= tw:
            return out[:th, :tw]
        import cv2

        return cv2.resize(out, (tw, th), interpolation=cv2.INTER_LINEAR)
