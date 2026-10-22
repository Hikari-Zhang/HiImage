"""统一管理一次模型使用的清理，保留原始推理异常。"""
import logging
from contextlib import contextmanager


@contextmanager
def model_session(model, cleanup: str = "unload_model"):
    try:
        yield model
    except BaseException:
        try:
            getattr(model, cleanup)()
        except BaseException:
            logging.getLogger(__name__).exception("模型资源清理失败: %s", cleanup)
        raise
    else:
        getattr(model, cleanup)()
