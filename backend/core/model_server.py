"""
IOPaint Server 进程管理器（单例）

针对扩散模型（AnyText / SD 系列）使用 iopaint start HTTP 服务模式代替 CLI 批处理模式：
  - 首次调用时按需启动，模型常驻内存，避免每次重载
  - 5 分钟内无调用自动关闭，释放显存/内存
  - 切换模型或设备时立即重启
"""
import os
import time
import threading
import subprocess
import base64
import io
import urllib.request
import urllib.error
import json
import sys
from typing import Optional

import numpy as np
import cv2

import app.config as _cfg

# 哪些模型走 Server 模式（扩散模型，加载慢）
_DIFFUSION_PREFIXES = ('runwayml/', 'andregn/', 'Sanster/', 'diffusers/')

SERVER_HOST = '127.0.0.1'

# 从配置文件读取，允许用户在 config/settings.json 中调整
def _keepalive() -> int:
    return int(_cfg.get('server.keepalive_seconds', 300))

def _port() -> int:
    return int(_cfg.get('server.port', 51821))

def _startup_timeout() -> int:
    return int(_cfg.get('server.startup_timeout', 120))


def is_diffusion_model(model_name: str) -> bool:
    return model_name.startswith(_DIFFUSION_PREFIXES)


class _ModelServer:
    """
    单例：管理一个 iopaint start 子进程。
    线程安全（调用端用 self._lock 保护）。
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._proc: Optional[subprocess.Popen] = None
        self._current_model: Optional[str] = None
        self._current_device: Optional[str] = None
        self._current_nsfw: Optional[bool] = None
        self._last_used: float = 0.0
        self._idle_timer: Optional[threading.Timer] = None
        self._iopaint_path = 'iopaint'
        self._log_lines: list = []   # 缓存子进程最近输出，供错误诊断

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def set_iopaint_path(self, path: str):
        self._iopaint_path = path

    def ensure_running(self, model_name: str, device: str, disable_nsfw: bool) -> str:
        """
        确保对应模型的 server 正在运行，返回 base URL（如 http://127.0.0.1:51821）。
        如果参数变化（模型/设备/nsfw），先停止旧进程再启动新的。
        """
        with self._lock:
            self._reset_idle_timer()

            needs_restart = (
                self._proc is None
                or self._proc.poll() is not None   # 进程已退出
                or self._current_model != model_name
                or self._current_device != device
                or self._current_nsfw != disable_nsfw
            )

            if needs_restart:
                self._stop_unlocked()
                self._start_unlocked(model_name, device, disable_nsfw)

            return f'http://{SERVER_HOST}:{_port()}'

    def stop(self):
        """主动停止（如程序退出时调用）"""
        with self._lock:
            self._stop_unlocked()

    # ------------------------------------------------------------------
    # 私有方法
    # ------------------------------------------------------------------

    def _start_unlocked(self, model_name: str, device: str, disable_nsfw: bool):
        """启动 iopaint start 子进程并等待就绪（已持锁）"""
        # 使用 config 中统一定义的 models 目录（项目根目录/models），
        # 避免因文件位置不同导致的路径计算错误
        model_dir = _cfg.MODELS_DIR

        cmd = [
            self._iopaint_path, 'start',
            '--host', SERVER_HOST,
            '--port', str(_port()),
            '--model', model_name,
            '--device', device,
            '--model-dir', model_dir,
        ]
        if disable_nsfw:
            cmd.append('--disable-nsfw-checker')

        # 继承当前环境变量，并注入 HuggingFace 凭据和镜像配置
        # PYTHONUNBUFFERED=1 禁用 Python 输出缓冲，确保下载进度实时显示
        env = os.environ.copy()
        env['PYTHONUNBUFFERED'] = '1'
        hf_token = _cfg.get('network.hf_token', '')
        hf_endpoint = _cfg.get('network.hf_endpoint', 'https://huggingface.co')
        if hf_token:
            env['HF_TOKEN'] = hf_token
            env['HUGGING_FACE_HUB_TOKEN'] = hf_token  # 兼容旧版 huggingface_hub
        if hf_endpoint:
            env['HF_ENDPOINT'] = hf_endpoint

        print(f"[ModelServer] 启动服务: {' '.join(cmd)}")
        self._log_lines = []   # 重置日志缓冲
        self._proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
            text=True,
        )
        self._current_model = model_name
        self._current_device = device
        self._current_nsfw = disable_nsfw

        # 异步打印服务日志
        threading.Thread(target=self._stream_logs, args=(self._proc, self._log_lines), daemon=True).start()

        # 等待服务就绪
        self._wait_ready()

    def _stop_unlocked(self):
        """停止当前进程（已持锁）"""
        if self._idle_timer is not None:
            self._idle_timer.cancel()
            self._idle_timer = None

        if self._proc is not None and self._proc.poll() is None:
            print(f"[ModelServer] 停止服务 (model={self._current_model})")
            self._proc.terminate()
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait()
        self._proc = None
        self._current_model = None
        self._current_device = None
        self._current_nsfw = None

    def _wait_ready(self):
        """轮询 /api/v1/server-config 直到服务就绪（在已持锁环境下调用）"""
        url = f'http://{SERVER_HOST}:{_port()}/api/v1/server-config'
        deadline = time.time() + _startup_timeout()
        while time.time() < deadline:
            # 检查进程是否已意外退出
            if self._proc and self._proc.poll() is not None:
                # 等待日志线程把剩余输出刷入缓冲（最多 2s）
                time.sleep(0.5)
                tail = '\n'.join(self._log_lines[-30:]) if self._log_lines else '（无日志）'
                # 识别常见原因，给出友好提示
                friendly = self._diagnose_error(tail)
                raise RuntimeError(
                    f"[ModelServer] iopaint 进程意外退出 (returncode={self._proc.returncode})\n"
                    f"{friendly}\n"
                    f"--- 子进程最后输出 ---\n{tail}"
                )
            try:
                req = urllib.request.Request(url)
                with urllib.request.urlopen(req, timeout=2) as resp:
                    if resp.status == 200:
                        print(f"[ModelServer] 服务就绪: {self._current_model}")
                        return
            except (urllib.error.URLError, OSError):
                pass
            time.sleep(1)
        raise TimeoutError(
            f"[ModelServer] 等待 iopaint 启动超时（{_startup_timeout()}s），模型: {self._current_model}"
        )

    @staticmethod
    def _diagnose_error(log_tail: str) -> str:
        """根据日志内容识别常见错误，返回友好提示"""
        log_lower = log_tail.lower()
        if 'not cached locally' in log_lower or 'model is not cached' in log_lower:
            return (
                "⚠️  模型未在本地缓存，且无法从 HuggingFace Hub 下载。\n"
                "请检查网络连接，或在「设置」中配置 HuggingFace 镜像地址（如 https://hf-mirror.com）。"
            )
        if 'connection' in log_lower and ('error' in log_lower or 'refused' in log_lower or 'timeout' in log_lower):
            return "⚠️  网络连接失败，无法访问 HuggingFace Hub，请检查网络或配置镜像。"
        if 'cuda' in log_lower and ('not available' in log_lower or 'no cuda' in log_lower):
            return "⚠️  CUDA 不可用，请在设置中将设备切换为 MPS 或 CPU。"
        if 'out of memory' in log_lower or 'oom' in log_lower:
            return "⚠️  内存/显存不足，请关闭其他应用后重试，或选择更轻量的模型（如 LaMa）。"
        if 'port' in log_lower and 'in use' in log_lower:
            return "⚠️  端口被占用，请在设置中更改 IOPaint 端口后重试。"
        return "iopaint 子进程启动失败，请查看下方日志获取详细信息。"

    def _reset_idle_timer(self):
        """重置空闲计时器（已持锁）"""
        if self._idle_timer is not None:
            self._idle_timer.cancel()
        keepalive = _keepalive()
        self._idle_timer = threading.Timer(keepalive, self._idle_shutdown)
        self._idle_timer.daemon = True
        self._idle_timer.start()
        self._last_used = time.time()

    def _idle_shutdown(self):
        """空闲超时回调（在后台线程调用）"""
        with self._lock:
            keepalive = _keepalive()
            idle = time.time() - self._last_used
            if idle >= keepalive - 1:
                print(f"[ModelServer] 空闲超过 {keepalive}s，自动停止 (model={self._current_model})")
                self._stop_unlocked()

    @staticmethod
    def _stream_logs(proc: subprocess.Popen, log_lines: list):
        """
        将子进程 stdout 实时转发到当前进程 stdout，同时缓存到 log_lines 供错误诊断。
        tqdm 进度条使用 \\r 原地刷新（不含 \\n），必须逐字符读取才能正确还原，
        否则按行读取会将所有进度更新积压到下一个 \\n 才一起输出。
        """
        try:
            buf = []
            while True:
                ch = proc.stdout.read(1)
                if not ch:
                    # 进程已关闭，刷出最后一段缓冲
                    if buf:
                        line = ''.join(buf)
                        sys.stdout.write(f"[iopaint] {line}\n")
                        sys.stdout.flush()
                        log_lines.append(line)
                    break
                if ch == '\r':
                    # 行内刷新（tqdm 进度条）：原地覆盖当前行
                    line = ''.join(buf)
                    sys.stdout.write(f"\r[iopaint] {line}")
                    sys.stdout.flush()
                    buf = []
                elif ch == '\n':
                    line = ''.join(buf)
                    sys.stdout.write(f"[iopaint] {line}\n")
                    sys.stdout.flush()
                    log_lines.append(line)
                    # 只保留最近 200 行，避免内存无限增长
                    if len(log_lines) > 200:
                        log_lines.pop(0)
                    buf = []
                else:
                    buf.append(ch)
        except (ValueError, OSError):
            pass


# 全局单例
_server = _ModelServer()


def get_server() -> _ModelServer:
    return _server


# ------------------------------------------------------------------
# HTTP inpaint 调用
# ------------------------------------------------------------------

def inpaint_via_server(
    image_rgb: np.ndarray,
    mask: np.ndarray,
    model_name: str,
    device: str,
    disable_nsfw: bool,
    iopaint_path: str = 'iopaint',
) -> np.ndarray:
    """
    通过 iopaint HTTP server 执行修复，返回 RGB numpy 图像。
    """
    srv = get_server()
    srv.set_iopaint_path(iopaint_path)
    base_url = srv.ensure_running(model_name, device, disable_nsfw)

    # 编码图像和掩码为 base64 PNG
    image_b64 = _ndarray_to_b64(cv2.cvtColor(image_rgb, cv2.COLOR_RGB2BGR), '.png')
    mask_b64 = _ndarray_to_b64(mask, '.png')

    payload = json.dumps({
        'image': image_b64,
        'mask': mask_b64,
    }).encode('utf-8')

    url = f'{base_url}/api/v1/inpaint'
    req = urllib.request.Request(
        url,
        data=payload,
        method='POST',
        headers={'Content-Type': 'application/json'},
    )

    try:
        with urllib.request.urlopen(req, timeout=1800) as resp:
            result_bytes = resp.read()
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='replace')
        raise RuntimeError(f"[ModelServer] inpaint 请求失败 HTTP {e.code}: {body}")

    # 解码结果图像
    result_arr = np.frombuffer(result_bytes, dtype=np.uint8)
    result_bgr = cv2.imdecode(result_arr, cv2.IMREAD_COLOR)
    if result_bgr is None:
        raise RuntimeError("[ModelServer] 无法解码服务返回的图像")
    return cv2.cvtColor(result_bgr, cv2.COLOR_BGR2RGB)


def _ndarray_to_b64(img: np.ndarray, ext: str) -> str:
    """将 numpy 图像编码为 base64 字符串（不含 data URI 前缀）"""
    success, buf = cv2.imencode(ext, img)
    if not success:
        raise RuntimeError("图像编码失败")
    return base64.b64encode(buf.tobytes()).decode('ascii')
