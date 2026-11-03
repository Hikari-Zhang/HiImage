"""一次性修复子进程。输入由 API 进程写入私有任务目录。"""
import json
from pathlib import Path
import sys


PROGRESS_PREFIX = "HIIMAGE_REPAIR_PROGRESS "


def main(directory: Path) -> None:
    def deny_network(event, _args):
        if event in ("socket.connect", "socket.getaddrinfo"):
            raise RuntimeError("修复进程只使用本地权重，请先下载模型")

    sys.addaudithook(deny_network)
    import numpy as np
    from app.services import image_processing

    request = json.loads((directory / "request.json").read_text(encoding="utf-8"))
    image = np.load(directory / "image.npy", allow_pickle=False)
    mask_path = directory / "mask.npy"
    mask = np.load(mask_path, allow_pickle=False) if mask_path.exists() else None

    def progress(percent, message):
        if (directory / "cancel").exists():
            raise InterruptedError("修复已取消")
        print(PROGRESS_PREFIX + json.dumps([percent, message]), flush=True)

    if request["operation"] == "inpaint":
        result = image_processing.inpaint_image(
            image, mask=mask, progress_callback=progress, **request["options"],
        )
    elif request["operation"] == "pipeline":
        result = image_processing.pipeline_image(
            image, mask, request["rois"], request["options"], progress_callback=progress,
        )
    else:
        raise ValueError("未知修复任务")
    if (directory / "cancel").exists():
        raise InterruptedError("修复已取消")
    np.save(directory / "result.npy", result, allow_pickle=False)


if __name__ == "__main__":
    directory = Path(sys.argv[1])
    try:
        main(directory)
    except RuntimeError as error:
        oom_type = getattr(sys.modules.get("torch"), "OutOfMemoryError", ())
        if isinstance(error, oom_type):
            (directory / "out_of_memory").touch()
        raise
