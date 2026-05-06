"""
配置读取模块
从 config/settings.json 加载用户配置，提供带默认值的访问接口。
文件不存在或字段缺失时自动回退到默认值，不影响程序运行。
"""
import json
import os
from typing import Any

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CONFIG_PATH = os.path.join(_PROJECT_ROOT, 'config', 'settings.json')

# 默认值（与 settings.json 保持一致）
_DEFAULTS = {
    'server.keepalive_seconds': 300,
    'server.port': 51821,
    'server.startup_timeout': 1800,
    'inpaint.default_dilation': 10,
    'inpaint.default_device': 'mps',
    'network.hf_endpoint': 'https://huggingface.co',
    'network.hf_token': '',
}

_cache: dict = {}
_loaded = False


def _load() -> dict:
    global _cache, _loaded
    if _loaded:
        return _cache
    _loaded = True
    try:
        with open(_CONFIG_PATH, 'r', encoding='utf-8') as f:
            raw = json.load(f)
        # 过滤掉 _comment 键，展平为 "section.key" 形式
        flat = {}
        for section, values in raw.items():
            if section.startswith('_'):
                continue
            if isinstance(values, dict):
                for key, val in values.items():
                    if not key.startswith('_'):
                        flat[f'{section}.{key}'] = val
        _cache = flat
        print(f"[Config] 已加载配置: {_CONFIG_PATH}")
    except FileNotFoundError:
        print(f"[Config] 配置文件不存在，使用默认值: {_CONFIG_PATH}")
    except Exception as e:
        print(f"[Config] 配置文件读取失败，使用默认值: {e}")
    return _cache


def get(key: str, default: Any = None) -> Any:
    """
    读取配置项，key 格式为 'section.name'（如 'server.keepalive_seconds'）。
    未找到时返回内置默认值，若内置也无则返回 default 参数。
    """
    cfg = _load()
    if key in cfg:
        return cfg[key]
    return _DEFAULTS.get(key, default)


def reload():
    """重新从文件加载配置（热重载）"""
    global _loaded
    _loaded = False
    _load()
