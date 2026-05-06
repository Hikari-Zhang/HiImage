"""
超分辨率核心模块 - 使用 Real-ESRGAN
支持 2x / 4x 图像增清，首次调用时自动下载模型权重（~18-65MB）
"""
import os
import cv2
import numpy as np
import urllib.request

# 模型信息：(model_name, scale, weight_filename, download_url, description)
UPSCALE_MODEL_LIST = [
    (
        "RealESRGAN_x4plus",
        4,
        "RealESRGAN_x4plus.pth",
        "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth",
        "4x 通用照片（推荐）",
        "通用场景，细节恢复最佳，适合照片/截图｜模型 ~65 MB",
    ),
    (
        "RealESRGAN_x4plus_anime_6B",
        4,
        "RealESRGAN_x4plus_anime_6B.pth",
        "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.2.4/RealESRGAN_x4plus_anime_6B.pth",
        "4x 动漫/插画",
        "动漫线稿/插画专用，线条更锐利｜模型 ~18 MB",
    ),
    (
        "RealESRGAN_x2plus",
        2,
        "RealESRGAN_x2plus.pth",
        "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.1/RealESRGAN_x2plus.pth",
        "2x 通用照片",
        "放大倍率适中，速度更快，文件更小｜模型 ~65 MB",
    ),
]

# 结构化分组（供 GUI 直接使用）
UPSCALE_MODEL_GROUPS = [
    ("── 超分辨率模型 ──", [
        (model_name, display_name, description)
        for model_name, scale, weight_file, url, display_name, description in UPSCALE_MODEL_LIST
    ])
]

# 扁平化 model_name 列表
AVAILABLE_UPSCALE_MODELS = [m[0] for m in UPSCALE_MODEL_LIST]

# 模型 name → scale 映射
_MODEL_SCALE = {m[0]: m[1] for m in UPSCALE_MODEL_LIST}
_MODEL_WEIGHT = {m[0]: m[2] for m in UPSCALE_MODEL_LIST}
_MODEL_URL = {m[0]: m[3] for m in UPSCALE_MODEL_LIST}

# 项目内模型缓存目录
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_WEIGHTS_DIR = os.path.join(_PROJECT_ROOT, 'models', 'realesrgan')


def _get_weight_path(model_name: str) -> str:
    return os.path.join(_WEIGHTS_DIR, _MODEL_WEIGHT[model_name])


def _download_weight(model_name: str, progress_callback=None) -> str:
    """下载模型权重，支持进度回调 callback(downloaded_bytes, total_bytes)"""
    os.makedirs(_WEIGHTS_DIR, exist_ok=True)
    url = _MODEL_URL[model_name]
    dest = _get_weight_path(model_name)

    print(f"[Upscaler] 正在下载模型权重: {url}")
    print(f"[Upscaler] 保存至: {dest}")

    def _reporthook(block_num, block_size, total_size):
        downloaded = block_num * block_size
        if progress_callback:
            progress_callback(downloaded, total_size)
        if total_size > 0:
            pct = min(100, downloaded * 100 // total_size)
            bar = '#' * (pct // 5) + '.' * (20 - pct // 5)
            print(f"\r[Upscaler] [{bar}] {pct}%  ", end='', flush=True)
        else:
            mb = downloaded / 1024 / 1024
            print(f"\r[Upscaler] 已下载 {mb:.1f} MB  ", end='', flush=True)

    try:
        urllib.request.urlretrieve(url, dest, reporthook=_reporthook)
        print()  # 换行
        print(f"[Upscaler] 下载完成: {dest}")
    except Exception as e:
        # 清理不完整文件
        if os.path.exists(dest):
            os.remove(dest)
        raise RuntimeError(f"模型下载失败: {e}\n请检查网络连接，或手动下载至 {dest}") from e

    return dest


class Upscaler:
    """
    Real-ESRGAN 超分辨率处理器

    用法：
        upscaler = Upscaler(model_name='RealESRGAN_x4plus', device='cpu')
        result_rgb = upscaler.upscale(image_rgb)
    """

    def __init__(self, model_name: str = 'RealESRGAN_x4plus', device: str = 'cpu'):
        if model_name not in AVAILABLE_UPSCALE_MODELS:
            raise ValueError(f"不支持的模型: {model_name}，可选: {AVAILABLE_UPSCALE_MODELS}")
        self.model_name = model_name
        self.device = device
        self.scale = _MODEL_SCALE[model_name]
        self._upsampler = None  # 懒加载

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def upscale(self, image: np.ndarray) -> np.ndarray:
        """
        超分辨率放大图像

        :param image: 输入图像 (numpy array, RGB 格式, uint8)
        :return: 放大后图像 (numpy array, RGB 格式, uint8)
        """
        if image is None or image.size == 0:
            raise ValueError("输入图像为空")

        self._ensure_model_loaded()

        # Real-ESRGAN 内部使用 BGR
        img_bgr = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)

        try:
            output_bgr, _ = self._upsampler.enhance(img_bgr, outscale=self.scale)
        except RuntimeError as e:
            raise RuntimeError(f"超分辨率处理失败: {e}") from e

        return cv2.cvtColor(output_bgr, cv2.COLOR_BGR2RGB)

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _ensure_model_loaded(self):
        """懒加载：首次调用时初始化模型（含自动下载权重）"""
        if self._upsampler is not None:
            return

        # 检查并下载权重
        weight_path = _get_weight_path(self.model_name)
        if not os.path.exists(weight_path):
            print(f"[Upscaler] 权重文件不存在，开始下载...")
            _download_weight(self.model_name)

        self._upsampler = self._build_upsampler(weight_path)

    def _build_upsampler(self, weight_path: str):
        """构建 RealESRGANer 实例"""
        try:
            from realesrgan import RealESRGANer
            from basicsr.archs.rrdbnet_arch import RRDBNet
        except ImportError as e:
            raise ImportError(
                f"缺少依赖包: {e}\n"
                "请在项目虚拟环境中执行：\n"
                "  pip install realesrgan basicsr facexlib"
            ) from e

        # 根据模型选择网络结构
        if self.model_name == 'RealESRGAN_x4plus_anime_6B':
            # anime 模型使用更小的 num_block=6
            model = RRDBNet(
                num_in_ch=3, num_out_ch=3,
                num_feat=64, num_block=6, num_grow_ch=32, scale=4
            )
        else:
            # x4plus 和 x2plus 均使用标准 23 块
            model = RRDBNet(
                num_in_ch=3, num_out_ch=3,
                num_feat=64, num_block=23, num_grow_ch=32,
                scale=self.scale
            )

        # 解析 device 为 PyTorch 格式
        half = False
        if self.device == 'cuda':
            gpu_id = 0
            half = True   # cuda 上使用半精度加速
        elif self.device == 'mps':
            gpu_id = 0    # mps 映射为 gpu_id=0（basicsr 内部特殊处理）
        else:
            gpu_id = None  # cpu

        upsampler = RealESRGANer(
            scale=self.scale,
            model_path=weight_path,
            model=model,
            tile=0,         # tile=0 表示不分块（整图处理）；超大图可改为 512
            tile_pad=10,
            pre_pad=0,
            half=half,
            gpu_id=gpu_id,
        )
        print(f"[Upscaler] 模型加载完成: {self.model_name} (device={self.device}, scale={self.scale}x)")
        return upsampler
