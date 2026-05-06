"""
后处理修复模块 - 修复 inpainting 后的背景错乱

支持三种方法：
  - poisson     : Poisson融合（OpenCV seamlessClone），修复边缘接缝，零额外依赖
  - gfpgan      : GFPGAN 人脸修复，修复人脸区域错乱，首次使用自动下载权重
  - lama_refine : 用 LaMa 二次精修修复区域，背景一致性最佳
"""
from __future__ import annotations

import os
import sys
import numpy as np
import cv2
from typing import Optional, Literal

PostMethod = Literal["poisson", "gfpgan", "lama_refine", "none"]

# GFPGAN 权重存放目录（放在项目 models/ 下统一管理）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_GFPGAN_WEIGHTS_DIR = os.path.join(_PROJECT_ROOT, "models", "gfpgan")
_GFPGAN_WEIGHT_FILE = "GFPGANv1.4.pth"
_GFPGAN_WEIGHT_URL = (
    "https://github.com/TencentARC/GFPGAN/releases/download/v1.3.4/GFPGANv1.4.pth"
)

# 可供 GUI 直接读取的方法列表
METHOD_GROUPS = [
    ("── 后处理修复 ──", [
        ("none",        "无后处理",                 "跳过后处理，直接输出"),
        ("poisson",     "Poisson融合（修边缘）",     "修复修复区域边缘的明显接缝，零额外模型，速度极快"),
        ("lama_refine", "LaMa 二次精修",             "用 LaMa 对修复区域做二次修正，背景纹理一致性更高"),
        ("gfpgan",      "GFPGAN（人脸区域专用）",    "修复人脸/人像区域的错乱，首次使用自动下载 ~340MB 权重"),
    ]),
]

AVAILABLE_METHODS = [m for _, methods in METHOD_GROUPS for m, _, _ in methods]


# ──────────────────────────────────────────────────────────────
# 公开接口
# ──────────────────────────────────────────────────────────────

def fix_background(
    original_rgb: np.ndarray,
    inpainted_rgb: np.ndarray,
    mask: np.ndarray,
    method: PostMethod = "poisson",
    device: str = "mps",
    iopaint_path: Optional[str] = None,
) -> np.ndarray:
    """
    对 inpainting 结果做后处理修复。

    :param original_rgb:  原始图像（去水印之前），RGB numpy array
    :param inpainted_rgb: inpainting 输出图像，RGB numpy array
    :param mask:          水印掩码，0/255 灰度图，与图像同尺寸
    :param method:        后处理方法，见 AVAILABLE_METHODS
    :param device:        推理设备（mps/cuda/cpu），影响 GFPGAN / LaMa
    :param iopaint_path:  iopaint 可执行路径（lama_refine 模式使用）
    :return:              处理后的 RGB numpy array
    """
    if method == "none" or method is None:
        return inpainted_rgb

    if method == "poisson":
        return _poisson_blend(original_rgb, inpainted_rgb, mask)

    if method == "gfpgan":
        return _gfpgan_enhance(inpainted_rgb, device=device)

    if method == "lama_refine":
        return _lama_refine(original_rgb, inpainted_rgb, mask, device=device, iopaint_path=iopaint_path)

    raise ValueError(f"不支持的后处理方法: {method}，可选: {AVAILABLE_METHODS}")


# ──────────────────────────────────────────────────────────────
# Poisson 融合
# ──────────────────────────────────────────────────────────────

def _poisson_blend(
    original_rgb: np.ndarray,
    inpainted_rgb: np.ndarray,
    mask: np.ndarray,
) -> np.ndarray:
    """
    Poisson 融合（seamlessClone）：
    将 inpainting 结果区域与原图做梯度域融合，消除明显的边缘接缝。
    """
    h, w = original_rgb.shape[:2]

    # seamlessClone 需要 BGR
    dst_bgr = cv2.cvtColor(original_rgb, cv2.COLOR_RGB2BGR)
    src_bgr = cv2.cvtColor(inpainted_rgb, cv2.COLOR_RGB2BGR)

    # 确保 mask 为 uint8 0/255
    mask_u8 = (mask > 127).astype(np.uint8) * 255

    # 找到 mask 的有效区域中心点
    coords = cv2.findNonZero(mask_u8)
    if coords is None:
        # 没有遮罩区域，直接返回
        return inpainted_rgb

    x, y, bw, bh = cv2.boundingRect(coords)
    cx = x + bw // 2
    cy = y + bh // 2

    # seamlessClone 要求中心点严格在图像内部且有足够边距
    cx = int(np.clip(cx, 1, w - 2))
    cy = int(np.clip(cy, 1, h - 2))

    try:
        result_bgr = cv2.seamlessClone(src_bgr, dst_bgr, mask_u8, (cx, cy), cv2.NORMAL_CLONE)
        return cv2.cvtColor(result_bgr, cv2.COLOR_BGR2RGB)
    except cv2.error as e:
        # Poisson 融合偶尔在极小遮罩区域下失败，退化为直接返回 inpainted 结果
        print(f"[BackgroundFixer] Poisson融合失败（退化为原始inpainting结果）: {e}")
        return inpainted_rgb


# ──────────────────────────────────────────────────────────────
# GFPGAN 人脸修复
# ──────────────────────────────────────────────────────────────

def _gfpgan_enhance(
    image_rgb: np.ndarray,
    device: str = "mps",
) -> np.ndarray:
    """
    使用 GFPGAN 修复整图中的人脸区域。
    首次调用时自动下载模型权重（~340MB）。
    """
    weight_path = _ensure_gfpgan_weights()

    try:
        from gfpgan import GFPGANer
    except ImportError:
        raise ImportError(
            "缺少 gfpgan 依赖包。\n"
            "请在项目虚拟环境中执行：pip install gfpgan"
        )

    # GFPGAN device 参数：0=GPU, -1=CPU，MPS 走 GPU 路径
    gpu_id = -1 if device == "cpu" else 0

    restorer = GFPGANer(
        model_path=weight_path,
        upscale=1,           # 不放大，只修复
        arch="clean",
        channel_multiplier=2,
        bg_upsampler=None,   # 不做背景超分，只修人脸
        device=None,         # 由 gpu_id 决定
    )

    # GFPGAN 内部使用 BGR
    img_bgr = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2BGR)
    _, _, restored_bgr = restorer.enhance(
        img_bgr,
        has_aligned=False,
        only_center_face=False,
        paste_back=True,
    )

    if restored_bgr is None:
        # 没有检测到人脸，原样返回
        print("[BackgroundFixer] GFPGAN 未检测到人脸，返回原图")
        return image_rgb

    return cv2.cvtColor(restored_bgr, cv2.COLOR_BGR2RGB)


def _ensure_gfpgan_weights() -> str:
    """确保 GFPGAN 权重文件存在，不存在则自动下载"""
    os.makedirs(_GFPGAN_WEIGHTS_DIR, exist_ok=True)
    weight_path = os.path.join(_GFPGAN_WEIGHTS_DIR, _GFPGAN_WEIGHT_FILE)

    if os.path.exists(weight_path):
        return weight_path

    print(f"[BackgroundFixer] 正在下载 GFPGAN 权重: {_GFPGAN_WEIGHT_URL}")
    import urllib.request

    def _reporthook(block_num, block_size, total_size):
        downloaded = block_num * block_size
        if total_size > 0:
            pct = min(100, downloaded * 100 // total_size)
            bar = '#' * (pct // 5) + '.' * (20 - pct // 5)
            print(f"\r[BackgroundFixer] [{bar}] {pct}%  ", end='', flush=True)
        else:
            mb = downloaded / 1024 / 1024
            print(f"\r[BackgroundFixer] 已下载 {mb:.1f} MB  ", end='', flush=True)

    try:
        urllib.request.urlretrieve(_GFPGAN_WEIGHT_URL, weight_path, reporthook=_reporthook)
        print()
        print(f"[BackgroundFixer] GFPGAN 权重下载完成: {weight_path}")
    except Exception as e:
        if os.path.exists(weight_path):
            os.remove(weight_path)
        raise RuntimeError(
            f"GFPGAN 权重下载失败: {e}\n"
            f"请手动下载至：{weight_path}\n"
            f"下载链接：{_GFPGAN_WEIGHT_URL}"
        ) from e

    return weight_path


# ──────────────────────────────────────────────────────────────
# LaMa 二次精修
# ──────────────────────────────────────────────────────────────

def _lama_refine(
    original_rgb: np.ndarray,
    inpainted_rgb: np.ndarray,
    mask: np.ndarray,
    device: str = "mps",
    iopaint_path: Optional[str] = None,
) -> np.ndarray:
    """
    用 LaMa 对 inpainting 结果做二次精修：
    对 inpainted_rgb 在遮罩区域再跑一次 LaMa，进一步改善背景纹理一致性。
    """
    # 构造一个略微收缩的 mask（防止 LaMa 二次修复时再次过度扩散）
    kernel = np.ones((5, 5), np.uint8)
    refined_mask = cv2.erode(mask, kernel, iterations=1)

    # 如果 mask 收缩后为空，退化为原始结果
    if cv2.countNonZero(refined_mask) == 0:
        return inpainted_rgb

    try:
        from core.inpainter import Inpainter
        inpainter = Inpainter(
            model_name="lama",
            device=device,
            dilation=0,       # 已经有 mask，不需要再扩张
            iopaint_path=iopaint_path,
        )
        return inpainter.remove_watermark_with_mask(inpainted_rgb, refined_mask)
    except Exception as e:
        print(f"[BackgroundFixer] LaMa 二次精修失败（退化为 inpainting 结果）: {e}")
        return inpainted_rgb
