"""模型分组的唯一序列化入口，供独立及聚合 API 共用。"""
from core.model_registry import get_default_model, get_models_for_mode, get_mode


def model_groups(mode: str) -> dict:
    groups: dict[str, list] = {}
    for model in get_models_for_mode(mode):
        item = {
            "id": model["id"],
            "name": model.get("name", model["id"]),
            "description": model.get("description", ""),
            "badge": model.get("badge", ""),
        }
        if mode == "upscale":
            item.update(
                scale=model.get("scale", 4),
                outscale=model.get("outscale", model.get("scale", 4)),
                supports_custom_outscale=model.get("supports_custom_outscale", False),
            )
        elif mode == "watermark_removal":
            item["device_override"] = model.get("device_override")
            item["pipeline"] = model.get("pipeline")
        elif mode == "cutout":
            item.update(
                recommended_for=model.get("recommended_for", ""),
                size_mb=model.get("size_mb"), provider=model.get("provider", ""),
            )
        label = model.get("display_group", "通用抠图" if mode == "cutout" else "其他")
        groups.setdefault(label, []).append(item)
    result = {"groups": [{"label": label, "models": models} for label, models in groups.items()]}
    config = get_mode(mode)
    result.update(available=config.get("available", True), unavailable_reason=config.get("unavailable_reason", ""))
    if mode == "watermark_removal":
        result["default_model"] = config.get("default_model")
    if mode == "cutout":
        try:
            result["default_model"] = get_default_model("cutout")
        except KeyError:
            result["default_model"] = "birefnet"
    return result
