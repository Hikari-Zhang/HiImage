"""
HiImage FastAPI 应用
"""
import os
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# 确保 backend/ 在 sys.path 中（core 模块可导入）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import get, PROJECT_ROOT, MODELS_DIR
from app.routers import system, inpaint, upscale, settings, logs, postprocess, synthesis, models as models_router_module
from app.websocket.progress import router as ws_router
from app.logging_manager import log_manager, setup_logging


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 初始化日志系统
    import asyncio
    setup_logging()
    log_manager.set_loop(asyncio.get_event_loop())

    # Startup: 设置环境变量
    hf_endpoint = get("network.hf_endpoint", "https://huggingface.co")
    hf_token = get("network.hf_token", "")

    if hf_endpoint:
        os.environ["HF_ENDPOINT"] = hf_endpoint
    if hf_token:
        os.environ["HF_TOKEN"] = hf_token

    # 模型缓存目录
    os.environ["HF_HOME"] = os.path.join(MODELS_DIR, "huggingface")
    os.environ["TORCH_HOME"] = os.path.join(MODELS_DIR, "torch")

    print(f"[Backend] HiImage API 启动")
    print(f"[Backend] 项目根目录: {PROJECT_ROOT}")
    print(f"[Backend] 模型目录: {MODELS_DIR}")

    log_manager.info("HiImage API 启动", source="backend")
    log_manager.info(f"项目根目录: {PROJECT_ROOT}", source="backend")
    log_manager.info(f"模型目录: {MODELS_DIR}", source="backend")

    yield

    # Shutdown: 停止 IOPaint Server（如果在运行）
    try:
        from core.model_server import get_server
        server = get_server()
        if server:
            server.stop()
    except Exception:
        pass
    print("[Backend] HiImage API 关闭")


app = FastAPI(
    title="HiImage API",
    version="2.0.0",
    lifespan=lifespan,
)

# CORS 中间件（允许 Electron renderer 访问）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(system.router, prefix="/api", tags=["system"])
app.include_router(inpaint.router, prefix="/api", tags=["inpaint"])
app.include_router(upscale.router, prefix="/api", tags=["upscale"])
app.include_router(settings.router, prefix="/api", tags=["settings"])
app.include_router(logs.router, prefix="/api", tags=["logs"])
app.include_router(postprocess.router, prefix="/api", tags=["postprocess"])
app.include_router(synthesis.router, prefix="/api", tags=["synthesis"])
app.include_router(models_router_module.router, prefix="/api", tags=["models"])
app.include_router(ws_router, prefix="/api")  # → /api/ws/progress
