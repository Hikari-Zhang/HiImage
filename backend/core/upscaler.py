"""
超分辨率核心模块 - 使用 Real-ESRGAN
支持 2x / 4x 图像增清，首次调用时自动下载模型权重（~8-65MB）

模型列表从 core/models.yaml 动态加载（upscale 模式），
按 display_group 字段分组，无需在代码中硬编码。
"""
import os
import cv2
import numpy as np
import urllib.request
from .paths import PROJECT_ROOT as _PR
from .paths import MODELS_CACHE_DIR as _MC


def _build_upscale_structures():
    """
    从 models.yaml 构建所有超分辨率相关数据结构。

    返回：(UPSCALE_MODEL_LIST, UPSCALE_MODEL_GROUPS, lookup_dicts)
      UPSCALE_MODEL_LIST:
        [(model_id, scale, weight_filename, download_url, display_name, description), ...]

      UPSCALE_MODEL_GROUPS:
        [(group_label, [(model_id, display_name, description), ...]), ...]

      lookup_dicts:
        (_MODEL_SCALE, _MODEL_WEIGHT, _MODEL_URL, _MODEL_ARCH, _MODEL_NUM_BLOCK, _MODEL_NUM_CONV)
    """
    try:
        from core.model_registry import get_models_for_mode
        models = get_models_for_mode("upscale")
    except Exception:
        models = []

    model_list = []
    for m in models:
        model_list.append((
            m["id"],
            m.get("scale", 4),
            m.get("weight_filename", f"{m['id']}.pth"),
            m.get("download_url", ""),
            m.get("name", m["id"]),
            m.get("description", ""),
        ))

    # 按 display_group 分组（保留 YAML 中的顺序）
    groups_dict: dict[str, list] = {}
    for m in models:
        label = m.get("display_group", "其他")
        if label not in groups_dict:
            groups_dict[label] = []
        groups_dict[label].append((
            m["id"],
            m.get("name", m["id"]),
            m.get("description", ""),
        ))
    model_groups = list(groups_dict.items())

    # 查找字典（model_id → 属性）
    scale_map  = {entry[0]: entry[1] for entry in model_list}
    weight_map = {entry[0]: entry[2] for entry in model_list}
    url_map    = {entry[0]: entry[3] for entry in model_list}

    # 架构相关参数（_build_upsampler 使用，来自 models.yaml 的 arch/num_block/num_conv 字段）
    arch_map      = {m["id"]: m.get("arch", "RRDBNet")   for m in models}
    num_block_map = {m["id"]: m.get("num_block", 23)      for m in models}
    num_conv_map  = {m["id"]: m.get("num_conv", 32)       for m in models}
    # outscale: 实际输出倍率，不设则等于 scale（权重倍率）
    outscale_map  = {m["id"]: m.get("outscale", m.get("scale", 4)) for m in models}

    return model_list, model_groups, (scale_map, weight_map, url_map, arch_map, num_block_map, num_conv_map, outscale_map)


# ── 模块级数据（进程内只构建一次）───────────────────────────────────────────

_list, _groups, _dicts = _build_upscale_structures()

UPSCALE_MODEL_LIST   = _list
UPSCALE_MODEL_GROUPS = _groups

_MODEL_SCALE, _MODEL_WEIGHT, _MODEL_URL, \
_MODEL_ARCH, _MODEL_NUM_BLOCK, _MODEL_NUM_CONV, _MODEL_OUTSCALE = _dicts

# 扁平化 model_id 列表（向后兼容旧接口）
AVAILABLE_UPSCALE_MODELS = [entry[0] for entry in UPSCALE_MODEL_LIST]

# 模型权重缓存目录
# 模型权重缓存目录（默认：~/.cache/hiimage/models/realesrgan/）
_WEIGHTS_DIR = str(_MC / 'realesrgan')


def _get_weight_path(model_name: str) -> str:
    """
    获取模型权重路径。
    使用 paths.py 统一解析（MODELS_CACHE_DIR / 'realesrgan' / weight_filename）。
    """
    from .paths import resolve_model_cache_path

    # 构建最小 cfg 字典，供 resolve_model_cache_path() 使用
    cfg = {
        "provider": "realesrgan",
        "weight_filename": _MODEL_WEIGHT.get(model_name, f"{model_name}.pth")
    }
    return str(resolve_model_cache_path(cfg))


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

    def __init__(self, model_name: str = 'RealESRGAN_x4plus', device: str = 'cpu', outscale: int | None = None):
        if model_name not in AVAILABLE_UPSCALE_MODELS:
            raise ValueError(f"不支持的模型: {model_name}，可选: {AVAILABLE_UPSCALE_MODELS}")
        self.model_name = model_name
        self.device = device
        self.scale = _MODEL_SCALE[model_name]          # 权重内置倍率（用于网络构建）
        # outscale: 实际输出倍率；前端传值优先，否则用 YAML outscale，最后 fallback 到 scale
        if outscale is not None:
            self.outscale = outscale
        else:
            self.outscale = _MODEL_OUTSCALE[model_name]
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
            output_bgr, _ = self._upsampler.enhance(img_bgr, outscale=self.outscale)
        except RuntimeError as e:
            raise RuntimeError(f"超分辨率处理失败: {e}") from e

        return cv2.cvtColor(output_bgr, cv2.COLOR_BGR2RGB)

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _ensure_model_loaded(self):
        """懒加载：首次调用时初始化模型"""
        if self._upsampler is not None:
            return

        # 检查权重文件是否存在
        weight_path = _get_weight_path(self.model_name)
        if not os.path.exists(weight_path):
            raise FileNotFoundError(
                f"模型权重文件不存在: {weight_path}\n"
                f"请先在「模型管理」中下载模型"
            )

        self._upsampler = self._build_upsampler(weight_path)

    def _build_upsampler(self, weight_path: str):
        """
        构建 RealESRGANer 实例。

        网络结构通过 models.yaml 中的 arch 字段决定：
          - arch: SRVGGNetCompact → 轻量模型（realesr-general-x4v3、realesr-animevideov3）
          - arch: RRDBNet         → 标准模型（其余所有）
                  num_block 决定深度：6（anime_6B）或 23（其他）
        """
        try:
            from realesrgan import RealESRGANer
            from basicsr.archs.rrdbnet_arch import RRDBNet
        except ImportError as e:
            raise ImportError(
                f"缺少依赖包: {e}\n"
                "请在项目虚拟环境中执行：\n"
                "  pip install realesrgan basicsr facexlib"
            ) from e

        arch      = _MODEL_ARCH.get(self.model_name, "RRDBNet")
        num_block = _MODEL_NUM_BLOCK.get(self.model_name, 23)
        num_conv  = _MODEL_NUM_CONV.get(self.model_name, 32)

        if arch == "SRVGGNetCompact":
            # 轻量模型：general-x4v3 (num_conv=32) / animevideov3 (num_conv=16)
            try:
                from basicsr.archs.srvgg_arch import SRVGGNetCompact
            except ImportError:
                try:
                    from realesrgan.archs.srvgg_arch import SRVGGNetCompact
                except ImportError as e:
                    raise ImportError(f"缺少 SRVGGNetCompact: {e}") from e
            model = SRVGGNetCompact(
                num_in_ch=3, num_out_ch=3,
                num_feat=64, num_conv=num_conv, upscale=4, act_type='prelu'
            )
        else:
            # 标准 RRDBNet：num_block=23（通用/2x）或 6（anime_6B）
            model = RRDBNet(
                num_in_ch=3, num_out_ch=3,
                num_feat=64, num_block=num_block, num_grow_ch=32,
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
        outscale_info = f", outscale={self.outscale}x" if self.outscale != self.scale else ""
        print(f"[Upscaler] 模型加载完成: {self.model_name} (device={self.device}, scale={self.scale}x{outscale_info})")
        return upsampler

