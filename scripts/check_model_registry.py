"""仅校验模型配置；不加载模型、不下载权重。"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

import yaml


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from core.model_schema import ModelConfigError, validate_registry


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", type=Path, default=ROOT / "backend/core/models.yaml")
    args = parser.parse_args()
    try:
        document = yaml.safe_load(args.path.read_text(encoding="utf-8"))
        models, modes = validate_registry(document)
    except (OSError, yaml.YAMLError, ModelConfigError) as exc:
        print(f"[FAIL] {args.path}: {exc}", file=sys.stderr)
        return 1
    print(f"[OK] Model registry: {len(models)} models, {len(modes)} modes (schema 1)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
