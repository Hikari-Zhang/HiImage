"""
人体服装分割器（基于 SegFormer B2 Clothes）

使用 mattmdjaga/segformer_b2_clothes 模型对人体图像做 18 类服装分割，
返回指定部位的 uint8 掩码（0/255）。

模型大小：~400 MB
首次调用时自动下载（使用 HuggingFace Hub）。
"""
from __future__ import annotations

import numpy as np
import cv2
from typing import Optional

from core.model_checker import resolve_hf_model_path

# ──────────────────────────────────────────────────────────────
# SegFormer 标签 ID 映射
# 来源：mattmdjaga/segformer_b2_clothes 模型 config.json
# ──────────────────────────────────────────────────────────────
LABEL_TO_ID: dict[str, int] = {
    "Background":    0,
    "Hat":           1,
    "Hair":          2,
    "Sunglasses":    3,
    "Upper-clothes": 4,
    "Skirt":         5,
    "Pants":         6,
    "Dress":         7,
    "Belt":          8,
    "Left-shoe":     9,
    "Right-shoe":    10,
    "Face":          11,
    "Left-leg":      12,
    "Right-leg":     13,
    "Left-arm":      14,
    "Right-arm":     15,
    "Bag":           16,
    "Scarf":         17,
    "Skin":          18,   # 一些版本中存在，不在官方列表但兼容
    "Socks":         19,   # 同上，部分 checkpoint 有此类
}

# 对称部位（需要合并左右）
_SYMMETRIC_PAIRS: dict[str, list[str]] = {
    "__shoes__":  ["Left-shoe", "Right-shoe"],
    "__gloves__": ["Left-glove", "Right-glove"],  # 若模型有此标签
    "__arms__":   ["Left-arm", "Right-arm"],
    "__legs__":   ["Left-leg", "Right-leg"],
}

_MODEL_ID = "mattmdjaga/segformer_b2_clothes"


class HumanParser:
    """
    惰性加载的 SegFormer 服装分割器（进程内单例）。
    """

    _instance: Optional["HumanParser"] = None
    _loaded: bool = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._loaded = False
            cls._instance._processor = None
            cls._instance._model = None
        return cls._instance

    # ------------------------------------------------------------------

    def _lazy_load(self):
        if self._loaded:
            return
        print(f"[HumanParser] 加载 SegFormer 服装分割模型: {_MODEL_ID}")
        from transformers import SegformerImageProcessor, SegformerForSemanticSegmentation

        local_path = resolve_hf_model_path(_MODEL_ID)
        self._processor = SegformerImageProcessor.from_pretrained(local_path)
        self._model = SegformerForSemanticSegmentation.from_pretrained(local_path)
        self._model.eval()
        self._loaded = True
        print("[HumanParser] 模型就绪")

    def segment(self, image_rgb: np.ndarray, part_label: str) -> np.ndarray:
        """
        对 image_rgb 执行分割，返回目标部位的 uint8 掩码（0/255）。

        :param image_rgb:   输入图像（RGB，uint8）
        :param part_label:  SegFormer label（如 "Upper-clothes"）或特殊标记 "__shoes__"
        :return:            uint8 掩码，255=目标部位，0=其余
        """
        self._lazy_load()

        import torch
        from PIL import Image as PILImage

        h, w = image_rgb.shape[:2]

        # 转为 PIL
        pil_img = PILImage.fromarray(image_rgb)

        # 推理
        inputs = self._processor(images=pil_img, return_tensors="pt")
        with torch.no_grad():
            outputs = self._model(**inputs)

        # 上采样到原始尺寸
        logits = outputs.logits  # (1, num_labels, H/4, W/4)
        upsampled = torch.nn.functional.interpolate(
            logits,
            size=(h, w),
            mode="bilinear",
            align_corners=False,
        )
        seg = upsampled.argmax(dim=1).squeeze(0).cpu().numpy().astype(np.int32)

        # 处理对称部位
        if part_label in _SYMMETRIC_PAIRS:
            target_labels = _SYMMETRIC_PAIRS[part_label]
        else:
            target_labels = [part_label]

        # 合并所有目标 label 的像素
        mask = np.zeros((h, w), dtype=np.uint8)
        for label in target_labels:
            label_id = LABEL_TO_ID.get(label)
            if label_id is not None:
                mask[seg == label_id] = 255

        # Morphological closing 填补孔洞
        if mask.max() > 0:
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15))
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

        return mask
