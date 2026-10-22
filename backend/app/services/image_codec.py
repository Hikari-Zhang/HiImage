"""Web 图像入口共用的编解码、像素预算与尺寸校验。"""
import base64
import binascii
import io
import warnings

import cv2
import numpy as np
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from PIL import Image, UnidentifiedImageError

from app.config import get


def check_image_size(value: str, max_mb: int | None = None) -> JSONResponse | None:
    """限制 Base64 所代表的压缩文件字节数，不将它当作像素内存。"""
    limit = int(get("image.max_decode_mb", 30)) if max_mb is None else max_mb
    raw = value.split(",", 1)[-1]
    size = (len(raw) * 3 // 4) - min(2, len(raw) - len(raw.rstrip("=")))
    if size > limit * 1024 * 1024:
        message = f"图像文件超过 {limit} MB 限制，请压缩后重试"
        return JSONResponse(status_code=413, content={"detail": message, "message": message})
    return None


def enforce_rgba_budget(
    width: int,
    height: int,
    scale: float = 1,
    *,
    max_mb: int | None = None,
    label: str = "输出",
) -> int:
    limit = int(get("image.max_output_mb", 256)) if max_mb is None else max_mb
    if not np.isfinite(scale) or scale < 1:
        raise HTTPException(422, "输出倍率必须是大于等于 1 的有限数值")
    size = int(np.ceil(width * scale)) * int(np.ceil(height * scale)) * 4
    if size > limit * 1024 * 1024:
        raise HTTPException(413, f"预计{label} RGBA {size / 1024 / 1024:.1f} MB，超过 {limit} MB 限制")
    return size


def validate_matching_dimensions(image: np.ndarray, other: np.ndarray, label: str = "遮罩") -> None:
    if image.shape[:2] != other.shape[:2]:
        raise HTTPException(422, f"{label}尺寸必须与原图一致")


def _decode(value: str, flags: int) -> np.ndarray:
    error = check_image_size(value)
    if error is not None:
        raise HTTPException(413, "图像文件超出大小限制")
    try:
        payload = base64.b64decode(value.split(",", 1)[-1], validate=True)
        # Image.open 只读取头部；先检查像素量，再交给 OpenCV 分配像素缓冲区。
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(payload)) as header:
                width, height = header.size
                enforce_rgba_budget(
                    width, height,
                    max_mb=int(get("image.max_input_rgba_mb", 256)), label="输入",
                )
                enforce_rgba_budget(width, height)
        image = cv2.imdecode(np.frombuffer(payload, np.uint8), flags)
        if image is None:
            raise ValueError("无法解码图像")
        enforce_rgba_budget(
            image.shape[1], image.shape[0],
            max_mb=int(get("image.max_input_rgba_mb", 256)), label="输入",
        )
        return image
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise HTTPException(413, "图像像素量超出安全解码限制") from exc
    except (ValueError, binascii.Error, OSError, UnidentifiedImageError, cv2.error) as exc:
        raise HTTPException(422, "无法解码图像，请上传有效的图片或遮罩") from exc


def decode_image(value: str) -> np.ndarray:
    return cv2.cvtColor(_decode(value, cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)


def decode_mask(value: str) -> np.ndarray:
    return _decode(value, cv2.IMREAD_GRAYSCALE)


def decode_rgba_image(value: str) -> np.ndarray:
    # 固定 uint8、四通道，统一灰度、RGB、RGBA 和 16 位输入的输出契约。
    image = _decode(value, cv2.IMREAD_UNCHANGED)
    if image.dtype == np.uint16:
        image = (image / 257).astype(np.uint8)
    if image.ndim == 2:
        return cv2.cvtColor(image, cv2.COLOR_GRAY2RGBA)
    return cv2.cvtColor(image, cv2.COLOR_BGRA2RGBA if image.shape[2] == 4 else cv2.COLOR_BGR2RGBA)


def _encode(image: np.ndarray) -> str:
    enforce_rgba_budget(image.shape[1], image.shape[0])
    success, buffer = cv2.imencode(".png", image)
    if not success:
        raise RuntimeError("图像编码失败")
    return base64.b64encode(buffer).decode("ascii")


def encode_image(image: np.ndarray) -> str:
    return _encode(cv2.cvtColor(image, cv2.COLOR_RGB2BGR))


def encode_rgba_image(image: np.ndarray) -> str:
    return _encode(cv2.cvtColor(image, cv2.COLOR_RGBA2BGRA))


def encode_mask(mask: np.ndarray) -> str:
    return _encode(mask)
