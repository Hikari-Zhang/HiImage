"""
HiImage FastAPI 应用
"""
import asyncio
import os
import sys
from contextlib import asynccontextmanager
from typing import Awaitable

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# 确保 backend/ 在 sys.path 中（core 模块可导入）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 优先使用 core.paths 作为路径的唯一数据源；如果旧版本缺少导出则回退到 app.config
try:
    from core.paths import PROJECT_ROOT, MODELS_DIR, apply_default_env_vars
except ImportError:
    from core.paths import PROJECT_ROOT, MODELS_DIR  # type: ignore[assignment]
    def apply_default_env_vars(): pass  # type: ignore[misc]

from app.config import get
from app.routers import system, inpaint, upscale, settings, logs, postprocess, synthesis, cutout, models as models_router_module
from app.websocket.progress import router as ws_router
from app.logging_manager import log_manager, setup_logging
from app.shared_executor import (
    inference_scheduler,
    install_inference_queue_handler,
)


def parse_cors_origins(value: str | None) -> list[str]:
    """解析逗号分隔的 CORS 来源；空值表示生产同源部署。"""
    if value is None:
        return ["http://127.0.0.1:5173", "http://localhost:5173"]
    return [origin.strip() for origin in value.split(",") if origin.strip()]


def cors_middleware_options(value: str | None) -> dict:
    """生成 CORS 中间件配置，通配来源时禁止凭据。"""
    origins = parse_cors_origins(value)
    wildcard = "*" in origins
    return {
        "allow_origins": origins,
        "allow_credentials": bool(origins) and not wildcard,
        "allow_methods": ["*"],
        "allow_headers": ["*"],
    }


async def _run_cleanup_resilient(
    operation: Awaitable[None],
    label: str,
) -> tuple[bool, BaseException | None]:
    """在独立任务中完成清理，记录外层取消并延迟传播。"""
    task = asyncio.create_task(operation, name=f"hiimage-shutdown-{label}")
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.cancelled():
                break
            cancelled = True
            log_manager.warning(
                f"关闭期间收到取消，继续等待: {label}",
                source="backend",
            )
        except BaseException:
            break
    try:
        task.result()
    except BaseException as error:
        return cancelled, error
    return cancelled, None


async def _shutdown_runtime() -> None:
    """完整清理调度器与 IOPaint，最后恢复调用者取消语义。"""
    stop_accepting = getattr(inference_scheduler, "stop_accepting", None)
    if stop_accepting is not None:
        stop_accepting()
    cancelled, scheduler_error = await _run_cleanup_resilient(
        inference_scheduler.shutdown(),
        "inference_scheduler",
    )
    if scheduler_error is not None:
        log_manager.error(
            f"推理调度器关闭失败: {scheduler_error}",
            source="backend",
        )
        close_cancelled, close_error = await _run_cleanup_resilient(
            asyncio.to_thread(inference_scheduler.close_executor),
            "inference_executor",
        )
        cancelled = cancelled or close_cancelled
        if close_error is not None:
            log_manager.error(
                f"推理工作线程关闭失败: {close_error}",
                source="backend",
            )

    try:
        from core.model_server import get_server

        server = get_server()
        if server:
            server.stop()
    except BaseException as error:
        log_manager.error(
            f"IOPaint Server 关闭失败: {error}",
            source="backend",
        )

    if cancelled:
        raise asyncio.CancelledError


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 初始化日志系统
    setup_logging()
    log_manager.set_loop(asyncio.get_running_loop())

    # Startup: 设置环境变量
    hf_endpoint = get("network.hf_endpoint", "https://huggingface.com")
    hf_token = get("network.hf_token", "")

    if hf_endpoint:
        os.environ["HF_ENDPOINT"] = hf_endpoint
        print(f"[Backend] HuggingFace 端点: {hf_endpoint}")
    if hf_token:
        os.environ["HF_TOKEN"] = hf_token

    # 为依赖环境变量的库设置默认路径（仅设置未定义的环境变量）
    apply_default_env_vars()
    # 在主线程预导入 model_server，确保 signal.signal(SIGTERM) 在主线程注册。
    # 若推迟到首次推理请求时导入，会在线程池工作线程中触发
    # "signal only works in main thread" ValueError。
    try:
        import core.model_server  # noqa: F401  — 副作用：注册 SIGTERM 处理器
    except Exception as _e:
        print(f"[Backend] model_server 预导入失败（非致命）: {_e}")

    # 为已下载的 IOPaint server 模式模型补全 hub/ 软链接，
    # 使 iopaint scan_diffusers_models() 能识别本地缓存，避免重复触发下载
    try:
        from core.model_setup import ensure_iopaint_hub_links
        ensure_iopaint_hub_links()
    except Exception as _e:
        print(f"[Backend] iopaint hub 链接补全失败（非致命）: {_e}")

    print(f"[Backend] HiImage API 启动")
    print(f"[Backend] 项目根目录: {PROJECT_ROOT}")
    print(f"[Backend] 模型目录: {MODELS_DIR}")

    log_manager.info("HiImage API 启动", source="backend")
    log_manager.info(f"项目根目录: {PROJECT_ROOT}", source="backend")
    log_manager.info(f"模型目录: {MODELS_DIR}", source="backend")

    yield

    # Shutdown: 先排空已接纳推理，再停止 IOPaint Server。
    await _shutdown_runtime()
    print("[Backend] HiImage API 关闭")


app = FastAPI(
    title="HiImage API",
    version="2.0.0",
    lifespan=lifespan,
)
install_inference_queue_handler(app)

# CORS 中间件（允许 Electron renderer 访问）
app.add_middleware(CORSMiddleware, **cors_middleware_options(
    os.environ.get("HIIMAGE_CORS_ORIGINS")
))

# 注册路由
app.include_router(system.router, prefix="/api", tags=["system"])
app.include_router(inpaint.router, prefix="/api", tags=["inpaint"])
app.include_router(upscale.router, prefix="/api", tags=["upscale"])
app.include_router(settings.router, prefix="/api", tags=["settings"])
app.include_router(logs.router, prefix="/api", tags=["logs"])
app.include_router(postprocess.router, prefix="/api", tags=["postprocess"])
app.include_router(synthesis.router, prefix="/api", tags=["synthesis"])
app.include_router(cutout.router, prefix="/api", tags=["cutout"])
app.include_router(models_router_module.router, prefix="/api", tags=["models"])
app.include_router(ws_router, prefix="/api")  # → /api/ws/progress
