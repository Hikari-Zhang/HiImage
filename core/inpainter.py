"""
水印去除核心模块 - 使用IOPaint
- 快速模型（lama/migan/zits 等）：CLI 批处理模式，按需调用
- 扩散模型（AnyText/SD 系列）  ：HTTP Server 模式，保活 5 分钟
"""
import cv2
import numpy as np
import subprocess
import os
import sys
import uuid
import shutil

from core.model_server import is_diffusion_model, inpaint_via_server


# 模型目录：按推荐优先级分组
# 结构：(group_label, [(model_id, display_name, description), ...])
# gui/preview_panel.py 直接引用此数据，无需重复维护
MODEL_GROUPS = [
    ("── 快速修复（本地推理）──", [
        ("lama",  "LaMa（推荐·通用）",   "综合最佳首选：速度快、质量好，适合绝大多数水印场景｜内存 ~500 MB，无需显卡"),
        ("migan", "MiGAN（GAN·快速）",   "基于生成对抗网络，速度快，背景纹理较规则时效果好｜内存 ~600 MB，无需显卡"),
        ("zits",  "ZITS（边缘感知）",     "边缘感知修复，文字/logo 边缘过渡自然｜内存 ~700 MB，无需显卡"),
        ("fcf",   "FCF（快速填充）",      "Fast Context-based Fill，背景简单/重复时效果佳｜内存 ~600 MB，无需显卡"),
        ("mat",   "MAT（精细修复）",      "Multi-scale Attention，质量最高但速度较慢｜内存 ~1.5 GB，无需显卡"),
        ("ldm",   "LDM（轻量扩散）",      "轻量级 Latent Diffusion，质量与速度均衡｜内存 ~1.5 GB，无需显卡"),
        ("manga", "Manga（漫画专用）",    "针对漫画/线稿优化，线条清晰无模糊｜内存 ~500 MB，无需显卡"),
        ("cv2",   "CV2（传统算法）",      "OpenCV 传统算法，无需 GPU，速度最快，质量有限｜内存 <100 MB，纯 CPU"),
    ]),
    ("── 专用模型（首次使用自动下载）──", [
        ("Sanster/AnyText", "AnyText（文字水印专用）", "识别字体、颜色后重建背景，文字类水印效果显著优于 LaMa；常驻内存保活5分钟｜下载 ~3 GB，运行 ~10 GB 统一内存"),
    ]),
    ("── 扩散模型（高质量·首次下载较大）──", [
        ("runwayml/stable-diffusion-inpainting",
         "SD Inpainting（复杂背景）",
         "Stable Diffusion 1.5 inpainting，复杂/渐变背景效果自然；常驻内存保活5分钟｜下载 ~4 GB，运行 ~8 GB 统一内存"),
        ("andregn/Realistic_Vision_V3.0-inpainting",
         "Realistic Vision（写实照片）",
         "写实照片场景下真实感最强，人像/风景推荐；常驻内存保活5分钟｜下载 ~4 GB，运行 ~8 GB 统一内存"),
        ("JunhaoZhuang/PowerPaint-v2-1",
         "PowerPaintV2（最强通用）",
         "支持文字引导（如 'remove watermark'），综合效果最强；常驻内存保活5分钟｜下载 ~5 GB，运行 ~12 GB 统一内存"),
        ("diffusers/stable-diffusion-xl-1.0-inpainting-0.1",
         "SDXL Inpainting（高分辨率）",
         "2K+ 图像细节最佳，速度最慢；常驻内存保活5分钟｜下载 ~7 GB，运行 ~16 GB 统一内存"),
    ]),
]

# 扁平化的 model_id 列表（兼容旧接口）
AVAILABLE_MODELS = [mid for _, models in MODEL_GROUPS for mid, _, _ in models]


class Inpainter:
    """AI水印去除器（快速模型用CLI，扩散模型用HTTP Server保活）"""

    MODEL_GROUPS = MODEL_GROUPS
    AVAILABLE_MODELS = AVAILABLE_MODELS

    def __init__(self, model_name='lama', iopaint_path=None, device='mps', dilation=10, disable_nsfw=False):
        """
        初始化去除器
        :param model_name: 模型ID，见 AVAILABLE_MODELS
        :param iopaint_path: iopaint可执行文件路径（如果不在PATH中）
        :param device: 计算设备 ('mps', 'cpu', 'cuda')
        :param dilation: 遮罩扩张像素数
        :param disable_nsfw: 禁用 NSFW 安全检查（SD 类模型必须开启）
        """
        self.model_name = model_name
        self.iopaint_path = iopaint_path or 'iopaint'
        self.device = device
        self.dilation = dilation
        self.disable_nsfw = disable_nsfw
        # CLI 模式超时：快速模型 5 分钟，扩散模型 30 分钟（首次下载时 CLI 也用得上）
        self._timeout = 1800 if is_diffusion_model(model_name) else 300
        self._project_tmp = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tmp'
        )
        self._cleanup_old_tmp()

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def create_mask(self, image_shape, roi_list, dilation=10):
        """
        根据ROI区域创建掩码
        :param image_shape: 图像形状 (height, width, channel)
        :param roi_list: ROI区域列表 [(x1, y1, x2, y2), ...]
        :param dilation: 遮罩扩张像素数（让遮罩稍大于ROI，确保完全覆盖水印）
        :return: 掩码 (numpy array, 0 or 255)
        """
        height, width = image_shape[:2]
        mask = np.zeros((height, width), dtype=np.uint8)

        for roi in roi_list:
            x1, y1, x2, y2 = roi

            # 扩张遮罩（确保完全覆盖水印边缘），同时夹住边界
            x1 = max(0, int(x1) - dilation)
            y1 = max(0, int(y1) - dilation)
            x2 = min(width, int(x2) + dilation)
            y2 = min(height, int(y2) + dilation)

            mask[y1:y2, x1:x2] = 255

        return mask

    def remove_watermark(self, image, roi_list, output_dir=None):
        """
        去除水印
        :param image: 原始图像 (numpy array, RGB格式)
        :param roi_list: ROI区域列表 [(x1, y1, x2, y2), ...]
        :param output_dir: 输出目录路径（可选，仅 CLI 模式使用）
        :return: 去除水印后的图像 (numpy array, RGB格式)
        """
        mask = self.create_mask(image.shape, roi_list, self.dilation)
        return self.remove_watermark_with_mask(image, mask, output_dir)

    def remove_watermark_with_mask(self, image, mask, output_dir=None):
        """
        使用自定义掩码去除水印
        :param image: 原始图像 (numpy array, RGB格式)
        :param mask: 掩码 (numpy array, 0 or 255)
        :param output_dir: 输出目录路径（可选，仅 CLI 模式使用）
        :return: 去除水印后的图像
        """
        try:
            if is_diffusion_model(self.model_name):
                # 扩散模型 → HTTP Server 模式（保活5分钟，无需每次重载）
                return inpaint_via_server(
                    image_rgb=image,
                    mask=mask,
                    model_name=self.model_name,
                    device=self.device,
                    disable_nsfw=self.disable_nsfw,
                    iopaint_path=self.iopaint_path,
                )
            else:
                # 快速模型 → CLI 模式
                return self._run_cli(image, mask, output_dir)

        except Exception as e:
            raise Exception(f"水印去除失败: {str(e)}")

    # ------------------------------------------------------------------
    # CLI 模式（快速模型）
    # ------------------------------------------------------------------

    def _run_cli(self, image: np.ndarray, mask: np.ndarray, output_dir=None) -> np.ndarray:
        """通过 iopaint run CLI 执行修复"""
        temp_dir = None
        try:
            os.makedirs(self._project_tmp, exist_ok=True)
            temp_dir = os.path.join(self._project_tmp, f'iopaint_{uuid.uuid4().hex}')
            os.makedirs(temp_dir, exist_ok=True)

            print(f"临时目录: {temp_dir}")

            image_path = os.path.join(temp_dir, 'image.png')
            cv2.imwrite(image_path, cv2.cvtColor(image, cv2.COLOR_RGB2BGR))
            print(f"已保存原图: {image_path}")

            mask_path = os.path.join(temp_dir, 'mask.png')
            cv2.imwrite(mask_path, mask)
            print(f"已保存掩码: {mask_path}")

            if output_dir is None:
                output_dir = os.path.join(temp_dir, 'output')
            os.makedirs(output_dir, exist_ok=True)
            print(f"输出目录: {output_dir}")

            result_rgb = self._run_iopaint_cli(image_path, mask_path, output_dir)
            shutil.rmtree(temp_dir, ignore_errors=True)
            return result_rgb

        except Exception:
            if temp_dir and os.path.exists(temp_dir):
                print(f"⚠️ 临时文件已保留用于调试: {temp_dir}")
            raise

    def _run_iopaint_cli(self, image_path: str, mask_path: str, output_dir: str) -> np.ndarray:
        """
        调用 IOPaint CLI 并返回 RGB 结果图像（流式打印日志，含下载进度）
        """
        cmd = [
            self.iopaint_path, 'run',
            '--image', image_path,
            '--mask', mask_path,
            '--output', output_dir,
            '--model', self.model_name,
            '--device', self.device,
        ]
        if self.disable_nsfw:
            cmd.append('--disable-nsfw')

        print(f"执行命令: {' '.join(cmd)}")

        # PYTHONUNBUFFERED=1 禁用子进程 Python 输出缓冲，确保下载进度实时可见
        env = os.environ.copy()
        env['PYTHONUNBUFFERED'] = '1'

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=env,
                text=True,
            )
        except Exception as e:
            raise Exception(f"IOPaint 启动失败: {e}")

        # 流式转发日志，正确处理 \r（tqdm 进度条原地刷新）
        stdout_lines = []
        try:
            buf = []
            while True:
                ch = proc.stdout.read(1)
                if not ch:
                    if buf:
                        line = ''.join(buf)
                        stdout_lines.append(line)
                        sys.stdout.write(line + '\n')
                        sys.stdout.flush()
                    break
                if ch == '\r':
                    line = ''.join(buf)
                    sys.stdout.write(f'\r{line}')
                    sys.stdout.flush()
                    buf = []
                elif ch == '\n':
                    line = ''.join(buf)
                    stdout_lines.append(line)
                    sys.stdout.write(line + '\n')
                    sys.stdout.flush()
                    buf = []
                else:
                    buf.append(ch)
        except (ValueError, OSError):
            pass

        try:
            proc.wait(timeout=self._timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            mins = self._timeout // 60
            raise Exception(f"IOPaint执行超时 (超过{mins}分钟):\n命令: {' '.join(cmd)}")

        returncode = proc.returncode
        stdout_text = '\n'.join(stdout_lines)
        print(f"返回码: {returncode}")

        if returncode != 0:
            raise Exception(
                f"IOPaint执行失败 (返回码 {returncode}):\n{stdout_text}"
            )

        output_path = os.path.join(output_dir, 'image.png')
        if not os.path.exists(output_path):
            files = os.listdir(output_dir)
            raise Exception(
                f"输出文件不存在: {output_path}\n"
                f"输出目录内容: {files}\n{stdout_text}"
            )

        result_bgr = cv2.imread(output_path)
        if result_bgr is None:
            raise Exception(
                f"无法读取处理结果: {output_path}\n{stdout_text}"
            )

        return cv2.cvtColor(result_bgr, cv2.COLOR_BGR2RGB)

    def _cleanup_old_tmp(self):
        """清理 tmp 目录下已存在的历史残留目录"""
        if not os.path.isdir(self._project_tmp):
            return
        for entry in os.scandir(self._project_tmp):
            if entry.is_dir() and entry.name.startswith('iopaint_'):
                shutil.rmtree(entry.path, ignore_errors=True)
