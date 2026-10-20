"""推理任务 ID 的共享校验类型。"""
from typing import Annotated

from pydantic import StringConstraints


TaskId = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=128),
]
