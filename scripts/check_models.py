#!/usr/bin/env python3
"""
HiImage 模型完整性检测 CLI

用法示例：
  python scripts/check_models.py                   # 检测全部模型
  python scripts/check_models.py --mode outfit_swap # 只检测某模式
  python scripts/check_models.py --model birefnet   # 只检测某模型
  python scripts/check_models.py --json             # JSON 格式输出
  python scripts/check_models.py --mode background_replace --json

退出码：
  0  所有被检测的模型均为 ok
  1  存在 missing / corrupted / unknown 状态的模型
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# ── 确保 backend/ 目录在 Python path 中 ──────────────────────────────────────
# 脚本位于 <PROJECT_ROOT>/scripts/，backend 在同级目录
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
_BACKEND_DIR = _PROJECT_ROOT / "backend"

if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

# 设置 HF_HOME（与 app/main.py 保持一致）
_MODELS_DIR = _PROJECT_ROOT / "models"
if "HF_HOME" not in os.environ:
    os.environ["HF_HOME"] = str(_MODELS_DIR / "huggingface")
if "TORCH_HOME" not in os.environ:
    os.environ["TORCH_HOME"] = str(_MODELS_DIR / "torch")


# ── 颜色/图标常量 ─────────────────────────────────────────────────────────────

_USE_COLOR = sys.stdout.isatty()

def _color(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _USE_COLOR else text

def _status_icon(status: str) -> str:
    return {
        "ok":        "✅",
        "missing":   "⚠️ ",
        "partial":   "⚠️ ",
        "corrupted": "❌",
        "unknown":   "❓",
    }.get(status, "❓")

def _status_color(status: str, text: str) -> str:
    code = {
        "ok":        "32",   # green
        "missing":   "33",   # yellow
        "partial":   "33",
        "corrupted": "31",   # red
        "unknown":   "90",   # dark gray
    }.get(status, "0")
    return _color(text, code)


# ── 输出格式 ──────────────────────────────────────────────────────────────────

def _print_table(results, title: str = "") -> int:
    """打印表格，返回非 ok 的数量。"""
    from core.model_registry import MODE_GROUPS

    if title:
        print(f"\n{_color(title, '1')}")   # bold

    # 按功能模式分组
    mode_map: dict[str, list] = {g["id"]: [] for g in MODE_GROUPS}
    untagged: list = []

    for r in results:
        from core.model_registry import MODEL_BY_ID
        cfg = MODEL_BY_ID.get(r.model_id, {})
        tags = cfg.get("tags", [])
        placed = False
        for tag in tags:
            if tag in mode_map:
                mode_map[tag].append(r)
                placed = True
                break
        if not placed:
            untagged.append(r)

    # 分组模式名 → 显示名
    mode_names = {g["id"]: g["name"] for g in MODE_GROUPS}

    has_printed = False
    for mode_id, mode_results in mode_map.items():
        if not mode_results:
            continue
        mode_name = mode_names.get(mode_id, mode_id)
        print(f"\n  {_color(f'[{mode_name}]', '36')}")  # cyan
        for r in mode_results:
            _print_row(r)
        has_printed = True

    if untagged:
        print(f"\n  {_color('[其他]', '36')}")
        for r in untagged:
            _print_row(r)

    bad = sum(1 for r in results if r.status != "ok")
    return bad


def _print_row(r) -> None:
    icon = _status_icon(r.status)
    status_str = _status_color(r.status, r.status.ljust(9))
    model_id = r.model_id.ljust(24)
    name = (r.name or "").ljust(28)
    print(f"    {icon}  {model_id}  {name}  {status_str}  {r.message}")


def _print_summary(results) -> None:
    ok_count       = sum(1 for r in results if r.status == "ok")
    missing_count  = sum(1 for r in results if r.status == "missing")
    partial_count  = sum(1 for r in results if r.status == "partial")
    corrupt_count  = sum(1 for r in results if r.status == "corrupted")
    unknown_count  = sum(1 for r in results if r.status == "unknown")

    sep = "═" * 70
    print(f"\n{_color(sep, '90')}")
    print(
        f"汇总: "
        f"{_color(f'✅ {ok_count} 正常', '32')}  "
        f"{_color(f'⚠️  {missing_count + partial_count} 缺失/不完整', '33')}  "
        f"{_color(f'❌ {corrupt_count} 损坏', '31')}  "
        f"{_color(f'❓ {unknown_count} 未知', '90')}"
        f"  （共 {len(results)} 个）"
    )

    if missing_count + partial_count + corrupt_count > 0:
        print(_color("\n提示：运行 `python scripts/post_install.py` 或手动下载缺失模型。", "33"))


def _output_json(results) -> None:
    data = [
        {
            "model_id":        r.model_id,
            "name":            r.name,
            "provider":        r.provider,
            "status":          r.status,
            "message":         r.message,
            "file_path":       r.file_path,
            "size_bytes":      r.size_bytes,
            "expected_size_mb": r.expected_size_mb,
        }
        for r in results
    ]
    print(json.dumps(data, ensure_ascii=False, indent=2))


# ── 主入口 ────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="HiImage 模型完整性检测",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--mode",  metavar="MODE_ID",  help="只检测指定功能模式下的模型")
    parser.add_argument("--model", metavar="MODEL_ID", help="只检测指定模型")
    parser.add_argument("--json",  action="store_true", help="以 JSON 格式输出")
    args = parser.parse_args()

    try:
        from core.model_checker import ModelChecker
    except ImportError as e:
        print(f"[ERROR] 无法导入 model_checker: {e}", file=sys.stderr)
        print(f"  请确保已激活虚拟环境：source venv/bin/activate", file=sys.stderr)
        return 1

    checker = ModelChecker(project_root=_PROJECT_ROOT)

    # 选择检测范围
    if args.model:
        results = [checker.check_model(args.model)]
    elif args.mode:
        results = checker.check_mode(args.mode)
        if not results:
            print(f"[ERROR] 未知模式 ID: {args.mode!r}", file=sys.stderr)
            print(f"  可选值：background_replace / outfit_swap / face_swap / "
                  f"virtual_tryon / prompt_inpaint / auto_segment_edit / instruction_edit",
                  file=sys.stderr)
            return 1
    else:
        results = checker.check_all()

    # 输出
    if args.json:
        _output_json(results)
    else:
        header = "═" * 70
        print(f"\n{_color('HiImage 模型完整性检测报告', '1;36')}")
        print(_color(header, "90"))

        scope = (
            f"模型: {args.model}" if args.model
            else f"模式: {args.mode}" if args.mode
            else "全部模型"
        )
        print(f"检测范围: {scope}  |  共 {len(results)} 个模型")

        _print_table(results)
        _print_summary(results)
        print()

    # 退出码
    bad = sum(1 for r in results if r.status != "ok")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
