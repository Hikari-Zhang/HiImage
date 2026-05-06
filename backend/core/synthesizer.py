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

模型配置（名称/描述/下载链接/支持参数/功能）统一由 core/model_registry.py 维护。
新增或修改模型时只需编辑 model_registry.py，本文件无需改动。
"""
from __future__ import annotations

import cv2
import numpy as np
from typing import Optional, List, Tuple

# ──────────────────────────────────────────────────────────────
# 从集中配置导入模型注册表与模式分组
# 新增模型时只需修改 core/model_registry.py，无需改此文件
# ──────────────────────────────────────────────────────────────
from core.model_registry import MODELS as SYNTHESIS_MODELS, MODE_GROUPS as SYNTHESIS_MODE_GROUPS

__all__ = ["SYNTHESIS_MODELS", "SYNTHESIS_MODE_GROUPS", "Synthesizer"]


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
        iopaint_path: Optional[str] = None,
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
        # ── Pre-flight：模型完整性预检 ────────────────────────────────────────
        # 在推理开始前检测模型文件是否存在/完整，提供明确的中文错误提示。
        # ImportError 时跳过检测（不阻断推理），保证向后兼容。
        try:
            from core.model_checker import ModelChecker
            _checker = ModelChecker()
            _chk = _checker.check_model(self.model_id)
            if _chk.status == "missing":
                raise RuntimeError(
                    f"模型 '{self.model_id}'（{_chk.name}）尚未下载。\n"
                    f"  详情：{_chk.message}\n"
                    f"  请先下载模型，或运行：python scripts/check_models.py"
                )
            elif _chk.status in ("corrupted", "partial"):
                raise RuntimeError(
                    f"模型 '{self.model_id}'（{_chk.name}）文件可能已损坏或不完整。\n"
                    f"  详情：{_chk.message}\n"
                    f"  建议重新下载，或运行：python scripts/check_models.py"
                )
        except ImportError:
            pass  # model_checker 不可用时跳过检测
        # ── End Pre-flight ────────────────────────────────────────────────────

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
            "sdxl_inpaint_prompt":  "diffusers_sd_xl_inpaint",
            "powerpaint_prompt":    "PowerPaint",
            "sd15_inpaint_prompt":  "runwayml/stable-diffusion-inpainting",
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
