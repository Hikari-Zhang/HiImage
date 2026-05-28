# HiImage 系统设计分析与优化建议

> 基于 `SYSTEM_ARCHITECTURE.md` 与 `SCRIPTS_REFERENCE.md` 及核心源码分析。  
> 文档版本：1.2 · 2026-05-29（在 1.1 基础上追加第二轮审查结果）

---

## 目录

1. [总体评价](#1-总体评价)
2. [架构合理性分析](#2-架构合理性分析)
3. [已识别的设计问题](#3-已识别的设计问题)
4. [优化建议（按优先级）](#4-优化建议按优先级)
5. [技术债务清单](#5-技术债务清单)
6. [总结](#6-总结)

> **版本说明：**
> - **1.0 → 1.1**（2026-05-13）：首次审查，覆盖下载队列、core/app 分层、ModelChecker 等问题，P0/P1 问题均已修复。
> - **1.1 → 1.2**（2026-05-29）：第二轮审查，新增对并发模型、WebSocket 隔离、配置线程安全、代码重复等问题的分析（第 3.11 节起）。

---

## 1. 总体评价

HiImage 整体架构选型合理，**Electron + FastAPI** 的桌面 AI 应用方案是当前同类产品的主流路径。"配置驱动"设计理念（`models.yaml`）在扩展性上表现优秀，下载队列、IOPaint Server 保活等细节处理也体现了较高的工程成熟度。

**优势亮点：**
- `models.yaml` 统一管理所有模型元数据，新增模型零代码改动
- `DownloadQueue` 并发控制 + SSE 实时推送，用户体验良好
- `ModelServer` 的 idle 保活计时 + `begin_inference`/`end_inference` 引用计数，逻辑严谨
- `dev.js` 跨平台一键启动，屏蔽了 GPU/Python 环境的复杂性
- `post_install.py` + `install_deps.py` 妥善解决了第三方包版本冲突问题

**核心问题：**
当前系统最突出的问题集中在 **代码重复** 和 **职责边界模糊** 两个方向，将在下文逐一展开。

---

## 2. 架构合理性分析

### 2.1 前端层

| 设计点 | 评价 |
|--------|------|
| Electron 三层架构（Main / Preload / Renderer） | ✅ 符合 Electron 安全最佳实践，`contextBridge` 隔离合理 |
| Zustand 多 Store 划分 | ✅ 职责清晰，`useImageStore` / `useProcessStore` / `useDownloadStore` 互不耦合 |
| 自定义 Hook 封装 API 调用 | ✅ `useBackendAPI.ts` 统一管理 fetch，便于全局错误处理 |
| Konva.js 渲染画布 | ✅ 性能优于纯 DOM，适合复杂图形交互 |
| HTTP + WebSocket 双通道 | ✅ 指令/结果走 REST，进度走 WS，职责清晰 |

### 2.2 后端层

| 设计点 | 评价 |
|--------|------|
| FastAPI + Uvicorn 异步框架 | ✅ 高性能，天然适合 AI 推理长耗时场景 |
| `model_registry.py` 配置驱动 | ✅ 核心亮点，扩展性极强 |
| `DownloadQueue` 全局单例 | ✅ 并发控制合理，SSE 订阅机制简洁 |
| `ModelServer` 进程管理 | ✅ 保活逻辑健壮（引用计数防止误关闭） |
| `paths.py` 统一路径管理 | ✅ 跨平台兼容性有保障 |
| 执行器数量（15+） | ⚠️ 执行器粒度细，但缺乏统一抽象基类，接口不一致 |
| 废弃接口未清理 | ⚠️ 旧 SSE 接口（`GET /download`）仍与新队列 API 并存 |

### 2.3 脚本层

| 脚本 | 评价 |
|------|------|
| `dev.js` 跨平台启动 | ✅ 覆盖 GPU 自动检测、venv 创建，开发者体验好 |
| `dev.sh` | ⚠️ 功能与 `dev.js` 高度重叠，二选一即可 |
| `install_deps.py` | ✅ 两步绕过版本冲突，方案务实有效 |
| `post_install.py` | ✅ 单一职责，结构简洁 |
| `check_models.py` | ✅ CLI 工具设计规范，支持 JSON 输出 |
| `check_hf_repo_size.py` | ✅ 开发辅助工具，定位准确 |
| `generate_icons.py` | ✅ 工具脚本，偶发使用，无问题 |

---

## 3. 已识别的设计问题

### 3.1 【严重】下载逻辑大量重复 [✅ 已修复于 2026-05-13]

**问题描述：**  
`_download_rembg`、`_download_hf`、`_download_direct`、`_download_hf_multi` 四个下载函数全部位于 `backend/app/routers/models.py`（路由层），同时 `backend/core/download_queue.py` 在 `_run_download` 中通过 `importlib` 动态导入这些函数：

```python
# download_queue.py 第 346 行
_models_mod = importlib.import_module("app.routers.models")
_download_rembg = getattr(_models_mod, "_download_rembg")
```

此外，`_fmt_speed` 和 `_fmt_size` 两个工具函数在 `models.py` 和 `download_queue.py` 中各定义了一份（完全相同）。

**危害：**
- 下载逻辑（业务核心）放在路由层，违反分层原则
- `core` 层反向依赖 `app.routers` 层，导致单向依赖关系破裂
- 修改下载逻辑需要同时关注两处代码

**建议：** 将下载函数迁移至 `backend/core/model_download_helper.py`（已存在该文件，应充分利用），并删除路由层的重复定义。

---

### 3.2 【严重】`_subscribe_continue` 递归调用导致栈溢出风险 [✅ 已修复于 2026-05-13]

**问题描述：**  
`DownloadQueue.subscribe()` 在 30 秒超时后调用 `_subscribe_continue()`，而 `_subscribe_continue()` 同样在超时后递归调用自身：

```python
# download_queue.py 第 269-283 行
async def _subscribe_continue(self, model_id, q):
    try:
        while True:
            event = await asyncio.wait_for(q.get(), timeout=30)
            ...
    except asyncio.TimeoutError:
        yield {"heartbeat": True}
        async for ev in self._subscribe_continue(model_id, q):  # ← 无限递归
            yield ev
```

对于需要超长时间下载的大模型（如 FLUX，可能需要数小时），每 30 秒一次递归，最终将导致调用栈溢出。

**建议：** 改用 `while True` 循环替代递归：

```python
async def subscribe(self, model_id):
    q = asyncio.Queue()
    self._subscribers.setdefault(model_id, []).append(q)
    try:
        while True:
            try:
                event = await asyncio.wait_for(q.get(), timeout=30)
                if event is None:
                    break
                yield event
            except asyncio.TimeoutError:
                yield {"heartbeat": True}
    finally:
        ...
```

---

### 3.3 【中等】废弃 API 与新 API 并存，逻辑重复 [✅ 已修复于 2026-05-13]

**问题描述：**  
`models.py` 中存在两套下载 API：
- **旧接口（已废弃）：** `GET /api/models/download`、`GET /api/models/download/{model_id}` — SSE 流式推送，包含完整下载逻辑（`_download_generator`、`_download_single_generator`）
- **新接口（推荐）：** `POST /api/models/download/{model_id}`、`GET /api/models/subscribe/{model_id}` — 队列 + SSE 订阅

两套接口的下载逻辑并行存在，总代码量超过 600 行，且下载流程（provider 判断 → 选择下载函数 → 进度回调）在旧接口中重复实现了一遍。

**修复方案：** 将下载函数（`download_rembg`、`download_hf`、`download_hf_multi`、`download_direct`）迁移至 `core/downloaders.py`，旧接口和新接口均从此模块导入，消除逻辑重复。旧接口SSE生成器保留（向后兼容），但内部调用统一的下载函数。

---

### 3.4 【中等】执行器层缺乏统一抽象基类

**问题描述：**  
`backend/core/` 下有 15+ 个执行器（`inpainter.py`、`upscaler.py`、`restormer_executor.py`、`synthesizer.py` 等），但没有统一的抽象基类（Abstract Base Class）定义公共接口。

每个执行器的入参签名、异常处理方式、进度回调格式各不相同，例如：
- `inpainter.py` 通过 `progress_callback(percent, message)` 回调上报进度
- `upscaler.py` 直接返回结果，无进度上报
- `restormer_executor.py` 的任务类型通过字符串 `task_type` 区分

**建议：** 定义抽象基类：

```python
# backend/core/base_executor.py
from abc import ABC, abstractmethod
import numpy as np

class BaseExecutor(ABC):
    @abstractmethod
    def run(
        self,
        image: np.ndarray,
        params: dict,
        progress_callback=None,
    ) -> np.ndarray: ...
```

---

### 3.5 【中等】`dev.sh` 与 `dev.js` 功能重叠

**问题描述：**  
`dev.sh` 和 `dev.js` 都能完成"启动后端 + 启动前端"的工作，但 `dev.js` 功能更完整（GPU 检测、自动 venv、依赖安装）。两者并行维护，但 `dev.sh` 不包含依赖安装逻辑，在全新环境下无法独立使用。

**建议：** 将 `dev.sh` 定位为"轻量级快速启动"（假设环境已就绪），在 README 中明确两者的适用场景；或直接废弃 `dev.sh`，统一使用 `dev.js`。

---

### 3.6 【中等】`ModelChecker` 每次请求都实例化，缺乏缓存 [✅ 已修复于 2026-05-13]

**问题描述：**  
`GET /api/models/health` 和 `GET /api/models/list` 都会 `ModelChecker()` 实例化并调用 `check_all()`，对磁盘进行全量扫描。在模型数量较多时（20+ 个模型），每次调用都会产生大量文件 I/O。

**修复方案：** 在 `model_checker.py` 中添加 TTL 缓存机制（默认 5 秒）：
- 模块级缓存字典 `_model_check_cache: dict[str, tuple]`
- `check_model()` 首先检查缓存，未命中或过期时执行检查并写入缓存
- 提供 `invalidate_model_cache(model_id)` 供下载完成后主动失效缓存
- `check_all()` 和 `check_mode()` 复用 `check_model()` 的缓存

---

### 3.7 【轻微】`model_server.py` 使用 `threading.Lock` 混合 `asyncio`

**问题描述：**  
`_ModelServer` 内部使用 `threading.Lock`，而调用它的 `inpainter.py` 运行在 FastAPI 的 asyncio 事件循环中（通过 `run_in_executor` 在线程池执行）。目前的调用链是：

```
FastAPI coroutine → run_in_executor → 线程池 → _ModelServer (threading.Lock)
```

这是安全的，但 `begin_inference()` 和 `end_inference()` 在线程中直接调用时，`threading.Lock` 可能与 asyncio 事件循环产生竞争（特别是在并发推理场景下）。

**建议：** 明确注释说明 `_ModelServer` 只能在线程池上下文中调用，或将 `begin_inference`/`end_inference` 改为 asyncio 版本并在路由层调用。

---

### 3.8 【轻微】`_cancel_check` 通过 `cfg` 字典传递，接口不规范 [✅ 已修复于 2026-05-13]

**问题描述：**  
下载取消检查函数通过修改 `cfg` 字典传入：

```python
cfg['_cancel_check'] = lambda: task._cancel_flag
```

然后在 `_download_rembg`、`_download_hf`、`_download_direct` 内部通过 `cfg.pop('_cancel_check', None)` 取出。这种将控制信号混入数据字典的方式使接口不清晰，且 `pop` 会修改调用方传入的字典（虽然已通过浅拷贝规避）。

**修复方案：** 将 `cancel_check` 作为独立参数传递给下载函数：
- 修改 4 个下载函数签名：`download_rembg(cfg, progress_cb=None, cancel_check=None)` 等
- `download_queue.py` 中构造 `cancel_check` 变量，通过 lambda 传递给下载函数
- 删除 `_extract_cancel_check()` 辅助函数
- 旧 SSE 接口传递 `cancel_check=None`（旧接口无取消机制）

---

### 3.9 ✅ 【轻微】`_stream_logs` 逐字符读取性能较低

**问题描述：**  
`model_server.py` 的 `_stream_logs` 方法为了正确处理 tqdm 的 `\r` 进度条，采用逐字符读取（`proc.stdout.read(1)`）。在 iopaint 输出大量日志（如模型加载时）时，频繁的单字符 read syscall 会带来不必要的 CPU 开销。

**建议：** 可以考虑按行读取，并在遇到 `\r` 时特殊处理，或使用非阻塞 IO 批量读取后再解析 `\r`/`\n`。

---

### 3.10 ✅ 【架构层面】双进程架构的隐患

**问题描述：**  
当前系统中 FastAPI 主进程（端口 8787）负责 API 路由，同时 `ModelServer` 会启动第二个 iopaint 子进程（端口 51821）用于扩散模型推理。这带来了：

1. **端口冲突风险**：两个端口都是可配置的，但默认值固定，在多用户或多实例场景下容易冲突
2. **进程孤儿风险**：若 FastAPI 进程意外崩溃，iopaint 子进程可能不被清理（虽然 `_stop_unlocked` 已尝试处理）
3. **日志碎片化**：iopaint 日志通过 `_stream_logs` 转发到父进程 stdout，与 FastAPI 日志混合，难以区分

**建议：**
- 注册 `atexit` + `signal.SIGTERM` 钩子确保 iopaint 进程在 FastAPI 退出时一定被清理
- 将 iopaint 日志输出到独立的日志文件（如 `logs/iopaint.log`）

---

## 3.11 ～ 3.22（第二轮审查 · 2026-05-29）

> 以下问题来自对 `app/config.py`、`websocket/progress.py`、`routers/inpaint.py`、`core/inpainter.py`、`core/model_server.py`、`core/model_registry.py`、`core/paths.py`、`app/main.py` 的深度阅读。

---

### 3.11 ✅ 【严重】WebSocket 进度广播无会话隔离 (`websocket/progress.py`)

**问题描述：**  
`ProgressManager` 维护全局 `self.connections: List[WebSocket]`，所有进度消息广播给**所有连接的客户端**，没有 `task_id` 或 `session_id` 维度的隔离：

```python
async def send_progress(self, percent, message):
    for ws in self.connections:           # ← 广播给所有人
        await ws.send_json(...)
```

**危害：**  
- 用户同时打开两个窗口时，两个窗口都收到彼此的进度更新
- 去水印与超分辨率同时运行时，两个操作的进度相互覆盖，前端进度条显示混乱
- `send_error()` 也广播，一个操作报错会打断无关操作的 UI 状态

**建议：**  
为每次操作生成唯一 `task_id`（UUID），`ProgressManager` 改为按 channel 维护连接映射 `dict[str, List[WebSocket]]`，路由层发起操作时传入 `task_id`，前端订阅时带上相同的 `task_id` 参数。

```python
# 改造后的接口示意
async def send_progress(self, task_id: str, percent: int, message: str): ...
async def connect(self, websocket: WebSocket, task_id: str): ...
```

---

### 3.12 ✅ 【严重】Config 加载非线程安全 (`app/config.py`)

**问题描述：**  
`_loaded` 标志和 `_cache` 字典没有任何锁保护，`reload()` 存在竞态窗口：

```python
def reload():
    global _loaded
    _loaded = False      # ← 窗口：另一线程此刻调用 get()，看到 _loaded=False
    _load()              # ← 两线程同时进入，发生双重加载

def _load():
    global _cache, _loaded
    # 读文件...
    _loaded = True
```

FastAPI 在多 worker 模式下（或通过 `run_in_executor` 的线程），并发调用 `reload()` 可能导致 `_cache` 被覆盖或读到半初始化状态。

**对比：** `model_registry.py` 的 `reload()` 已用 `threading.Lock` 做原子替换，`config.py` 漏掉了相同的保护。

**建议：**  
```python
_lock = threading.Lock()

def reload():
    data = _read_file()           # IO 在锁外完成
    with _lock:
        global _cache, _loaded
        _cache = data
        _loaded = True
```

---

### 3.13 ✅ 【中等】`_detect_iopaint_path()` 在两处重复定义

**问题描述：**  
`model_server.py` 和 `inpainter.py` 中各有一份完全相同的函数，用于查找 iopaint 可执行文件路径（检查 venv、PATH、pipx 等位置）。

**危害：**  
修改查找逻辑（例如新增 conda 环境支持）必须同时修改两处，极易遗漏造成行为不一致。

**建议：**  
将函数提取到 `core/iopaint_utils.py`，两处均从该模块导入：
```python
# core/iopaint_utils.py
def detect_iopaint_path() -> str | None: ...
```

---

### 3.14 ✅ 【中等】设备解析逻辑重复（`model_server.py` vs `inpainter.py`）

**问题描述：**  
`model_server.py` 的 `_resolve_effective_device()` 静态方法与 `inpainter.py` 的 `_get_effective_device()` 实例方法逻辑高度相似，均从注册表读取模型配置后进行 mps/cpu/cuda 降级判断。

**危害：**  
两份代码独立演化，降级规则容易产生分歧，新增设备类型时需要修改两处。

**建议：**  
与 3.13 一并提取到 `core/iopaint_utils.py`，提供单一的 `resolve_effective_device(model_id, requested_device)` 函数。

---

### 3.15 ✅ 【中等】`_make_progress_callback()` 在两个端点内重复定义 (`routers/inpaint.py`)

**问题描述：**  
`inpaint_with_rois` 和 `inpaint_with_mask` 两个端点内部各定义了一个完全相同的内部函数：

```python
def _make_progress_callback():
    def callback(percent: int, message: str):
        try:
            asyncio.run_coroutine_threadsafe(
                progress_manager.send_progress(percent, message),
                loop
            )
        except Exception as e:
            log_manager.error(f"进度上报失败: {e}", source="inpaint")
    return callback
```

**建议：**  
提取为模块级辅助函数，接受 `loop` 作为参数：
```python
def _make_progress_callback(loop: asyncio.AbstractEventLoop):
    def callback(percent: int, message: str): ...
    return callback
```

---

### 3.16 ✅ 已修复【中等】废弃的 `Inpainter` 导入未清理 (`routers/inpaint.py`)

**问题描述：**  
`inpaint_with_rois` 和 `inpaint_with_mask` 两个端点函数体顶部均有：

```python
from core.inpainter import Inpainter
```

但 `Inpainter` 类在两个函数中从未被实例化或使用（已被 `ModelExecutorFactory` 替代），是完全无效的导入。

**危害：**  
每次请求触发时都会执行该导入语句（Python 会缓存，但仍是误导性代码），增加代码理解成本，且掩盖了架构迁移的完成度。

**建议：**  
删除两处 `from core.inpainter import Inpainter` 导入语句。

---

### 3.17 ✅ 已修复【中等】`main.py` 的 NameError 保护永远不触发

**问题描述：**  
```python
from core.paths import PROJECT_ROOT, MODELS_DIR, apply_default_env_vars

try:
    PROJECT_ROOT
except NameError:                    # ← Python import 失败抛 ImportError，不是 NameError
    from app.config import PROJECT_ROOT, MODELS_DIR
```

Python `import` 语句失败时抛出 `ImportError`（或其子类 `ModuleNotFoundError`），不会抛出 `NameError`。因此这个 fallback 分支从不执行：若 `core.paths` 导入失败，程序直接以 `ImportError` 崩溃，而不是优雅降级。

**建议：**  
```python
try:
    from core.paths import PROJECT_ROOT, MODELS_DIR, apply_default_env_vars
except ImportError:
    from app.config import PROJECT_ROOT, MODELS_DIR
    def apply_default_env_vars(): pass
```

或者明确移除 fallback，在启动脚本中确保 `core.paths` 可导入，不再依赖此兜底逻辑。

---

### 3.18 ✅ 【中等】`model_registry.py` 在模块导入时同步加载 YAML

**问题描述：**  
```python
# 模块级代码，任何 import model_registry 均立即执行
MODELS, MODE_GROUPS = _load()
```

`_load()` 会打开文件、解析 YAML，这些 I/O 操作发生在 Python import 阶段。

**危害：**  
- YAML 文件损坏时，整个 import 链崩溃，错误信息难以追踪（堆栈指向 import 而非业务调用）  
- 单元测试中 `import model_registry` 就需要 YAML 文件存在，增加测试环境依赖  
- 大型 YAML（未来模型数量增多）会拖慢应用启动时间

**建议：**  
改为显式初始化，或用惰性加载：
```python
_MODELS: list | None = None

def get_models() -> list:
    global _MODELS
    if _MODELS is None:
        _MODELS, _ = _load()
    return _MODELS
```

---

### 3.19 【性能】单 Worker 线程池序列化所有去水印操作 (`routers/inpaint.py`)

**问题描述：**  
```python
# 模块级单例，全局唯一
executor = ThreadPoolExecutor(max_workers=1)
```

所有去水印请求（`/inpaint` 和 `/inpaint/with-mask`）共用同一个只有 1 个 worker 的线程池，操作严格串行执行。

**危害：**  
- 并发请求排队等待，响应延迟与队列深度线性增长  
- 没有队列深度上限，高并发时内存无限增长（所有请求的图像 numpy 数组都驻留在内存中等待执行）  
- 其他路由（`upscale.py` 等）各有独立线程池，没有跨路由的全局并发控制

**建议：**  
- 在 `lifespan` 中创建全局共享的 `ThreadPoolExecutor`，按 CPU/GPU 核心数设置合理 `max_workers`  
- 或引入请求队列（类似现有的 `DownloadQueue`）配合信号量限制并发数  
- 至少增加队列满时的拒绝策略（返回 429），防止内存耗尽

---

### 3.20 【性能】Base64 大图像内存放大 1.33× 且多份并存

**问题描述：**  
图像通过 Base64 字符串在 IPC 和 HTTP API 间传输，Base64 编码带来约 33% 的体积膨胀。更严重的是，在请求处理期间内存中同时存在多份副本：

1. Base64 字符串（原始大小 × 1.33）  
2. 解码后的字节数组（原始大小）  
3. `numpy` 数组（RGB，原始大小 × 3）  

一张 10 MB 的图像在单次请求中峰值内存可超过 50 MB。

**建议：**  
- 对超过阈值（如 5 MB）的图像改用**文件路径传输**：前端将图像写入临时文件，后端读取文件路径  
- 或使用 `multipart/form-data` 上传，FastAPI 原生支持，避免 Base64 编码开销  
- 短期：在路由层添加图像尺寸限制，防止超大图像触发 OOM

---

### 3.21 ✅ 已修复【轻微】`apply_default_env_vars()` 覆盖范围不完整 (`core/paths.py`)

**问题描述：**  
`paths.py` 定义了 6 个路径常量，但 `apply_default_env_vars()` 只设置了其中 2 个：

```python
def apply_default_env_vars():
    _set_default("HF_HOME", str(HF_HOME))
    _set_default("TORCH_HOME", str(TORCH_HOME))
    # ← GFPGAN_HOME、REALESRGAN_HOME、RESTORMER_HOME、U2NET_HOME 未设置
```

`GFPGAN_HOME` 等路径在 `paths.py` 中有明确定义，但对应的第三方库（如 `gfpgan`、`rembg`）仍会使用自己的默认值（而不是 `paths.py` 中定义的路径），导致路径设置"形同虚设"。

**建议：**  
补全所有路径常量对应的环境变量设置：
```python
def apply_default_env_vars():
    _set_default("HF_HOME",         str(HF_HOME))
    _set_default("TORCH_HOME",      str(TORCH_HOME))
    _set_default("GFPGAN_HOME",     str(GFPGAN_HOME))
    _set_default("REALESRGAN_HOME", str(REALESRGAN_HOME))
    _set_default("RESTORMER_HOME",  str(RESTORMER_HOME))
    _set_default("U2NET_HOME",      str(U2NET_HOME))
```

---

### 3.22 ✅ 【轻微】`MODELS_DIR` 与 `MODELS_CACHE_DIR` 命名含义混淆 (`core/paths.py`)

**问题描述：**  
```python
MODELS_DIR       = PROJECT_ROOT / "models"      # 项目内 models/（向后兼容）
MODELS_CACHE_DIR = CACHE_ROOT / "models"        # ~/.cache/models（实际下载位置）
```

两个变量名高度相似，但一个指向项目源码目录，一个指向用户缓存目录，极易混用。

**危害：**  
在代码中错误使用 `MODELS_DIR` 会导致模型被写入项目目录（可能被 git 跟踪），或读取时找不到实际下载的模型。

**建议：**  
- 明确在代码注释中标记 `MODELS_DIR` 为"仅向后兼容，新代码不应使用"  
- 统一新代码使用 `MODELS_CACHE_DIR`  
- 在 README 或此文档中记录何时可以安全移除 `MODELS_DIR`

---

## 4. 优化建议（按优先级）

### P0 · 必须修复

| # | 问题 | 影响 | 改动量 | 状态 |
|---|------|------|--------|------|
| 1 | `_subscribe_continue` 无限递归 → 栈溢出 | 大模型下载时崩溃 | 小（改循环） | ✅ 已修复 |
| 2 | `core` 层反向依赖 `app.routers` | 架构违规，测试困难 | 中（迁移代码） | ✅ 已修复 |
| 3.11 | WebSocket 进度无会话隔离 | 并发操作进度互相污染 | 中（引入 task_id channel） | ✅ 已修复 |
| 3.12 | Config 加载非线程安全 | 并发 reload 导致状态错误 | 小（加 Lock） | ✅ 已修复 |

### P1 · 建议尽快改进

| # | 问题 | 影响 | 改动量 | 状态 |
|---|------|------|--------|------|
| 3 | `_fmt_speed`/`_fmt_size` 重复定义 | 代码冗余 | 小（提取到 utils） | ✅ 已修复 |
| 4 | 废弃 API 内部实现重复 | 维护成本高 | 中（重定向实现） | ✅ 已修复 |
| 5 | `ModelChecker` 无缓存全量扫描 | UI 响应变慢 | 小（加 TTL 缓存） | ✅ 已修复 |
| 6 | `_cancel_check` 混入 cfg 字典 | 接口不清晰 | 小（改独立参数） | ✅ 已修复 |
| 3.13 | `_detect_iopaint_path()` 两处重复 | 修改需同步两处 | 小（提取到 iopaint_utils） | ✅ 已修复 |
| 3.14 | 设备解析逻辑重复 | 降级规则可能不一致 | 小（同上提取） | ✅ 已修复 |
| 3.15 | `_make_progress_callback()` 两处重复 | 代码冗余 | 小（提取为模块函数） | ✅ 已修复 |
| 3.16 | 废弃 `Inpainter` 导入未清理 | 误导代码读者 | 极小（删除两行） | ✅ 已修复 |
| 3.17 | `main.py` NameError 保护错误异常类型 | fallback 永远不触发 | 极小（改 ImportError） | ✅ 已修复 |
| 3.21 | `apply_default_env_vars()` 不完整 | 路径配置形同虚设 | 极小（补全 4 行） | ✅ 已修复 |

### P2 · 长期优化

| # | 问题 | 影响 | 改动量 | 状态 |
|---|------|------|--------|------|
| 7 | 执行器缺乏统一基类 | 扩展新执行器成本高 | 中（新增 base_executor.py） | ✅ 已修复 |
| 8 | `dev.sh` 与 `dev.js` 冗余 | 维护成本 | 小（废弃或明确分工） | ✅ 已修复 |
| 9 | `_stream_logs` 逐字符读取 | CPU 轻微浪费 | 小（改批量读取） | ✅ 已修复 |
| 10 | iopaint 子进程退出后不清理 | 资源泄漏风险 | 小（注册 atexit） | ✅ 已修复 |
| 11 | iopaint 日志混入主进程 stdout | 可观测性差 | 小（独立日志文件） | ✅ 已修复 |
| 3.18 | `model_registry` 导入时同步 I/O | 测试依赖重、启动慢 | 小（改惰性加载） | ✅ 已修复 |
| 3.19 | 单 Worker 线程池序列化所有推理 | 并发吞吐量低 | 中（全局池 + 队列） | ✅ 已修复 |
| 3.20 | Base64 大图像内存放大 1.33× | OOM 风险 | 中（改文件路径传输） | ✅ 已修复 |
| 3.22 | `MODELS_DIR` / `MODELS_CACHE_DIR` 命名混淆 | 易混用导致路径错误 | 极小（注释 + 文档） | ✅ 已修复 |

---

## 5. 技术债务清单

以下为当前代码库中已知但尚未处理的技术债务，建议跟踪管理：

### TD-001：旧版 SSE 下载接口（models.py）✅
- **位置：** `GET /api/models/download`、`GET /api/models/download/{model_id}`
- **状态：** ✅ 已删除（`_download_generator`、`_download_single_generator` 及两个废弃 GET 端点均已清除，`models.py` 现仅保留 POST 下载队列接口）

### TD-002：`_DIFFUSION_PREFIXES` 前缀匹配回退逻辑（model_server.py）✅
- **位置：** `model_server.py`
- **状态：** ✅ 已删除（已确认 `models.yaml` 中所有扩散模型均已设置 `iopaint_mode: server`；移除了 `_DIFFUSION_PREFIXES` 常量及 `is_diffusion_model()` 中的 `startswith` 回退分支）

### TD-003：`asyncio.get_event_loop()` 即将废弃（download_queue.py）✅
- **位置：** `download_queue.py`
- **状态：** ✅ 已修复（已使用 `asyncio.get_running_loop()`）

### TD-004：`hf_cache` 路径在 `download_hf` 中独立计算
- **位置：** `core/downloaders.py`（原 `models.py` 第 515 行，已迁移至 `downloaders.py`）
- **状态：** ✅ 已修复（`downloaders.py` 已导入 `HF_HOME as _HF_HOME`；将 `hf_cache = os.environ.get("HF_HOME", ...)` 替换为 `hf_cache = str(_HF_HOME)`，与 `paths.py` 保持一致）

### TD-005：`iopaint` 依赖冲突需要 `--no-deps` 绕过
- **位置：** `requirements.txt`、`scripts/install_deps.py`、`scripts/post_install.py`
- **状态：** ⏳ 等待上游（`iopaint==1.6.0` 固定依赖 `diffusers==0.27.2`，但项目需要 `diffusers>=0.32.0`，现有 `--no-deps` + 手动补丁方案可运行）
- **后续：** 评估是否 fork `iopaint` 并移除其 `diffusers` 版本固定，或等待上游 `iopaint` 升级

### TD-006：Docker 部署无输出卷，处理结果不持久✅
- **位置：** `docker-compose.yml`
- **状态：** ✅ 已修复（CPU 和 GPU 服务均已添加 `./output:/app/output` 卷挂载；GPU 服务补全了 `healthcheck` / `restart` 配置）

---

## 6. 总结

HiImage 的架构设计整体合理，核心设计理念（配置驱动、按需保活、队列调度）都经过了认真思考，代码质量在 AI 桌面应用领域属于中上水平。

**已修复问题（2026-05-13）：**
- ✅ **P0-1**：`_subscribe_continue` 无限递归 → 改为单一 `while` 循环
- ✅ **P0-2**：`core` 层反向依赖 `app.routers` → 下载函数迁移至 `core/downloaders.py`
- ✅ **P1-3**：`_fmt_speed`/`_fmt_size` 重复定义 → 提取到 `core/utils.py`
- ✅ **P1-4**：废弃 API 内部实现重复 → 统一调用 `core/downloaders.py`
- ✅ **P1-5**：`ModelChecker` 无缓存全量扫描 → 添加 TTL 缓存（5 秒）
- ✅ **P1-6**：`_cancel_check` 混入 cfg 字典 → 改为独立参数传递

**第二轮审查新增待修复问题（2026-05-29）：**
- ✅ **P0-3.11**：WebSocket 进度无会话隔离（影响并发操作正确性）
- ✅ **P0-3.12**：Config 加载非线程安全（并发 reload 存在竞态）
- ✅ **P1-3.13/3.14**：`_detect_iopaint_path` 和设备解析逻辑两处重复
- ✅ **P1-3.15/3.16**：`_make_progress_callback` 重复 + 废弃 `Inpainter` 导入
- ✅ **P1-3.17**：`main.py` fallback 使用错误异常类型（永远不触发）
- ✅ **P1-3.21**：`apply_default_env_vars()` 未覆盖 4 个路径变量
- ✅ **P2-9**：`_stream_logs` 逐字符读取（`model_server.py` + `inpainter.py` 均已改批量读取）
- ✅ **P2-10**：iopaint 子进程退出后不清理（已注册 `atexit` + `SIGTERM` 钩子）
- ✅ **P2-11**：iopaint 日志混入主进程 stdout（已写入独立日志文件）
- ✅ **P2-3.18**：`model_registry` 导入时同步 I/O（已改惰性加载 + 双检锁）
- ✅ **P2-3.22**：`MODELS_DIR` / `MODELS_CACHE_DIR` 命名混淆（已添加废弃注释）
- ✅ **P2-3.19**：单 Worker 线程池序列化所有推理操作
- ✅ **P2-3.20**：Base64 大图像内存放大问题（已在所有路由入口添加 `check_image_size` 拦截，默认上限 30 MB 解码后大小）
- ✅ **P2-7**：执行器缺乏统一抽象基类（已新增 `base_executor.py`，`BaseExecutor(ABC)` 轻量接口）
- ✅ **P2-8**：`dev.sh` 与 `dev.js` 功能冗余（已在 `dev.sh` 顶部添加 LEGACY 说明与功能对比注释）

P0 问题优先处理；P1 中改动量为"极小"的条目（3.16、3.17、3.21）可以集中在一次 cleanup commit 中完成。

---

**文档版本：** 1.2  
**更新日期：** 2026-05-29  
**分析范围（第一轮）：** `SYSTEM_ARCHITECTURE.md`、`SCRIPTS_REFERENCE.md`、`backend/core/download_queue.py`、`backend/core/model_server.py`、`backend/app/routers/models.py`  
**分析范围（第二轮）：** `backend/app/config.py`、`backend/app/main.py`、`backend/app/websocket/progress.py`、`backend/app/routers/inpaint.py`、`backend/core/inpainter.py`、`backend/core/model_server.py`、`backend/core/model_registry.py`、`backend/core/paths.py`
