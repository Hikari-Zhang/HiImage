"""
基于 HSV 色彩空间的服装换色模块

通过 HSV Hue 通道替换实现服装颜色替换：
  - 有色目标（红/蓝/绿/黄等）：替换 H 通道，插值 S 通道，保留 V（布料光影）
  - 无色目标（黑/白/灰）：  调整 V 通道 + 强制去饱和
  - 边缘羽化：通过 Gaussian blur mask 实现自然过渡
"""
from __future__ import annotations

import numpy as np
import cv2


# ──────────────────────────────────────────────────────────────
# 颜色表（颜色名 → BGR 值，OpenCV 默认色彩空间）
# ──────────────────────────────────────────────────────────────

# 先定义 RGB，再做转换（更直观）
_COLOR_TABLE_RGB: dict[str, tuple[int, int, int]] = {
    # 无色系
    "黑色": (10,   10,  10),
    "黑":   (10,   10,  10),
    "白色": (245, 245, 245),
    "白":   (245, 245, 245),
    "灰色": (128, 128, 128),
    "灰":   (128, 128, 128),
    "深灰": ( 64,  64,  64),
    "浅灰": (192, 192, 192),
    # 有色系
    "红色": (220,  30,  30),
    "红":   (220,  30,  30),
    "深红": (139,   0,   0),
    "浅红": (255, 130, 130),
    "玫瑰红": (220,  60,  90),
    "蓝色": ( 30,  90, 210),
    "蓝":   ( 30,  90, 210),
    "深蓝": (  0,   0, 139),
    "浅蓝": (135, 170, 230),
    "天蓝": ( 87, 165, 225),
    "藏青色": ( 25,  50, 100),
    "藏青": ( 25,  50, 100),
    "绿色": ( 40, 170,  60),
    "绿":   ( 40, 170,  60),
    "深绿": (  0, 100,   0),
    "浅绿": (144, 238, 144),
    "军绿": ( 85, 107,  47),
    "黄色": (240, 210,  20),
    "黄":   (240, 210,  20),
    "橙色": (240, 120,  20),
    "橙":   (240, 120,  20),
    "紫色": (140,  50, 180),
    "紫":   (140,  50, 180),
    "粉色": (255, 160, 190),
    "粉红": (255, 105, 180),
    "棕色": (130,  80,  40),
    "棕":   (130,  80,  40),
    "咖啡色": (130,  80,  40),
    "米色": (245, 235, 200),
    "米白": (245, 235, 200),
    "金色": (210, 175,  50),
    "金":   (210, 175,  50),
    "银色": (192, 192, 192),
    "银":   (192, 192, 192),
}

# 转为 HSV（OpenCV 色彩空间：H 0-179, S 0-255, V 0-255）
COLOR_TABLE: dict[str, tuple[int, int, int]] = {}
for _name, _rgb in _COLOR_TABLE_RGB.items():
    _bgr = np.array([[list(reversed(_rgb))]], dtype=np.uint8)
    _hsv = cv2.cvtColor(_bgr, cv2.COLOR_BGR2HSV)[0, 0]
    COLOR_TABLE[_name] = (int(_hsv[0]), int(_hsv[1]), int(_hsv[2]))

# 无色系：S 接近 0，不需要替换 H
_ACHROMATIC_COLORS = frozenset([
    "黑色", "黑", "白色", "白", "灰色", "灰",
    "深灰", "浅灰", "银色", "银",
])


# ──────────────────────────────────────────────────────────────
# 主接口
# ──────────────────────────────────────────────────────────────

def replace_color_in_mask(
    image_rgb: np.ndarray,
    mask: np.ndarray,
    color_name: str,
    feather_radius: int = 15,
    saturation_blend: float = 0.7,
) -> np.ndarray:
    """
    在 mask 覆盖的区域内替换颜色。

    :param image_rgb:        原图（RGB uint8）
    :param mask:             目标区域掩码（uint8，255=目标，0=背景）
    :param color_name:       目标颜色名（在 COLOR_TABLE 中查找）
    :param feather_radius:   边缘羽化半径（像素）
    :param saturation_blend: 饱和度混合比例（0=保留原饱和度，1=完全用目标饱和度）
    :return:                 替换后的 RGB 图像
    """
    if color_name not in COLOR_TABLE:
        raise ValueError(
            f"不支持的颜色：「{color_name}」\n"
            f"支持的颜色：{', '.join(sorted(COLOR_TABLE.keys()))}"
        )

    target_h, target_s, target_v = COLOR_TABLE[color_name]

    # 原图转 HSV
    src_bgr = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2BGR)
    src_hsv = cv2.cvtColor(src_bgr, cv2.COLOR_BGR2HSV).astype(np.float32)

    # 生成羽化 mask（float32，0.0~1.0）
    mask_f = mask.astype(np.float32) / 255.0
    if feather_radius > 0:
        ksize = feather_radius * 2 + 1
        mask_f = cv2.GaussianBlur(mask_f, (ksize, ksize), feather_radius / 2.0)

    result_hsv = src_hsv.copy()

    if color_name in _ACHROMATIC_COLORS:
        # 无色系：去饱和 + 调整亮度
        # 计算亮度调整因子（相对原始亮度的缩放）
        v_ratio = target_v / 128.0   # 128 为中性灰参考
        new_v = np.clip(src_hsv[:, :, 2] * v_ratio, 0, 255)
        result_hsv[:, :, 1] = src_hsv[:, :, 1] * (1.0 - mask_f)   # 降低饱和度
        result_hsv[:, :, 2] = src_hsv[:, :, 2] * (1.0 - mask_f) + new_v * mask_f
    else:
        # 有色系：替换 H，混合 S，保留 V（保留布料光影）
        result_hsv[:, :, 0] = (
            src_hsv[:, :, 0] * (1.0 - mask_f)
            + target_h * mask_f
        )
        # S 通道：在原始 S 与目标 S 之间插值
        new_s = src_hsv[:, :, 1] * (1.0 - saturation_blend) + target_s * saturation_blend
        result_hsv[:, :, 1] = (
            src_hsv[:, :, 1] * (1.0 - mask_f)
            + new_s * mask_f
        )
        # V 通道不变（保留光影细节）

    result_hsv = np.clip(result_hsv, 0, 255).astype(np.uint8)
    result_bgr = cv2.cvtColor(result_hsv, cv2.COLOR_HSV2BGR)
    return cv2.cvtColor(result_bgr, cv2.COLOR_BGR2RGB)
