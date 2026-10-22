"""
IOPaint Server 进程管理器（单例）

针对扩散模型（AnyText / SD 系列）使用 iopaint start HTTP 服务模式代替 CLI 批处理模式：
  - 首次调用时按需启动，模型常驻内存，避免每次重载
  - 5 分钟内无调用自动关闭，释放显存/内存
  - 切换模型或设备时立即重启
"""
import os
import signal
import time
import threading
import subprocess
import base64
import io
import urllib.request
import urllib.error
import json
import sys
import atexit
from pathlib import Path
from typing import Optional

import numpy as np
import cv2

# 使用统一路径管理器
from core.paths import MODELS_DIR as _MODELS_DIR, HF_HOME as _HF_HOME, detect_iopaint_path
from core.utils import resolve_effective_device
import app.config as _cfg


SERVER_HOST = '127.0.0.1'

# 从配置文件读取，允许用户在 config/settings.json 中调整
def _keepalive() -> int:
    return int(_cfg.get('server.keepalive_seconds', 300))

def _port() -> int:
    return int(_cfg.get('server.port', 51821))

def _startup_timeout() -> int:
    return int(_cfg.get('server.startup_timeout', 120))

def _low_mem() -> bool:
    return bool(_cfg.get('server.low_mem', True))

def _cpu_offload() -> bool:
    return bool(_cfg.get('server.cpu_offload', False))

def _cpu_textencoder() -> bool:
    return bool(_cfg.get('server.cpu_textencoder', False))


def is_diffusion_model(model_id: str) -> bool:
    """
    判断模型是否需要走 iopaint HTTP Server 保活模式（扩散模型）。

    检查顺序：
      1. 通过注册表 registry ID（如 "wm_anytext"）查找 iopaint_mode 字段
      2. 通过 iopaint_model_id 反向查找（兼容直接传入 "Sanster/AnyText" 的旧代码）
    """
    try:
        from core.model_registry import MODEL_BY_ID, MODELS
        from core.constants import IOPaintMode
        # 优先：按 registry ID 查找（如 "wm_anytext"）
        if model_id in MODEL_BY_ID:
            return MODEL_BY_ID[model_id].get("iopaint_mode") == IOPaintMode.SERVER
        # 次之：通过 iopaint_model_id 反向查找（兼容直接传入 "Sanster/AnyText" 的旧调用）
        for m in MODELS:
            if m.get("iopaint_model_id") == model_id:
                return m.get("iopaint_mode") == IOPaintMode.SERVER
    except Exception:
        pass
    return False


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
        self._active_inferences: int = 0   # 正在进行的推理请求数，> 0 时不允许 idle 关闭
        self._iopaint_path = detect_iopaint_path()
        self._log_lines: list = []   # 缓存子进程最近输出，供错误诊断
        self._iopaint_log_file = None  # 独立日志文件句柄

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def set_iopaint_path(self, path: str):
        self._iopaint_path = path

    def ensure_running(self, model_name: str, device: str, disable_nsfw: bool) -> str:
        """
        确保对应模型的 server 正在运行，返回 base URL（如 http://127.0.0.1:51821）。
        如果参数变化（模型/设备/nsfw），先停止旧进程再启动新的。

        注意：device 比较使用「有效设备」——若模型有 device_override 配置，
        则以 override 值为准，避免在 MPS/CPU 之间无意义地重启保活服务。
        """
        with self._lock:
            # 不在此处重置 idle 计时器：计时应从推理结束后开始（由 end_inference 负责）。
            # 这里只检查进程是否需要重启。

            # 计算此次请求的有效设备（可能被 models.yaml 的 device_override 覆盖）
            effective_device = resolve_effective_device(model_name, device)

            needs_restart = (
                self._proc is None
                or self._proc.poll() is not None   # 进程已退出
                or self._current_model != model_name
                or self._current_device != effective_device
                or self._current_nsfw != disable_nsfw
            )

            if needs_restart:
                self._stop_unlocked()
                self._start_unlocked(model_name, effective_device, disable_nsfw)

            return f'http://{SERVER_HOST}:{_port()}'

    @staticmethod
    def _is_model_cached(model_name: str) -> bool:
        """
        检查模型是否已在本地缓存（manual/ 或标准 hub/ 目录）且文件完整。
        对于内置 IOPaint 模型（名称不含 /），始终返回 True。

        注意：此处仅做快速缓存存在性检查，深度完整性验证（包括 LFS 指针检测）
        由 _start_unlocked() 的预启动检查（ModelChecker）负责。
        """
        if '/' not in model_name:
            return True
        from pathlib import Path
        # 优先检查 manual/ 目录（HiImage 旧格式下载存放位置）
        # 只要目录有实际文件（且非 git-lfs 指针桩），视为已缓存
        manual_dir = Path(_HF_HOME) / "manual" / model_name.replace("/", "--")
        if manual_dir.is_dir():
            has_files = False
            has_lfs_stub = False
            _LFS_WEIGHT_SUFFIXES = {".bin", ".pt", ".pth", ".ckpt"}
            try:
                for f in manual_dir.rglob("*"):
                    if not f.is_file():
                        continue
                    has_files = True
                    # git-lfs 指针文件：< 1 KB 的权重文件，头部含 "git-lfs.github.com"
                    if f.suffix.lower() in _LFS_WEIGHT_SUFFIXES and f.stat().st_size < 1024:
                        try:
                            head = f.read_bytes(200).decode("utf-8", errors="ignore")
                            if "git-lfs.github.com" in head:
                                has_lfs_stub = True
                                break
                        except OSError:
                            pass
            except OSError:
                pass
            if has_files and not has_lfs_stub:
                return True
            # 空目录或全为 LFS 指针桩 → 继续检查 hub/

        # 检查标准 hub 缓存（snapshot_download local_files_only 方式）
        try:
            from huggingface_hub import snapshot_download
            hub_dir = Path(_HF_HOME) / "hub"
            snapshot_download(
                repo_id=model_name,
                local_files_only=True,
                cache_dir=str(hub_dir),
            )
            return True
        except Exception:
            return False

    @staticmethod
    def _register_manual_model(model_name: str) -> bool:
        """
        将 manual/ 目录中的模型注册到标准 HF hub 缓存中（纯离线，不触发网络请求）。

        HiImage 下载器将模型保存在 manual/ 目录，iopaint / diffusers 只认标准
        hub 缓存结构（hub/models--xxx/refs/main + snapshots/<hash>/）。
        此函数通过符号链接将两者桥接，无需重新下载任何文件。

        返回 True 表示注册成功或已存在，False 表示 manual/ 不存在（尚未下载）。
        """
        from pathlib import Path
        import os

        manual_dir = Path(_HF_HOME) / "manual" / model_name.replace("/", "--")
        if not manual_dir.exists():
            return False

        hub_dir = Path(_HF_HOME) / "hub"
        hub_dir.mkdir(parents=True, exist_ok=True)

        model_cache_dir = hub_dir / f"models--{model_name.replace('/', '--')}"
        refs_main = model_cache_dir / "refs" / "main"

        # 若 refs/main 已存在，hub 缓存结构完整，直接跳过
        if refs_main.exists():
            print(f"[ModelServer] 模型 {model_name} hub 缓存结构已存在，跳过注册")
            return True

        # 离线注册：手动构建标准 hub 缓存目录结构
        try:
            import hashlib
            import shutil

            # 用全部文件路径（含子目录递归）的哈希作为伪 commit hash
            all_files = sorted(
                str(f.relative_to(manual_dir))
                for f in manual_dir.rglob("*") if f.is_file()
            )
            fake_hash = hashlib.sha256("".join(all_files).encode()).hexdigest()[:40]

            snapshots_dir = model_cache_dir / "snapshots" / fake_hash
            refs_dir = model_cache_dir / "refs"
            refs_dir.mkdir(parents=True, exist_ok=True)
            refs_dir.joinpath("main").write_text(fake_hash)

            snapshots_dir.mkdir(parents=True, exist_ok=True)
            (model_cache_dir / "blobs").mkdir(exist_ok=True)

            # 将 manual/ 下所有条目（文件 + 子目录）软链接到快照目录
            # 注意：diffusers 模型包含 unet/ vae/ text_encoder/ 等子目录，
            # 必须处理 is_dir() 条目，不能仅过滤 is_file()
            for item in manual_dir.iterdir():
                dest = snapshots_dir / item.name
                if not dest.exists() and not dest.is_symlink():
                    try:
                        os.symlink(str(item.resolve()), str(dest))
                    except OSError:
                        if item.is_file():
                            shutil.copy2(str(item), str(dest))
                        # 子目录无法链接时跳过（不影响主流程）

            print(f"[ModelServer] 模型 {model_name} 已注册到标准缓存（离线模式）")
            return True
        except Exception as e:
            print(f"[ModelServer] 离线注册失败: {e}")
            return False

    @staticmethod
    def _ensure_dependencies(model_name: str) -> None:
        """
        确保模型的 HF 依赖项已下载到缓存。
        在启动 iopaint server 前调用。
        """
        from core.model_registry import MODEL_BY_ID
        cfg = MODEL_BY_ID.get(model_name)
        if not cfg:
            return
        deps = cfg.get("dependencies", [])
        if not deps:
            return

        from huggingface_hub import snapshot_download
        from pathlib import Path
        cache_dir = str(Path(_HF_HOME) / "hub")

        for dep in deps:
            try:
                snapshot_download(repo_id=dep, cache_dir=cache_dir)
                print(f"[ModelServer] 依赖已缓存: {dep}")
            except Exception as e:
                print(f"[ModelServer] 依赖下载失败 {dep}: {e}")

    def stop(self):
        """主动停止（如程序退出时调用）"""
        with self._lock:
            self._stop_unlocked()

    # ------------------------------------------------------------------
    # 私有方法
    # ------------------------------------------------------------------

    def _start_unlocked(self, model_name: str, effective_device: str, disable_nsfw: bool):
        """启动 iopaint start 子进程并等待就绪（已持锁）。

        :param effective_device: 已由 ensure_running 通过 resolve_effective_device 解析的设备名。
        """
        # 使用 paths.py 中统一定义的 models 目录
        model_dir = str(_MODELS_DIR)

        _startup_cfg = None   # 注册表配置，供后续预启动检查和命令构造复用
        try:
            from core.model_registry import MODEL_BY_ID, MODELS
            # 优先按 registry ID 查找；若直接传入了 iopaint_model_id 则反向查找
            _startup_cfg = MODEL_BY_ID.get(model_name)
            if _startup_cfg is None:
                for m in MODELS:
                    if m.get("iopaint_model_id") == model_name:
                        _startup_cfg = m
                        break
        except Exception:
            pass  # 注册表不可用时不阻断启动

        # ── 启动前模型完整性检查 ───────────────────────────────────────────────
        # 对 HF 托管的 server 模式模型做完整性检测，防止以损坏文件（如 git-lfs 指针
        # 桩、截断下载）启动 IOPaint，避免晦涩的 "PytorchStreamReader failed" /
        # "config.json not found in directory snapshots\main" 等报错。
        # 若检测到 CORRUPTED / PARTIAL，直接抛出友好错误，提示用户重新下载。
        if _startup_cfg and (_startup_cfg.get("hf_model_id") or _startup_cfg.get("hf_models")):
            try:
                from core.model_checker import ModelChecker
                _check = ModelChecker().check_model(_startup_cfg["id"], use_cache=False)
                if _check.status in ("corrupted", "partial"):
                    raise RuntimeError(
                        f"模型「{_startup_cfg.get('name', model_name)}」文件损坏或不完整"
                        f"（{_check.message}）。\n"
                        "请在「模型管理」页面重新下载该模型后再试。"
                    )
            except RuntimeError:
                raise   # 完整性检查失败：向上传递，不启动 IOPaint
            except Exception:
                pass    # ModelChecker 意外失败时不阻断启动（保守策略）

        # 确保依赖模型已下载到缓存
        self._ensure_dependencies(model_name)

        # 若模型在 manual/ 目录中，先注册到标准 HF hub 缓存
        # 这样 iopaint 才能通过 repo ID 找到模型
        self._register_manual_model(model_name)

        cmd = [
            self._iopaint_path, 'start',
            '--host', SERVER_HOST,
            '--port', str(_port()),
            '--model', model_name,
            '--device', effective_device,
        ]
        # 本地模型（不含 /）使用项目模型目录
        # 扩散模型（含 /）不指定 --model-dir，使用 HuggingFace 默认缓存路径
        if '/' not in model_name:
            cmd.extend(['--model-dir', model_dir])
        if disable_nsfw:
            cmd.append('--disable-nsfw-checker')
        # 显存优化参数（从 settings.json 读取，默认值偏向省显存）
        # --low-mem / --cpu-offload / --cpu-textencoder 在 CPU 设备上无意义，跳过
        if effective_device != 'cpu':
            if _low_mem():
                cmd.append('--low-mem')
            if _cpu_offload():
                cmd.append('--cpu-offload')
            if _cpu_textencoder():
                cmd.append('--cpu-textencoder')

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
        # 离线模式：仅当模型已在本地缓存中时才强制离线，
        # 否则允许 iopaint 首次使用时从 HuggingFace Hub 下载。
        # 注意：必须显式设置 local_files_only=False，避免 huggingface_hub 默认启用离线模式。
        if self._is_model_cached(model_name):
            env['HF_HUB_OFFLINE'] = '1'
            env['TRANSFORMERS_OFFLINE'] = '1'
        else:
            # 显式禁用离线模式，允许从 HuggingFace Hub 下载
            env['HF_HUB_OFFLINE'] = '0'
            env['TRANSFORMERS_OFFLINE'] = '0'
            # 确保 local_files_only 不会被设置为 True
            env.pop('HF_HUB_LOCAL_FILES_ONLY', None)

        print(f"[ModelServer] 启动服务: {' '.join(cmd)}")
        self._log_lines = []   # 重置日志缓冲
        popen_kwargs = dict(
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
            text=True,
        )
        # macOS/Linux: 新进程组，便于 os.killpg 一次杀光
        if hasattr(os, 'setsid'):
            popen_kwargs['start_new_session'] = True
        self._proc = subprocess.Popen(cmd, **popen_kwargs)
        self._current_model = model_name
        self._current_device = effective_device
        self._current_nsfw = disable_nsfw

        # 异步打印服务日志
        # 准备独立日志文件（提升可观测性，与主进程日志分离）
        _log_dir = os.path.join(os.path.dirname(__file__), "..", "logs")
        os.makedirs(_log_dir, exist_ok=True)
        self._iopaint_log_file = open(os.path.join(_log_dir, "iopaint.log"), "a", encoding="utf-8")
        threading.Thread(
            target=self._stream_logs,
            args=(self._proc, self._log_lines, self._iopaint_log_file),
            daemon=True,
        ).start()
        # 等待服务就绪
        self._wait_ready()

    def _stop_unlocked(self):
        """停止当前进程及其子进程（已持锁）"""
        if self._idle_timer is not None:
            self._idle_timer.cancel()
            self._idle_timer = None

        if self._proc is not None and self._proc.poll() is None:
            pid = self._proc.pid
            print(f"[ModelServer] 停止服务 (model={self._current_model}, pid={pid})")
            # 先尝试 SIGTERM
            self._proc.terminate()
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                # SIGTERM 无效，用 SIGKILL 强杀进程组（macOS/Linux）
                print(f"[ModelServer] SIGTERM 超时，发送 SIGKILL 到进程组 pgid={pid}")
                try:
                    os.killpg(os.getpgid(pid), 0)  # 检查进程组是否存在
                    os.killpg(os.getpgid(pid), signal.SIGKILL)
                except (ProcessLookupError, OSError):
                    # 进程组已不存在，直接 kill 子进程
                    self._proc.kill()
                self._proc.wait()
        self._proc = None
        self._current_model = None
        self._current_device = None
        self._current_nsfw = None
        # 关闭独立日志文件
        if self._iopaint_log_file:
            try:
                self._iopaint_log_file.close()
            except Exception:
                pass
            self._iopaint_log_file = None

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
        if 'config.json' in log_lower and ('not found' in log_lower or 'no file named' in log_lower):
            return (
                "⚠️  模型文件结构不完整（缺少 config.json）。\n"
                "可能原因：旧版下载器仅获取了 git-lfs 指针文件而非实际权重。\n"
                "请在「模型管理」页面重新下载该模型。"
            )
        if 'pytorchstreamreader' in log_lower or 'failed reading zip archive' in log_lower:
            return (
                "⚠️  模型权重文件损坏（无法解析为 zip/pickle 格式）。\n"
                "可能原因：旧版下载器下载了 git-lfs 指针桩而非真实权重文件。\n"
                "请在「模型管理」页面重新下载该模型。"
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

    def begin_inference(self):
        """推理开始前调用，暂停 idle 关闭计时（线程安全）"""
        with self._lock:
            self._active_inferences += 1
            # 取消当前 idle 计时器，推理期间不允许触发
            if self._idle_timer is not None:
                self._idle_timer.cancel()
                self._idle_timer = None

    def end_inference(self):
        """推理结束后调用，恢复 idle 计时（线程安全）"""
        with self._lock:
            self._active_inferences = max(0, self._active_inferences - 1)
            self._last_used = time.time()
            # 只有无活跃推理时才重启 idle 计时
            if self._active_inferences == 0 and self._proc is not None:
                self._reset_idle_timer()

    def _idle_shutdown(self):
        """空闲超时回调（在后台线程调用）"""
        with self._lock:
            # 有推理正在进行时不关闭（双重保险）
            if self._active_inferences > 0:
                print(f"[ModelServer] idle 触发但有 {self._active_inferences} 个活跃推理，跳过关闭")
                # 重新启动计时
                self._reset_idle_timer()
                return
            keepalive = _keepalive()
            idle = time.time() - self._last_used
            if idle >= keepalive - 1:
                print(f"[ModelServer] 空闲超过 {keepalive}s，自动停止 (model={self._current_model})")
                self._stop_unlocked()

    @staticmethod
    def _stream_logs(proc: subprocess.Popen, log_lines: list, log_file=None):
        """
        将子进程 stdout 实时转发到当前进程 stdout，同时缓存到 log_lines 供错误诊断，
        并将完整日志写入独立文件（log_file）以便离线排查。
        tqdm 进度条使用 \\r 原地刷新，批量读取（256 字符/次）后在缓冲中定位 \\r/\\n
        拆分行/帧，与逐字符版本行为完全一致，但减少了约 256 倍的 read syscall 开销。
        """
        try:
            buf = ""
            while True:
                chunk = proc.stdout.read(256)
                if not chunk:
                    # 进程已关闭，刷出最后一段缓冲
                    if buf:
                        sys.stdout.write(f"[iopaint] {buf}\n")
                        sys.stdout.flush()
                        log_lines.append(buf)
                        if len(log_lines) > 200:
                            log_lines.pop(0)
                        if log_file:
                            try:
                                log_file.write(buf + '\n')
                                log_file.flush()
                            except OSError:
                                pass
                    break
                buf += chunk
                # 消费缓冲中所有完整的行 / 回车帧
                while True:
                    ri = buf.find('\r')
                    ni = buf.find('\n')
                    if ri == -1 and ni == -1:
                        break
                    if ri == -1 or (ni != -1 and ni < ri):
                        # \n 先出现 —— 完整日志行
                        line, buf = buf[:ni], buf[ni + 1:]
                        sys.stdout.write(f"[iopaint] {line}\n")
                        sys.stdout.flush()
                        log_lines.append(line)
                        # 只保留最近 200 行，避免内存无限增长
                        if len(log_lines) > 200:
                            log_lines.pop(0)
                        if log_file:
                            try:
                                log_file.write(line + '\n')
                                log_file.flush()
                            except OSError:
                                pass
                    else:
                        # \r 先出现 —— tqdm 进度条原地刷新帧
                        # \r\n（Windows CRLF）：跳过紧随的 \n，避免产生空日志行
                        remainder = buf[ri + 1:]
                        line, buf = buf[:ri], remainder[1:] if remainder.startswith('\n') else remainder
                        sys.stdout.write(f"\r[iopaint] {line}")
                        sys.stdout.flush()
        except (ValueError, OSError):
            pass


# 全局单例
_server = _ModelServer()

# 注册进程退出钩子，确保 iopaint 子进程在主进程退出时被清理
atexit.register(_server.stop)


def _handle_sigterm(signum, frame):
    _server.stop()
    raise SystemExit(0)


if hasattr(signal, 'SIGTERM'):
    try:
        signal.signal(signal.SIGTERM, _handle_sigterm)
    except ValueError:
        # signal.signal() 只能在主线程调用；若模块首次被工作线程导入则跳过注册，
        # main.py 的 lifespan 启动时会在主线程完成预导入，确保处理器最终注册。
        pass


def get_server() -> _ModelServer:
    return _server


# ------------------------------------------------------------------
# HTTP inpaint 调用
# ------------------------------------------------------------------

def _listen_diffusion_progress(
    base_url: str,
    sd_steps: int,
    progress_callback,
    stop_event: threading.Event,
):
    """
    在后台线程中连接 iopaint 的 socket.io 端点，监听 DDIM/扩散步进度事件，
    将步骤数（0-based）转换为百分比后通过 progress_callback 上报。

    iopaint 在每个扩散步完成后 emit:
      diffusion_progress {"step": N}   (N 从 0 开始)
      diffusion_finish                  (推理完成)

    percent 计算：step 从 0 到 sd_steps-1，映射到前端进度 20% ~ 95%
    （20% 为请求发出前占位进度，95% 留给图像解码阶段）
    """
    try:
        import socketio as _sio_module
    except ImportError:
        return  # python-socketio 不可用，跳过进度监听

    # iopaint 的 socket.io 挂载在 /ws 路径（ASGIApp 中挂载）
    sio_url = base_url.replace('http://', 'http://')  # 保持 http
    sio = _sio_module.Client(reconnection=False, logger=False, engineio_logger=False)

    @sio.on('diffusion_progress')
    def on_progress(data):
        if stop_event.is_set():
            return
        step = data.get('step', 0)
        # 将 step 映射到 20%~95% 区间：保留头尾给加载/收尾阶段
        if sd_steps > 0:
            pct = 20 + int((step + 1) / sd_steps * 75)
            pct = min(pct, 95)
        else:
            pct = 50
        try:
            progress_callback(pct, f"扩散采样中... {step + 1}/{sd_steps} 步")
        except Exception:
            pass

    @sio.on('diffusion_finish')
    def on_finish(data=None):
        stop_event.set()
        try:
            sio.disconnect()
        except Exception:
            pass

    try:
        # socketio_path 指定 socket.io 服务器的挂载路径（iopaint 挂载在 /ws）
        sio.connect(sio_url, socketio_path='/ws/socket.io', wait_timeout=10)
        # 阻塞等待，直到推理完成或外部要求停止
        while not stop_event.is_set() and sio.connected:
            sio.sleep(0.1)
    except Exception as e:
        print(f"[ModelServer] socket.io 进度监听失败（非致命）: {e}")
    finally:
        try:
            if sio.connected:
                sio.disconnect()
        except Exception:
            pass


def inpaint_via_server(
    image_rgb: np.ndarray,
    mask: np.ndarray,
    model_name: str,
    device: str,
    disable_nsfw: bool,
    iopaint_path: str = 'iopaint',
    prompt: str = '',
    negative_prompt: str = '',
    sd_steps: int = 50,
    sd_guidance_scale: float = 7.5,
    sd_seed: int = 42,
    progress_callback=None,
    enable_powerpaint_v2: bool = False,
) -> np.ndarray:
    """
    通过 iopaint HTTP server 执行修复，返回 RGB numpy 图像。

    :param prompt:             SD 系列模型的正向文字引导（非 SD 模型忽略）
    :param negative_prompt:    SD 系列模型的负向提示词（为空时使用默认值）
    :param sd_steps:           扩散步数（SD 模型生效）
    :param sd_guidance_scale:  CFG scale（SD 模型生效）
    :param sd_seed:            随机种子（SD 模型生效）
    :param progress_callback:  可选回调，签名 (percent: int, message: str)；
                               由 socket.io 监听 iopaint 的 diffusion_progress 事件驱动，
                               percent 范围 20%~95%（推理阶段），每个 DDIM 步更新一次
    """
    srv = get_server()
    srv.set_iopaint_path(iopaint_path)
    base_url = srv.ensure_running(model_name, device, disable_nsfw)

    # 标记推理开始：暂停 idle 计时，防止长时间推理期间 server 被自动关闭
    srv.begin_inference()

    # ── 启动 socket.io 进度监听线程 ─────────────────────────────────
    # iopaint server 通过 socket.io (/ws) emit diffusion_progress 事件，
    # 在 HTTP 请求阻塞期间，后台线程并行订阅该事件，将步骤映射为百分比后回调。
    stop_event = threading.Event()
    sio_thread = None
    if progress_callback is not None:
        sio_thread = threading.Thread(
            target=_listen_diffusion_progress,
            args=(base_url, sd_steps, progress_callback, stop_event),
            daemon=True,
            name='iopaint-sio-progress',
        )
        sio_thread.start()

    # 编码图像和掩码为 base64 PNG
    image_b64 = _ndarray_to_b64(cv2.cvtColor(image_rgb, cv2.COLOR_RGB2BGR), '.png')
    mask_b64 = _ndarray_to_b64(mask, '.png')

    payload_dict: dict = {
        'image': image_b64,
        'mask': mask_b64,
        # iopaint InpaintRequest 字段：
        #   sd_steps   —— SD/AnyText 等扩散模型的扩散步数
        #   ldm_steps  —— 旧版 LDM 模型步数，同时赋值兼容旧版 iopaint
        'sd_steps': sd_steps,
        'ldm_steps': sd_steps,
        'sd_guidance_scale': sd_guidance_scale,
        'sd_seed': sd_seed,
        # 始终发送 prompt 字段——AnyText 等模型要求该字段存在（即使为空字符串）；
        # 非 SD 模型（LaMa/ZITS 等）会忽略此字段
        'prompt': prompt,
        'negative_prompt': (
            negative_prompt if negative_prompt
            else 'blurry, low quality, deformed, artifacts, watermark'
        ),
        'hd_strategy': 'Crop',
        'hd_strategy_resize_limit': 1024,
        'hd_strategy_crop_trigger_size': 800,
        'hd_strategy_crop_margin': 196,
    }

    # PowerPaint v2：需要在 InpaintRequest 里传 enable_powerpaint_v2=true
    # 以触发 iopaint ModelManager 的 PowerPaintV2 加载路径
    if enable_powerpaint_v2:
        payload_dict["enable_powerpaint_v2"] = True

    payload = json.dumps(payload_dict).encode('utf-8')

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
        # 等待日志线程将 iopaint 打印的错误堆栈刷入缓冲（500 的 traceback 在响应之后才输出）
        time.sleep(1.0)
        log_tail = '\n'.join(srv._log_lines[-40:]) if srv._log_lines else '（无日志）'
        raise RuntimeError(
            f"[ModelServer] inpaint 请求失败 HTTP {e.code}: {body}\n"
            f"--- iopaint 服务器最近日志 ---\n{log_tail}"
        )
    finally:
        # 推理结束：恢复 idle 计时（无论成功/失败都必须执行）
        srv.end_inference()
        # HTTP 请求结束（无论成功/失败），通知 socket.io 线程退出
        stop_event.set()
        if sio_thread is not None:
            sio_thread.join(timeout=3)

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
