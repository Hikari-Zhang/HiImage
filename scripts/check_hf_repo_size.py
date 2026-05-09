#!/usr/bin/env python3
"""
查询 HuggingFace 仓库的实际大小（包括 Git LFS 文件）。

用法：
    python scripts/check_hf_repo_size.py Sanster/PowerPaint_v2
"""
from __future__ import annotations

import sys
import os
from urllib.parse import urljoin

try:
    from huggingface_hub import HfApi, list_repo_files
    from huggingface_hub.errors import GatedRepoError, RepositoryNotFoundError
except ImportError:
    print("[错误] huggingface_hub 未安装，请运行: pip install huggingface_hub")
    sys.exit(1)


def get_repo_size(repo_id: str, token: str = None) -> None:
    """通过 HF API 获取仓库所有文件的大小（包括 LFS 文件）。"""
    api = HfApi(token=token)

    print(f"[仓库] {repo_id}")

    # 获取仓库信息（包含文件列表和大小）
    try:
        repo_info = api.model_info(repo_id, token=token)
    except GatedRepoError:
        print(f"[错误] 仓库 {repo_id} 是门控模型，需要 HF Token")
        return
    except RepositoryNotFoundError:
        print(f"[错误] 仓库 {repo_id} 不存在或无权限")
        return
    except Exception as e:
        print(f"[错误] 获取仓库信息失败: {e}")
        return

    # 过滤不需要的文件（与 _download_hf 一致）
    IGNORE_SUFFIXES = {".msgpack", ".h5"}
    IGNORE_PREFIXES = ("flax_model", "tf_model")

    total_bytes = 0
    file_count = 0
    ignored_count = 0

    print(f"{'文件':<60} {'大小':>10}")
    print("-" * 75)

    for file_info in repo_info.siblings:
        path = file_info.rfilename

        # 过滤
        if any(path.endswith(s) for s in IGNORE_SUFFIXES):
            ignored_count += 1
            continue
        if any(path.startswith(p) for p in IGNORE_PREFIXES):
            ignored_count += 1
            continue

        # 获取文件大小（LFS 文件也能正确获取）
        size = getattr(file_info, "size", None)
        if size is None:
            # 无法获取大小，跳过
            print(f"{path:<60} {'?':>10}")
            continue

        total_bytes += size
        file_count += 1
        size_str = _fmt_size(size)
        print(f"{path:<60} {size_str:>10}")

    print("-" * 75)
    print(f"[文件数] {file_count} (忽略 {ignored_count} 个)")
    print(f"[总大小] {_fmt_size(total_bytes)} ({total_bytes // (1024*1024)} MB)")
    print(f"[建议 size_mb] {total_bytes // (1024*1024)}")


def _fmt_size(total_bytes: int) -> str:
    if total_bytes >= 1024 * 1024 * 1024:
        return f"{total_bytes / (1024**3):.1f} GB"
    elif total_bytes >= 1024 * 1024:
        return f"{total_bytes / (1024**2):.0f} MB"
    elif total_bytes >= 1024:
        return f"{total_bytes / 1024:.0f} KB"
    return f"{total_bytes} B"


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python scripts/check_hf_repo_size.py <repo_id>")
        sys.exit(1)

    repo_id = sys.argv[1]
    token = os.environ.get("HF_TOKEN")
    get_repo_size(repo_id, token)
