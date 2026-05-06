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

import config as _cfg

# 哪些模型走 Server 模式（扩散模型，加载慢）
_DIFFUSION_PREFIXES = ('runwayml/', 'andregn/', 'Sanster/', 'JunhaoZhuang/', 'diffusers/')

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
        # 获取项目 models 目录，与 main.py 保持一致
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        model_dir = os.path.join(project_root, 'models')

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

        # 继承当前环境变量（包含 HF_ENDPOINT、HF_HOME 等）
        # PYTHONUNBUFFERED=1 禁用 Python 输出缓冲，确保下载进度实时显示
        env = os.environ.copy()
        env['PYTHONUNBUFFERED'] = '1'

        print(f"[ModelServer] 启动服务: {' '.join(cmd)}")
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
        threading.Thread(target=self._stream_logs, args=(self._proc,), daemon=True).start()

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
                raise RuntimeError(
                    f"[ModelServer] iopaint start 进程意外退出 (returncode={self._proc.returncode})"
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
            idle = time.time() - self._last_used
            if idle >= KEEPALIVE_SECONDS - 1:
                print(f"[ModelServer] 空闲超过 {KEEPALIVE_SECONDS}s，自动停止 (model={self._current_model})")
                self._stop_unlocked()

    @staticmethod
    def _stream_logs(proc: subprocess.Popen):
        """
        将子进程 stdout 实时转发到当前进程 stdout。
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
                        sys.stdout.write(f"[iopaint] {''.join(buf)}\n")
                        sys.stdout.flush()
                    break
                if ch == '\r':
                    # 行内刷新（tqdm 进度条）：原地覆盖当前行
                    sys.stdout.write(f"\r[iopaint] {''.join(buf)}")
                    sys.stdout.flush()
                    buf = []
                elif ch == '\n':
                    sys.stdout.write(f"[iopaint] {''.join(buf)}\n")
                    sys.stdout.flush()
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
