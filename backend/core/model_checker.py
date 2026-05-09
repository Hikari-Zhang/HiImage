"""
模型完整性检测器

检测层级（按速度从快到慢）：
  Level 0: 文件/缓存目录是否存在（< 1ms）
  Level 1: 文件大小是否合理（< 1ms，基于 size_mb 的 ±50% 范围，排除截断下载）
  Level 2: .safetensors 文件头验证（< 10ms，不加载权重数据）

用法：
  from core.model_checker import ModelChecker

  checker = ModelChecker()
  result  = checker.check_model("birefnet")   # 检测单个模型
  results = checker.check_all()               # 检测全部模型
  results = checker.check_mode("outfit_swap") # 检测某个功能模式下的所有模型
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional

# 延迟导入：model_registry 在模块级加载 models.yaml，
# 这里同样在模块级引入（一次性，无性能问题）。
from core.model_registry import MODELS, MODEL_BY_ID

# 状态字面量
ModelStatus = Literal["ok", "missing", "partial", "corrupted", "unknown"]


@dataclass
class ModelCheckResult:
    """单个模型的检测结果。"""
    model_id: str
    name: str
    provider: str
    status: ModelStatus
    message: str
    file_path: Optional[str] = None
    size_bytes: Optional[int] = None
    expected_size_mb: Optional[float] = None


class ModelChecker:
    """
    检测所有注册模型的文件完整性。

    三类存储路径的处理策略：
      1. rembg 模型     → ~/.u2net/<rembg_model_name>.onnx
      2. 本地直接路径    → <PROJECT_ROOT>/<local_path>（GFPGAN、Real-ESRGAN 等）
      3. HuggingFace 缓存 → 通过 scan_cache_dir() 判断是否完整下载
    """

    def __init__(self, project_root: Optional[Path] = None):
        # 默认为 backend/ 目录的父级（即 PROJECT_ROOT）
        self.project_root = project_root or Path(__file__).parent.parent.parent
        self.models_dir = self.project_root / "models"

        # HF_HOME 由 app/main.py 在启动时设置；此处读取环境变量，回退到默认值
        hf_home = os.environ.get("HF_HOME") or str(self.models_dir / "huggingface")
        self.hf_cache_dir = Path(hf_home) / "hub"

    # ── 公共 API ─────────────────────────────────────────────────────────────

    def check_model(self, model_id: str) -> ModelCheckResult:
        """检测单个模型，返回结果（不抛异常）。"""
        cfg = MODEL_BY_ID.get(model_id)
        if not cfg:
            return ModelCheckResult(
                model_id=model_id,
                name=model_id,
                provider="unknown",
                status="unknown",
                message=f"模型 ID '{model_id}' 不在注册表中",
            )

        provider = cfg.get("provider", "")

        if provider == "rembg":
            return self._check_rembg(cfg)
        elif provider == "IOPaint" and cfg.get("iopaint_mode") == "cli":
            # IOPaint 内置的快速模型（lama/migan/zits 等）：随 iopaint 包一起安装，
            # 无独立权重文件，无需下载，直接标记为 ok/builtin
            return ModelCheckResult(
                model_id=model_id,
                name=cfg.get("name", model_id),
                provider=provider,
                status="ok",
                message="内置模型（随 iopaint 包安装，无需单独下载）",
            )
        elif cfg.get("local_path"):
            return self._check_local_path(cfg)
        elif cfg.get("hf_models"):
            return self._check_hf_multi(cfg)
        elif cfg.get("hf_model_id"):
            return self._check_hf_cache(cfg)
        else:
            return ModelCheckResult(
                model_id=model_id,
                name=cfg.get("name", model_id),
                provider=provider,
                status="unknown",
                message="无法确定模型存储路径（缺少 hf_model_id / hf_models 和 local_path）",
            )

    def check_all(self) -> list[ModelCheckResult]:
        """检测全部注册模型，按 models.yaml 中的顺序返回。"""
        return [self.check_model(m["id"]) for m in MODELS]

    def check_mode(self, mode_id: str) -> list[ModelCheckResult]:
        """检测某个功能模式下的所有模型。"""
        from core.model_registry import get_models_for_mode
        return [self.check_model(m["id"]) for m in get_models_for_mode(mode_id)]

    # ── 内部检测方法 ─────────────────────────────────────────────────────────

    def _check_rembg(self, cfg: dict) -> ModelCheckResult:
        """
        rembg 模型：检查 ~/.u2net/<onnx_filename> 是否存在且大小合理。

        onnx_filename 示例：
          birefnet      → BiRefNet-general-epoch_244.onnx
          u2net         → u2net.onnx
          modnet        → u2net_human_seg.onnx
          isnet         → isnet-general-use.onnx
          rmbg          → bria-rmbg-2.0.onnx
        """
        u2net_home = Path(os.environ.get("U2NET_HOME", Path.home() / ".u2net"))
        onnx_filename = cfg.get("onnx_filename")
        if not onnx_filename:
            # 向后兼容旧字段
            session_name = cfg.get("rembg_session_name", cfg.get("rembg_model_name", cfg["id"]))
            onnx_filename = f"{session_name}.onnx"

        onnx_path = u2net_home / onnx_filename
        return self._check_file(cfg, onnx_path)

    def _check_local_path(self, cfg: dict) -> ModelCheckResult:
        """
        直接路径模型（GFPGAN、Real-ESRGAN 等）：
        local_path 为相对于 PROJECT_ROOT 的路径。
        """
        path = self.project_root / cfg["local_path"]
        return self._check_file(cfg, path)

    def _check_hf_multi(self, cfg: dict) -> ModelCheckResult:
        """
        组合多子模型完整性检测（hf_models 列表）。

        逐个检测每个子模型，汇总规则：
          - 任意子模型 missing  → 整体 missing
          - 任意子模型 corrupted → 整体 corrupted
          - 任意子模型 partial  → 整体 partial
          - 全部 ok             → 整体 ok
          - 其余               → unknown
        """
        hf_models: list[dict] = cfg.get("hf_models", [])
        model_id = cfg["id"]
        name = cfg.get("name", model_id)
        provider = cfg.get("provider", "")
        total_size_mb = cfg.get("size_mb")

        sub_results: list[ModelCheckResult] = []
        for sub in hf_models:
            sub_cfg = {
                "id":        model_id,   # 报错时仍引用父 ID
                "name":      sub.get("name", sub["id"]),
                "provider":  provider,
                "hf_model_id": sub["id"],
                "size_mb":   sub.get("size_mb"),
            }
            sub_results.append(self._check_hf_cache(sub_cfg))

        # ── 汇总状态 ─────────────────────────────────────────────────────────
        STATUS_PRIORITY = {"missing": 4, "corrupted": 3, "partial": 2, "unknown": 1, "ok": 0}
        worst = max(sub_results, key=lambda r: STATUS_PRIORITY.get(r.status, 0))

        total_bytes = sum(r.size_bytes for r in sub_results if r.size_bytes)

        if worst.status == "ok":
            ok_count = len(sub_results)
            size_info = f"{total_bytes // (1024 * 1024)} MB" if total_bytes else ""
            message = f"{ok_count} 个子模型全部就绪" + (f", {size_info}" if size_info else "")
        else:
            # 找出所有有问题的子模型
            bad = [r for r in sub_results if r.status != "ok"]
            bad_names = ", ".join(r.name for r in bad)
            message = f"{worst.status}: {bad_names} — {bad[0].message}"

        return ModelCheckResult(
            model_id=model_id,
            name=name,
            provider=provider,
            status=worst.status,
            message=message,
            size_bytes=total_bytes or None,
            expected_size_mb=total_size_mb,
        )

    def _check_hf_cache(self, cfg: dict) -> ModelCheckResult:
        """
        HuggingFace Hub 缓存模型。

        检测顺序：
          1. 优先检查 manual/ 路径（_download_hf 写入位置）
          2. 再 fallback 到标准 hub/ scan_cache_dir 扫描
        """
        repo_id = cfg["hf_model_id"]
        name = cfg.get("name", "")
        provider = cfg.get("provider", "")
        size_mb = cfg.get("size_mb")

        # ── 1. 优先检查 manual/ 路径 ──────────────────────────────────────────
        manual_dir = self.hf_cache_dir.parent / "manual" / repo_id.replace("/", "--")
        if manual_dir.exists() and manual_dir.is_dir():
            # 只统计权重文件，忽略 README/json/tokenizer 等配置文件
            WEIGHT_SUFFIXES = {".safetensors", ".bin", ".onnx", ".pth", ".pt", ".ckpt"}
            weight_files = [
                f for f in manual_dir.rglob("*")
                if f.is_file() and f.suffix.lower() in WEIGHT_SUFFIXES
            ]
            all_files = [f for f in manual_dir.rglob("*") if f.is_file()]

            if not weight_files:
                # 目录存在但无权重文件（可能只有 README 或下载中断）
                return ModelCheckResult(
                    model_id=cfg["id"],
                    name=name,
                    provider=provider,
                    status="partial" if all_files else "missing",
                    message="缓存目录存在但无权重文件（下载可能中断）",
                    file_path=str(manual_dir),
                    expected_size_mb=size_mb,
                )

            total_bytes = sum(f.stat().st_size for f in all_files)

            # 大小合理性检查
            if size_mb:
                expected_min_bytes = int(size_mb * 0.5 * 1024 * 1024)
                if total_bytes < expected_min_bytes:
                    actual_mb = total_bytes / (1024 * 1024)
                    return ModelCheckResult(
                        model_id=cfg["id"],
                        name=name,
                        provider=provider,
                        status="corrupted",
                        message=(
                            f"文件过小: {actual_mb:.1f} MB（期望 ~{size_mb} MB），"
                            "可能下载不完整"
                        ),
                        file_path=str(manual_dir),
                        size_bytes=total_bytes,
                        expected_size_mb=size_mb,
                    )

            # .safetensors 文件头验证（只读前 8 字节 header length，避免阻塞大文件）
            for sf in weight_files:
                if sf.suffix.lower() == ".safetensors":
                    try:
                        with open(str(sf), "rb") as _f:
                            header_len_bytes = _f.read(8)
                        if len(header_len_bytes) < 8:
                            raise ValueError("文件过短，无法读取 header length")
                        import struct
                        header_len = struct.unpack("<Q", header_len_bytes)[0]
                        # header length 合理性检查：不应超过 100MB
                        if header_len == 0 or header_len > 100 * 1024 * 1024:
                            raise ValueError(f"header length 异常: {header_len}")
                    except Exception as e:
                        return ModelCheckResult(
                            model_id=cfg["id"],
                            name=name,
                            provider=provider,
                            status="corrupted",
                            message=f"safetensors 文件头损坏 ({sf.name}): {e}",
                            file_path=str(sf),
                            size_bytes=total_bytes,
                            expected_size_mb=size_mb,
                        )

            return ModelCheckResult(
                model_id=cfg["id"],
                name=name,
                provider=provider,
                status="ok",
                message=f"{len(weight_files)} 个权重文件, {total_bytes // (1024 * 1024)} MB",
                file_path=str(manual_dir),
                size_bytes=total_bytes,
                expected_size_mb=size_mb,
            )

        # ── 2. Fallback：标准 HF hub/ 缓存扫描 ───────────────────────────────
        try:
            from huggingface_hub import scan_cache_dir
        except ImportError:
            return ModelCheckResult(
                model_id=cfg["id"],
                name=name,
                provider=provider,
                status="unknown",
                message="huggingface_hub 未安装，无法检测 HF 缓存",
            )

        if not self.hf_cache_dir.exists():
            return ModelCheckResult(
                model_id=cfg["id"],
                name=name,
                provider=provider,
                status="missing",
                message=f"HF 缓存目录不存在: {self.hf_cache_dir}",
                expected_size_mb=size_mb,
            )

        try:
            cache_info = scan_cache_dir(self.hf_cache_dir)
        except Exception as e:
            return ModelCheckResult(
                model_id=cfg["id"],
                name=name,
                provider=provider,
                status="unknown",
                message=f"无法扫描 HF 缓存: {e}",
            )

        for repo in cache_info.repos:
            if repo.repo_id == repo_id:
                repo_size_mb = repo.size_on_disk // (1024 * 1024)

                if repo.nb_files == 0:
                    return ModelCheckResult(
                        model_id=cfg["id"],
                        name=name,
                        provider=provider,
                        status="partial",
                        message="缓存目录存在但无有效权重文件（可能下载中断）",
                        file_path=str(repo.repo_path),
                        size_bytes=repo.size_on_disk,
                        expected_size_mb=size_mb,
                    )

                repo_has_warning = any(
                    str(repo.repo_path) in str(w)
                    for w in cache_info.warnings
                )
                if repo_has_warning:
                    return ModelCheckResult(
                        model_id=cfg["id"],
                        name=name,
                        provider=provider,
                        status="corrupted",
                        message="HF 缓存目录存在结构性问题（文件可能损坏）",
                        file_path=str(repo.repo_path),
                        size_bytes=repo.size_on_disk,
                        expected_size_mb=size_mb,
                    )

                return ModelCheckResult(
                    model_id=cfg["id"],
                    name=name,
                    provider=provider,
                    status="ok",
                    message=f"{repo.nb_files} 个文件, {repo_size_mb} MB",
                    file_path=str(repo.repo_path),
                    size_bytes=repo.size_on_disk,
                    expected_size_mb=size_mb,
                )

        return ModelCheckResult(
            model_id=cfg["id"],
            name=name,
            provider=provider,
            status="missing",
            message=f"未在 HF 缓存中找到 {repo_id}（manual: {manual_dir}, hub: {self.hf_cache_dir}）",
            expected_size_mb=size_mb,
        )

    def _check_file(self, cfg: dict, path: Path) -> ModelCheckResult:
        """
        通用文件检测：
          1. 存在性
          2. 大小合理性（允许 ±50% 误差，主要排除明显截断的下载）
          3. .safetensors 文件头验证（不读取权重数据）
        """
        name = cfg.get("name", cfg["id"])
        provider = cfg.get("provider", "")
        size_mb = cfg.get("size_mb")

        if not path.exists():
            return ModelCheckResult(
                model_id=cfg["id"],
                name=name,
                provider=provider,
                status="missing",
                message=f"文件不存在: {path}",
                expected_size_mb=size_mb,
            )

        size_bytes = path.stat().st_size

        # 大小合理性检查（仅对有参考大小的模型）
        if size_mb:
            expected_min_bytes = int(size_mb * 0.5 * 1024 * 1024)
            if size_bytes < expected_min_bytes:
                actual_mb = size_bytes / (1024 * 1024)
                return ModelCheckResult(
                    model_id=cfg["id"],
                    name=name,
                    provider=provider,
                    status="corrupted",
                    message=(
                        f"文件过小: {actual_mb:.1f} MB（期望 ~{size_mb} MB）"
                        "，可能下载不完整"
                    ),
                    file_path=str(path),
                    size_bytes=size_bytes,
                    expected_size_mb=size_mb,
                )

        # safetensors 文件头验证（只读前 8 字节，避免阻塞）
        if path.suffix == ".safetensors":
            try:
                with open(str(path), "rb") as _f:
                    header_len_bytes = _f.read(8)
                if len(header_len_bytes) < 8:
                    raise ValueError("文件过短，无法读取 header length")
                import struct
                header_len = struct.unpack("<Q", header_len_bytes)[0]
                if header_len == 0 or header_len > 100 * 1024 * 1024:
                    raise ValueError(f"header length 异常: {header_len}")
            except Exception as e:
                return ModelCheckResult(
                    model_id=cfg["id"],
                    name=name,
                    provider=provider,
                    status="corrupted",
                    message=f"safetensors 文件头损坏: {e}",
                    file_path=str(path),
                    size_bytes=size_bytes,
                    expected_size_mb=size_mb,
                )

        actual_mb = size_bytes / (1024 * 1024)
        return ModelCheckResult(
            model_id=cfg["id"],
            name=name,
            provider=provider,
            status="ok",
            message=f"{actual_mb:.0f} MB",
            file_path=str(path),
            size_bytes=size_bytes,
            expected_size_mb=size_mb,
        )
