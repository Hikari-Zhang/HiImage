"""
模型管理端点

GET    /api/models/health              → 检测所有注册模型的完整性
GET    /api/models/health/{model_id}   → 检测单个模型的完整性
GET    /api/models/list                → 返回所有注册模型的元数据（含当前状态）

── 新下载队列 API（推荐） ──────────────────────────────────────────────────────
POST   /api/models/download/{model_id} → 提交单模型下载任务（返回任务状态）
POST   /api/models/download/bulk       → 批量提交（一键下载），返回任务列表
DELETE /api/models/download/{model_id} → 取消下载任务
GET    /api/models/subscribe/{model_id}→ SSE 订阅单个模型的实时状态变更
GET    /api/models/queue               → 查询当前队列中所有活跃任务

── 旧 SSE 接口（已废弃，保留向下兼容） ─────────────────────────────────────────
GET    /api/models/download            → SSE 流：一键下载所有缺失模型（旧）
GET    /api/models/download/{model_id} → SSE 流：下载单个模型（旧）

DELETE /api/models/{model_id}/files    → 删除指定模型的本地权重文件（不修改 models.yaml）
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from core.model_checker import ModelChecker
from core.model_registry import MODELS, MODE_GROUPS
from core.constants import ModelStatus as MS, DownloadStatus as DS, Provider, IOPaintMode

logger = logging.getLogger("models")

router = APIRouter(prefix="/models", tags=["models"])


def _result_to_dict(r) -> dict:
    """将 ModelCheckResult 转为可序列化字典。"""
    return {
        "id":               r.model_id,
        "name":             r.name,
        "provider":         r.provider,
        "status":           r.status,
        "message":          r.message,
        "file_path":        r.file_path,
        "size_mb":          round(r.size_bytes / (1024 * 1024), 1) if r.size_bytes else None,
        "expected_size_mb": r.expected_size_mb,
    }


@router.get("/health")
async def models_health():
    """
    检测所有注册模型的文件完整性。

    返回格式：
    ```json
    {
      "models": [
        {"id": "birefnet", "status": "ok", "message": "100 MB", ...},
        {"id": "flux_fill", "status": "missing", "message": "未在 HF 缓存中找到 ...", ...}
      ],
      "summary": {"ok": 18, "missing": 6, "corrupted": 0, "unknown": 2, "total": 26}
    }
    ```
    """
    checker = ModelChecker()
    results = checker.check_all()
    return {
        "models": [_result_to_dict(r) for r in results],
        "summary": {
            "ok":       sum(1 for r in results if r.status == MS.OK),
            "missing":  sum(1 for r in results if r.status in (MS.MISSING, MS.PARTIAL)),
            "corrupted": sum(1 for r in results if r.status == MS.CORRUPTED),
            "unknown":  sum(1 for r in results if r.status == MS.UNKNOWN),
            "total":    len(results),
        },
    }


@router.get("/health/{model_id}")
async def model_health(model_id: str):
    """
    检测单个模型的文件完整性。

    返回格式：
    ```json
    {"id": "birefnet", "status": "ok", "message": "100 MB", ...}
    ```
    """
    checker = ModelChecker()
    result = checker.check_model(model_id)
    if result.status == MS.UNKNOWN and result.message.startswith("模型 ID"):
        raise HTTPException(status_code=404, detail=f"未知模型 ID: {model_id!r}")
    return _result_to_dict(result)


@router.get("/list")
async def models_list():
    """
    返回所有注册模型的元数据（来自 models.yaml），
    附带实时完整性状态。
    """
    checker = ModelChecker()
    check_map = {r.model_id: r for r in checker.check_all()}

    result = []
    for m in MODELS:
        mid = m["id"]
        r = check_map.get(mid)
        result.append({
            **{k: v for k, v in m.items() if k not in ("supported_params",)},
            "status":  r.status if r else MS.UNKNOWN,
            "message": r.message if r else "",
        })

    return {
        "models": result,
        "mode_groups": MODE_GROUPS,
    }


# ── 一键下载所有缺失模型（SSE 流） ──────────────────────────────────────────

def _sse(event: str, data: dict) -> str:
    """构造一条 SSE 消息。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _fmt_speed(bytes_per_sec: float) -> str:
    """将字节/秒格式化为人类可读字符串。"""
    if bytes_per_sec >= 1024 * 1024:
        return f"{bytes_per_sec / (1024 * 1024):.1f} MB/s"
    elif bytes_per_sec >= 1024:
        return f"{bytes_per_sec / 1024:.0f} KB/s"
    return f"{bytes_per_sec:.0f} B/s"


def _fmt_size(total_bytes: int) -> str:
    if total_bytes >= 1024 * 1024 * 1024:
        return f"{total_bytes / (1024**3):.1f} GB"
    elif total_bytes >= 1024 * 1024:
        return f"{total_bytes / (1024**2):.0f} MB"
    elif total_bytes >= 1024:
        return f"{total_bytes / 1024:.0f} KB"
    return f"{total_bytes} B"


async def _download_generator(request: Request):
    """
    异步生成器：依次下载每个缺失/损坏的模型，逐条推送 SSE 事件。

    SSE 事件类型：
      start     → 开始处理（含总数）
      model     → 单个模型状态变更（checking / skipped / downloading / done / error）
                  downloading 状态时含 speed / downloaded / total_size 字段
      finish    → 全部完成
    """
    checker = ModelChecker()
    loop = asyncio.get_event_loop()

    # IOPaint cli 模型（需要下载权重但由 iopaint 自动管理），单独记录跳过原因
    cli_models = [
        m for m in MODELS
        if m.get("provider") == Provider.IOPAINT and m.get("iopaint_mode") == IOPaintMode.CLI
    ]
    # 过滤出需要下载的模型
    downloadable = [m for m in MODELS if m not in cli_models]

    if cli_models:
        logger.info(
            f"[一键下载] 跳过 {len(cli_models)} 个 IOPaint cli 模型"
            f"（首次使用时由 iopaint 自动下载）："
            f" {', '.join(m.get('name', m['id']) for m in cli_models)}"
        )

    logger.info(f"[一键下载] 开始：共 {len(downloadable)} 个可下载模型，{len(cli_models)} 个 IOPaint 内置跳过")
    yield _sse("start", {"total": len(downloadable), "message": f"共 {len(downloadable)} 个可下载模型，正在检测..."})

    ok_count = 0
    skip_count = 0
    fail_count = 0

    for idx, model_cfg in enumerate(downloadable):
        # 客户端断开时停止
        if await request.is_disconnected():
            logger.warning("[一键下载] 客户端断开连接，终止下载")
            break

        mid = model_cfg["id"]
        name = model_cfg.get("name", mid)
        provider = model_cfg.get("provider", "")

        # 检测当前状态
        logger.debug(f"[一键下载] 检测 ({idx + 1}/{len(downloadable)}): {name}")
        result = await loop.run_in_executor(None, checker.check_model, mid)

        if result.status == MS.OK:
            skip_count += 1
            logger.info(f"[一键下载] 已存在，跳过 ({idx + 1}/{len(downloadable)}): {name} — {result.message}")
            yield _sse("model", {
                "id": mid, "name": name, "index": idx + 1, "total": len(downloadable),
                "status": DS.SKIPPED, "message": f"已存在，跳过 ({result.message})"
            })
            continue

        if result.status == MS.CORRUPTED:
            logger.warning(f"[一键下载] 文件损坏，重新下载 ({idx + 1}/{len(downloadable)}): {name} — {result.message}")
        elif result.status == MS.PARTIAL:
            logger.warning(f"[一键下载] 下载不完整，重新下载 ({idx + 1}/{len(downloadable)}): {name} — {result.message}")
        else:
            logger.info(f"[一键下载] 缺失，开始下载 ({idx + 1}/{len(downloadable)}): {name}")

        # 开始下载 — 通过 asyncio.Queue 从工作线程接收进度
        progress_queue: asyncio.Queue = asyncio.Queue()

        yield _sse("model", {
            "id": mid, "name": name, "index": idx + 1, "total": len(downloadable),
            "status": DS.DOWNLOADING, "message": "准备下载...",
            "speed": "", "downloaded": "", "total_size": "",
        })

        # 在线程池中运行下载，进度通过 queue 传回
        def _put(data: dict):
            loop.call_soon_threadsafe(progress_queue.put_nowait, data)

        if provider == Provider.REMBG:
            logger.info(f"[一键下载] 使用 rembg 下载 ONNX 权重: {name}")
            future = loop.run_in_executor(None, _download_rembg, model_cfg, _put)
        elif model_cfg.get("local_path") and model_cfg.get("download_url") and not model_cfg.get("hf_model_id") and not model_cfg.get("hf_models"):
            logger.info(f"[一键下载] 直接下载权重文件: {name} → {model_cfg['local_path']}")
            future = loop.run_in_executor(None, _download_direct, model_cfg, _put)
        elif model_cfg.get("hf_models"):
            logger.info(f"[一键下载] 从 HuggingFace 下载组合模型: {name} ({len(model_cfg['hf_models'])} 个子模型)")
            future = loop.run_in_executor(None, _download_hf_multi, model_cfg, _put)
        elif model_cfg.get("hf_model_id"):
            logger.info(f"[一键下载] 从 HuggingFace 下载: {name} (repo: {model_cfg['hf_model_id']})")
            future = loop.run_in_executor(None, _download_hf, model_cfg, _put)
        else:
            logger.warning(f"[一键下载] 无下载来源，跳过: {name} (id={mid}, provider={provider})")
            yield _sse("model", {
                "id": mid, "name": name, "index": idx + 1, "total": len(downloadable),
                "status": DS.SKIPPED, "message": "无下载来源，跳过"
            })
            skip_count += 1
            continue

        try:
            # 持续消费进度，直到 future 结束
            _last_log_pct = -1
            while not future.done():
                await asyncio.sleep(0.2)
                # 消费当前 queue 中所有待处理的进度消息
                while not progress_queue.empty():
                    item = progress_queue.get_nowait()
                    if isinstance(item, dict):
                        # 每 10% 向日志写一次进度（避免日志刷屏）
                        msg = item.get("message", "")
                        if "%" in msg:
                            try:
                                pct = int(msg.split("%")[0].split()[-1])
                                if pct // 10 > _last_log_pct // 10:
                                    _last_log_pct = pct
                                    logger.debug(
                                        f"[下载进度] {name}: {pct}%"
                                        f" {item.get('downloaded','')} / {item.get('total_size','')}"
                                        f" @ {item.get('speed','')}"
                                    )
                            except (ValueError, IndexError):
                                pass
                        yield _sse("model", {
                            "id": mid, "name": name, "index": idx + 1, "total": len(downloadable),
                            "status": DS.DOWNLOADING,
                            **item,
                        })

            # future 结束后再清空一次剩余队列
            while not progress_queue.empty():
                item = progress_queue.get_nowait()
                if isinstance(item, dict):
                    yield _sse("model", {
                        "id": mid, "name": name, "index": idx + 1, "total": len(downloadable),
                        "status": DS.DOWNLOADING,
                        **item,
                    })

            # 检查是否有异常
            exc = future.exception()
            if exc:
                raise exc

            ok_count += 1
            logger.info(f"[一键下载] ✓ 下载完成 ({idx + 1}/{len(downloadable)}): {name}")
            yield _sse("model", {
                "id": mid, "name": name, "index": idx + 1, "total": len(downloadable),
                "status": DS.DONE, "message": "下载完成",
                "speed": "", "downloaded": "", "total_size": "",
            })

        except Exception as e:
            fail_count += 1
            logger.error(f"[一键下载] ✗ 下载失败 ({idx + 1}/{len(downloadable)}): {name} — {e}", exc_info=True)
            yield _sse("model", {
                "id": mid, "name": name, "index": idx + 1, "total": len(downloadable),
                "status": DS.ERROR, "message": f"下载失败: {str(e)[:200]}",
                "speed": "", "downloaded": "", "total_size": "",
            })

    summary_msg = f"完成：{ok_count} 个下载成功，{skip_count} 个已跳过，{fail_count} 个失败"
    if fail_count > 0:
        logger.warning(f"[一键下载] {summary_msg}")
    else:
        logger.info(f"[一键下载] {summary_msg}")
    yield _sse("finish", {
        "ok": ok_count,
        "skipped": skip_count,
        "failed": fail_count,
        "message": summary_msg
    })


def _download_rembg(cfg: dict, progress_cb=None) -> None:
    """
    下载 rembg ONNX 模型到 ~/.u2net/。

    完全绕过 rembg 的 pooch 机制，使用 urllib 直接下载 ONNX 文件，
    支持实时速度/进度回调，支持通过 network.github_mirror 配置镜像加速。

    GitHub Releases 镜像示例（settings.json）：
      "network": { "github_mirror": "https://mirror.ghproxy.com" }

    下载后写入 ~/.u2net/<onnx_filename>，与 rembg 的 U2NET_HOME 保持一致。
    """
    import time
    import urllib.request
    from pathlib import Path
    from app.config import get as get_config

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
        logger.info(f"[rembg 下载] 使用镜像加速: {github_mirror} → {onnx_filename}")
    else:
        url = github_url
        logger.info(f"[rembg 下载] 直接下载: {onnx_filename}")

    u2net_home = Path(os.environ.get("U2NET_HOME", Path.home() / ".u2net"))
    u2net_home.mkdir(parents=True, exist_ok=True)
    dest = u2net_home / onnx_filename

    logger.debug(f"[rembg 下载] 目标路径: {dest}")

    # 文件已存在且大小合理则跳过（避免重复下载）
    size_mb = cfg.get("size_mb", 0) or 0
    if dest.exists() and size_mb:
        existing_mb = dest.stat().st_size / (1024 * 1024)
        if existing_mb >= size_mb * 0.5:
            logger.info(f"[rembg 下载] 文件已存在（{existing_mb:.0f} MB），跳过: {onnx_filename}")
            if progress_cb:
                progress_cb({"message": f"文件已存在，跳过", "speed": "", "downloaded": "", "total_size": ""})
            return

    logger.info(f"[rembg 下载] 开始下载: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "HiImage/2.0"})
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            content_length = resp.headers.get("Content-Length")
            total_bytes = int(content_length) if content_length else 0
            if total_bytes:
                logger.info(f"[rembg 下载] 文件大小: {_fmt_size(total_bytes)}")

            downloaded = 0
            start_time = time.monotonic()
            last_report_time = start_time
            last_report_bytes = 0

            with open(dest, "wb") as f:
                while True:
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
                            "speed": _fmt_speed(speed),
                            "downloaded": _fmt_size(downloaded),
                            "total_size": _fmt_size(total_bytes) if total_bytes else "?",
                        })

        elapsed_total = time.monotonic() - start_time
        avg_speed = downloaded / elapsed_total if elapsed_total > 0 else 0
        logger.info(
            f"[rembg 下载] 完成: {onnx_filename}"
            f" ({_fmt_size(downloaded)}, 耗时 {elapsed_total:.1f}s, 均速 {_fmt_speed(avg_speed)})"
        )

    except Exception as e:
        # 下载失败时删除可能的残缺文件
        if dest.exists():
            try:
                dest.unlink()
                logger.warning(f"[rembg 下载] 已删除残缺文件: {dest}")
            except OSError:
                pass
        logger.error(f"[rembg 下载] 失败: {onnx_filename} — {e}")
        raise RuntimeError(
            f"下载 {onnx_filename} 失败: {e}\n"
            f"原始地址: {github_url}\n"
            f"如访问 GitHub 困难，可在设置 → 网络 中配置 GitHub 镜像加速地址\n"
            f"（如 https://mirror.ghproxy.com）"
        ) from e


def _download_hf_multi(cfg: dict, progress_cb=None) -> None:
    """
    组合多子模型下载（hf_models 列表）。

    逐个调用 _download_hf 下载每个子模型，汇聚进度回调。
    progress_cb 收到的 message 前缀为 "[子模型名]"，方便前端区分。
    """
    hf_models: list[dict] = cfg.get("hf_models", [])
    total_repos = len(hf_models)

    for repo_idx, sub in enumerate(hf_models):
        sub_name = sub.get("name", sub["id"])
        logger.info(f"[多模型下载] 开始子模型 ({repo_idx + 1}/{total_repos}): {sub_name} ({sub['id']})")

        # 构造临时子 cfg，复用 _download_hf 的所有逻辑
        sub_cfg = {
            **cfg,                         # 继承父配置（HF_TOKEN、HF_ENDPOINT 等）
            "id":          cfg["id"],
            "name":        sub_name,
            "hf_model_id": sub["id"],
            "size_mb":     sub.get("size_mb"),
        }

        def _wrapped_cb(data: dict, _name=sub_name, _idx=repo_idx, _total=total_repos):
            if progress_cb and data:
                # 在 message 前加子模型前缀
                orig_msg = data.get("message", "")
                data["message"] = f"[{_name}] ({_idx + 1}/{_total}) {orig_msg}"
                progress_cb(data)

        _download_hf(sub_cfg, _wrapped_cb)

    logger.info(f"[多模型下载] 全部完成: {cfg.get('name', cfg['id'])} ({total_repos} 个子模型)")


def _download_hf(cfg: dict, progress_cb=None) -> None:
    """
    通过 huggingface_hub 逐文件下载 HF 模型，支持实时速度回调。

    - 遵循 HF_ENDPOINT 环境变量（镜像站支持）
    - 遵循 HF_TOKEN 环境变量（门控模型授权）
    - 401/403 时抛出友好错误信息
    """
    import time
    import urllib.request
    from huggingface_hub import list_repo_files, hf_hub_url
    from huggingface_hub.errors import GatedRepoError, RepositoryNotFoundError
    from app.config import MODELS_DIR

    hf_cache = str(Path(MODELS_DIR) / "huggingface")
    token = os.environ.get("HF_TOKEN") or None
    repo_id = cfg["hf_model_id"]
    name = cfg.get("name", repo_id)

    hf_endpoint = os.environ.get("HF_ENDPOINT", "https://huggingface.co")
    logger.info(f"[HF 下载] 开始: {name} (repo: {repo_id}, endpoint: {hf_endpoint})")

    IGNORE_SUFFIXES = {".msgpack", ".h5"}
    IGNORE_PREFIXES = ("flax_model", "tf_model")

    # 下载前先检测：如果已存在但状态为 corrupted，删除整个缓存目录重新下载
    from core.model_checker import ModelChecker
    checker = ModelChecker()
    pre_check = checker.check_model(cfg["id"])
    if pre_check.status == MS.CORRUPTED:
        manual_dir = Path(hf_cache) / "manual" / repo_id.replace("/", "--")
        if manual_dir.exists():
            import shutil
            try:
                shutil.rmtree(manual_dir)
                logger.warning(f"[HF 下载] 检测到损坏，已删除缓存目录: {manual_dir}")
            except OSError as rm_err:
                logger.error(f"[HF 下载] 删除损坏缓存目录失败: {manual_dir} — {rm_err}")
        # 同时清理标准 hub 缓存（如果存在）
        try:
            from huggingface_hub import scan_cache_dir
            hub_dir = Path(hf_cache) / "hub"
            if hub_dir.exists():
                cache_info = scan_cache_dir(hub_dir)
                for repo in cache_info.repos:
                    if repo.repo_id == repo_id:
                        shutil.rmtree(repo.repo_path)
                        logger.warning(f"[HF 下载] 检测到损坏，已删除 hub 缓存: {repo.repo_path}")
                        break
        except Exception:
            pass

    try:
        # 获取文件列表
        logger.info(f"[HF 下载] 获取文件列表: {repo_id}")
        try:
            all_files = list(list_repo_files(repo_id, token=token))
        except GatedRepoError:
            name = cfg.get("name", repo_id)
            raise RuntimeError(
                f"{name} 是门控模型，需要先在 HuggingFace 网站同意使用协议，"
                f"并在设置中填写 HF Token（https://huggingface.co/{repo_id}）"
            )
        except RepositoryNotFoundError:
            raise RuntimeError(
                f"仓库 {repo_id} 不存在或无访问权限，请检查模型 ID 或 HF Token"
            )

        # 过滤不需要的文件
        files = [
            f for f in all_files
            if not any(f.endswith(s) for s in IGNORE_SUFFIXES)
            and not any(f.startswith(p) for p in IGNORE_PREFIXES)
        ]

        logger.info(f"[HF 下载] 共 {len(files)} 个文件需要下载 (过滤掉 {len(all_files) - len(files)} 个): {repo_id}")

        # 构建本地缓存目录（简化路径，不用 huggingface_hub 复杂的 blob 结构）
        repo_dir = Path(hf_cache) / "manual" / repo_id.replace("/", "--")
        repo_dir.mkdir(parents=True, exist_ok=True)
        logger.debug(f"[HF 下载] 本地缓存目录: {repo_dir}")

        total_files = len(files)
        skipped_files = 0
        for file_idx, filename in enumerate(files):
            dest = repo_dir / filename
            dest.parent.mkdir(parents=True, exist_ok=True)

            if dest.exists():
                # 检查文件大小是否合理（至少 > 1KB，排除空文件/截断文件）
                # 对于权重文件（.safetensors/.bin）要求至少 1MB
                file_size = dest.stat().st_size
                is_weight_file = dest.suffix.lower() in {".safetensors", ".bin", ".pth", ".pt", ".ckpt"}
                min_size = 1024 * 1024 if is_weight_file else 1024  # 权重文件 1MB，其他 1KB
                if file_size < min_size:
                    # 文件过小，认为是损坏/截断，删除后重新下载
                    try:
                        dest.unlink()
                        logger.warning(f"[HF 下载] 文件过小（{file_size} B），删除并重新下载: {filename}")
                    except OSError:
                        pass
                elif dest.suffix.lower() == ".safetensors":
                    # 快速验证文件头（只读 8 字节），避免 safe_open 阻塞大文件
                    header_ok = False
                    try:
                        import struct as _struct
                        with open(str(dest), "rb") as _f:
                            _hdr = _f.read(8)
                        if len(_hdr) == 8:
                            _hlen = _struct.unpack("<Q", _hdr)[0]
                            header_ok = (0 < _hlen <= 100 * 1024 * 1024)
                        if not header_ok:
                            raise ValueError("header length 异常")
                    except Exception as sf_err:
                        logger.warning(
                            f"[HF 下载] safetensors 文件头损坏，删除并重新下载: {filename} — {sf_err}"
                        )
                        try:
                            dest.unlink()
                        except OSError:
                            pass
                    if header_ok:
                        skipped_files += 1
                        logger.debug(f"[HF 下载] 已存在且完整，跳过 ({file_idx + 1}/{total_files}): {filename} ({file_size // 1024} KB)")
                        if progress_cb:
                            progress_cb({
                                "message": f"文件已存在，跳过 ({file_idx + 1}/{total_files}): {filename}",
                                "speed": "", "downloaded": "", "total_size": "",
                            })
                        continue
                else:
                    skipped_files += 1
                    logger.debug(f"[HF 下载] 已存在且完整，跳过 ({file_idx + 1}/{total_files}): {filename} ({file_size // 1024} KB)")
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

            logger.info(f"[HF 下载] 下载文件 ({file_idx + 1}/{total_files}): {filename}")
            req = urllib.request.Request(url, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=300) as resp:
                    content_length = resp.headers.get("Content-Length")
                    total_bytes = int(content_length) if content_length else 0
                    if total_bytes:
                        logger.debug(f"[HF 下载] 文件大小: {_fmt_size(total_bytes)} — {filename}")

                    downloaded = 0
                    start_time = time.monotonic()
                    last_report_time = start_time
                    last_report_bytes = 0

                    with open(dest, "wb") as f:
                        while True:
                            chunk = resp.read(256 * 1024)  # 256KB chunks
                            if not chunk:
                                break
                            f.write(chunk)
                            downloaded += len(chunk)

                            now = time.monotonic()
                            elapsed_since_last = now - last_report_time
                            if elapsed_since_last >= 0.5 and progress_cb:  # 每 0.5s 推一次
                                speed = (downloaded - last_report_bytes) / elapsed_since_last
                                last_report_time = now
                                last_report_bytes = downloaded

                                pct = f"{downloaded * 100 // total_bytes}%" if total_bytes else ""
                                progress_cb({
                                    "message": f"({file_idx + 1}/{total_files}) {filename} {pct}",
                                    "speed": _fmt_speed(speed),
                                    "downloaded": _fmt_size(downloaded),
                                    "total_size": _fmt_size(total_bytes) if total_bytes else "?",
                                })

                    elapsed_file = time.monotonic() - start_time
                    avg_speed = downloaded / elapsed_file if elapsed_file > 0 else 0
                    logger.info(
                        f"[HF 下载] 文件完成 ({file_idx + 1}/{total_files}): {filename}"
                        f" ({_fmt_size(downloaded)}, {elapsed_file:.1f}s, {_fmt_speed(avg_speed)})"
                    )
            except Exception as file_err:
                # 删除残缺文件
                if dest.exists():
                    try:
                        dest.unlink()
                        logger.warning(f"[HF 下载] 已删除残缺文件: {dest}")
                    except OSError:
                        pass
                logger.error(f"[HF 下载] 文件下载失败 ({file_idx + 1}/{total_files}): {filename} — {file_err}")
                raise RuntimeError(f"下载文件 {filename} 失败: {file_err}") from file_err

        if skipped_files == total_files:
            logger.info(f"[HF 下载] 所有文件已存在，无需下载: {name}")
        else:
            logger.info(f"[HF 下载] 全部完成: {name} ({total_files - skipped_files} 个新下载, {skipped_files} 个已存在)")

    except RuntimeError:
        raise
    except Exception as e:
        logger.error(f"[HF 下载] 未预期错误: {name} — {e}")
        raise RuntimeError(str(e)) from e


def _download_direct(cfg: dict, progress_cb=None) -> None:
    """通过 urllib 直接下载单文件模型（如 Real-ESRGAN .pth 文件），支持速度回调。"""
    import time
    import urllib.request
    from app.config import PROJECT_ROOT

    dest = Path(PROJECT_ROOT) / cfg["local_path"]
    dest.parent.mkdir(parents=True, exist_ok=True)

    url = cfg["download_url"]
    name = cfg.get("name", cfg.get("id", url))
    logger.info(f"[直接下载] 开始: {name} → {dest.name} ({url})")

    req = urllib.request.Request(url, headers={"User-Agent": "HiImage/2.0"})

    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            content_length = resp.headers.get("Content-Length")
            total_bytes = int(content_length) if content_length else 0
            if total_bytes:
                logger.info(f"[直接下载] 文件大小: {_fmt_size(total_bytes)} — {dest.name}")

            downloaded = 0
            start_time = time.monotonic()
            last_report_time = start_time
            last_report_bytes = 0

            with open(dest, "wb") as f:
                while True:
                    chunk = resp.read(256 * 1024)  # 256KB chunks
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
                            "speed": _fmt_speed(speed),
                            "downloaded": _fmt_size(downloaded),
                            "total_size": _fmt_size(total_bytes) if total_bytes else "?",
                        })

        elapsed_total = time.monotonic() - start_time
        avg_speed = downloaded / elapsed_total if elapsed_total > 0 else 0
        logger.info(
            f"[直接下载] 完成: {dest.name}"
            f" ({_fmt_size(downloaded)}, 耗时 {elapsed_total:.1f}s, 均速 {_fmt_speed(avg_speed)})"
        )

    except Exception as e:
        # 删除残缺文件
        if dest.exists():
            try:
                dest.unlink()
                logger.warning(f"[直接下载] 已删除残缺文件: {dest}")
            except OSError:
                pass
        logger.error(f"[直接下载] 失败: {dest.name} — {e}")
        raise


# ── 新下载队列 API ────────────────────────────────────────────────────────────

@router.post("/download/bulk")
async def queue_download_bulk():
    """
    批量提交所有缺失/损坏的模型到下载队列（一键下载入口）。

    - 自动检测所有缺失/损坏模型
    - 批量提交，已在队列中的自动去重
    - 返回本次提交的任务列表

    返回格式：
    ```json
    {
      "submitted": 5,
      "tasks": [{"modelId": ..., "status": "downloading"|"queued", ...}]
    }
    ```
    """
    from core.model_checker import ModelChecker
    from core.download_queue import get_download_queue

    checker = ModelChecker()
    loop = asyncio.get_event_loop()

    # 过滤可下载模型（排除 IOPaint cli 内置模型）
    downloadable = [
        m for m in MODELS
        if not (m.get("provider") == Provider.IOPAINT and m.get("iopaint_mode") == IOPaintMode.CLI)
    ]

    # 检测哪些模型需要下载
    results = await loop.run_in_executor(None, checker.check_all)
    status_map = {r.model_id: r.status for r in results}

    need_download = [
        m for m in downloadable
        if status_map.get(m["id"], MS.MISSING) != MS.OK
    ]

    queue = get_download_queue()
    tasks = queue.bulk_submit([m["id"] for m in need_download])

    return {
        "submitted": len(need_download),
        "tasks": [t.to_dict() for t in tasks],
    }


@router.post("/download/{model_id}")
async def queue_download_single(model_id: str):
    """
    提交单个模型下载任务到全局队列。

    - 去重：已在队列/下载中则返回现有任务状态
    - 有空槽则立即开始，否则排队等待

    返回格式：
    ```json
    {"modelId": "lama", "modelName": "LaMa", "status": "downloading", "position": 0, ...}
    ```
    """
    from core.model_registry import MODEL_BY_ID
    cfg = MODEL_BY_ID.get(model_id)
    logger.info(f"[POST /download/{model_id}] 收到请求, cfg 存在={cfg is not None}")
    if not cfg:
        raise HTTPException(status_code=404, detail=f"未知模型 ID: {model_id!r}")

    if cfg.get("provider") == Provider.IOPAINT and cfg.get("iopaint_mode") == IOPaintMode.CLI:
        raise HTTPException(
            status_code=400,
            detail="IOPaint 内置模型将在首次使用时自动下载，无需手动触发"
        )

    from core.download_queue import get_download_queue
    queue = get_download_queue()
    task = queue.submit(model_id)
    logger.info(f"[POST /download/{model_id}] submit 返回: status={task.status!r}")
    return task.to_dict()


@router.delete("/download/{model_id}")
async def cancel_download(model_id: str):
    """
    取消指定模型的下载任务（queued 或 downloading 状态均可取消）。
    """
    from core.download_queue import get_download_queue
    queue = get_download_queue()
    success = queue.cancel(model_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"未找到活跃的下载任务: {model_id!r}")
    return {"ok": True, "message": f"已取消: {model_id}"}


@router.get("/subscribe/{model_id}")
async def subscribe_model(model_id: str, request: Request):
    """
    SSE 订阅：实时推送指定模型的下载状态变更。

    - 连接后立即推送当前状态
    - 每次状态变更（queued→downloading→done/error）推送一次
    - 任务完成/失败后服务端自动关闭连接
    - 支持多个客户端同时订阅同一模型

    事件类型：status
    """
    from core.download_queue import get_download_queue
    queue = get_download_queue()

    async def _generate():
        async for event in queue.subscribe(model_id):
            if await request.is_disconnected():
                break
            if event.get("heartbeat"):
                # 发送 SSE 注释作为心跳，保持连接
                yield ": heartbeat\n\n"
            else:
                yield _sse("status", event)

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/queue")
async def get_queue_status():
    """
    查询当前下载队列中所有活跃任务（queued + downloading）。

    返回格式：
    ```json
    {
      "active": [...],
      "max_concurrent": 3
    }
    ```
    """
    from core.download_queue import get_download_queue
    queue = get_download_queue()
    return {
        "active": [t.to_dict() for t in queue.list_active()],
        "max_concurrent": queue.max_concurrent,
    }


# ── 旧 SSE 接口（保留向下兼容）────────────────────────────────────────────────

@router.get("/download")
async def models_download(request: Request):
    """
    [已废弃] SSE 流：一键下载所有缺失/损坏的模型。
    新代码请使用 POST /api/models/download/bulk + GET /api/models/subscribe/{id}。

    客户端通过 EventSource 连接此接口，接收实时进度推送。
    事件类型：start / model / finish
    """
    return StreamingResponse(
        _download_generator(request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


async def _download_single_generator(request: Request, model_id: str):
    """
    异步生成器：下载指定单个模型，推送 SSE 事件。
    事件类型与 _download_generator 保持一致：start / model / finish
    """
    from core.model_registry import MODEL_BY_ID

    cfg = MODEL_BY_ID.get(model_id)
    if not cfg:
        yield _sse("finish", {
            "ok": 0, "skipped": 0, "failed": 1,
            "message": f"未知模型 ID: {model_id!r}"
        })
        return

    checker = ModelChecker()
    loop = asyncio.get_event_loop()
    name = cfg.get("name", model_id)

    # IOPaint cli 模型：首次使用由 iopaint 自动下载，此接口无法主动触发
    if cfg.get("provider") == Provider.IOPAINT and cfg.get("iopaint_mode") == IOPaintMode.CLI:
        logger.info(
            f"[单模型下载] 跳过 IOPaint cli 模型: {name}"
            f"（iopaint 将在首次使用时自动下载权重，size_mb={cfg.get('size_mb')} MB）"
        )
        yield _sse("finish", {
            "ok": 0, "skipped": 1, "failed": 0,
            "message": "IOPaint 内置模型将在首次使用时自动下载，无需手动触发"
        })
        return
    provider = cfg.get("provider", "")

    logger.info(f"[单模型下载] 开始：{name} (id={model_id})")
    yield _sse("start", {"total": 1, "message": f"准备下载 {name}..."})

    # 检测当前状态
    result = await loop.run_in_executor(None, checker.check_model, model_id)
    if result.status == MS.OK:
        logger.info(f"[单模型下载] 已存在，跳过: {name} — {result.message}")
        yield _sse("model", {
            "id": model_id, "name": name, "index": 1, "total": 1,
            "status": DS.SKIPPED, "message": f"已存在，跳过 ({result.message})"
        })
        yield _sse("finish", {"ok": 0, "skipped": 1, "failed": 0, "message": "模型已存在，无需重新下载"})
        return

    progress_queue: asyncio.Queue = asyncio.Queue()

    yield _sse("model", {
        "id": model_id, "name": name, "index": 1, "total": 1,
        "status": DS.DOWNLOADING, "message": "准备下载...",
        "speed": "", "downloaded": "", "total_size": "",
    })

    def _put(data: dict):
        loop.call_soon_threadsafe(progress_queue.put_nowait, data)

    if provider == Provider.REMBG:
        logger.info(f"[单模型下载] 使用 rembg 下载 ONNX 权重: {name}")
        future = loop.run_in_executor(None, _download_rembg, cfg, _put)
    elif cfg.get("local_path") and cfg.get("download_url") and not cfg.get("hf_model_id") and not cfg.get("hf_models"):
        logger.info(f"[单模型下载] 直接下载权重文件: {name} → {cfg['local_path']}")
        future = loop.run_in_executor(None, _download_direct, cfg, _put)
    elif cfg.get("hf_models"):
        logger.info(f"[单模型下载] 从 HuggingFace 下载组合模型: {name} ({len(cfg['hf_models'])} 个子模型)")
        future = loop.run_in_executor(None, _download_hf_multi, cfg, _put)
    elif cfg.get("hf_model_id"):
        logger.info(f"[单模型下载] 从 HuggingFace 下载: {name} (repo: {cfg['hf_model_id']})")
        future = loop.run_in_executor(None, _download_hf, cfg, _put)
    else:
        logger.warning(f"[单模型下载] 无下载来源，跳过: {name} (id={model_id}, provider={provider})")
        yield _sse("model", {
            "id": model_id, "name": name, "index": 1, "total": 1,
            "status": DS.SKIPPED, "message": "无下载来源，跳过"
        })
        yield _sse("finish", {"ok": 0, "skipped": 1, "failed": 0, "message": "该模型无可用下载来源"})
        return

    try:
        while not future.done():
            if await request.is_disconnected():
                future.cancel()
                return
            await asyncio.sleep(0.2)
            # 消费当前 queue 中所有待处理的进度消息
            while not progress_queue.empty():
                item = progress_queue.get_nowait()
                if isinstance(item, dict):
                    yield _sse("model", {
                        "id": model_id, "name": name, "index": 1, "total": 1,
                        "status": DS.DOWNLOADING,
                        **item,
                    })

        # 清空剩余队列
        while not progress_queue.empty():
            item = progress_queue.get_nowait()
            if isinstance(item, dict):
                yield _sse("model", {
                    "id": model_id, "name": name, "index": 1, "total": 1,
                    "status": DS.DOWNLOADING,
                    **item,
                })

        exc = future.exception()
        if exc:
            raise exc

        yield _sse("model", {
            "id": model_id, "name": name, "index": 1, "total": 1,
            "status": DS.DONE, "message": "下载完成",
            "speed": "", "downloaded": "", "total_size": "",
        })
        logger.info(f"[单模型下载] 完成：{name}")
        yield _sse("finish", {"ok": 1, "skipped": 0, "failed": 0, "message": f"{name} 下载完成"})

    except Exception as e:
        logger.error(f"[单模型下载] 失败：{name} — {e}", exc_info=True)
        yield _sse("model", {
            "id": model_id, "name": name, "index": 1, "total": 1,
            "status": DS.ERROR, "message": f"下载失败: {str(e)[:200]}",
            "speed": "", "downloaded": "", "total_size": "",
        })
        yield _sse("finish", {"ok": 0, "skipped": 0, "failed": 1, "message": f"下载失败: {str(e)[:100]}"})


@router.get("/download/{model_id}")
async def model_download_single(model_id: str, request: Request):
    """
    [已废弃] SSE 流：下载单个指定模型。
    新代码请使用 POST /api/models/download/{model_id} + GET /api/models/subscribe/{model_id}。

    客户端通过 EventSource 连接此接口，接收实时进度推送。
    事件类型：start / model / finish
    """
    return StreamingResponse(
        _download_single_generator(request, model_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.delete("/{model_id}/files")
async def delete_model_files(model_id: str):
    """
    删除指定模型的本地权重文件（不修改 models.yaml）。

    - rembg   → 删除 ~/.u2net/<rembg_model_name>.onnx
    - 本地文件 → 删除 PROJECT_ROOT/<local_path>
    - HF 缓存 → 删除 huggingface cache 中对应的 repo 目录
    - IOPaint cli 内置模型 → 无文件可删，直接返回提示
    """
    from core.model_registry import MODEL_BY_ID
    from app.config import PROJECT_ROOT, MODELS_DIR
    import shutil

    cfg = MODEL_BY_ID.get(model_id)
    if not cfg:
        raise HTTPException(status_code=404, detail=f"未知模型 ID: {model_id!r}")

    provider = cfg.get("provider", "")

    # IOPaint 内置模型（cli 模式）：无独立文件
    if provider == "IOPaint" and cfg.get("iopaint_mode") == "cli":
        return {"ok": True, "message": "内置模型无需删除文件（随 iopaint 包安装）"}

    deleted: list[str] = []
    not_found: list[str] = []

    # ── rembg 模型 ──────────────────────────────────────────────────────────
    if provider == "rembg":
        u2net_home = Path(os.environ.get("U2NET_HOME", Path.home() / ".u2net"))
        rembg_name = cfg.get("rembg_model_name", model_id)

        # 可能是子目录形式（如 briaai/RMBG-2.0）
        onnx_path = u2net_home / f"{rembg_name}.onnx"
        onnx_dir = u2net_home / rembg_name  # 子目录形式

        if onnx_path.exists():
            onnx_path.unlink()
            deleted.append(str(onnx_path))
        elif onnx_dir.exists() and onnx_dir.is_dir():
            shutil.rmtree(onnx_dir)
            deleted.append(str(onnx_dir))
        else:
            not_found.append(str(onnx_path))

    # ── 本地路径文件（realesrgan/facexlib 等）──────────────────────────────
    if cfg.get("local_path"):
        local = Path(PROJECT_ROOT) / cfg["local_path"]
        # 安全检查：只允许删 models/ 目录下的文件
        try:
            local.resolve().relative_to(Path(PROJECT_ROOT).resolve() / "models")
        except ValueError:
            raise HTTPException(status_code=400, detail=f"路径安全检查失败，拒绝删除: {local}")

        if local.exists():
            local.unlink()
            deleted.append(str(local))
        else:
            not_found.append(str(local))

    # ── HuggingFace 缓存（diffusers / IOPaint server / HiImage 等）──────────
    hf_repo_ids: list[str] = []
    if cfg.get("hf_models"):
        # 组合模型：收集所有子模型的 repo_id
        hf_repo_ids = [sub["id"] for sub in cfg["hf_models"]]
    elif cfg.get("hf_model_id") and not cfg.get("local_path"):
        hf_repo_ids = [cfg["hf_model_id"]]

    for repo_id in hf_repo_ids:
        hf_cache_dir = Path(MODELS_DIR) / "huggingface"
        manual_dir = hf_cache_dir / "manual" / repo_id.replace("/", "--")

        # 1. 优先删手动下载目录（_download_hf 使用的路径）
        if manual_dir.exists():
            shutil.rmtree(manual_dir)
            deleted.append(str(manual_dir))
        else:
            # 2. 尝试通过 huggingface_hub scan_cache_dir 找标准缓存目录
            try:
                from huggingface_hub import scan_cache_dir as hf_scan
                hub_dir = hf_cache_dir / "hub"
                if hub_dir.exists():
                    cache_info = hf_scan(hub_dir)
                    for repo in cache_info.repos:
                        if repo.repo_id == repo_id:
                            shutil.rmtree(repo.repo_path)
                            deleted.append(str(repo.repo_path))
                            break
                    else:
                        not_found.append(f"HF cache: {repo_id}")
                else:
                    not_found.append(f"HF cache dir 不存在: {hub_dir}")
            except Exception as e:
                logger.error(f"[删除模型] 扫描 HF 缓存失败：{cfg.get('name', model_id)} ({repo_id}) — {e}", exc_info=True)
                raise HTTPException(status_code=500, detail=f"扫描 HF 缓存失败: {e}")

    if not deleted and not_found:
        logger.warning(f"[删除模型] 未找到可删除的文件：{model_id}")
        return {
            "ok": False,
            "message": f"未找到可删除的文件",
            "not_found": not_found,
        }

    logger.info(f"[删除模型] {cfg.get('name', model_id)}：已删除 {len(deleted)} 个文件/目录")
    return {
        "ok": True,
        "message": f"已删除 {len(deleted)} 个文件/目录",
        "deleted": deleted,
        "not_found": not_found,
    }
