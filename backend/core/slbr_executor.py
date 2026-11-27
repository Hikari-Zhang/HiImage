"""Selectively apply the background residual predicted by SLBR CLWD."""
import cv2
import numpy as np

from .inpaint_regions import prepare_mask, repair_regions
from .model_executor import BaseModelExecutor
from .paths import find_local_model_path


class SLBRExecutor(BaseModelExecutor):
    input_size = 512

    def __init__(self, model_config: dict, device: str):
        super().__init__(model_config, "cpu")
        self._session = None

    def load_model(self):
        if self._session is not None:
            return
        import onnxruntime as ort

        path = find_local_model_path(self.model_config)
        options = ort.SessionOptions()
        options.intra_op_num_threads = 4
        session = ort.InferenceSession(path.read_bytes(), options, providers=["CPUExecutionProvider"])
        inputs = [(item.name, item.type, item.shape) for item in session.get_inputs()]
        outputs = [(item.name, item.type, item.shape) for item in session.get_outputs()]
        shape = [1, 3, self.input_size, self.input_size]
        if inputs != [("image", "tensor(float)", shape)] or outputs != [("residual", "tensor(float)", shape)]:
            raise ValueError("SLBR 权重的输入或输出格式不符，请用 scripts/export_slbr.py 重新导出")
        self._session = session

    def _repair(self, image, _mask):
        height, width = image.shape[:2]
        side = self.input_size
        scale = min(1.0, side / max(height, width))
        scaled_height, scaled_width = max(1, round(height * scale)), max(1, round(width * scale))
        pixels = image if scale == 1.0 else cv2.resize(image, (scaled_width, scaled_height), interpolation=cv2.INTER_AREA)
        top, left = (side - scaled_height) // 2, (side - scaled_width) // 2
        padded = np.pad(pixels, ((top, side - scaled_height - top), (left, side - scaled_width - left), (0, 0)),
                        mode="reflect")
        tensor = padded.transpose(2, 0, 1)[None].astype(np.float32) / 255.0
        residual, = self._session.run(["residual"], {"image": tensor})
        delta = residual[0, :, top:top + scaled_height, left:left + scaled_width].transpose(1, 2, 0)
        if not np.isfinite(delta).all():
            raise ValueError("SLBR 返回了无效的像素")
        if scale != 1.0:
            delta = cv2.resize(delta, (width, height), interpolation=cv2.INTER_LINEAR)
        return np.rint(np.clip(image.astype(np.float32) + 255.0 * delta, 0, 255)).astype(np.uint8)

    def execute(self, image, *, mask=None, rois=None, dilation=0, disable_nsfw=False,
                progress_callback=None):
        if image.ndim != 3 or image.shape[2] != 3 or image.dtype != np.uint8 or not image.size:
            raise ValueError("需要非空 RGB uint8 图像")
        selection = prepare_mask(image.shape, mask, rois, dilation)
        if not selection.any():
            return image.copy()
        if progress_callback:
            progress_callback(25, "正在加载 SLBR 权重")
        self.load_model()
        if progress_callback:
            progress_callback(45, "正在修复选区")
        # Small images use every pixel as context, matching the 512 px benchmark.
        margin = self.input_size if max(image.shape[:2]) <= self.input_size else 128
        result = repair_regions(image, selection, self._repair, margin=margin, feather=0)
        if progress_callback:
            progress_callback(90, "选区修复完成")
        return result

    def unload_model(self):
        self._session = None

    def supports_mask(self):
        return True
