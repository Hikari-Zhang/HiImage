"""支持 Unicode 路径的图片文件读写工具。"""

from os import PathLike
from pathlib import Path

import cv2
import numpy as np


def write_image(
    file_path: str | PathLike[str],
    image: np.ndarray,
) -> None:
    """
    将 OpenCV 图片写入文件，并支持 Windows 中文路径。

    :param file_path: 输出文件路径，扩展名用于选择图片编码格式。
    :param image: OpenCV 图片数组。
    :raises OSError: 图片编码或文件写入失败。
    """
    path = Path(file_path)
    extension = path.suffix or ".png"

    try:
        success, encoded = cv2.imencode(extension, image)
    except cv2.error as exc:
        raise OSError(f"图片编码失败: {path}: {exc}") from exc

    if not success or encoded is None:
        raise OSError(f"图片编码失败: {path}")

    try:
        path.write_bytes(encoded.tobytes())
    except OSError as exc:
        raise OSError(f"图片写入失败: {path}: {exc}") from exc
