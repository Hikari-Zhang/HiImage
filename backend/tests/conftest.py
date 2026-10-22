"""测试启动配置：支持从仓库根或 backend 目录运行。"""
from pathlib import Path
import sys
import pytest


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


@pytest.fixture(autouse=True)
def isolated_task_registry(monkeypatch, tmp_path):
    """Each test represents a fresh server; task ID deduplication is process-wide."""
    from app import task_route, shared_executor
    from app.routers import tasks
    from app.websocket import progress
    from app.services.task_registry import TaskRegistry
    from app.services.task_results import TaskResults

    registry = TaskRegistry(result_store=TaskResults(base_dir=tmp_path))
    for module in (task_route, shared_executor, tasks, progress):
        monkeypatch.setattr(module, "task_registry", registry)
    return registry
