"""
超分辨率核心模块 - 使用 Real-ESRGAN
支持 2x / 4x 图像增清，首次调用时自动下载模型权重（~18-65MB）
"""
import os
import cv2
import numpy as np
import urllib.request

# 模型信息：(model_name, scale, weight_filename, download_url, display_name, description)
# 排序规则：同组内按推荐度从高到低
UPSCALE_MODEL_LIST = [
    # ── 通用超分辨率 ──
    (
        "RealESRGAN_x4plus",
        4,
        "RealESRGAN_x4plus.pth",
        "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth",
        "4x 通用照片（推荐）",
        "通用场景，综合细节恢复最佳，适合照片/截图；强 GAN 锐化，细节感强｜模型 ~65 MB",
    ),
    (
        "RealESRGAN_x2plus",
        2,
        "RealESRGAN_x2plus.pth",
        "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.1/RealESRGAN_x2plus.pth",
        "2x 通用照片",
        "2 倍放大，速度更快，文件更小，放大幅度要求不高时优先选此｜模型 ~65 MB",
    ),
    # ── 精细化增强（去噪/去模糊）──
    (
        "realesr-general-x4v3",
        4,
        "realesr-general-x4v3.pth",
        "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesr-general-x4v3.pth",
        "4x 精细化增强·轻量（推荐）",
        "专为真实世界压缩/模糊图设计：同时去噪+去模糊+放大，效果优于 x4plus，体积仅 17 MB｜速度最快",
    ),
    (
        "RealESRNet_x4plus",
        4,
        "RealESRNet_x4plus.pth",
        "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.1/RealESRNet_x4plus.pth",
        "4x 自然细化（无 GAN）",
        "无 GAN 判别器，避免过度锐化伪影，色彩还原最自然；人像/风景/需要忠实还原的场景首选｜模型 ~65 MB",
    ),
    # ── 动漫/插画 ──
    (
        "RealESRGAN_x4plus_anime_6B",
        4,
        "RealESRGAN_x4plus_anime_6B.pth",
        "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.2.4/RealESRGAN_x4plus_anime_6B.pth",
        "4x 动漫/插画（静图）",
        "针对动漫线稿/赛璐璐风格插画优化，线条锐利、无晕染｜模型 ~18 MB",
    ),
    (
        "realesr-animevideov3",
        4,
        "realesr-animevideov3.pth",
        "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesr-animevideov3.pth",
        "4x 动漫视频/帧序列",
        "动漫视频逐帧处理专用：更少闪烁、更好时间一致性；也可用于动漫截帧精细化｜模型 ~8 MB",
    ),
]

# 结构化分组（供 API 和 GUI 直接使用）
UPSCALE_MODEL_GROUPS = [
    ("── 通用超分辨率 ──", [
        (model_name, display_name, description)
        for model_name, scale, weight_file, url, display_name, description in UPSCALE_MODEL_LIST
        if model_name in ("RealESRGAN_x4plus", "RealESRGAN_x2plus")
    ]),
    ("── 精细化增强（去噪/去模糊）──", [
        (model_name, display_name, description)
        for model_name, scale, weight_file, url, display_name, description in UPSCALE_MODEL_LIST
        if model_name in ("realesr-general-x4v3", "RealESRNet_x4plus")
    ]),
    ("── 动漫/插画 ──", [
        (model_name, display_name, description)
        for model_name, scale, weight_file, url, display_name, description in UPSCALE_MODEL_LIST
        if model_name in ("RealESRGAN_x4plus_anime_6B", "realesr-animevideov3")
    ]),
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
        if self.model_name in ('realesr-general-x4v3', 'realesr-animevideov3'):
            # SRVGGNetCompact 轻量模型
            # general-x4v3: num_conv=32；animevideov3: num_conv=16
            try:
                from basicsr.archs.srvgg_arch import SRVGGNetCompact
            except ImportError:
                try:
                    from realesrgan.archs.srvgg_arch import SRVGGNetCompact
                except ImportError as e:
                    raise ImportError(f"缺少 SRVGGNetCompact: {e}") from e
            num_conv = 32 if self.model_name == 'realesr-general-x4v3' else 16
            model = SRVGGNetCompact(
                num_in_ch=3, num_out_ch=3,
                num_feat=64, num_conv=num_conv, upscale=4, act_type='prelu'
            )
        elif self.model_name == 'RealESRGAN_x4plus_anime_6B':
            # anime 静图模型：更小的 num_block=6
            model = RRDBNet(
                num_in_ch=3, num_out_ch=3,
                num_feat=64, num_block=6, num_grow_ch=32, scale=4
            )
        else:
            # x4plus、x2plus、RealESRNet_x4plus 均使用标准 23 块 RRDBNet
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
