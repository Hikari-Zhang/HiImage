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
  results = checker.check_mode("cutout")      # 检测某个功能模式下的所有模型
"""
from __future__ import annotations

import time
from threading import Lock
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

from core import paths
from core.hf_weights import check_file, check_snapshot

# ── TTL 缓存配置 ────────────────────────────────────────────────────────────
# 缓存有效期（秒）：平衡实时性与性能，5 秒足够覆盖一次 UI 刷新的周期
MODEL_CHECK_CACHE_TTL = 5.0

# 模块级缓存：{model_id: (ModelCheckResult, timestamp)}
_model_check_cache: dict[str, tuple] = {}
_cache_lock = Lock()
_cache_generation = 0


def invalidate_model_cache(model_id: str | None = None) -> None:
    """
    主动失效模型检查缓存。

    :param model_id: 指定模型 ID 则只失效该模型；None 时清空全部缓存。
                    下载完成后应调用此方法刷新状态。
    """
    global _cache_generation
    with _cache_lock:
        _cache_generation += 1
        if model_id is None:
            _model_check_cache.clear()
        else:
            _model_check_cache.pop(model_id, None)


def resolve_hf_model_path(repo_id: str) -> str:
    return paths.resolve_hf_model_path(repo_id)

# 延迟导入：model_registry 在模块级加载 models.yaml，
# 这里同样在模块级引入（一次性，无性能问题）。
from core.model_registry import MODELS, MODEL_BY_ID
from core.constants import ModelStatus as MS, Provider

# 状态字面量（保留向后兼容的类型别名）
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
    """检查当前推理路径上的模型权重及其必需文件。"""

    def check_model(self, model_id: str, use_cache: bool = True) -> ModelCheckResult:
        now = time.monotonic()
        with _cache_lock:
            generation = _cache_generation
            cached = _model_check_cache.get(model_id)
            if use_cache and cached and now - cached[1] < MODEL_CHECK_CACHE_TTL:
                return cached[0]

        cfg = MODEL_BY_ID.get(model_id)
        if not cfg:
            result = ModelCheckResult(
                model_id=model_id,
                name=model_id,
                provider=MS.UNKNOWN,
                status=MS.UNKNOWN,
                message=f"模型 ID '{model_id}' 不在注册表中",
            )
            return result

        result = self.check_config(cfg)
        with _cache_lock:
            current = _model_check_cache.get(model_id)
            if generation == _cache_generation and (current is None or now >= current[1]):
                _model_check_cache[model_id] = (result, now)
        return result

    def check_config(self, cfg: dict) -> ModelCheckResult:
        """直接检测一份配置，供下载队列检查提交时保存的配置快照。"""
        provider = cfg.get("provider", "")
        model_id = cfg["id"]
        for repo in cfg.get("dependencies", []):
            if paths.find_hf_snapshot(repo) is None:
                return ModelCheckResult(
                    model_id, cfg.get("name", model_id), provider, MS.PARTIAL, f"缺少模型依赖: {repo}",
                )

        if provider == Provider.REMBG:
            result = self._check_rembg(cfg)
        elif cfg.get("local_path"):
            result = self._check_local_path(cfg)
        elif cfg.get("hf_models"):
            result = self._check_hf_multi(cfg)
        elif cfg.get("hf_model_id"):
            result = self._check_hf_cache(cfg)
        else:
            result = ModelCheckResult(
                model_id=model_id,
                name=cfg.get("name", model_id),
                provider=provider,
                status=MS.UNKNOWN,
                message="无法确定模型存储路径（缺少 hf_model_id / hf_models 和 local_path）",
            )

        return result

    def check_all(self) -> list[ModelCheckResult]:
        """检测全部注册模型，按 models.yaml 中的顺序返回。"""
        return [self.check_model(m["id"]) for m in MODELS]

    def check_mode(self, mode_id: str) -> list[ModelCheckResult]:
        """检测某个功能模式下的所有模型。"""
        from core.model_registry import get_models_for_mode
        return [self.check_model(m["id"]) for m in get_models_for_mode(mode_id)]

    # ── 内部检测方法 ─────────────────────────────────────────────────────────

    def _check_rembg(self, cfg: dict) -> ModelCheckResult:
        return self._check_file(cfg, paths.rembg_model_path(cfg))

    def _check_local_path(self, cfg: dict) -> ModelCheckResult:
        return self._check_file(cfg, paths.find_local_model_path(cfg))

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
                "hf_required_files": sub.get("hf_required_files"),
            }
            sub_results.append(self._check_hf_cache(sub_cfg))

        # ── 汇总状态 ─────────────────────────────────────────────────────────
        STATUS_PRIORITY = {MS.MISSING: 4, MS.CORRUPTED: 3, MS.PARTIAL: 2, MS.UNKNOWN: 1, MS.OK: 0}
        worst = max(sub_results, key=lambda r: STATUS_PRIORITY.get(r.status, 0))

        total_bytes = sum(r.size_bytes for r in sub_results if r.size_bytes)

        if worst.status == MS.OK:
            ok_count = len(sub_results)
            size_info = f"{total_bytes // (1024 * 1024)} MB" if total_bytes else ""
            message = f"{ok_count} 个子模型全部就绪" + (f", {size_info}" if size_info else "")
        else:
            # 找出所有有问题的子模型
            bad = [r for r in sub_results if r.status != "ok"]
            bad_names = ", ".join(r.name for r in bad)
            message = f"{worst.status}: {bad_names} — {worst.message}"

        return ModelCheckResult(
            model_id=model_id,
            name=name,
            provider=provider,
            status=worst.status,
            message=message,
            file_path=next((r.file_path for r in sub_results if r.file_path), None),
            size_bytes=total_bytes or None,
            expected_size_mb=total_size_mb,
        )

    def _check_hf_cache(self, cfg: dict) -> ModelCheckResult:
        repo_id = cfg["hf_model_id"]
        directory = None
        size = None
        try:
            directory = paths.find_hf_snapshot(repo_id)
            if directory is None:
                status, message = MS.MISSING, f"未找到本地模型快照: {repo_id}"
            else:
                size = check_snapshot(directory, cfg)
                status, message = MS.OK, f"必需文件齐全，权重 {size / (1024 * 1024):.0f} MB"
        except FileNotFoundError as exc:
            status, message = MS.PARTIAL, f"缺少模型文件: {exc.filename or exc}"
        except ImportError:
            status, message = MS.UNKNOWN, "未安装 huggingface_hub"
        except (OSError, ValueError) as exc:
            status, message = MS.CORRUPTED, f"模型文件异常: {exc}"
        return ModelCheckResult(
            model_id=cfg["id"], name=cfg.get("name", repo_id), provider=cfg.get("provider", ""),
            status=status, message=message, file_path=str(directory) if directory is not None else None,
            size_bytes=size, expected_size_mb=cfg.get("size_mb"),
        )

    def _check_file(self, cfg: dict, path: Path) -> ModelCheckResult:
        model_id = cfg["id"]
        name = cfg.get("name", model_id)
        provider = cfg.get("provider", "")
        size_mb = cfg.get("size_mb")
        size_bytes = None
        try:
            if not path.is_file():
                raise FileNotFoundError(path)
            size_bytes = check_file(path)
            if size_mb and size_bytes < int(size_mb * 0.5 * 1024 * 1024):
                raise ValueError(f"权重文件过小: {size_bytes / (1024 * 1024):.1f} MB")
        except FileNotFoundError:
            return ModelCheckResult(model_id, name, provider, MS.MISSING, cfg.get("setup_hint", f"文件不存在: {path}"),
                                    expected_size_mb=size_mb)
        except (OSError, ValueError) as error:
            status, message = MS.CORRUPTED, str(error)
        else:
            status, message = MS.OK, f"{size_bytes / (1024 * 1024):.0f} MB"
        return ModelCheckResult(model_id, name, provider, status, message, str(path), size_bytes, size_mb)
