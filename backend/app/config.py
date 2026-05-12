"""
配置管理模块 - 读取/写入 config/settings.json
"""
import json
import os
from typing import Any

# 项目根目录（backend/ 的上级）
# 使用 core.paths 中的定义（单一数据源）
from core.paths import PROJECT_ROOT, MODELS_DIR

CONFIG_PATH = os.path.join(PROJECT_ROOT, "config", "settings.json")

# 默认值
DEFAULTS = {
    "server.keepalive_seconds": 300,
    "server.port": 51821,
    "server.startup_timeout": 1800,
    "server.low_mem": True,
    "server.cpu_offload": False,
    "server.cpu_textencoder": False,
    "inpaint.default_dilation": 10,
    "inpaint.default_device": "mps",
    "network.hf_endpoint": "https://huggingface.com",
    "network.hf_token": "",
    "download.max_concurrent": 3,
}

_cache: dict = {}
_loaded = False


def _load() -> dict:
    global _cache, _loaded
    if _loaded:
        return _cache
    _loaded = True
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
        flat = {}
        for section, values in raw.items():
            if section.startswith("_"):
                continue
            if isinstance(values, dict):
                for key, val in values.items():
                    if not key.startswith("_"):
                        flat[f"{section}.{key}"] = val
        _cache = flat
    except (FileNotFoundError, json.JSONDecodeError):
        _cache = {}
    return _cache


def get(key: str, default: Any = None) -> Any:
    """读取配置项"""
    cfg = _load()
    if key in cfg:
        return cfg[key]
    return DEFAULTS.get(key, default)


def get_all() -> dict:
    """获取全部配置（合并默认值）"""
    cfg = _load()
    merged = {**DEFAULTS, **cfg}
    return merged


def save(settings: dict) -> None:
    """保存设置到 config/settings.json"""
    # 将扁平化 key 还原为嵌套结构
    nested = {}
    for key, value in settings.items():
        parts = key.split(".", 1)
        if len(parts) == 2:
            section, name = parts
            if section not in nested:
                nested[section] = {}
            nested[section][name] = value
        else:
            nested[key] = value

    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(nested, f, indent=2, ensure_ascii=False)

    # 刷新缓存
    reload()


def reload():
    """重新加载配置"""
    global _loaded
    _loaded = False
    _load()
