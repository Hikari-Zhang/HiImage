"""
Rembg 抠图执行器

包装 rembg 库，用于图像抠图（matting）。
"""
import numpy as np
from typing import Dict, Any
from .model_executor import BaseModelExecutor


class RembgExecutor(BaseModelExecutor):
    """
    Rembg 抠图执行器

    支持模型：
    - birefnet（推荐·通用）
    - rmbg（商业级精度）
    - isnet（通用目标分割）
    - isnet_anime（动漫优化）
    - u2net（通用显著目标检测）
    - modnet（轻量人像抠图）
    """

    def __init__(self, model_config: Dict[str, Any], device: str):
        super().__init__(model_config, device)
        self._session = None

    def load_model(self) -> None:
        """加载 rembg session"""
        import rembg

        # 从配置中获取 rembg session 名称
        session_name = self.model_config.get("rembg_session_name", "birefnet-general")
        self._session = rembg.new_session(session_name)

    def execute(self, image: np.ndarray, **kwargs) -> np.ndarray:
        """
        执行抠图（不需要 mask）

        :param image: 输入图像（RGB, uint8）
        :return: 抠图结果（RGBA, uint8）
        """
        if self._session is None:
            self.load_model()

        # RGB → BGR
        import cv2
        image_bgr = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)

        # 编码为 bytes
        _, img_bytes = cv2.imencode(".png", image_bgr)

        # 抠图
        from io import BytesIO
        img_bytes_io = BytesIO(img_bytes.tobytes())
        result_bytes = rembg.remove(img_bytes_io.read(), session=self._session)

        # 解码结果
        result_bytes_io = BytesIO(result_bytes)
        result_arr = np.frombuffer(result_bytes_io.read(), dtype=np.uint8)
        result_bgra = cv2.imdecode(result_arr, cv2.IMREAD_UNCHANGED)

        if result_bgra is None or result_bgra.shape[2] < 4:
            raise RuntimeError("抠图失败：无法获取 Alpha 通道")

        # BGRA → RGBA → RGB（如果需要）
        result_rgba = cv2.cvtColor(result_bgra, cv2.COLOR_BGRA2RGBA)

        return result_rgba

    def unload_model(self) -> None:
        """卸载模型（rembg 不提供显式卸载接口）"""
        self._session = None

    def supports_mask(self) -> bool:
        """Rembg 不需要 mask"""
        return False
