"""
全局共享线程执行器 — 所有 AI 推理路由共用，确保同一时刻只有一个 GPU 操作在运行。

设计原则：
  - max_workers=1：AI 推理（去水印、超分、抠图等）均占用 GPU，并发执行无益且
    容易触发显存溢出；序列化执行更稳定
  - 模块级单例：各 router 直接 import，无需通过依赖注入传递
  - 生命周期：进程退出时由 Python 解释器自动调用 shutdown（atexit 语义）

替代方案：
  若未来需要支持 CPU/GPU 并行（例如 CPU 模型与 GPU 模型同时运行），
  可将 max_workers 改为 2 并在任务提交时附加 device 标签做调度。
"""
from concurrent.futures import ThreadPoolExecutor

# 所有推理路由共用的单线程执行器
# 同一时刻只有一个 GPU 操作在运行，防止显存溢出
executor = ThreadPoolExecutor(max_workers=1)
