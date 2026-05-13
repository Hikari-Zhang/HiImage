"""
核心工具函数（供 core 和 app 层共同使用）。

原本在 app/routers/models.py 和 core/download_queue.py 中各自定义了一份，
现提取到此处，消除重复代码和反向依赖。
"""


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
