"""模型文件的删除计划、共享引用检查和受限目录删除。"""
import os
from pathlib import Path
import shutil

from core import paths


def _repositories(cfg):
    repos = {sub["id"] for sub in cfg.get("hf_models", [])}
    if cfg.get("hf_model_id") and not cfg.get("local_path"):
        repos.add(cfg["hf_model_id"])
    return repos


def _repo_targets(repo):
    # 注册表是服务端配置，也必须防止错误配置越出缓存目录。
    if any(part in ("", ".", "..") for part in repo.split("/")) or "\\" in repo:
        raise ValueError(f"无效模型仓库: {repo}")
    name = repo.replace("/", "--")
    targets = []
    for hub, manual in paths.hf_cache_locations():
        targets.append((hub / f"models--{name}", hub))
        if manual is not None:
            targets.append((manual / name, manual))
    return list(dict.fromkeys(targets))


def _model_resources(cfg):
    resources = {}
    if cfg.get("provider") == "rembg":
        targets = paths.rembg_locations(cfg)
        resources["onnx"] = (targets[0][0].name, targets)
    if cfg.get("local_path"):
        locations = paths.local_model_locations(cfg)
        local = locations[0]
        roots = [paths.CACHE_ROOT, paths.MODELS_DIR, paths.PROJECT_ROOT / "models"]
        provider_root = paths.model_cache_root(cfg)
        if provider_root is not None:
            roots.append(provider_root)
        roots.extend(paths.model_storage_dirs())
        targets = []
        for location in locations:
            resolved = location.resolve()
            root = next((r for r in roots if resolved.is_relative_to(r.resolve())
                         and resolved != r.resolve()), None)
            if root is None:
                raise ValueError(f"路径安全检查失败，拒绝删除: {location}")
            targets.append((location, root))
        legacy = paths.legacy_model_cache_path(cfg)
        if legacy and legacy[0] != local:
            targets.append(legacy)
        resources["local"] = (local.name, targets)
    for repo in sorted(_repositories(cfg)):
        resources[f"hf:{repo}"] = (repo, _repo_targets(repo))
    return resources


def deletion_resources(cfg):
    resources = _model_resources(cfg)
    for key, (label, targets) in resources.items():
        # 用户可能将同一权重保存在不同磁盘。删除当前库的副本，保留后续库。
        for root in paths.model_storage_dirs():
            selected = [target for target in targets if target[0].is_relative_to(root) and os.path.lexists(target[0])]
            if selected:
                resources[key] = (label, selected)
                break
    return resources


def deletion_targets(cfg):
    return [target for _, targets in deletion_resources(cfg).values() for target in targets]


def model_resource_paths(cfg):
    """模型可能读写的缓存路径，包括组合模型和依赖仓库。"""
    result = [p.resolve() for _, targets in _model_resources(cfg).values() for p, _ in targets]
    repos = set(cfg.get("dependencies", []))
    if cfg.get("local_path") and cfg.get("hf_model_id"):
        repos.add(cfg["hf_model_id"])
    for repo in repos:
        result.extend(p.resolve() for p, _ in _repo_targets(repo))
    return result


def shared_model_ids(model_id, registry):
    return sorted(set(affected_model_ids(deletion_targets(registry[model_id]), registry)) - {model_id})


def affected_model_ids(targets, registry):
    deleting = [p.resolve() for p, _ in targets]
    return sorted(model_id for model_id, cfg in registry.items() if any(
        a.is_relative_to(b) or b.is_relative_to(a)
        for a in deleting for b in model_resource_paths(cfg)
    ))


def delete_files(cfg):
    return delete_targets(deletion_targets(cfg))


def delete_targets(targets):
    # 先验证整个计划，再删除，避免后面的错误配置导致前面已部分删除。
    for target, root in targets:
        resolved = target.resolve()
        if resolved == root.resolve() or not resolved.is_relative_to(root.resolve()):
            raise ValueError(f"路径安全检查失败，拒绝删除: {target}")
    deleted, not_found = [], []
    for target, _ in targets:
        if not os.path.lexists(target):
            not_found.append(str(target))
            continue
        if target.is_symlink() or target.is_file():
            target.unlink()
        elif getattr(target, "is_junction", lambda: False)():
            # Windows junction 只删除入口，不递归进入链接目标。
            target.rmdir()
        else:
            shutil.rmtree(target)
        deleted.append(str(target))
    return {"ok": bool(deleted) or not targets,
            "message": f"已删除 {len(deleted)} 个文件/目录" if deleted else "未找到可删除的文件",
            "deleted": deleted, "not_found": not_found}
