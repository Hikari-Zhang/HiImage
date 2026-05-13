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

def _extract_cancel_check(cfg: dict):
    """
    从 cfg 中弹出 '_cancel_check' 函数并返回。
    返回的函数在无取消请求时返回 False；调用方应定期检查并抛出 InterruptedError。
    """
    return cfg.pop("_cancel_check", None)


def _check_cancel(cancel_check) -> None:
    """如果 cancel_check 返回 True，抛出 InterruptedError。"""
    if cancel_check and cancel_check():
        raise InterruptedError("下载已取消")


# ── rembg ONNX 下载 ────────────────────────────────────────────────────────────

def download_rembg(cfg: dict, progress_cb=None) -> None:
    """
    下载 rembg ONNX 模型到 ~/.u2net/。

    完全绕过 rembg 的 pooch 机制，使用 urllib 直接下载 ONNX 文件，
    支持实时速度/进度回调，支持通过 network.github_mirror 配置镜像加速。

    GitHub Releases 镜像示例（settings.json）：
      "network": { "github_mirror": "https://mirror.ghproxy.com" }

    下载后写入 ~/.u2net/<onnx_filename>，与 rembg 的 U2NET_HOME 保持一致。
    """
    from app.config import get as get_config

    cancel_check = _extract_cancel_check(cfg)

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

def download_hf(cfg: dict, progress_cb=None) -> None:
    """
    通过 huggingface_hub 逐文件下载 HF 模型，支持实时速度回调。

    - 遵循 HF_ENDPOINT 环境变量（镜像站支持）
    - 遵循 HF_TOKEN 环境变量（门控模型授权）
    - 401/403 时抛出友好错误信息
    """
    cancel_check = _extract_cancel_check(cfg)

    def _on_cancel():
        _check_cancel(cancel_check)

    from huggingface_hub import list_repo_files, hf_hub_url
    from huggingface_hub.errors import GatedRepoError, RepositoryNotFoundError

    hf_cache = os.environ.get("HF_HOME", str(Path.home() / ".cache" / "huggingface"))
    token = os.environ.get("HF_TOKEN") or None
    repo_id = cfg["hf_model_id"]
    name = cfg.get("name", repo_id)

    hf_endpoint = os.environ.get("HF_ENDPOINT", "https://huggingface.com")

    IGNORE_SUFFIXES = {".msgpack", ".h5"}
    IGNORE_PREFIXES = ("flax_model", "tf_model")

    # 下载前先检测：如果已存在但状态为 corrupted，删除整个缓存目录重新下载
    from core.model_checker import ModelChecker
    checker = ModelChecker()
    pre_check = checker.check_model(cfg["id"])
    if pre_check.status == "corrupted":
        manual_dir = Path(hf_cache) / "manual" / repo_id.replace("/", "--")
        if manual_dir.exists():
            shutil.rmtree(manual_dir)
        try:
            from huggingface_hub import scan_cache_dir
            hub_dir = Path(hf_cache) / "hub"
            if hub_dir.exists():
                cache_info = scan_cache_dir(hub_dir)
                for repo in cache_info.repos:
                    if repo.repo_id == repo_id:
                        shutil.rmtree(repo.repo_path)
                        break
        except Exception:
            pass

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

    repo_dir = Path(hf_cache) / "manual" / repo_id.replace("/", "--")
    repo_dir.mkdir(parents=True, exist_ok=True)

    total_files = len(files)
    skipped_files = 0
    for file_idx, filename in enumerate(files):
        _on_cancel()

        dest = repo_dir / filename
        dest.parent.mkdir(parents=True, exist_ok=True)

        if dest.exists():
            file_size = dest.stat().st_size
            is_weight_file = dest.suffix.lower() in {".safetensors", ".bin", ".pth", ".pt", ".ckpt"}
            min_size = 1024 * 1024 if is_weight_file else 1024
            if file_size < min_size:
                try:
                    dest.unlink()
                except OSError:
                    pass
            elif dest.suffix.lower() == ".safetensors":
                header_ok = False
                try:
                    import struct
                    with open(str(dest), "rb") as _f:
                        _hdr = _f.read(8)
                    if len(_hdr) == 8:
                        _hlen = struct.unpack("<Q", _hdr)[0]
                        header_ok = (0 < _hlen <= 100 * 1024 * 1024)
                    if not header_ok:
                        raise ValueError("header length 异常")
                except Exception:
                    try:
                        dest.unlink()
                    except OSError:
                        pass
                if header_ok:
                    skipped_files += 1
                    if progress_cb:
                        progress_cb({
                            "message": f"文件已存在，跳过 ({file_idx + 1}/{total_files}): {filename}",
                            "speed": "", "downloaded": "", "total_size": "",
                        })
                    continue
            else:
                skipped_files += 1
                if progress_cb:
                    progress_cb({
                        "message": f"文件已存在，跳过 ({file_idx + 1}/{total_files}): {filename}",
                        "speed": "", "downloaded": "", "total_size": "",
                    })
                continue

        url = hf_hub_url(repo_id=repo_id, filename=filename)
        headers = {"User-Agent": "HiImage/2.0"}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        req = urllib.request.Request(url, headers=headers)
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
                                "message": f"({file_idx + 1}/{total_files}) {filename} {pct}",
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
        except Exception as file_err:
            if dest.exists():
                try:
                    dest.unlink()
                except OSError:
                    pass
            raise RuntimeError(f"下载文件 {filename} 失败: {file_err}") from file_err

    if skipped_files == total_files:
        pass  # 所有文件已存在


def download_hf_multi(cfg: dict, progress_cb=None) -> None:
    """
    组合多子模型下载（hf_models 列表）。

    逐个调用 download_hf 下载每个子模型，汇聚进度回调。
    progress_cb 收到的 message 前缀为 "[子模型名]"，方便前端区分。
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

        download_hf(sub_cfg, _wrapped_cb)


# ── 直接 HTTP 下载 ────────────────────────────────────────────────────────────

def download_direct(cfg: dict, progress_cb=None) -> None:
    """
    通过 urllib 直接下载单文件模型（如 Real-ESRGAN .pth 文件），支持速度回调。
    - 支持通过 cfg['_cancel_check'] 检查取消请求。
    """
    cancel_check = _extract_cancel_check(cfg)

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
