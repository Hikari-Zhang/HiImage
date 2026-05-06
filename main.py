"""
ClearWaterMark - 图片水印清除工具
应用入口
"""
import sys
import os
import atexit

# 添加项目根目录到sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 将模型统一下载到工程目录的 models/ 文件夹
# 必须在 iopaint / diffusers / torch 等库 import 之前设置，否则不生效
_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
_MODEL_DIR = os.path.join(_PROJECT_ROOT, 'models')
os.makedirs(_MODEL_DIR, exist_ok=True)

os.environ['XDG_CACHE_HOME'] = _MODEL_DIR          # IOPaint 传统模型（lama/mat/zits 等）
os.environ['HF_HOME'] = os.path.join(_MODEL_DIR, 'huggingface')  # HuggingFace 扩散模型

# HuggingFace 镜像地址：从配置文件读取，方便用户切换
import config as _cfg
_hf_endpoint = _cfg.get('network.hf_endpoint', '').strip()
if _hf_endpoint:
    os.environ['HF_ENDPOINT'] = _hf_endpoint

# HuggingFace Access Token：下载 gated 模型（如 PowerPaint-v2-1）时需要
_hf_token = _cfg.get('network.hf_token', '').strip()
if _hf_token:
    os.environ['HF_TOKEN'] = _hf_token

from PySide6.QtWidgets import QApplication
from gui.main_window import MainWindow
from core.model_server import get_server

# 程序退出时确保 iopaint server 子进程被清理
atexit.register(get_server().stop)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("ClearWaterMark")
    app.setOrganizationName("Hikari")

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
