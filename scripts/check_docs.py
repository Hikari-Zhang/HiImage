#!/usr/bin/env python3
"""Validate active documentation links, npm commands, and Web positioning."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parent.parent
ACTIVE_DOCS = (
    "README.md",
    "docs/SYSTEM_ARCHITECTURE.md",
    "docs/system-design.md",
    "docs/PRD.md",
    "docs/SCRIPTS_REFERENCE.md",
)
LINK_PATTERN = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
NPM_RUN_PATTERN = re.compile(r"\bnpm run ([a-zA-Z0-9:_-]+)")
DESKTOP_PATTERN = re.compile(r"\bElectron\b|electron-|电子桌面|桌面(?:应用|工具|客户端)")


def load_scripts(root: Path) -> set[str]:
    """Load the union of root and frontend npm script names."""
    scripts: set[str] = set()
    for relative_path in ("package.json", "frontend/package.json"):
        path = root / relative_path
        if not path.exists():
            continue
        document = json.loads(path.read_text(encoding="utf-8"))
        scripts.update(document.get("scripts", {}))
    return scripts


def validate_active_docs(root: Path, relative_paths: tuple[str, ...]) -> None:
    """Validate active Markdown documents against the checked-in project."""
    available_scripts = load_scripts(root)
    for relative_path in relative_paths:
        path = root / relative_path
        assert path.is_file(), f"active document missing: {relative_path}"
        content = path.read_text(encoding="utf-8")
        assert "Python 3.9+" not in content, (
            f"{relative_path} must document Python 3.10+ as the minimum"
        )
        outdated_batch_limits = (
            "单文件最多 50 MB",
            "单文件 50 MB",
            "每张不超过 50 MB",
            "100 张与 50 MB",
            "类型、50 MB",
            "执行 50 MB 单文件限制",
        )
        assert not any(value in content for value in outdated_batch_limits), (
            f"{relative_path} must document the 20 MB frontend file limit"
        )
        match = DESKTOP_PATTERN.search(content)
        assert match is None, (
            f"{relative_path} still presents the current product as Electron/desktop: "
            f"{match.group(0)}"
        )
        for command in NPM_RUN_PATTERN.findall(content):
            assert command in available_scripts, (
                f"{relative_path} references unknown npm script: {command}"
            )
        for raw_target in LINK_PATTERN.findall(content):
            target = raw_target.strip().split(maxsplit=1)[0].strip("<>")
            if (
                not target
                or target.startswith(("#", "http://", "https://", "mailto:"))
            ):
                continue
            local_path = unquote(target.split("#", 1)[0])
            resolved = (path.parent / local_path).resolve()
            assert resolved.exists(), (
                f"{relative_path} has broken local link: {target}"
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "documents",
        nargs="*",
        default=list(ACTIVE_DOCS),
        help="repository-relative Markdown paths",
    )
    args = parser.parse_args()
    try:
        validate_active_docs(ROOT, tuple(args.documents))
    except (AssertionError, json.JSONDecodeError) as error:
        print(f"[FAIL] {error}", file=sys.stderr)
        return 1
    print(f"[OK] Validated {len(args.documents)} active documentation files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
