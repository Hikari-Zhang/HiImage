"""在下载和推理前校验功能是否有可用实现。"""
from core.model_registry import get_mode, get_model


class FeatureUnavailable(RuntimeError):
    pass


def require_mode(mode: str) -> None:
    try:
        config = get_mode(mode)
    except KeyError as exc:
        raise ValueError(f"未知模式: {mode}") from exc
    if config.get("available", True) is False:
        raise FeatureUnavailable(config.get("unavailable_reason", "功能暂不可用"))


def require_model(mode: str, model_id: str) -> None:
    require_mode(mode)
    try:
        config = get_model(model_id)
    except KeyError as exc:
        raise FeatureUnavailable("所选模型已移除，请重新选择可用模型") from exc
    if mode not in config.get("tags", []):
        raise ValueError(f"模型 {model_id} 不支持模式 {mode}")
