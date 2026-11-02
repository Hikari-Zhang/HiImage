"""
水印去除核心模块 - 使用IOPaint
- 快速模型（lama/migan/zits 等）：CLI 批处理模式，按需调用
- 扩散模型（AnyText/SD 系列）  ：HTTP Server 模式，保活 5 分钟

模型列表从 core/models.yaml 动态加载（watermark_removal 模式），
按 display_group 字段分组，无需在代码中硬编码。
"""
import cv2
import numpy as np
import subprocess
import os
import sys
import uuid
import re
import shutil
import tempfile
import codecs
import logging
from collections import deque
from contextlib import contextmanager
import signal
import time
from pathlib import Path

from core.model_server import is_diffusion_model, inpaint_via_server
from core.paths import detect_iopaint_path
from core.utils import resolve_effective_device, default_device_for_platform
from core.paths import TMP_DIR


def create_iopaint_temp_dir(base_dir: Path | str = TMP_DIR) -> Path:
    """创建单次 IOPaint CLI 请求的唯一临时目录。"""
    return Path(tempfile.mkdtemp(prefix="iopaint_", dir=str(base_dir)))


def _stop_cli_process(proc: subprocess.Popen) -> None:
    """回收超时/异常的 CLI，优先终止其进程树，再等待直接子进程退出。"""
    if os.name != "nt":
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if proc.poll() is None:
        proc.kill()
    proc.wait(timeout=5)


@contextmanager
def _windows_cli_job(proc):
    """用独立 Windows Job 回收 CLI 的后代进程，不扫描或终止宿主进程。"""
    if os.name != "nt":
        yield
        return
    import ctypes
    from ctypes import wintypes

    class BasicLimits(ctypes.Structure):
        _fields_ = [
            ("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong),
            ("flags", wintypes.DWORD), ("min_ws", ctypes.c_size_t), ("max_ws", ctypes.c_size_t),
            ("active_limit", wintypes.DWORD), ("affinity", ctypes.c_size_t),
            ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD),
        ]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("basic", BasicLimits), ("io_counters", ctypes.c_ulonglong * 6),
            ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
            ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t),
        ]

    class Accounting(ctypes.Structure):
        _fields_ = [
            ("times", ctypes.c_longlong * 4), ("page_faults", wintypes.DWORD),
            ("total_processes", wintypes.DWORD), ("active_processes", wintypes.DWORD),
            ("terminated_processes", wintypes.DWORD),
        ]

    class ThreadEntry(ctypes.Structure):
        _fields_ = [
            ("size", wintypes.DWORD), ("usage", wintypes.DWORD),
            ("thread_id", wintypes.DWORD), ("process_id", wintypes.DWORD),
            ("base_priority", wintypes.LONG), ("delta_priority", wintypes.LONG),
            ("flags", wintypes.DWORD),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.TerminateJobObject.restype = wintypes.BOOL
    kernel.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
    kernel.QueryInformationJobObject.restype = wintypes.BOOL
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Thread32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(ThreadEntry)]
    kernel.Thread32First.restype = wintypes.BOOL
    kernel.Thread32Next.argtypes = kernel.Thread32First.argtypes
    kernel.Thread32Next.restype = wintypes.BOOL
    kernel.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenThread.restype = wintypes.HANDLE
    kernel.ResumeThread.argtypes = [wintypes.HANDLE]
    kernel.ResumeThread.restype = wintypes.DWORD
    job = kernel.CreateJobObjectW(None, None)
    if not job:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            raise ctypes.WinError(ctypes.get_last_error())
        if not kernel.AssignProcessToJobObject(job, int(proc._handle)):
            raise ctypes.WinError(ctypes.get_last_error())
        # 进程以 CREATE_SUSPENDED 创建，先入 Job 再启动，避免 launcher 的子进程逃逸。
        snapshot = kernel.CreateToolhelp32Snapshot(4, 0)  # TH32CS_SNAPTHREAD
        if snapshot == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            entry = ThreadEntry()
            entry.size = ctypes.sizeof(entry)
            found = kernel.Thread32First(snapshot, ctypes.byref(entry))
            while found and entry.process_id != proc.pid:
                found = kernel.Thread32Next(snapshot, ctypes.byref(entry))
            if not found:
                raise RuntimeError("无法定位 IOPaint CLI 启动线程")
            thread = kernel.OpenThread(2, False, entry.thread_id)  # THREAD_SUSPEND_RESUME
            if not thread:
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                if kernel.ResumeThread(thread) == 0xFFFFFFFF:
                    raise ctypes.WinError(ctypes.get_last_error())
            finally:
                kernel.CloseHandle(thread)
        finally:
            kernel.CloseHandle(snapshot)
        yield
    finally:
        try:
            if not kernel.TerminateJobObject(job, 1):
                raise ctypes.WinError(ctypes.get_last_error())
            # Job 关闭/终止是异步的；等待全部进程退出后才清理日志文件。
            deadline = time.monotonic() + 5
            accounting = Accounting()
            while True:
                if not kernel.QueryInformationJobObject(job, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None):
                    raise ctypes.WinError(ctypes.get_last_error())
                if accounting.active_processes == 0:
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError("IOPaint 子进程组回收超时")
                time.sleep(0.01)
        finally:
            kernel.CloseHandle(job)


def _stream_cli_output(cmd, env, timeout, consume) -> int:
    """日志走独立文件句柄，读取已有数据不会等待子进程输出或关闭管道。"""
    deadline = time.monotonic() + timeout
    with _cli_log_directory() as log_dir:
        log_path = Path(log_dir) / "stdout.log"
        # 分别 open，不能 dup 同一个句柄，否则读取 seek 会改变子进程写入位置。
        with log_path.open("wb") as writer, log_path.open("rb") as reader:
            try:
                proc = subprocess.Popen(
                    cmd, stdout=writer, stderr=subprocess.STDOUT, env=env,
                    start_new_session=os.name != "nt",
                    creationflags=0x4 if os.name == "nt" else 0,  # CREATE_SUSPENDED
                )
            except OSError as exc:
                raise RuntimeError(f"IOPaint 启动失败: {exc}") from exc
            try:
                with _windows_cli_job(proc):
                    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
                    while True:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise subprocess.TimeoutExpired(cmd, timeout)
                        chunk = reader.read(8192)
                        if chunk:
                            consume(decoder.decode(chunk))
                            continue
                        if proc.poll() is not None:
                            break
                        try:
                            proc.wait(timeout=min(0.05, remaining))
                        except subprocess.TimeoutExpired:
                            pass
                    consume(decoder.decode(b"", final=True))
                    return proc.returncode
            finally:
                if os.name != "nt" or proc.poll() is None:
                    _stop_cli_process(proc)


@contextmanager
def _cli_log_directory():
    directory = tempfile.TemporaryDirectory(prefix="hiimage_cli_log_")
    try:
        yield directory.name
    finally:
        primary_error = sys.exc_info()[1]
        deadline = time.monotonic() + 0.5
        while True:
            try:
                directory.cleanup()
                break
            except PermissionError:
                # Windows 进程已结束后，文件句柄释放仍可能短暂滞后。
                if time.monotonic() >= deadline:
                    if primary_error is not None:
                        logging.getLogger(__name__).exception("CLI 日志清理失败: %s", directory.name)
                        break
                    raise
                time.sleep(0.01)


def _build_model_groups() -> list:
    """
    从 models.yaml 构建 MODEL_GROUPS（watermark_removal 模式）。

    返回格式（与旧版硬编码保持一致，供 GUI/Preview 使用）：
      [(group_label, [(model_id, display_name, description), ...]), ...]

    其中 model_id 为 registry ID（如 "wm_lama"），调用时通过
    _resolve_iopaint_model_id() 转换为 iopaint 实际模型参数。
    """
    try:
        from core.model_registry import get_models_for_mode
        models = get_models_for_mode("watermark_removal")
    except Exception:
        return []

    # 按 display_group 分组（保留 YAML 中的顺序）
    groups: dict[str, list] = {}
    for m in models:
        group_label = m.get("display_group", "其他")
        if group_label not in groups:
            groups[group_label] = []
        groups[group_label].append((
            m["id"],
            m.get("name", m["id"]),
            m.get("description", ""),
        ))

    return list(groups.items())


def _is_server_mode(model_id: str) -> bool:
    """
    判断模型是否应使用 iopaint HTTP Server 保活模式。

    直接从注册表读取 iopaint_mode 字段，比 is_diffusion_model() 更可靠：
      1. 优先按 registry ID 精确匹配（如 "wm_sdxl"）
      2. 按 iopaint_model_id 反向查找（向后兼容旧代码直接传 HF repo_id 的情况）
      3. 回退到 model_server.is_diffusion_model（兜底，保持向后兼容）
    """
    try:
        from core.model_registry import MODEL_BY_ID, MODELS
        from core.constants import IOPaintMode
        cfg = MODEL_BY_ID.get(model_id)
        if cfg is not None:
            return cfg.get("iopaint_mode") == IOPaintMode.SERVER
        # 反向查找：model_id 可能是 iopaint_model_id（如 "Sanster/AnyText"）
        for m in MODELS:
            if m.get("iopaint_model_id") == model_id:
                return m.get("iopaint_mode") == IOPaintMode.SERVER
    except Exception:
        pass
    # 最终兜底
    return is_diffusion_model(model_id)


def _resolve_iopaint_model_id(model_id: str) -> str:
    """
    将 registry ID（如 "wm_anytext"）解析为 iopaint 实际模型参数（如 "Sanster/AnyText"）。

    如果传入的本身就是 iopaint_model_id（如旧代码直接传 "lama"），则原样返回。
    """
    try:
        from core.model_registry import MODEL_BY_ID
        cfg = MODEL_BY_ID.get(model_id)
        if cfg:
            # 优先使用 iopaint_model_id（扩散模型的 HF repo）；
            # cli 模型的 iopaint_model_id 与简短名相同（如 "lama"）
            return cfg.get("iopaint_model_id", model_id)
    except Exception:
        pass
    return model_id


# 模型目录：从注册表动态构建，按 display_group 分组
# GUI/preview_panel.py 直接引用此数据，无需重复维护
MODEL_GROUPS = _build_model_groups()

# 扁平化的 model_id 列表（registry ID，兼容旧接口）
AVAILABLE_MODELS = [mid for _, models in MODEL_GROUPS for mid, _, _ in models]


class Inpainter:
    """AI水印去除器（快速模型用CLI，扩散模型用HTTP Server保活）"""

    MODEL_GROUPS = MODEL_GROUPS
    AVAILABLE_MODELS = AVAILABLE_MODELS

    def __init__(self, model_name='wm_lama', iopaint_path=None, device=None, dilation=10, disable_nsfw=False,
                 prompt: str = '', negative_prompt: str = '',
                 sd_steps: int = 50, sd_guidance_scale: float = 7.5, sd_seed: int = 42,
                 progress_callback=None,
                 max_size: int = 2048):  # 新增：最大边长限制
        """
        初始化去除器

        :param model_name: 模型 ID，可以是：
                           - registry ID（如 "wm_lama"、"wm_anytext"，来自 models.yaml）
                           - iopaint 原生模型名（如 "lama"、"Sanster/AnyText"，向后兼容）
        :param iopaint_path: iopaint可执行文件路径（如果不在PATH中）
        :param device: 计算设备 ('mps', 'cpu', 'cuda')
        :param dilation: 遮罩扩张像素数
        :param disable_nsfw: 禁用 NSFW 安全检查（SD 类模型必须开启）
        :param prompt: SD 系列模型的正向文字引导（非 SD 模型忽略）
        :param negative_prompt: SD 系列模型的负向提示词
        :param sd_steps: 扩散步数
        :param sd_guidance_scale: CFG scale
        :param sd_seed: 随机种子
        :param progress_callback: 可选回调函数，签名为 progress_callback(percent: int, message: str)
        :param max_size: 最大边长限制（像素），超过此尺寸的图像将自动缩放，默认 2048
        """
        # 保留原始 registry ID 供外部查询；内部使用 _iopaint_model_id 与 iopaint 交互
        self.model_name = model_name
        self._iopaint_model_id = _resolve_iopaint_model_id(model_name)
        self._use_server_mode = _is_server_mode(model_name)  # 初始化时确定，避免重复查询
        # PowerPaint v2 需要在每次推理请求中传 enable_powerpaint_v2=True
        self._enable_powerpaint_v2 = False
        try:
            from core.model_registry import MODEL_BY_ID
            cfg = MODEL_BY_ID.get(model_name)
            if cfg:
                self._enable_powerpaint_v2 = bool(cfg.get("iopaint_enable_powerpaint_v2", False))
        except Exception:
            pass
        self.iopaint_path = iopaint_path or detect_iopaint_path()
        # 读取 models.yaml 中的 device_override，覆盖用户传入的 device
        self.device = resolve_effective_device(
            self.model_name, device or default_device_for_platform()
        )
        self.dilation = dilation
        self.disable_nsfw = disable_nsfw
        self.prompt = prompt
        self.negative_prompt = negative_prompt
        self.sd_steps = sd_steps
        self.sd_guidance_scale = sd_guidance_scale
        self.sd_seed = sd_seed
        self.progress_callback = progress_callback
        # CLI 模式超时：快速模型 5 分钟，扩散模型 30 分钟（首次下载时 CLI 也用得上）
        self._timeout = 1800 if self._use_server_mode else 300
        self._project_tmp = str(TMP_DIR)
        self._cleanup_old_tmp()
        # 新增：最大边长限制（超过此尺寸会自动缩放）
        self.max_size = max_size

    def _cleanup_old_tmp(self, max_age_hours: int = 24):
        """
        清理 tmp/ 目录中超过 max_age_hours 小时的旧临时目录（iopaint_* 前缀）。
        仅在初始化时调用一次，防止多次失败后残留目录堆积。
        """
        import time
        if not os.path.exists(self._project_tmp):
            return
        now = time.time()
        cutoff = now - max_age_hours * 3600
        try:
            for entry in os.scandir(self._project_tmp):
                if entry.is_dir() and entry.name.startswith('iopaint_'):
                    try:
                        if entry.stat().st_mtime < cutoff:
                            shutil.rmtree(entry.path, ignore_errors=True)
                    except OSError:
                        pass
        except OSError:
            pass

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
            if self._use_server_mode:
                # 扩散模型 → HTTP Server 模式（保活5分钟，无需每次重载）
                # 注意：传给 server 的是 iopaint_model_id（如 "Sanster/AnyText"）
                # progress_callback 同步透传：inpaint_via_server 在后台线程通过
                # socket.io 监听 iopaint 的 diffusion_progress 事件（每 DDIM 步一次）
                return inpaint_via_server(
                    image_rgb=image,
                    mask=mask,
                    model_name=self._iopaint_model_id,
                    device=self.device,
                    disable_nsfw=self.disable_nsfw,
                    iopaint_path=self.iopaint_path,
                    prompt=self.prompt,
                    negative_prompt=self.negative_prompt,
                    sd_steps=self.sd_steps,
                    sd_guidance_scale=self.sd_guidance_scale,
                    sd_seed=self.sd_seed,
                    progress_callback=self.progress_callback,
                    enable_powerpaint_v2=self._enable_powerpaint_v2,
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
            temp_dir = str(create_iopaint_temp_dir(self._project_tmp))

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
            return result_rgb

        finally:
            if temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)

    def _run_iopaint_cli(self, image_path: str, mask_path: str, output_dir: str) -> np.ndarray:
        """
        调用 IOPaint CLI 并返回 RGB 结果图像（流式打印日志，含下载进度）
        会从 tqdm 进度条中解析百分比并通过回调上报进度
        """
        cmd = [
            self.iopaint_path, 'run',
            '--image', image_path,
            '--mask', mask_path,
            '--output', output_dir,
            '--model', self._iopaint_model_id,   # 使用解析后的 iopaint 模型参数
            '--device', self.device,
        ]
        # --disable-nsfw 已在 iopaint 1.6.0 中移除，不再传递

        print(f"执行命令: {' '.join(cmd)}")

        # PYTHONUNBUFFERED=1 禁用子进程 Python 输出缓冲，确保下载进度实时可见
        env = os.environ.copy()
        env['PYTHONUNBUFFERED'] = '1'
        env['PYTHONIOENCODING'] = 'utf-8'

        stdout_lines = deque(maxlen=200)
        last_progress = None
        buf = ""
        skip_lf = False

        def emit(line, carriage_return=False):
            nonlocal last_progress
            stdout_lines.append(line[-8192:])
            sys.stdout.write(('\r' if carriage_return else '') + line + ('' if carriage_return else '\n'))
            sys.stdout.flush()
            match = re.search(r'(\d+)%', line)
            if match and self.progress_callback:
                percent = int(match.group(1))
                if 0 <= percent <= 100 and percent != last_progress:
                    self.progress_callback(percent, "处理中...")
                    last_progress = percent

        def consume(text):
            nonlocal buf, skip_lf
            if skip_lf and text:
                if text.startswith('\n'):
                    text = text[1:]
                skip_lf = False
            buf += text
            while True:
                match = re.search(r'[\r\n]', buf)
                if match is None:
                    # 无换行的异常长日志只保留尾部，诊断缓冲不随输出无限增长。
                    if len(buf) > 16384:
                        emit(buf[:-8192])
                        buf = buf[-8192:]
                    return
                index = match.start()
                separator = buf[index]
                line, buf = buf[:index], buf[index + 1:]
                if separator == '\r':
                    if buf.startswith('\n'):
                        buf = buf[1:]
                    elif not buf:
                        skip_lf = True
                emit(line, carriage_return=separator == '\r')

        try:
            returncode = _stream_cli_output(cmd, env, self._timeout, consume)
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(f"IOPaint执行超时 (超过{self._timeout:g}秒):\n命令: {' '.join(cmd)}") from exc
        if buf:
            emit(buf)
        stdout_text = '\n'.join(stdout_lines)
        print(f"返回码: {returncode}")

        if returncode != 0:
            # 检查是否是内存不足导致的 SIGKILL（返回码 -9 或 137）
            if returncode == -9 or returncode == 137:
                error_msg = (
                    f"IOPaint 执行失败：进程被系统终止（返回码 {returncode}）\n\n"
                    f"可能原因：\n"
                    f"  1. 内存不足（MAT 模型需要较大内存）\n"
                    f"  2. 处理超时\n\n"
                    f"建议：\n"
                    f"  • 使用更轻量的模型（lama/zits）\n"
                    f"  • 缩小图片尺寸后重试\n"
                    f"  • 关闭其他占用内存的应用\n"
                )
            else:
                error_msg = f"IOPaint 执行失败 (返回码 {returncode}):\n{stdout_text}"

            raise Exception(error_msg)

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

        # 上报最终进度为 100%
        if self.progress_callback:
            self.progress_callback(100, "处理完成")

        return cv2.cvtColor(result_bgr, cv2.COLOR_BGR2RGB)


