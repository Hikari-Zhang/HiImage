"""单进程模型使用与删除互斥；使用许可覆盖真实工作而非页面状态。"""
from contextlib import contextmanager
import threading


class ModelResourcesBusy(RuntimeError):
    pass


class ModelResourceGuard:
    """允许并行使用，删除必须独占；暂按全局资源保守保护。"""

    def __init__(self):
        self._condition = threading.Condition()
        self._users = 0
        self._deleting = False

    @contextmanager
    def use(self):
        with self._condition:
            self._condition.wait_for(lambda: not self._deleting)
            self._users += 1
        try:
            yield
        finally:
            with self._condition:
                self._users -= 1
                self._condition.notify_all()

    @contextmanager
    def deleting(self):
        with self._condition:
            if self._users or self._deleting:
                raise ModelResourcesBusy("当前有下载、推理或删除任务正在使用模型资源，请结束后重试")
            self._deleting = True
        try:
            yield
        finally:
            with self._condition:
                self._deleting = False
                self._condition.notify_all()


model_resources = ModelResourceGuard()
