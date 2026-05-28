"""
模型下载函数集合（供 DownloadQueue 和旧 SSE 接口共用）。

之前这些函数定义在 app/routers/models.py 中，导致 core/download_queue.py
必须通过 importlib 反向导入 app 层，违反分层架构。
现将它们移至 core 层，两个调用方均从此模块导入。
"""

import os
import shutil
import time
import urllib.request
from pathlib import Path

from core.utils import fmt_speed, fmt_size
from core.paths import resolve_model_cache_path, U2NET_HOME, HF_HOME as _HF_HOME


# ── 取消检查辅助 ───────────────────────────────────────────────────────────────

def _check_cancel(cancel_check) -> None:
    """如果 cancel_check 返回 True，抛出 InterruptedError。"""
    if cancel_check and cancel_check():
        raise InterruptedError("下载已取消")


# ── rembg ONNX 下载 ────────────────────────────────────────────────────────────

def download_rembg(cfg: dict, progress_cb=None, cancel_check=None) -> None:
    """
    下载 rembg ONNX 模型到 ~/.u2net/。

    完全绕过 rembg 的 pooch 机制，使用 urllib 直接下载 ONNX 文件，
    支持实时速度/进度回调，支持通过 network.github_mirror 配置镜像加速。

    GitHub Releases 镜像示例（settings.json）：
      "network": { "github_mirror": "https://mirror.ghproxy.com" }

    下载后写入 ~/.u2net/<onnx_filename>，与 rembg 的 U2NET_HOME 保持一致。

    :param cancel_check: 可选，无参函数，返回 True 时抛出 InterruptedError 终止下载。
    """

    from app.config import get as get_config

    def _on_cancel():
        _check_cancel(cancel_check)

    import urllib.request
    from pathlib import Path

    onnx_filename = cfg.get("onnx_filename")
    if not onnx_filename:
        # 向后兼容
        session_name = cfg.get("rembg_session_name", cfg.get("rembg_model_name", cfg["id"]))
        onnx_filename = f"{session_name}.onnx"

    github_url = cfg.get("github_download_url", "")
    if not github_url:
        raise ValueError(f"rembg 模型缺少 github_download_url 字段: {cfg['id']!r}")

    # 支持 GitHub 镜像加速（如 https://mirror.ghproxy.com）
    github_mirror = get_config("network.github_mirror", "").rstrip("/")
    if github_mirror:
        url = f"{github_mirror}/{github_url}"
    else:
        url = github_url

    u2net_home = Path(os.environ.get("U2NET_HOME", Path.home() / ".u2net"))
    u2net_home.mkdir(parents=True, exist_ok=True)
    dest = u2net_home / onnx_filename

    # 文件已存在且大小合理则跳过（避免重复下载）
    size_mb = cfg.get("size_mb", 0) or 0
    if dest.exists() and size_mb:
        existing_mb = dest.stat().st_size / (1024 * 1024)
        if existing_mb >= size_mb * 0.5:
            if progress_cb:
                progress_cb({"message": f"文件已存在，跳过", "speed": "", "downloaded": "", "total_size": ""})
            return

    req = urllib.request.Request(url, headers={"User-Agent": "HiImage/2.0"})
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            content_length = resp.headers.get("Content-Length")
            total_bytes = int(content_length) if content_length else 0

            downloaded = 0
            start_time = time.monotonic()
            last_report_time = start_time
            last_report_bytes = 0

            with open(dest, "wb") as f:
                while True:
                    _on_cancel()
                    chunk = resp.read(256 * 1024)  # 256KB
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)

                    now = time.monotonic()
                    elapsed = now - last_report_time
                    if elapsed >= 0.5 and progress_cb:
                        speed = (downloaded - last_report_bytes) / elapsed
                        last_report_time = now
                        last_report_bytes = downloaded
                        pct = f"{downloaded * 100 // total_bytes}%" if total_bytes else ""
                        progress_cb({
                            "message": f"{onnx_filename} {pct}",
                            "speed": fmt_speed(speed),
                            "downloaded": fmt_size(downloaded),
                            "total_size": fmt_size(total_bytes) if total_bytes else "?",
                        })

        elapsed_total = time.monotonic() - start_time
        avg_speed = downloaded / elapsed_total if elapsed_total > 0 else 0

    except InterruptedError:
        if dest.exists():
            try:
                dest.unlink()
            except OSError:
                pass
        raise
    except Exception as e:
        if dest.exists():
            try:
                dest.unlink()
            except OSError:
                pass
        raise RuntimeError(
            f"下载 {onnx_filename} 失败: {e}\n"
            f"原始地址: {github_url}\n"
            f"如访问 GitHub 困难，可在设置 → 网络 中配置 GitHub 镜像加速地址\n"
            f"（如 https://mirror.ghproxy.com）"
        ) from e


# ── HuggingFace 单模型下载 ─────────────────────────────────────────────────────

def _make_tqdm_class(progress_cb, cancel_check, file_idx: int, total_files: int, filename: str):
    """
    工厂函数：生成绑定当前文件上下文的 tqdm 兼容进度类。

    hf_hub_download() 内部使用 tqdm 显示进度，传入自定义类可将下载进度
    转发给我们的 progress_cb 回调，并在 update() 中检查取消信号。
    """
    class _HFProgressAdapter:
        def __init__(self, *, total=None, **kw):
            self.total = total  # 文件总字节数（Content-Length）
            self.n = 0          # 已下载字节数
            self._last_t = time.monotonic()
            self._last_n = 0

        def update(self, n=1):
            # 取消检查：在每次 chunk 更新时触发
            if cancel_check and cancel_check():
                raise InterruptedError("下载已取消")
            self.n += n
            now = time.monotonic()
            elapsed = now - self._last_t
            if elapsed >= 0.5 and progress_cb:
                speed = (self.n - self._last_n) / elapsed
                self._last_t = now
                self._last_n = self.n
                pct = f"{self.n * 100 // self.total}%" if self.total else ""
                progress_cb({
                    "message": f"({file_idx + 1}/{total_files}) {filename} {pct}",
                    "speed": fmt_speed(speed),
                    "downloaded": fmt_size(self.n),
                    "total_size": fmt_size(self.total) if self.total else "?",
                })

        def __enter__(self):
            return self

        def __exit__(self, *args, **kwargs):
            pass

        def close(self):
            pass

        def set_postfix(self, *args, **kwargs):
            pass

        def set_description(self, *args, **kwargs):
            pass

    return _HFProgressAdapter


def download_hf(cfg: dict, progress_cb=None, cancel_check=None) -> None:
    """
    通过 huggingface_hub 逐文件下载 HF 模型到标准 hub 缓存格式，支持实时速度回调。

    下载路径：~/.cache/huggingface/hub/models--<org>--<repo>/（标准 HF hub 格式）
    不再使用 manual/ 扁平目录。

    - 遵循 HF_ENDPOINT 环境变量（镜像站支持）
    - 遵循 HF_TOKEN 环境变量（门控模型授权）
    - 401/403 时抛出友好错误信息
    - 进度和取消通过自定义 tqdm 类（_HFProgressAdapter）传递给 hf_hub_download()

    :param cancel_check: 可选，无参函数，返回 True 时抛出 InterruptedError 终止下载。
    """

    def _on_cancel():
        _check_cancel(cancel_check)

    import inspect
    from huggingface_hub import list_repo_files, hf_hub_download
    from huggingface_hub.errors import GatedRepoError, RepositoryNotFoundError

    hf_cache = str(_HF_HOME)
    token = os.environ.get("HF_TOKEN") or None
    repo_id = cfg["hf_model_id"]
    name = cfg.get("name", repo_id)

    # tqdm_class 参数在 huggingface_hub >= 0.20 才支持，兼容旧版
    _supports_tqdm_class = "tqdm_class" in inspect.signature(hf_hub_download).parameters

    IGNORE_SUFFIXES = {".msgpack", ".h5"}
    IGNORE_PREFIXES = ("flax_model", "tf_model")

    # 下载前先检测：如果已存在但状态为 corrupted，删除缓存目录重新下载
    from core.model_checker import ModelChecker
    checker = ModelChecker()
    pre_check = checker.check_model(cfg["id"])
    if pre_check.status == "corrupted":
        # 清理 hub/ 目录（新路径）——直接按路径公式删除，避免 scan_cache_dir
        # 对合成符号链接结构（无真实 blobs）返回不一致结果的问题
        try:
            hub_model_dir = Path(hf_cache) / "hub" / ("models--" + repo_id.replace("/", "--"))
            if hub_model_dir.exists():
                shutil.rmtree(hub_model_dir)
        except Exception:
            pass
        # 向后兼容：也清理旧的 manual/ 目录
        manual_dir = Path(hf_cache) / "manual" / repo_id.replace("/", "--")
        if manual_dir.exists():
            shutil.rmtree(manual_dir)

    try:
        all_files = list(list_repo_files(repo_id, token=token))
    except GatedRepoError:
        raise RuntimeError(
            f"{name} 是门控模型，需要先在 HuggingFace 网站同意使用协议，"
            f"并在设置中填写 HF Token（https://huggingface.com/{repo_id}）"
        )
    except RepositoryNotFoundError:
        raise RuntimeError(
            f"仓库 {repo_id} 不存在或无访问权限，请检查模型 ID 或 HF Token"
        )

    files = [
        f for f in all_files
        if not any(f.endswith(s) for s in IGNORE_SUFFIXES)
        and not any(f.startswith(p) for p in IGNORE_PREFIXES)
    ]

    hub_cache_dir = str(Path(hf_cache) / "hub")
    total_files = len(files)

    for file_idx, filename in enumerate(files):
        _on_cancel()  # 文件间取消检查

        if progress_cb:
            progress_cb({
                "message": f"准备下载 ({file_idx + 1}/{total_files}): {filename}",
                "speed": "", "downloaded": "", "total_size": "",
            })

        tqdm_cls = _make_tqdm_class(progress_cb, cancel_check, file_idx, total_files, filename)

        try:
            hf_kwargs: dict = dict(
                repo_id=repo_id,
                filename=filename,
                cache_dir=hub_cache_dir,
                token=token,
            )
            if _supports_tqdm_class:
                hf_kwargs["tqdm_class"] = tqdm_cls
            hf_hub_download(**hf_kwargs)
        except InterruptedError:
            raise
        except Exception as file_err:
            raise RuntimeError(f"下载文件 {filename} 失败: {file_err}") from file_err


def download_hf_multi(cfg: dict, progress_cb=None, cancel_check=None) -> None:
    """
    组合多子模型下载（hf_models 列表）。

    逐个调用 download_hf 下载每个子模型，汇聚进度回调。
    progress_cb 收到的 message 前缀为 "[子模型名]"，方便前端区分。

    :param cancel_check: 可选，无参函数，返回 True 时抛出 InterruptedError 终止下载。
    """
    hf_models: list[dict] = cfg.get("hf_models", [])
    total_repos = len(hf_models)

    for repo_idx, sub in enumerate(hf_models):
        sub_name = sub.get("name", sub["id"])

        sub_cfg = {
            **cfg,
            "id": cfg["id"],
            "name": sub_name,
            "hf_model_id": sub["id"],
            "size_mb": sub.get("size_mb"),
        }

        def _wrapped_cb(data: dict, _name=sub_name, _idx=repo_idx, _total=total_repos):
            if progress_cb and data:
                orig_msg = data.get("message", "")
                data["message"] = f"[{_name}] ({_idx + 1}/{_total}) {orig_msg}"
                progress_cb(data)

        download_hf(sub_cfg, _wrapped_cb, cancel_check)


# ── 直接 HTTP 下载 ────────────────────────────────────────────────────────────

def download_direct(cfg: dict, progress_cb=None, cancel_check=None) -> None:
    """
    通过 urllib 直接下载单文件模型（如 Real-ESRGAN .pth 文件），支持速度回调。

    :param cancel_check: 可选，无参函数，返回 True 时抛出 InterruptedError 终止下载。
    """

    def _on_cancel():
        _check_cancel(cancel_check)

    dest = resolve_model_cache_path(cfg)
    dest.parent.mkdir(parents=True, exist_ok=True)

    url = cfg["download_url"]
    name = cfg.get("name", cfg.get("id", url))

    req = urllib.request.Request(url, headers={"User-Agent": "HiImage/2.0"})

    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            content_length = resp.headers.get("Content-Length")
            total_bytes = int(content_length) if content_length else 0

            downloaded = 0
            start_time = time.monotonic()
            last_report_time = start_time
            last_report_bytes = 0

            with open(dest, "wb") as f:
                while True:
                    _on_cancel()
                    chunk = resp.read(256 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)

                    now = time.monotonic()
                    elapsed_since_last = now - last_report_time
                    if elapsed_since_last >= 0.5 and progress_cb:
                        speed = (downloaded - last_report_bytes) / elapsed_since_last
                        last_report_time = now
                        last_report_bytes = downloaded
                        pct = f"{downloaded * 100 // total_bytes}%" if total_bytes else ""
                        progress_cb({
                            "message": pct,
                            "speed": fmt_speed(speed),
                            "downloaded": fmt_size(downloaded),
                            "total_size": fmt_size(total_bytes) if total_bytes else "?",
                        })

    except InterruptedError:
        if dest.exists():
            try:
                dest.unlink()
            except OSError:
                pass
        raise
    except Exception as e:
        if dest.exists():
            try:
                dest.unlink()
            except OSError:
                pass
        raise
