"""
模型执行器框架 —— 解耦 IOPaint 硬编码

所有模型执行器需实现 BaseModelExecutor 接口。
ModelExecutorFactory 根据 models.yaml 的 provider 字段自动分发。

新增模型只需：
1. 实现 BaseModelExecutor 接口
2. 在 ModelExecutorFactory.create_executor() 中注册
3. 在 models.yaml 中添加配置（设置对应的 provider）
"""
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
import numpy as np


class BaseModelExecutor(ABC):
    """
    所有模型执行器的抽象基类

    设计约定：
    - 输入：RGB numpy array（uint8）
    - 输出：RGB numpy array（uint8）
    - 如需 mask：通过 **kwargs 透传，由具体执行器决定是否使用
    """

    def __init__(self, model_config: Dict[str, Any], device: str):
        self.model_config = model_config
        self.device = device

    @abstractmethod
    def load_model(self) -> None:
        """加载模型到内存（懒加载，首次执行时调用）"""
        pass

    @abstractmethod
    def execute(self, image: np.ndarray, **kwargs) -> np.ndarray:
        """
        执行模型推理

        :param image: 输入图像（RGB numpy array, uint8）
        :param kwargs: 模型特定参数，例如：
                      - mask: np.ndarray（Inpainting 模型需要）
                      - prompt: str（SD 类模型需要）
                      - rois: list（区域限定）
        :return: 处理后的图像（RGB numpy array, uint8）
        """
        pass

    @abstractmethod
    def unload_model(self) -> None:
        """从内存卸载模型，释放显存"""
        pass

    def supports_mask(self) -> bool:
        """该执行器是否需要 mask（供 API 层判断参数合法性）"""
        return False

    def get_config(self, key: str, default: Any = None) -> Any:
        """获取模型配置中的字段，带默认值"""
        return self.model_config.get(key, default)

    def offload(self) -> None:
        """
        卸载模型（别名，与现有代码风格保持一致）
        等价于 unload_model()
        """
        self.unload_model()


class ModelExecutorFactory:
    """根据 provider 创建对应的执行器"""

    @staticmethod
    def create_executor(model_config: Dict[str, Any], device: str):
        """
        根据模型配置创建对应的执行器

        :param model_config: 模型配置字典（来自 models.yaml）
        :param device: 推理设备 (mps/cpu/cuda)
        :return: BaseModelExecutor 实例
        """
        provider = model_config.get("provider")

        if provider == "IOPaint":
            from .iopaint_executor import IOPaintExecutor
            return IOPaintExecutor(model_config, device)

        elif provider == "diffusers":
            from .diffusers_executor import DiffusersExecutor
            return DiffusersExecutor(model_config, device)

        elif provider == "HiImage":
            from .hiimage_executor import HiImageExecutor
            return HiImageExecutor(model_config, device)

        elif provider == "restormer":
            from .restormer_executor import RestormerExecutor
            return RestormerExecutor(model_config, device)

        elif provider == "realesrgan":
            # 复用现有 upscaler 实现
            from .realesrgan_executor import RealESRGANExecutor
            return RealESRGANExecutor(model_config, device)

        elif provider == "rembg":
            from .rembg_executor import RembgExecutor
            return RembgExecutor(model_config, device)

        elif provider == "facexlib":
            from .facexlib_executor import FacexlibExecutor
            return FacexlibExecutor(model_config, device)

        else:
            raise ValueError(
                f"未知的 provider: {provider}，"
                f"模型 ID: {model_config.get('id', 'unknown')}"
            )

    @staticmethod
    def get_supported_providers() -> list:
        """返回所有支持的 provider 列表（用于验证 models.yaml）"""
        return [
            "IOPaint",
            "diffusers",
            "HiImage",
            "restormer",
            "nafnet",
            "realesrgan",
            "rembg",
            "facexlib",
        ]
