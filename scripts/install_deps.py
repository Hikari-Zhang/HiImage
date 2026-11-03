"""使用同一解析过程安装原生后端依赖。平台 PyTorch 请先安装。"""
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REQUIREMENTS = PROJECT_ROOT / "backend" / "requirements.txt"


def main():
    subprocess.run([sys.executable,"-m","pip","uninstall","-y","iopaint"],check=True)
    subprocess.run([sys.executable,"-m","pip","install","-r",str(REQUIREMENTS)],check=True)
    subprocess.run([sys.executable,"-m","pip","check"],check=True)
    print("依赖安装完成；下一步运行 scripts/post_install.py")


if __name__ == "__main__":
    main()
