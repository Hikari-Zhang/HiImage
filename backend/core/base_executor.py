"""
通用执行器抽象基类

为不依赖 models.yaml / ModelExecutorFactory 体系的执行器（如 Inpainter、Upscaler、
Synthesizer 等具有更丰富构造参数的类）提供统一的 run() 接口契约。

与 BaseModelExecutor（model_executor.py）的区别：
  - BaseModelExecutor：面向 ModelExecutorFactory 分发体系，构造函数固定为
    (model_config, device)，适合在 models.yaml 中注册的标准化模型执行器。
  - BaseExecutor（本文件）：轻量接口，不约束构造函数签名，适合拥有自定义构造
    逻辑的高层执行器。新增模型执行器优先考虑 BaseModelExecutor；只有在无法适配
    标准构造签名时才使用 BaseExecutor。

用法示例::

    class MyExecutor(BaseExecutor):
        def run(self, image, params, progress_callback=None):
            ...
            return result_image
"""
from abc import ABC, abstractmethod
from typing import Callable, Dict, Any, Optional

import numpy as np


class BaseExecutor(ABC):
    """
    通用图像处理执行器抽象基类。

    约定：
    - 输入  ``image``：RGB numpy array，dtype=uint8
    - 输出：RGB numpy array，dtype=uint8
    - ``params``：执行器特定参数字典（mask、rois、prompt 等）
    - ``progress_callback``：可选的进度回调 ``(percent: int, message: str) -> None``
    """

    @abstractmethod
    def run(
        self,
        image: np.ndarray,
        params: Optional[Dict[str, Any]] = None,
        progress_callback: Optional[Callable[[int, str], None]] = None,
    ) -> np.ndarray:
        """
        执行图像处理并返回结果图像。

        :param image: 输入图像（RGB numpy array, uint8）
        :param params: 执行器特定参数，例如::

                       {"mask": mask_arr, "rois": [...], "dilation": 10}

        :param progress_callback: 进度回调，签名 ``(percent: int, message: str) -> None``；
                                  为 None 时跳过进度推送。
        :return: 处理后的图像（RGB numpy array, uint8）
        """
        ...
