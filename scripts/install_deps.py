"""
HiImage 依赖安装脚本

解决 iopaint 1.6.0 与 diffusers>=0.32.0 的版本冲突问题：
  - iopaint 1.6.0 硬锁 diffusers==0.27.2
  - HiImage 的 FLUX 系列功能需要 diffusers>=0.32.0

安装策略（两步绕过冲突）：
  Step 1: 正常安装 requirements.txt（iopaint 会拉取 diffusers==0.27.2）
  Step 2: --no-deps 强制覆盖为高版本（跳过 iopaint 的版本约束检查）

用法：
    python scripts/install_deps.py
"""
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
REQUIREMENTS = PROJECT_ROOT / "backend" / "requirements.txt"

# 与 iopaint 1.6.0 冲突、需要 --no-deps 单独安装的包
CONFLICT_PACKAGES = [
    "diffusers>=0.32.0",
    "transformers>=4.47.0,<5.0",
    "huggingface-hub>=0.27.0,<1.0",
    "peft>=0.9.0",
]

pip = [sys.executable, "-m", "pip"]


def run(cmd: list[str]) -> int:
    print(f"\n$ {' '.join(cmd)}")
    return subprocess.run(cmd).returncode


def main():
    print("=" * 60)
    print("HiImage 依赖安装")
    print("=" * 60)

    # Step 1: 正常安装 requirements.txt
    print("\n[Step 1/2] 安装主要依赖...")
    rc = run(pip + ["install", "-r", str(REQUIREMENTS)])
    if rc != 0:
        print("\n❌ Step 1 失败，请检查错误信息")
        sys.exit(1)

    # Step 2: 强制覆盖冲突包（--no-deps 跳过版本约束检查）
    print("\n[Step 2/2] 升级 FLUX 所需包（覆盖 iopaint 版本约束）...")
    rc = run(pip + ["install"] + CONFLICT_PACKAGES + ["--no-deps"])
    if rc != 0:
        print("\n❌ Step 2 失败，请手动执行：")
        print(f"   pip install {' '.join(CONFLICT_PACKAGES)} --no-deps")
        sys.exit(1)

    # 验证
    print("\n[验证] 检查 FLUX Pipeline 可导入...")
    result = subprocess.run(
        [sys.executable, "-c",
         "from diffusers import FluxImg2ImgPipeline, FluxFillPipeline; print('OK')"],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        print("✅ diffusers FLUX Pipelines 导入成功")
    else:
        print("⚠️  验证失败：", result.stderr.strip())

    print("\n✅ 安装完成，下一步：python scripts/post_install.py")


if __name__ == "__main__":
    main()
