"""
智能合成核心处理器

支持以下功能模式：
  - background_replace:   换背景（抠图 + 合成新背景）
  - outfit_swap:          换装模拟（基于 Inpaint 的服装替换）
  - face_swap:            换脸模拟（基于 Inpaint 的人脸替换）
  - virtual_tryon:        虚拟试穿（衣物图 + 人物图 → 试穿结果）
  - prompt_inpaint:       精准替换（手动框选 ROI + 文字描述 → SD Inpainting）
  - auto_segment_edit:    智能定位（中文指令 → 自动识别服装部位 → HSV 换色 / SD 换风格）
  - instruction_edit:     自由编辑（自然语言指令 → InstructPix2Pix 全图语义编辑）

各模式处理策略：
  - background_replace:   使用 rembg (RMBG/U2Net) 抠图 + ROI 区域合成
  - outfit_swap:          使用 LaMa/SD Inpainting 替换指定 ROI 区域为参考图纹理
  - face_swap:            使用 GFPGAN 增强 + 区域叠覆合成
  - virtual_tryon:        参考图引导 Inpainting（轻量近似方案）
  - prompt_inpaint:       SD 1.5 Inpainting 文字引导区域替换
  - auto_segment_edit:    SegFormer 自动分割 + HSV 换色 / SD Inpainting
  - instruction_edit:     InstructPix2Pix 无掩码全图语义编辑
"""
from __future__ import annotations

import cv2
import numpy as np
from typing import Optional, List, Tuple


# ──────────────────────────────────────────────────────────────
# 模型注册表（带功能说明）
# ──────────────────────────────────────────────────────────────

SYNTHESIS_MODELS = [
    # ── 换背景（按质量排序）──
    {
        "id": "birefnet",
        "name": "BiRefNet-General",
        "provider": "rembg",
        "tags": ["background_replace"],
        "description": "双向精化网络，2024 SOTA 抠图，边缘细节极佳，复杂场景首选",
        "recommended_for": "换背景",
        "requires_reference": False,
        "size_mb": 100,
        "badge": "推荐",
    },
    {
        "id": "rmbg",
        "name": "RMBG 2.0",
        "provider": "BRIA",
        "tags": ["background_replace"],
        "description": "BRIA RMBG 2.0 —— 商业级抠图精度，人像/产品均优秀",
        "recommended_for": "换背景",
        "requires_reference": False,
        "size_mb": 176,
    },
    {
        "id": "isnet",
        "name": "IS-Net General",
        "provider": "rembg",
        "tags": ["background_replace"],
        "description": "ISNet 通用目标分割，产品图/商品摄影效果极佳，细节保留好",
        "recommended_for": "换背景（商品）",
        "requires_reference": False,
        "size_mb": 120,
    },
    {
        "id": "isnet_anime",
        "name": "IS-Net Anime",
        "provider": "rembg",
        "tags": ["background_replace"],
        "description": "针对动漫/插画优化的分割模型，发丝与细线条保留精准",
        "recommended_for": "换背景（动漫）",
        "requires_reference": False,
        "size_mb": 120,
    },
    {
        "id": "u2net",
        "name": "U²-Net",
        "provider": "rembg",
        "tags": ["background_replace"],
        "description": "通用显著目标检测经典模型，人像/商品/动物均兼顾",
        "recommended_for": "换背景（通用）",
        "requires_reference": False,
        "size_mb": 176,
    },
    {
        "id": "modnet",
        "name": "MODNet",
        "provider": "rembg",
        "tags": ["background_replace"],
        "description": "轻量实时人像抠图，速度极快，适合批量与预览",
        "recommended_for": "换背景（快速）",
        "requires_reference": False,
        "size_mb": 25,
        "badge": "快速",
    },
    # ── 换装 / 换脸 / 试穿（按质量排序）──
    {
        "id": "flux_fill",
        "name": "FLUX.1-Fill-dev",
        "provider": "diffusers",
        "tags": ["outfit_swap", "virtual_tryon", "face_swap"],
        "description": "FLUX.1 官方 Inpainting 变体，语义理解力极强，细节还原最佳，需 16-24GB VRAM",
        "recommended_for": "换装 / 试穿 / 换脸（最高质量）",
        "requires_reference": False,
        "size_mb": 23800,
        "badge": "高质量",
    },
    {
        "id": "sdxl",
        "name": "Stable Diffusion XL",
        "provider": "IOPaint",
        "tags": ["outfit_swap", "virtual_tryon"],
        "description": "SDXL Inpainting，1024px 高分辨率生成，服装纹理自然，需 12GB+ VRAM",
        "recommended_for": "换装 / 试穿（高分辨率）",
        "requires_reference": False,
        "size_mb": 7000,
    },
    {
        "id": "powerpaint",
        "name": "PowerPaint v2",
        "provider": "IOPaint",
        "tags": ["outfit_swap", "virtual_tryon"],
        "description": "多任务 Inpainting，专为局部换装/试穿设计，结构保留优秀",
        "recommended_for": "换装 / 试穿",
        "requires_reference": False,
        "size_mb": 4500,
        "badge": "推荐",
    },
    {
        "id": "mat",
        "name": "MAT",
        "provider": "IOPaint",
        "tags": ["outfit_swap", "face_swap"],
        "description": "Mask-Aware Transformer，不规则掩码处理精度最高，边缘自然无痕",
        "recommended_for": "换装 / 换脸（精细边缘）",
        "requires_reference": True,
        "size_mb": 500,
    },
    {
        "id": "lama_inpaint",
        "name": "LaMa",
        "provider": "IOPaint",
        "tags": ["outfit_swap", "face_swap"],
        "description": "大感受野卷积修复，纹理延续自然，速度快，小显存也能跑",
        "recommended_for": "换装 / 区域修复",
        "requires_reference": True,
        "size_mb": 200,
    },
    {
        "id": "zits",
        "name": "ZITS",
        "provider": "IOPaint",
        "tags": ["outfit_swap"],
        "description": "基于 Transformer 的结构线修复，布料纹理/格纹/条纹重建出色",
        "recommended_for": "换装（纹理类服装）",
        "requires_reference": True,
        "size_mb": 200,
    },
    {
        "id": "sd15",
        "name": "Stable Diffusion 1.5",
        "provider": "IOPaint",
        "tags": ["outfit_swap", "face_swap", "virtual_tryon"],
        "description": "文字引导 Inpainting，可配合提示词描述目标样式，创意度高",
        "recommended_for": "换装 / 换脸（文字引导）",
        "requires_reference": False,
        "size_mb": 4000,
    },
    {
        "id": "gfpgan",
        "name": "GFPGAN v1.4",
        "provider": "facexlib",
        "tags": ["face_swap"],
        "description": "人脸生成对抗网络，专精人脸修复与超分增强，换脸后处理首选",
        "recommended_for": "换脸 / 人脸增强",
        "requires_reference": True,
        "size_mb": 340,
        "badge": "推荐",
    },
    # ── 方案A：精准替换（按质量排序）──
    {
        "id": "flux_fill_prompt",
        "name": "FLUX.1-Fill-dev",
        "provider": "diffusers",
        "tags": ["prompt_inpaint"],
        "description": "FLUX.1 Inpainting，2024 SOTA，文字理解力最强，细节还原极佳，需 16-24GB VRAM",
        "recommended_for": "精准替换（最高质量）",
        "requires_reference": False,
        "size_mb": 23800,
        "badge": "高质量",
    },
    {
        "id": "sdxl_inpaint_prompt",
        "name": "SDXL Inpainting",
        "provider": "IOPaint",
        "tags": ["prompt_inpaint"],
        "description": "SDXL 文字引导精准替换，1024px 高精度输出，需 12GB+ VRAM",
        "recommended_for": "精准替换（高质量）",
        "requires_reference": False,
        "size_mb": 7000,
        "badge": "高质量",
    },
    {
        "id": "powerpaint_prompt",
        "name": "PowerPaint v2",
        "provider": "IOPaint",
        "tags": ["prompt_inpaint"],
        "description": "PowerPaint 文字引导，结构保留佳，替换边缘自然，推荐首选",
        "recommended_for": "精准替换",
        "requires_reference": False,
        "size_mb": 4500,
        "badge": "推荐",
    },
    {
        "id": "sd15_inpaint_prompt",
        "name": "SD 1.5 Inpainting",
        "provider": "IOPaint",
        "tags": ["prompt_inpaint"],
        "description": "SD 1.5 文字引导 Inpainting，速度快，显存需求低，适合快速预览",
        "recommended_for": "精准替换（快速）",
        "requires_reference": False,
        "size_mb": 4000,
    },
    # ── 方案B：智能定位（按质量排序）──
    {
        "id": "grounded_sam_flux",
        "name": "GDINO + SAM + FLUX.1-Fill",
        "provider": "HiImage",
        "tags": ["auto_segment_edit"],
        "description": "GroundingDINO 零样本检测 + SAM 精细分割 + FLUX.1-Fill 修复，任意目标、边缘最精准，需 16-24GB VRAM",
        "recommended_for": "换色/换风格（最高质量）",
        "requires_reference": False,
        "size_mb": 25500,
        "badge": "高质量",
    },
    {
        "id": "grounded_sam_sdxl",
        "name": "GDINO + SAM + SDXL",
        "provider": "HiImage",
        "tags": ["auto_segment_edit"],
        "description": "GroundingDINO + SAM 精准分割 + SDXL Inpainting，分割边缘精细，高分辨率输出，需 12GB VRAM",
        "recommended_for": "换色/换风格（高质量）",
        "requires_reference": False,
        "size_mb": 9500,
    },
    {
        "id": "auto_segment_hsv",
        "name": "SegFormer + HSV 换色",
        "provider": "HiImage",
        "tags": ["auto_segment_edit"],
        "description": "自动识别服装部位，HSV 色彩空间换色，亚秒级响应，保留布料光影",
        "recommended_for": "换色（纯色）",
        "requires_reference": False,
        "size_mb": 400,
        "badge": "推荐",
    },
    {
        "id": "auto_segment_sd15",
        "name": "SegFormer + SD 1.5",
        "provider": "HiImage + IOPaint",
        "tags": ["auto_segment_edit"],
        "description": "自动分割后用 SD 1.5 Inpainting 替换，支持纹理/风格类指令",
        "recommended_for": "换风格 / 换纹理",
        "requires_reference": False,
        "size_mb": 4400,
    },
    # ── 方案C：自由编辑（按画质排序）──
    {
        "id": "flux",
        "name": "FLUX.1-dev",
        "provider": "diffusers",
        "tags": ["instruction_edit"],
        "description": "Black Forest Labs 2024 SOTA，文字理解力极强，细节还原最佳，需 24GB+ VRAM",
        "recommended_for": "自由编辑（最高画质）",
        "requires_reference": False,
        "size_mb": 24000,
        "badge": "高质量",
    },
    {
        "id": "sdxl_img2img",
        "name": "SDXL Img2Img",
        "provider": "diffusers",
        "tags": ["instruction_edit"],
        "description": "SDXL 图生图，1024px 高分辨率输出，画质明显优于 SD 1.5，需 12GB+ VRAM",
        "recommended_for": "自由编辑（高分辨率）",
        "requires_reference": False,
        "size_mb": 7000,
    },
    {
        "id": "magicbrush",
        "name": "MagicBrush",
        "provider": "diffusers",
        "tags": ["instruction_edit"],
        "description": "高质量指令编辑数据集微调的 IP2P，指令跟随更精准，兼顾速度与效果",
        "recommended_for": "自由语义编辑（精准指令）",
        "requires_reference": False,
        "size_mb": 5000,
        "badge": "推荐",
    },
    {
        "id": "instruct_pix2pix",
        "name": "InstructPix2Pix",
        "provider": "diffusers",
        "tags": ["instruction_edit"],
        "description": "原版 timbrooks/instruct-pix2pix，SD 1.5 底座，显存需求低，适合快速预览",
        "recommended_for": "自由语义编辑（快速）",
        "requires_reference": False,
        "size_mb": 5000,
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
        "default_model": "birefnet",
        "needs_reference": True,   # 参考图 = 新背景图
        "reference_label": "新背景图",
        "needs_roi": False,
        "needs_prompt": False,
    },
    {
        "id": "outfit_swap",
        "name": "换装模拟",
        "icon": "shirt",
        "description": "在指定区域替换服装纹理",
        "models": [m["id"] for m in SYNTHESIS_MODELS if "outfit_swap" in m["tags"]],
        "default_model": "powerpaint",
        "needs_reference": True,   # 参考图 = 目标服装图
        "reference_label": "目标服装图",
        "needs_roi": False,
        "needs_prompt": False,
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
        "needs_roi": False,
        "needs_prompt": False,
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
        "needs_roi": False,
        "needs_prompt": False,
    },
    # ── 方案A：精准替换 ──
    {
        "id": "prompt_inpaint",
        "name": "精准替换",
        "icon": "crosshair",
        "description": "手动框选区域，输入文字描述，SD 1.5 按描述替换选定区域",
        "models": [m["id"] for m in SYNTHESIS_MODELS if "prompt_inpaint" in m["tags"]],
        "default_model": "powerpaint_prompt",
        "needs_reference": False,
        "needs_roi": True,
        "needs_prompt": True,
        "prompt_label": "替换描述",
        "prompt_hint": "例：a red down jacket / 一件红色羽绒服",
    },
    # ── 方案B：智能定位 ──
    {
        "id": "auto_segment_edit",
        "name": "智能定位",
        "icon": "wand",
        "description": "输入中文指令自动识别服装部位并换色/换风格，无需手动框选",
        "models": [m["id"] for m in SYNTHESIS_MODELS if "auto_segment_edit" in m["tags"]],
        "default_model": "auto_segment_hsv",
        "needs_reference": False,
        "needs_roi": False,
        "needs_prompt": True,
        "prompt_label": "编辑指令",
        "prompt_hint": "例：将上衣换成黑色 / 把裤子改成牛仔风格",
    },
    # ── 方案C：自由编辑 ──
    {
        "id": "instruction_edit",
        "name": "自由编辑",
        "icon": "message",
        "description": "自然语言指令驱动全图语义编辑，无需参考图或框选区域",
        "models": [m["id"] for m in SYNTHESIS_MODELS if "instruction_edit" in m["tags"]],
        "default_model": "magicbrush",
        "needs_reference": False,
        "needs_roi": False,
        "needs_prompt": True,
        "prompt_label": "编辑指令",
        "prompt_hint": "例：make the background a forest / 将背景换成夜晚城市",
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
        elif self.mode == "prompt_inpaint":
            self._source_image = source_rgb
            self._rois = rois or []
            return self._run_prompt_inpaint()
        elif self.mode == "auto_segment_edit":
            self._source_image = source_rgb
            self._rois = rois or []
            return self._run_auto_segment_edit()
        elif self.mode == "instruction_edit":
            self._source_image = source_rgb
            return self._run_instruction_edit()
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
            # 高质量（推荐）
            "birefnet":    "birefnet-general",
            "rmbg":        "briaai/RMBG-2.0",
            "isnet":       "isnet-general-use",
            "isnet_anime": "isnet-anime",
            # 通用 / 快速
            "u2net":       "u2net",
            "modnet":      "modnet_portrait_matting",
        }
        rembg_model = model_map.get(self.model_id, "birefnet-general")

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
        from core.model_server import inpaint_via_server

        # flux_fill：直接走 FluxFiller，不经 IOPaint
        if self.model_id == "flux_fill":
            from core.flux_filler import FluxFiller
            filler = FluxFiller(device=self.device)
            try:
                result = filler.inpaint(
                    image_rgb=base_rgb,
                    mask=mask,
                    prompt=self.prompt or "high quality, photorealistic",
                )
            finally:
                filler.offload()
        else:
            # 将前端模型 ID 映射到 IOPaint 实际模型名
            _iopaint_model_map = {
                "sdxl":         "diffusers_sd_xl_inpaint",
                "powerpaint":   "PowerPaint",
                "mat":          "mat",
                "lama_inpaint": "lama",
                "zits":         "zits",
                "sd15":         "runwayml/stable-diffusion-inpainting",
            }
            inpaint_model = _iopaint_model_map.get(self.model_id, "lama")

            inpainter = Inpainter(
                model_name=inpaint_model,
                device=self.device,
                dilation=0,
                disable_nsfw=False,
                iopaint_path=self.iopaint_path,
                prompt=self.prompt,
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

    # ------------------------------------------------------------------
    # 方案A：精准替换（prompt_inpaint）
    # ------------------------------------------------------------------

    def _run_prompt_inpaint(self) -> np.ndarray:
        """
        手动框选区域 + 文字描述 → Inpainting 精准替换。
        要求：self._rois 非空，self.prompt 非空。
        """
        if not self._rois:
            raise ValueError("精准替换模式需要框选至少一个区域（ROI）")
        if not self.prompt.strip():
            raise ValueError("精准替换模式需要输入替换描述（prompt）")

        from core.model_server import inpaint_via_server

        source_rgb = self._source_image
        h, w = source_rgb.shape[:2]

        # 构造掩码
        mask = np.zeros((h, w), dtype=np.uint8)
        for (x1, y1, x2, y2) in self._rois:
            x1, y1 = max(0, int(x1)), max(0, int(y1))
            x2, y2 = min(w, int(x2)), min(h, int(y2))
            mask[y1:y2, x1:x2] = 255

        # flux_fill_prompt：直接走 FluxFiller，不经 IOPaint
        if self.model_id == "flux_fill_prompt":
            from core.flux_filler import FluxFiller
            filler = FluxFiller(device=self.device)
            try:
                return filler.inpaint(
                    image_rgb=source_rgb,
                    mask=mask,
                    prompt=self.prompt,
                )
            finally:
                filler.offload()

        # 其余模型走 IOPaint
        _prompt_model_map = {
            "sdxl_inpaint_prompt":      "diffusers_sd_xl_inpaint",
            "powerpaint_prompt":         "PowerPaint",
            "sd15_inpaint_prompt":       "runwayml/stable-diffusion-inpainting",
        }
        iopaint_model = _prompt_model_map.get(
            self.model_id, "runwayml/stable-diffusion-inpainting"
        )

        return inpaint_via_server(
            image_rgb=source_rgb,
            mask=mask,
            model_name=iopaint_model,
            device=self.device,
            disable_nsfw=True,
            iopaint_path=self.iopaint_path,
            prompt=self.prompt,
        )

    # ------------------------------------------------------------------
    # 方案B：智能定位（auto_segment_edit）
    # ------------------------------------------------------------------

    def _run_auto_segment_edit(self) -> np.ndarray:
        """
        中文指令 → 解析意图 → SegFormer 自动分割服装部位 → HSV 换色 / SD 换风格。
        当模型选择 auto_segment_sd15 时，颜色变更也走 SD Inpainting 路径（更真实但更慢）。
        """
        if not self.prompt.strip():
            raise ValueError("智能定位模式需要输入编辑指令（prompt）")

        from core.intent_parser import parse_instruction
        from core.color_replacer import replace_color_in_mask
        from core.model_server import inpaint_via_server

        source_rgb = self._source_image

        # 按模型选择分割器：grounded_sam_* 使用 GroundingDINO+SAM，其余使用 SegFormer
        use_grounded = self.model_id.startswith("grounded_sam")
        use_sd = (self.model_id == "auto_segment_sd15")
        use_flux_fill = (self.model_id == "grounded_sam_flux")
        use_sdxl_fill = (self.model_id == "grounded_sam_sdxl")

        # Step 1: 解析中文指令
        intent = parse_instruction(self.prompt)

        # Step 2: 分割目标服装部位
        if use_grounded:
            from core.grounded_segmenter import GroundedSegmenter
            segmenter = GroundedSegmenter()
            mask = segmenter.segment(source_rgb, intent.part)
        else:
            from core.human_parser import HumanParser
            parser = HumanParser()
            mask = parser.segment(source_rgb, intent.part)

        if mask is None or mask.max() == 0:
            raise RuntimeError(
                f"未能在图像中检测到「{intent.part}」区域，请确认图像中包含该部位"
            )

        # Step 3: 根据动作类型执行编辑
        if intent.action == "color_change" and not use_sd and not use_flux_fill and not use_sdxl_fill:
            # HSV 换色：亚秒级，保留布料光影
            return replace_color_in_mask(source_rgb, mask, intent.value)

        elif use_flux_fill or use_sdxl_fill:
            # 高质量 Inpainting 路径（FLUX.1-Fill / SDXL）
            if intent.action == "color_change":
                inpaint_prompt = f"{intent.value} colored clothing, photorealistic, high quality, detailed fabric texture"
            else:
                inpaint_prompt = f"{intent.value} style clothing, high quality, detailed, photorealistic"

            if use_flux_fill:
                from core.flux_filler import FluxFiller
                filler = FluxFiller(device=self.device)
                try:
                    return filler.inpaint(
                        image_rgb=source_rgb,
                        mask=mask,
                        prompt=inpaint_prompt,
                    )
                finally:
                    filler.offload()
            else:
                # grounded_sam_sdxl → SDXL Inpainting
                return inpaint_via_server(
                    image_rgb=source_rgb,
                    mask=mask,
                    model_name="diffusers_sd_xl_inpaint",
                    device=self.device,
                    disable_nsfw=True,
                    iopaint_path=self.iopaint_path,
                    prompt=inpaint_prompt,
                )

        elif intent.action == "color_change" and use_sd:
            # SD 换色：效果更真实，耗时更长
            sd_prompt = f"{intent.value} colored clothing, photorealistic, high quality"
            return inpaint_via_server(
                image_rgb=source_rgb,
                mask=mask,
                model_name="runwayml/stable-diffusion-inpainting",
                device=self.device,
                disable_nsfw=True,
                iopaint_path=self.iopaint_path,
                prompt=sd_prompt,
            )

        elif intent.action == "style_change":
            # 风格替换：调用 SD Inpainting
            sd_prompt = f"{intent.value} style clothing, high quality, detailed"
            return inpaint_via_server(
                image_rgb=source_rgb,
                mask=mask,
                model_name="runwayml/stable-diffusion-inpainting",
                device=self.device,
                disable_nsfw=True,
                iopaint_path=self.iopaint_path,
                prompt=sd_prompt,
            )

        else:
            raise ValueError(f"无法解析指令：{self.prompt}\n支持格式：将[部位]换成[颜色/风格]")

    # ------------------------------------------------------------------
    # 方案C：自由编辑（instruction_edit）
    # ------------------------------------------------------------------

    def _run_instruction_edit(self) -> np.ndarray:
        """
        自然语言指令 → 图生图全图语义编辑（无掩码、无参考图）。

        模型分发（按 self.model_id）：
          flux             → FLUX.1-dev Img2Img（最高画质，需 24GB+ VRAM）
          sdxl_img2img     → SDXL Img2Img（高分辨率，需 12GB+ VRAM）
          magicbrush       → MagicBrush（精准指令跟随，推荐默认）
          instruct_pix2pix → 原版 InstructPix2Pix（SD 1.5，快速预览）
        """
        if not self.prompt.strip():
            raise ValueError("自由编辑模式需要输入编辑指令（prompt）")

        from core.instruction_editor import InstructionEditor

        # model_id 与 InstructionEditor 的 model_key 一一对应
        editor = InstructionEditor(model_key=self.model_id, device=self.device)
        try:
            return editor.edit(self._source_image, self.prompt)
        finally:
            editor.offload()
