"""复合编辑调用方使用的 FLUX Fill 适配入口。"""
from .model_executor import ModelExecutorFactory
from .model_registry import get_model


class FluxFiller:
    def __init__(self, device: str = "cuda"):
        self._executor = ModelExecutorFactory.create_executor(get_model("flux_fill"), device)

    def inpaint(self, image_rgb, mask, prompt: str, **params):
        return self._executor.execute(image_rgb, mask=mask, prompt=prompt, **params)

    def offload(self):
        self._executor.unload_model()
