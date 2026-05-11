"""
GroundingDINO + SAM 零样本分割器

使用 IDEA-Research/grounding-dino-base + facebook/sam-vit-large 实现
任意文本驱动的精准分割：
  1. GroundingDINO 检测目标边框（zero-shot，英文效果最佳）
  2. SAM 以边框为 prompt 生成像素级精细掩码

相比 SegFormer 的优势：
  - 不限于 18 个预定义服装类别，任意目标均可分割
  - SAM 分割边缘更精细，头发丝/布料边缘保留更好
  - 对非标准姿态、遮挡场景更鲁棒

模型大小：~340MB (GroundingDINO-base) + ~1.25GB (SAM ViT-L)
需要 transformers>=4.37.0
"""
from __future__ import annotations

import numpy as np
import cv2
from typing import Optional

from core.model_checker import resolve_hf_model_path

_GDINO_MODEL_ID = "IDEA-Research/grounding-dino-base"
_SAM_MODEL_ID   = "facebook/sam-vit-large"

# SegFormer label / 中文部位名 → GroundingDINO 友好的英文查询词
# GroundingDINO 底座是 BERT，英文效果显著优于中文
_QUERY_MAP: dict[str, str] = {
    # SegFormer labels
    "Upper-clothes":  "shirt jacket coat sweater upper clothing",
    "Pants":          "pants trousers jeans",
    "Skirt":          "skirt",
    "Dress":          "dress",
    "Hat":            "hat cap",
    "Left-shoe":      "shoes",
    "Right-shoe":     "shoes",
    "__shoes__":      "shoes",
    "__gloves__":     "gloves",
    "Belt":           "belt",
    "Scarf":          "scarf",
    "Hair":           "hair",
    "Face":           "face",
    "Skin":           "skin",
    "Background":     "background",
    "Socks":          "socks",
    "Bag":            "bag",
    # 常见中文直传（会自动翻译）
    "上衣": "shirt jacket coat sweater upper clothing",
    "裤子": "pants trousers",
    "裙子": "skirt",
    "鞋子": "shoes",
    "帽子": "hat cap",
    "头发": "hair",
    "皮肤": "skin",
}


def _to_query(text: str) -> str:
    """将部位标签或中文名翻译为 GroundingDINO 查询词。"""
    return _QUERY_MAP.get(text, text)


class GroundedSegmenter:
    """
    GroundingDINO + SAM 联合分割器（进程内单例，惰性加载）。
    """
    _instance: Optional["GroundedSegmenter"] = None
    _loaded: bool = False

    def __new__(cls):
        if cls._instance is None:
            inst = super().__new__(cls)
            inst._loaded = False
            inst._gdino_processor = None
            inst._gdino_model = None
            inst._sam_model = None
            inst._sam_processor = None
            cls._instance = inst
        return cls._instance

    # ------------------------------------------------------------------

    def _lazy_load(self):
        if self._loaded:
            return

        from transformers import (
            AutoProcessor,
            AutoModelForZeroShotObjectDetection,
            SamModel,
            SamProcessor,
        )

        print(f"[GroundedSegmenter] 加载 GroundingDINO: {_GDINO_MODEL_ID}")
        gdino_path = resolve_hf_model_path(_GDINO_MODEL_ID)
        self._gdino_processor = AutoProcessor.from_pretrained(gdino_path)
        self._gdino_model = AutoModelForZeroShotObjectDetection.from_pretrained(gdino_path)
        self._gdino_model.eval()

        print(f"[GroundedSegmenter] 加载 SAM: {_SAM_MODEL_ID}")
        sam_path = resolve_hf_model_path(_SAM_MODEL_ID)
        self._sam_model = SamModel.from_pretrained(sam_path)
        self._sam_processor = SamProcessor.from_pretrained(sam_path)
        self._sam_model.eval()

        self._loaded = True
        print("[GroundedSegmenter] 模型就绪")

    # ------------------------------------------------------------------

    def segment(
        self,
        image_rgb: np.ndarray,
        part_label: str,
        box_threshold: float = 0.25,
        text_threshold: float = 0.20,
    ) -> np.ndarray:
        """
        对 image_rgb 中 part_label 描述的目标生成精细分割掩码。

        :param image_rgb:      输入图像（RGB uint8）
        :param part_label:     部位标签（SegFormer label 或中文名）
        :param box_threshold:  GroundingDINO 边框置信阈值
        :param text_threshold: GroundingDINO 文字匹配阈值
        :return:               uint8 掩码，255=目标，0=背景
        """
        self._lazy_load()

        import torch
        from PIL import Image as PILImage

        h, w = image_rgb.shape[:2]
        pil_img = PILImage.fromarray(image_rgb)
        text_query = _to_query(part_label)

        # ── Step 1: GroundingDINO 检测目标边框 ──────────────────────────
        gdino_inputs = self._gdino_processor(
            images=pil_img,
            text=text_query,
            return_tensors="pt",
        )
        with torch.no_grad():
            gdino_outputs = self._gdino_model(**gdino_inputs)

        results = self._gdino_processor.post_process_grounded_object_detection(
            gdino_outputs,
            gdino_inputs["input_ids"],
            box_threshold=box_threshold,
            text_threshold=text_threshold,
            target_sizes=[(h, w)],
        )[0]

        boxes = results["boxes"].cpu().numpy()  # shape (N, 4) in xyxy

        if len(boxes) == 0:
            print(f"[GroundedSegmenter] 未检测到「{part_label}」，尝试降低阈值...")
            # 尝试更低阈值（保底尝试）
            results = self._gdino_processor.post_process_grounded_object_detection(
                gdino_outputs,
                gdino_inputs["input_ids"],
                box_threshold=0.1,
                text_threshold=0.1,
                target_sizes=[(h, w)],
            )[0]
            boxes = results["boxes"].cpu().numpy()

        if len(boxes) == 0:
            return np.zeros((h, w), dtype=np.uint8)

        # ── Step 2: SAM 对每个边框生成精细掩码 ────────────────────────────
        # SAM processor 期望 input_boxes: List[List[List[float]]]（batch × boxes × 4）
        input_boxes_sam = [[[float(b[0]), float(b[1]), float(b[2]), float(b[3])]] for b in boxes]

        final_mask = np.zeros((h, w), dtype=np.uint8)

        for box_coords in input_boxes_sam:
            sam_inputs = self._sam_processor(
                pil_img,
                input_boxes=[box_coords],   # wrap in batch dim
                return_tensors="pt",
            )
            with torch.no_grad():
                sam_outputs = self._sam_model(**sam_inputs)

            # post_process_masks 返回 List[Tensor(num_boxes, 3, H, W)]
            masks_list = self._sam_processor.image_processor.post_process_masks(
                sam_outputs.pred_masks.cpu(),
                sam_inputs["original_sizes"].cpu(),
                sam_inputs["reshaped_input_sizes"].cpu(),
            )
            if not masks_list:
                continue

            masks_tensor = masks_list[0]   # (num_boxes=1, 3, H, W)
            iou_scores   = sam_outputs.iou_scores[0].cpu()  # (num_boxes=1, 3)

            # 每个 box 取 IoU 分数最高的掩码
            best_idx = iou_scores[0].argmax().item()
            mask_bool = masks_tensor[0, best_idx].numpy()  # (H, W)

            final_mask = np.maximum(final_mask, mask_bool.astype(np.uint8) * 255)

        # 形态学 closing 填补孔洞
        if final_mask.max() > 0:
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15))
            final_mask = cv2.morphologyEx(final_mask, cv2.MORPH_CLOSE, kernel)

        return final_mask
