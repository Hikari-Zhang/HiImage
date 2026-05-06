"""
中文服装编辑指令解析器

支持格式：
  - 将[部位]换成[颜色]         → action=color_change
  - 把[部位]改成[颜色]         → action=color_change
  - 将[部位]改成[风格]         → action=style_change
  - 将[部位]换成[风格]风格      → action=style_change
  - [部位]换[颜色]             → action=color_change（简写）

支持部位（中文 → SegFormer label）：
  上衣/衬衫/外套/夹克/毛衣/连衣裙 → Upper-clothes
  裤子/牛仔裤/短裤               → Pants
  裙子/半身裙                    → Skirt
  鞋子/运动鞋/靴子               → Left-shoe + Right-shoe
  帽子/棒球帽/头盔               → Hat
  手套                          → Left-glove + Right-glove
  袜子                          → Socks
  腰带/皮带                     → Belt
  围巾                          → Scarf
  头发                          → Hair
  皮肤/肤色                     → Skin
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


# ──────────────────────────────────────────────────────────────
# 映射表
# ──────────────────────────────────────────────────────────────

# 中文部位词 → SegFormer 标准 label
PART_ALIASES: dict[str, str] = {
    # 上半身
    "上衣": "Upper-clothes",
    "衬衫": "Upper-clothes",
    "外套": "Upper-clothes",
    "夹克": "Upper-clothes",
    "毛衣": "Upper-clothes",
    "卫衣": "Upper-clothes",
    "T恤": "Upper-clothes",
    "t恤": "Upper-clothes",
    "连衣裙": "Upper-clothes",
    "旗袍": "Upper-clothes",
    "西装": "Upper-clothes",
    "大衣": "Upper-clothes",
    "风衣": "Upper-clothes",
    "背心": "Upper-clothes",
    "马甲": "Upper-clothes",
    # 下半身
    "裤子": "Pants",
    "牛仔裤": "Pants",
    "短裤": "Pants",
    "西裤": "Pants",
    "运动裤": "Pants",
    "裙子": "Skirt",
    "半身裙": "Skirt",
    "短裙": "Skirt",
    "长裙": "Skirt",
    # 鞋
    "鞋子": "__shoes__",   # 特殊标记，表示需要合并左右鞋
    "鞋": "__shoes__",
    "运动鞋": "__shoes__",
    "靴子": "__shoes__",
    "高跟鞋": "__shoes__",
    "拖鞋": "__shoes__",
    "凉鞋": "__shoes__",
    # 帽子
    "帽子": "Hat",
    "棒球帽": "Hat",
    "头盔": "Hat",
    "遮阳帽": "Hat",
    # 手套
    "手套": "__gloves__",
    # 袜子
    "袜子": "Socks",
    "袜": "Socks",
    # 配件
    "腰带": "Belt",
    "皮带": "Belt",
    "围巾": "Scarf",
    # 身体部位
    "头发": "Hair",
    "发型": "Hair",
    "皮肤": "Skin",
    "肤色": "Skin",
    "脸": "Face",
    "背景": "Background",
}

# 颜色词集合（用于判断是换色还是换风格）
KNOWN_COLORS: frozenset[str] = frozenset([
    "黑色", "黑",
    "白色", "白",
    "红色", "红",
    "蓝色", "蓝",
    "绿色", "绿",
    "黄色", "黄",
    "橙色", "橙",
    "紫色", "紫",
    "粉色", "粉红",
    "灰色", "灰",
    "棕色", "棕", "咖啡色",
    "米色", "米白",
    "藏青色", "藏青",
    "深蓝", "浅蓝", "天蓝",
    "深红", "浅红", "玫瑰红",
    "深绿", "浅绿", "军绿",
    "深灰", "浅灰",
    "金色", "金", "银色", "银",
])

# 风格关键词（用于识别 style_change）
STYLE_KEYWORDS: frozenset[str] = frozenset([
    "牛仔", "皮革", "格纹", "条纹", "迷彩", "格子",
    "蕾丝", "丝绸", "羊绒", "羽绒", "棉质",
    "运动风格", "正式风格", "休闲风格", "复古风格",
    "风格", "款式", "材质",
])


# ──────────────────────────────────────────────────────────────
# 解析结果
# ──────────────────────────────────────────────────────────────

@dataclass
class ParsedIntent:
    """指令解析结果"""
    part: str           # SegFormer label（如 "Upper-clothes"）或 "__shoes__"
    action: str         # "color_change" 或 "style_change"
    value: str          # 颜色名（如 "红色"）或风格描述（如 "牛仔风格"）
    raw_text: str       # 原始指令文本


# ──────────────────────────────────────────────────────────────
# 解析函数
# ──────────────────────────────────────────────────────────────

def parse_instruction(text: str) -> ParsedIntent:
    """
    解析中文服装编辑指令，返回 ParsedIntent。

    :raises ValueError: 无法识别部位或无法理解指令格式
    """
    text = text.strip()

    # 模式1: 将/把 [部位] 换成/改成/变成 [目标]
    # 模式2: [部位] 换 [目标]（简写）
    patterns = [
        r"[将把](.+?)(?:换成|改成|变成|改为|换为)(.+?)(?:风格|款式|材质|色)?$",
        r"(.+?)(?:换|改)(?:成|为)?(.+?)(?:风格|款式|材质|色)?$",
    ]

    matched_part_text: Optional[str] = None
    matched_value: Optional[str] = None

    for pat in patterns:
        m = re.search(pat, text)
        if m:
            matched_part_text = m.group(1).strip()
            matched_value = m.group(2).strip()
            break

    if not matched_part_text or not matched_value:
        raise ValueError(
            f"无法解析指令：「{text}」\n"
            "支持格式：\n"
            "  将[部位]换成[颜色]   例：将上衣换成黑色\n"
            "  把[部位]改成[风格]   例：把裤子改成牛仔风格"
        )

    # 识别服装部位
    part_label = _match_part(matched_part_text)
    if part_label is None:
        raise ValueError(
            f"无法识别服装部位：「{matched_part_text}」\n"
            f"支持的部位：{', '.join(sorted(PART_ALIASES.keys()))}"
        )

    # 判断动作类型：换色 vs 换风格
    action, normalized_value = _classify_action(matched_value)

    return ParsedIntent(
        part=part_label,
        action=action,
        value=normalized_value,
        raw_text=text,
    )


def _match_part(text: str) -> Optional[str]:
    """在 PART_ALIASES 中查找最长匹配"""
    # 优先尝试完整匹配
    if text in PART_ALIASES:
        return PART_ALIASES[text]
    # 尝试包含匹配（从最长词开始）
    for alias in sorted(PART_ALIASES.keys(), key=len, reverse=True):
        if alias in text:
            return PART_ALIASES[alias]
    return None


def _classify_action(value_text: str) -> tuple[str, str]:
    """
    判断目标是颜色还是风格。
    返回 (action, normalized_value)
    """
    # 先检查是否包含颜色词
    for color in sorted(KNOWN_COLORS, key=len, reverse=True):
        if color in value_text:
            return "color_change", color

    # 再检查风格词
    for style in sorted(STYLE_KEYWORDS, key=len, reverse=True):
        if style in value_text:
            return "style_change", value_text

    # 若无法明确判断，尝试按颜色词后缀推断（"红色的" → "红色"）
    # 默认当作风格处理
    return "style_change", value_text
