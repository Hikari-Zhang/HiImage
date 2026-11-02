"""模型文件的删除计划、共享引用检查和受限目录删除。"""
import os
from pathlib import Path
import shutil

from core import paths


def _repositories(cfg, *, dependencies=False):
    repos = {sub["id"] for sub in cfg.get("hf_models", [])}
    if cfg.get("hf_model_id") and not cfg.get("local_path"):
        repos.add(cfg["hf_model_id"])
    if dependencies:
        repos.update(cfg.get("dependencies", []))
    return repos


def _repo_targets(repo):
    # 注册表是服务端配置，也必须防止错误配置越出缓存目录。
    if any(part in ("", ".", "..") for part in repo.split("/")) or "\\" in repo:
        raise ValueError(f"无效模型仓库: {repo}")
    name = repo.replace("/", "--")
    return [(paths.HF_HOME / "manual" / name, paths.HF_HOME / "manual"),
            (paths.get_hf_hub_dir() / f"models--{name}", paths.get_hf_hub_dir())]


def deletion_targets(cfg):
    targets = []
    if cfg.get("provider") == "rembg":
        name = cfg.get("rembg_session_name", cfg.get("rembg_model_name", cfg["id"]))
        filename = cfg.get("onnx_filename", f"{name}.onnx")
        targets.append((paths.U2NET_HOME / filename, paths.U2NET_HOME))
    if cfg.get("local_path"):
        local = Path(paths.resolve_model_cache_path(cfg))
        roots = [paths.CACHE_ROOT, paths.MODELS_DIR]
        provider_root = {"realesrgan": paths.REALESRGAN_HOME, "gfpgan": paths.GFPGAN_HOME,
                         "restormer": paths.RESTORMER_HOME}.get(cfg.get("provider"))
        if provider_root is not None:
            roots.append(provider_root)
        root = next((r for r in roots if local.resolve().is_relative_to(r.resolve())
                     and local.resolve() != r.resolve()), None)
        if root is None:
            raise ValueError(f"路径安全检查失败，拒绝删除: {local}")
        targets.append((local, root))
    for repo in sorted(_repositories(cfg)):
        targets.extend(_repo_targets(repo))
    return targets


def model_resource_paths(cfg):
    """模型可能读写的缓存路径，包括组合模型和依赖仓库。"""
    result = [p.resolve() for p, _ in deletion_targets(cfg)]
    repos = _repositories(cfg, dependencies=True)
    if cfg.get("hf_model_id"):
        repos.add(cfg["hf_model_id"])
    for repo in repos:
        result.extend(p.resolve() for p, _ in _repo_targets(repo))
    return result


def shared_model_ids(model_id, registry):
    cfg = registry[model_id]
    deleting = [p.resolve() for p, _ in deletion_targets(cfg)]
    repos = _repositories(cfg)
    shared = []
    for other_id, other in registry.items():
        if other_id == model_id:
            continue
        if repos & _repositories(other, dependencies=True):
            shared.append(other_id)
            continue
        # 同时覆盖两个本地条目指向同一文件、符号链接及目录内的文件引用。
        if any(a.is_relative_to(b) or b.is_relative_to(a)
               for a in deleting for b in model_resource_paths(other)):
            shared.append(other_id)
    return sorted(shared)


def delete_files(cfg):
    targets = deletion_targets(cfg)
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
