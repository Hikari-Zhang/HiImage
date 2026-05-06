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
        self.project_root = project_root or Path(__file__).parent.parent
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
        elif cfg.get("hf_model_id"):
            return self._check_hf_cache(cfg)
        else:
            return ModelCheckResult(
                model_id=model_id,
                name=cfg.get("name", model_id),
                provider=provider,
                status="unknown",
                message="无法确定模型存储路径（缺少 hf_model_id 和 local_path）",
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
        rembg 模型：检查 ~/.u2net/<rembg_model_name>.onnx 是否存在且大小合理。

        rembg_model_name 示例：
          birefnet      → birefnet-general
          u2net         → u2net
          modnet        → modnet_portrait_matting
          isnet         → isnet-general-use
          isnet_anime   → isnet-anime
          rmbg          → briaai/RMBG-2.0   （带子目录）
        """
        u2net_home = Path(os.environ.get("U2NET_HOME", Path.home() / ".u2net"))
        rembg_model_name = cfg.get("rembg_model_name", cfg["id"])

        # 处理带斜杠的模型名（如 briaai/RMBG-2.0 → 子目录）
        onnx_path = u2net_home / f"{rembg_model_name}.onnx"
        return self._check_file(cfg, onnx_path)

    def _check_local_path(self, cfg: dict) -> ModelCheckResult:
        """
        直接路径模型（GFPGAN、Real-ESRGAN 等）：
        local_path 为相对于 PROJECT_ROOT 的路径。
        """
        path = self.project_root / cfg["local_path"]
        return self._check_file(cfg, path)

    def _check_hf_cache(self, cfg: dict) -> ModelCheckResult:
        """
        HuggingFace Hub 缓存模型：使用 scan_cache_dir() 判断是否完整下载。

        成功标准：
          - repo 存在于缓存目录
          - nb_files > 0（有实际权重文件）
          - scan_cache_dir() 未报告该 repo 的结构性损坏警告
        """
        try:
            from huggingface_hub import scan_cache_dir
        except ImportError:
            return ModelCheckResult(
                model_id=cfg["id"],
                name=cfg.get("name", ""),
                provider=cfg.get("provider", ""),
                status="unknown",
                message="huggingface_hub 未安装，无法检测 HF 缓存",
            )

        repo_id = cfg["hf_model_id"]

        # 若缓存目录不存在，直接返回 missing
        if not self.hf_cache_dir.exists():
            return ModelCheckResult(
                model_id=cfg["id"],
                name=cfg.get("name", ""),
                provider=cfg.get("provider", ""),
                status="missing",
                message=f"HF 缓存目录不存在: {self.hf_cache_dir}",
                expected_size_mb=cfg.get("size_mb"),
            )

        try:
            cache_info = scan_cache_dir(self.hf_cache_dir)
        except Exception as e:
            return ModelCheckResult(
                model_id=cfg["id"],
                name=cfg.get("name", ""),
                provider=cfg.get("provider", ""),
                status="unknown",
                message=f"无法扫描 HF 缓存: {e}",
            )

        # 从扫描结果中查找目标 repo
        for repo in cache_info.repos:
            if repo.repo_id == repo_id:
                size_mb = repo.size_on_disk // (1024 * 1024)

                if repo.nb_files == 0:
                    return ModelCheckResult(
                        model_id=cfg["id"],
                        name=cfg.get("name", ""),
                        provider=cfg.get("provider", ""),
                        status="partial",
                        message="缓存目录存在但无有效权重文件（可能下载中断）",
                        file_path=str(repo.repo_path),
                        size_bytes=repo.size_on_disk,
                        expected_size_mb=cfg.get("size_mb"),
                    )

                # 检查全局损坏警告（scan_cache_dir 会标记损坏的 repo）
                repo_has_warning = any(
                    str(repo.repo_path) in str(w)
                    for w in cache_info.warnings
                )
                if repo_has_warning:
                    return ModelCheckResult(
                        model_id=cfg["id"],
                        name=cfg.get("name", ""),
                        provider=cfg.get("provider", ""),
                        status="corrupted",
                        message="HF 缓存目录存在结构性问题（文件可能损坏）",
                        file_path=str(repo.repo_path),
                        size_bytes=repo.size_on_disk,
                        expected_size_mb=cfg.get("size_mb"),
                    )

                return ModelCheckResult(
                    model_id=cfg["id"],
                    name=cfg.get("name", ""),
                    provider=cfg.get("provider", ""),
                    status="ok",
                    message=f"{repo.nb_files} 个文件, {size_mb} MB",
                    file_path=str(repo.repo_path),
                    size_bytes=repo.size_on_disk,
                    expected_size_mb=cfg.get("size_mb"),
                )

        # 未在缓存中找到该 repo
        return ModelCheckResult(
            model_id=cfg["id"],
            name=cfg.get("name", ""),
            provider=cfg.get("provider", ""),
            status="missing",
            message=f"未在 HF 缓存中找到 {repo_id}（路径: {self.hf_cache_dir}）",
            expected_size_mb=cfg.get("size_mb"),
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

        # safetensors 文件头验证
        if path.suffix == ".safetensors":
            try:
                from safetensors import safe_open  # type: ignore
                with safe_open(str(path), framework="pt", device="cpu") as f:
                    _ = list(f.keys())  # 触发 header 解析（不加载 tensor 数据）
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
