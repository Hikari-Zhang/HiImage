"""
核心工具函数（供 core 和 app 层共同使用）。

原本在 app/routers/models.py 和 core/download_queue.py 中各自定义了一份，
现提取到此处，消除重复代码和反向依赖。
"""
import platform


def fmt_speed(bytes_per_sec: float) -> str:
    """将字节/秒格式化为人类可读字符串。"""
    if bytes_per_sec >= 1024 * 1024:
        return f"{bytes_per_sec / (1024 * 1024):.1f} MB/s"
    elif bytes_per_sec >= 1024:
        return f"{bytes_per_sec / 1024:.0f} KB/s"
    return f"{bytes_per_sec:.0f} B/s"


def fmt_size(total_bytes: int) -> str:
    """将字节数格式化为人类可读字符串。"""
    if total_bytes >= 1024 * 1024 * 1024:
        return f"{total_bytes / (1024**3):.1f} GB"
    elif total_bytes >= 1024 * 1024:
        return f"{total_bytes / (1024**2):.0f} MB"
    elif total_bytes >= 1024:
        return f"{total_bytes / 1024:.0f} KB"
    return f"{total_bytes} B"


def default_device_for_platform(
    system: str | None = None,
    machine: str | None = None,
) -> str:
    """返回平台默认设备：仅 Apple Silicon macOS 默认 MPS。"""
    current_system = system if system is not None else platform.system()
    current_machine = machine if machine is not None else platform.machine()
    is_apple_silicon = current_machine.lower() in {"arm64", "aarch64"}
    return "mps" if current_system == "Darwin" and is_apple_silicon else "cpu"


def resolve_default_device(
    explicit: str | None = None,
    system: str | None = None,
    machine: str | None = None,
) -> str:
    """显式设置优先，否则使用平台默认设备。"""
    return explicit or default_device_for_platform(system, machine)


def resolve_effective_device(model_name: str, device: str) -> str:
    """
    查询 models.yaml 中模型的 device_override 字段，返回实际使用的设备名。

    查找顺序：
      1. 按 model_name 直接匹配 MODEL_BY_ID
      2. 按 engine_model_id 字段回退匹配（兼容原生引擎的模型别名）
      3. 均未找到或无 device_override 时，返回原始 device

    用法：
        from core.utils import resolve_effective_device
        device = resolve_effective_device("wm_lama", "mps")
    """
    try:
        from core.model_registry import MODEL_BY_ID, MODELS  # lazy import 避免循环依赖
        cfg = MODEL_BY_ID.get(model_name)
        if cfg is None:
            # 按 engine_model_id 回退查找
            for m in MODELS:
                if m.get("engine_model_id") == model_name:
                    cfg = m
                    break
        if cfg:
            override = cfg.get("device_override")
            if override:
                return override
    except Exception:
        pass
    return device
