"""
Rembg 抠图执行器

包装 rembg 库，用于图像抠图（matting）。
"""
import os

import numpy as np
from typing import Dict, Any
from .model_executor import BaseModelExecutor
from .paths import rembg_model_path
from .downloaders import download_rembg, rembg_checksum_matches


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
        import onnxruntime as ort
        from rembg.sessions import sessions_class

        session_name = self.model_config["rembg_session_name"]
        implementation = next((cls for cls in sessions_class if cls.name() == session_name), None)
        if implementation is None:
            raise ValueError(f"rembg 未提供会话：{session_name}")
        weight = rembg_model_path(self.model_config, installed=True)
        if not weight.is_file() or not rembg_checksum_matches(self.model_config, weight):
            download_rembg(self.model_config)
            weight = rembg_model_path(self.model_config)

        # rembg 通常根据会话名重新拼文件名；直接交给 ONNX 已选定的注册表权重。
        class LocalSession(implementation):
            @classmethod
            def download_models(cls, *args, **kwargs):
                return str(weight)

        options = ort.SessionOptions()
        if threads := os.environ.get("OMP_NUM_THREADS"):
            options.inter_op_num_threads = options.intra_op_num_threads = int(threads)
        self._session = LocalSession(session_name, options)

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
        import rembg
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
