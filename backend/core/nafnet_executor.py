"""
NAFNet 图像去模糊执行器

NAFNet (Nonlinear Activation Free Network) 是极快的图像去模糊模型。
同样不需要 mask，Image-to-Image 复原。

输入：RGB 图像（numpy array, uint8）
输出：RGB 图像（numpy array, uint8）
"""
import numpy as np
import torch
from typing import Dict, Any
from .model_executor import BaseModelExecutor


class NAFNetExecutor(BaseModelExecutor):
    """
    NAFNet 图像去模糊执行器

    特点：
    - 极快速度（无非线性激活函数）
    - 适合实时预览场景
    """

    def __init__(self, model_config: Dict[str, Any], device: str):
        super().__init__(model_config, device)
        self._pipeline = None

    def load_model(self) -> None:
        """加载 NAFNet 模型权重"""
        from .nafnet_model import NAFNetPipeline

        model_path = self._get_model_path()
        self._pipeline = NAFNetPipeline.from_pretrained(
            model_path,
            device=self.device,
        )
        self._pipeline.eval()

    def execute(self, image: np.ndarray, **kwargs) -> np.ndarray:
        """
        执行 NAFNet 推理（不需要 mask）

        :param image: 输入图像（RGB, uint8）
        :param kwargs: 支持以下参数：（暂无）
        :return: 去模糊后的图像（RGB, uint8）
        """
        if self._pipeline is None:
            self.load_model()

        # 转换输入格式
        input_tensor = self._numpy_to_tensor(image)

        with torch.no_grad():
            output_tensor = self._pipeline(input_tensor)

        return self._tensor_to_numpy(output_tensor)

    def unload_model(self) -> None:
        """卸载模型，释放显存"""
        if self._pipeline is not None:
            del self._pipeline
            self._pipeline = None
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            elif hasattr(torch, "mps") and torch.backends.mps.is_available():
                torch.mps.empty_cache()

    def supports_mask(self) -> bool:
        """NAFNet 不需要 mask"""
        return False

    def _get_model_path(self) -> str:
        """获取模型权重路径"""
        import os
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        local_path = self.model_config.get("local_path", "models/nafnet")
        return os.path.join(project_root, local_path)

    @staticmethod
    def _numpy_to_tensor(image: np.ndarray) -> torch.Tensor:
        """RGB numpy array → PyTorch tensor (B, C, H, W)"""
        import cv2
        import torch
        # RGB → BGR
        image_bgr = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
        # (H, W, C) → (B, C, H, W)
        tensor = torch.from_numpy(image_bgr).float() / 255.0
        tensor = tensor.permute(2, 0, 1).unsqueeze(0)
        return tensor.to(next(iter(ModelExecutor.__subclasses__())).device
        # Note: This is a bit hacky. Let me fix it.

    # Let me rewrite this properly...

# Actually, I realize I made an error in the code. Let me rewrite `nafnet_executor.py` properly.

# The issue is in `_numpy_to_tensor()` - I referenced `ModelExecutor.__subclasses__()` 
# which is incorrect.

# Let me rewrite the file properly.</think>我需要修正 `nafnet_executor.py` 中的错误。让我重新写这个文件。
<tool_call>Write<tool_sep>
<arg_key>content</arg_key>
<arg_value>"""
NAFNet 图像去模糊执行器

NAFNet (Nonlinear Activation Free Network) 是极快的图像去模糊模型。
同样不需要 mask，Image-to-Image 复原。

输入：RGB 图像（numpy array, uint8）
输出：RGB 图像（numpy array, uint8）
"""
import numpy as np
import torch
from typing import Dict, Any
from .model_executor import BaseModelExecutor


class NAFNetExecutor(BaseModelExecutor):
    """
    NAFNet 图像去模糊执行器

    特点：
    - 极快速度（无非线性激活函数）
    - 适合实时预览场景
    """

    def __init__(self, model_config: Dict[str, Any], device: str):
        super().__init__(model_config, device)
        self._pipeline = None

    def load_model(self) -> None:
        """加载 NAFNet 模型权重"""
        from .nafnet_model import NAFNetPipeline

        model_path = self._get_model_path()
        self._pipeline = NAFNetPipeline.from_pretrained(
            model_path,
            device=self.device,
        )
        self._pipeline.eval()
        # 将模型移到正确的设备
        if self.device == "cuda" and torch.cuda.is_available():
            self._pipeline = self._pipeline.cuda()
        elif self.device == "mps" and hasattr(torch, "mps") and torch.backends.mps.is_available():
            self._pipeline = self._pipeline.to("mps")

    def execute(self, image: np.ndarray, **kwargs) -> np.ndarray:
        """
        执行 NAFNet 推理（不需要 mask）

        :param image: 输入图像（RGB, uint8）
        :param kwargs: 支持以下参数：（暂无）
        :return: 去模糊后的图像（RGB, uint8）
        """
        if self._pipeline is None:
            self.load_model()

        # 转换输入格式
        input_tensor = self._numpy_to_tensor(image)

        with torch.no_grad():
            output_tensor = self._pipeline(input_tensor)

        return self._tensor_to_numpy(output_tensor)

    def unload_model(self) -> None:
        """卸载模型，释放显存"""
        if self._pipeline is not None:
            del self._pipeline
            self._pipeline = None
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            elif hasattr(torch, "mps") and torch.backends.mps.is_available():
                torch.mps.empty_cache()

    def supports_mask(self) -> bool:
        """NAFNet 不需要 mask"""
        return False

    def _get_model_path(self) -> str:
        """获取模型权重路径"""
        import os
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        local_path = self.model_config.get("local_path", "models/nafnet")
        return os.path.join(project_root, local_path)

    def _numpy_to_tensor(self, image: np.ndarray) -> torch.Tensor:
        """RGB numpy array → PyTorch tensor (B, C, H, W)"""
        import cv2
        import torch
        # RGB → BGR
        image_bgr = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
        # (H, W, C) → (B, C, H, W)
        tensor = torch.from_numpy(image_bgr).float() / 255.0
        tensor = tensor.permute(2, 0, 1).unsqueeze(0)
        # 移到正确的设备
        if self.device == "cuda" and torch.cuda.is_available():
            tensor = tensor.cuda()
        elif self.device == "mps" and hasattr(torch, "mps") and torch.backends.mps.is_available():
            tensor = tensor.to("mps")
        return tensor

    def _tensor_to_numpy(self, tensor: torch.Tensor) -> np.ndarray:
        """PyTorch tensor (B, C, H, W) → RGB numpy array (H, W, C)"""
        import numpy as np
        import cv2
        import torch
        # (B, C, H, W) → (C, H, W)
        tensor = tensor.squeeze(0).permute(1, 2, 0).cpu()
        # 限制到 [0, 1]
        tensor = torch.clamp(tensor, 0, 1)
        # 转换为 numpy
        image = (tensor.numpy() * 255.0).clip(0, 255).astype(np.uint8)
        # BGR → RGB
        return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
