"""
智能合成核心处理器

支持以下功能模式：
  - background_replace: 换背景（抠图 + 合成新背景）
  - outfit_swap:        换装模拟（基于 Inpaint 的服装替换）
  - face_swap:          换脸模拟（基于 Inpaint 的人脸替换）
  - virtual_tryon:      虚拟试穿（衣物图 + 人物图 → 试穿结果）

各模式处理策略：
  - background_replace: 使用 rembg (RMBG/U2Net) 抠图 + ROI 区域合成
  - outfit_swap:        使用 LaMa/SD Inpainting 替换指定 ROI 区域为参考图纹理
  - face_swap:          使用 GFPGAN 增强 + 区域叠覆合成
  - virtual_tryon:      参考图引导 Inpainting（轻量近似方案）
"""
from __future__ import annotations

import cv2
import numpy as np
from typing import Optional, List, Tuple


# ──────────────────────────────────────────────────────────────
# 模型注册表（带功能说明）
# ──────────────────────────────────────────────────────────────

SYNTHESIS_MODELS = [
    {
        "id": "rmbg",
        "name": "RMBG 2.0",
        "provider": "BRIA",
        "tags": ["background_replace"],
        "description": "BRIA RMBG 2.0 —— 最新 SOTA 抠图模型，边缘精度极高，适合换背景",
        "recommended_for": "换背景",
        "requires_reference": False,
        "size_mb": 176,
    },
    {
        "id": "u2net",
        "name": "U²-Net",
        "provider": "rembg",
        "tags": ["background_replace"],
        "description": "通用显著目标检测，支持人像/商品/动物，换背景的经典选择",
        "recommended_for": "换背景",
        "requires_reference": False,
        "size_mb": 176,
    },
    {
        "id": "modnet",
        "name": "MODNet",
        "provider": "rembg",
        "tags": ["background_replace"],
        "description": "实时人像抠图，速度快，适合批量处理",
        "recommended_for": "换背景（人像）",
        "requires_reference": False,
        "size_mb": 25,
    },
    {
        "id": "lama_inpaint",
        "name": "LaMa（区域替换）",
        "provider": "IOPaint",
        "tags": ["outfit_swap", "face_swap"],
        "description": "大感受野卷积修复，擅长纹理延续，用于换装/换脸区域重建",
        "recommended_for": "换装 / 区域替换",
        "requires_reference": True,
        "size_mb": 200,
    },
    {
        "id": "sd15",
        "name": "Stable Diffusion 1.5",
        "provider": "IOPaint",
        "tags": ["outfit_swap", "face_swap", "virtual_tryon"],
        "description": "文生图引导 Inpainting，可用文字描述目标服装/背景，创意度高",
        "recommended_for": "换装 / 试穿（文字引导）",
        "requires_reference": False,
        "size_mb": 4000,
    },
    {
        "id": "powerpaint",
        "name": "PowerPaint v2",
        "provider": "IOPaint",
        "tags": ["outfit_swap", "virtual_tryon"],
        "description": "多任务 Inpainting，专为局部编辑设计，换装效果自然",
        "recommended_for": "换装 / 试穿",
        "requires_reference": False,
        "size_mb": 4500,
    },
    {
        "id": "gfpgan",
        "name": "GFPGAN v1.4",
        "provider": "facexlib",
        "tags": ["face_swap"],
        "description": "人脸生成对抗网络，专精人脸区域修复与增强，换脸合成后处理首选",
        "recommended_for": "换脸 / 人脸增强",
        "requires_reference": True,
        "size_mb": 340,
    },
]

# 模式分组（前端用于按功能过滤）
SYNTHESIS_MODE_GROUPS = [
    {
        "id": "background_replace",
        "name": "换背景",
        "icon": "image",
        "description": "智能抠图后替换背景图",
        "models": [m["id"] for m in SYNTHESIS_MODELS if "background_replace" in m["tags"]],
        "default_model": "rmbg",
        "needs_reference": True,   # 参考图 = 新背景图
        "reference_label": "新背景图",
    },
    {
        "id": "outfit_swap",
        "name": "换装模拟",
        "icon": "shirt",
        "description": "在指定区域替换服装纹理",
        "models": [m["id"] for m in SYNTHESIS_MODELS if "outfit_swap" in m["tags"]],
        "default_model": "lama_inpaint",
        "needs_reference": True,   # 参考图 = 目标服装图
        "reference_label": "目标服装图",
    },
    {
        "id": "face_swap",
        "name": "换脸模拟",
        "icon": "user",
        "description": "在人脸区域替换目标人脸（仅用于合法创作）",
        "models": [m["id"] for m in SYNTHESIS_MODELS if "face_swap" in m["tags"]],
        "default_model": "gfpgan",
        "needs_reference": True,   # 参考图 = 目标人脸图
        "reference_label": "目标人脸图",
    },
    {
        "id": "virtual_tryon",
        "name": "虚拟试穿",
        "icon": "sparkles",
        "description": "将服装自然穿上人物照（AI 近似方案）",
        "models": [m["id"] for m in SYNTHESIS_MODELS if "virtual_tryon" in m["tags"]],
        "default_model": "sd15",
        "needs_reference": True,
        "reference_label": "服装图",
    },
]


# ──────────────────────────────────────────────────────────────
# 核心处理器
# ──────────────────────────────────────────────────────────────

class Synthesizer:
    """
    智能合成处理器

    :param mode:       处理模式 ID
    :param model_id:   所用模型 ID
    :param device:     推理设备 (mps/cpu/cuda)
    :param iopaint_path: IOPaint 可执行路径（换装/换脸场景需要）
    """

    def __init__(
        self,
        mode: str,
        model_id: str,
        device: str = "mps",
        iopaint_path: str = "iopaint",
        prompt: str = "",
    ):
        self.mode = mode
        self.model_id = model_id
        self.device = device
        self.iopaint_path = iopaint_path
        self.prompt = prompt

    # ------------------------------------------------------------------
    # 公共入口
    # ------------------------------------------------------------------

    def run(
        self,
        source_rgb: np.ndarray,
        rois: Optional[List[Tuple[int, int, int, int]]] = None,
        reference_rgb: Optional[np.ndarray] = None,
    ) -> np.ndarray:
        """
        执行合成处理。

        :param source_rgb:    主图（RGB numpy, uint8）
        :param rois:          处理区域 [(x1,y1,x2,y2),...]；None 表示全图
        :param reference_rgb: 参考图（新背景/服装/人脸图），模式需要时必传
        :return:              合成结果（RGB numpy, uint8）
        """
        if self.mode == "background_replace":
            return self._background_replace(source_rgb, reference_rgb, rois)
        elif self.mode == "outfit_swap":
            return self._inpaint_region(source_rgb, rois, reference_rgb, "outfit")
        elif self.mode == "face_swap":
            return self._inpaint_region(source_rgb, rois, reference_rgb, "face")
        elif self.mode == "virtual_tryon":
            return self._virtual_tryon(source_rgb, rois, reference_rgb)
        else:
            raise ValueError(f"未知的合成模式: {self.mode}")

    # ------------------------------------------------------------------
    # 换背景
    # ------------------------------------------------------------------

    def _background_replace(
        self,
        source_rgb: np.ndarray,
        background_rgb: Optional[np.ndarray],
        rois: Optional[List[Tuple[int, int, int, int]]],
    ) -> np.ndarray:
        """
        流程：
        1. 使用 rembg 提取前景（带 alpha）
        2. 将前景合成到新背景图上
        3. 若提供了 ROI，则仅在 ROI 内替换（其余保持原始背景）
        """
        import rembg

        h, w = source_rgb.shape[:2]

        # Step 1: 抠图（输出 RGBA）
        model_map = {
            "rmbg": "briaai/RMBG-2.0",
            "u2net": "u2net",
            "modnet": "modnet_portrait_matting",
        }
        rembg_model = model_map.get(self.model_id, "u2net")

        src_bgr = cv2.cvtColor(source_rgb, cv2.COLOR_RGB2BGR)
        _, src_bytes = cv2.imencode(".png", src_bgr)
        rgba_bytes = rembg.remove(src_bytes.tobytes(), model_name=rembg_model)
        rgba_arr = np.frombuffer(rgba_bytes, np.uint8)
        fg_rgba = cv2.imdecode(rgba_arr, cv2.IMREAD_UNCHANGED)  # BGRA

        if fg_rgba is None or fg_rgba.shape[2] < 4:
            raise RuntimeError("抠图失败：无法获取 Alpha 通道")

        alpha = fg_rgba[:, :, 3:4].astype(np.float32) / 255.0
        fg_rgb = cv2.cvtColor(fg_rgba[:, :, :3], cv2.COLOR_BGR2RGB).astype(np.float32)

        # Step 2: 准备背景
        if background_rgb is not None:
            bg = cv2.resize(background_rgb, (w, h)).astype(np.float32)
        else:
            # 没有参考背景时，使用纯白背景
            bg = np.ones((h, w, 3), np.float32) * 255.0

        # Step 3: Alpha 合成
        result = fg_rgb * alpha + bg * (1.0 - alpha)
        result = np.clip(result, 0, 255).astype(np.uint8)

        # Step 4: 若指定 ROI，则仅在 ROI 区域内应用替换
        if rois:
            output = source_rgb.copy()
            for (x1, y1, x2, y2) in rois:
                x1, y1 = max(0, x1), max(0, y1)
                x2, y2 = min(w, x2), min(h, y2)
                output[y1:y2, x1:x2] = result[y1:y2, x1:x2]
            return output

        return result

    # ------------------------------------------------------------------
    # 换装 / 换脸（Inpainting 区域替换）
    # ------------------------------------------------------------------

    def _inpaint_region(
        self,
        source_rgb: np.ndarray,
        rois: Optional[List[Tuple[int, int, int, int]]],
        reference_rgb: Optional[np.ndarray],
        hint: str = "outfit",
    ) -> np.ndarray:
        """
        流程：
        1. 如果有参考图，先将参考图贴入 ROI 区域作为「底图」
        2. 使用选择的 Inpaint 模型对 ROI 区域做重建（语义修复），使边缘自然过渡
        """
        h, w = source_rgb.shape[:2]

        if not rois:
            return source_rgb.copy()

        # 构造 mask
        mask = np.zeros((h, w), dtype=np.uint8)
        for (x1, y1, x2, y2) in rois:
            mask[y1:y2, x1:x2] = 255

        # 如果有参考图，先将参考图平铺/缩放至 ROI 区域（作为 inpaint 底图提示）
        base_rgb = source_rgb.copy()
        if reference_rgb is not None:
            for (x1, y1, x2, y2) in rois:
                roi_h, roi_w = y2 - y1, x2 - x1
                if roi_h <= 0 or roi_w <= 0:
                    continue
                ref_resized = cv2.resize(reference_rgb, (roi_w, roi_h))
                # 简单叠覆（后续 inpaint 会做语义融合）
                base_rgb[y1:y2, x1:x2] = ref_resized

        # 使用 IOPaint Inpainter 做修复
        from core.inpainter import Inpainter
        inpaint_model = self.model_id if self.model_id in ("lama", "lama_inpaint", "sd15", "powerpaint") else "lama"
        if inpaint_model == "lama_inpaint":
            inpaint_model = "lama"

        inpainter = Inpainter(
            model_name=inpaint_model,
            device=self.device,
            dilation=0,
            disable_nsfw=False,
            iopaint_path=self.iopaint_path,
        )
        result = inpainter.remove_watermark_with_mask(base_rgb, mask)

        # 人脸模式：使用 GFPGAN 进一步增强脸部细节
        if hint == "face" and self.model_id == "gfpgan":
            result = self._gfpgan_enhance(result, rois)

        return result

    # ------------------------------------------------------------------
    # 虚拟试穿（AI 近似方案：参考图引导 Inpaint）
    # ------------------------------------------------------------------

    def _virtual_tryon(
        self,
        source_rgb: np.ndarray,
        rois: Optional[List[Tuple[int, int, int, int]]],
        garment_rgb: Optional[np.ndarray],
    ) -> np.ndarray:
        """
        近似试穿：
        将服装图缩放后贴入人体 ROI 区域，再用 Inpaint 做边缘语义融合。
        （完整 IDM-VTON / OOTDiffusion 方案可在后续版本接入）
        """
        return self._inpaint_region(source_rgb, rois, garment_rgb, hint="outfit")

    # ------------------------------------------------------------------
    # GFPGAN 人脸增强
    # ------------------------------------------------------------------

    def _gfpgan_enhance(
        self,
        image_rgb: np.ndarray,
        rois: Optional[List[Tuple[int, int, int, int]]] = None,
    ) -> np.ndarray:
        """使用 GFPGAN 增强指定区域（或全图）的人脸细节"""
        try:
            from gfpgan import GFPGANer
            import torch

            device_str = "mps" if self.device == "mps" else ("cuda" if self.device == "cuda" else "cpu")
            enhancer = GFPGANer(
                model_path=None,  # 自动下载
                upscale=1,
                arch="clean",
                channel_multiplier=2,
                bg_upsampler=None,
            )
            _, _, enhanced = enhancer.enhance(
                cv2.cvtColor(image_rgb, cv2.COLOR_RGB2BGR),
                has_aligned=False,
                only_center_face=False,
                paste_back=True,
            )
            return cv2.cvtColor(enhanced, cv2.COLOR_BGR2RGB)
        except Exception as e:
            print(f"[Synthesizer] GFPGAN 增强失败（跳过）: {e}")
            return image_rgb
