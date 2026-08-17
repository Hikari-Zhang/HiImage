# HiImage 系统设计文档

> **项目**: HiImage - AI 驱动的图像处理桌面工具  
> **版本**: 2.0.0  
> **技术栈**: Electron + React + FastAPI + PyTorch  
> **更新时间**: 2026-05-26

---

## 目录

1. [系统概述](#1-系统概述)
2. [架构设计](#2-架构设计)
3. [后端设计](#3-后端设计)
4. [前端设计](#4-前端设计)
5. [数据流设计](#5-数据流设计)
6. [核心功能实现](#6-核心功能实现)
7. [状态管理设计](#7-状态管理设计)
8. [API 设计](#8-api-设计)
9. [模型管理系统](#9-模型管理系统)
10. [部署与打包](#10-部署与打包)
11. [扩展性与维护](#11-扩展性与维护)

---

## 1. 系统概述

### 1.1 产品定位

HiImage 是一款基于 Electron + React + FastAPI 构建的 **AI 驱动图像处理桌面工具**，提供去水印、超分辨率、智能合成等能力。

### 1.2 核心功能

| 功能模块 | 说明 |
|---------|------|
| **去水印** | 框选水印区域，AI 修复，支持 30+ 模型（快速模型无需下载，扩散模型首次自动下载） |
| **抠图** | BiRefNet / rembg 抠图，画笔微调与边缘精修 |
| **超分辨率** | Real-ESRGAN 2x/4x 放大，支持通用/精细化/动漫三类模型，**支持批量处理** |
| **智能合成** | 7 种模式：换背景 / 换装 / 换脸 / 虚拟试穿 / 精准替换 / 智能定位 / 自由编辑 |
| **跨页流水线** | 去水印 / 超分 / 合成共享工作图；选区与画笔按页隔离 |
| **批量处理** | 一次性导入多张图片，逐张处理，实时显示进度，支持 Electron 直接读取本地文件 |

### 1.3 技术选型

| 层级 | 技术栈 | 说明 |
|------|---------|------|
| 前端框架 | Electron + React 18 + TypeScript | 跨平台桌面应用 |
| 构建工具 | Vite | 快速热更新 |
| 状态管理 | Zustand | 轻量级、TypeScript 友好 |
| UI 样式 | Tailwind CSS | 暗色主题 + Lucide React 图标 |
| 画布 | Konva.js | ROI 区域绘制 |
| 后端框架 | FastAPI + Uvicorn | 高性能异步 API |
| AI 推理 | IOPaint / Real-ESRGAN / rembg / GFPGAN | 多模型支持 |
| 打包工具 | electron-builder | 跨平台打包 |

---

## 2. 架构设计

### 2.1 整体架构图

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           Electron 应用                              │
│                                                                     │
│  ┌─────────────────────┐      ┌─────────────────────────────┐    │
│  │   主进程 (Main)      │      │   渲染进程 (Renderer)       │    │
│  │                      │      │                             │    │
│  │ - 窗口管理           │◄─────┤ - React UI               │    │
│  │ - 后端生命周期管理  │ IPC  │ - Zustand 状态管理       │    │
│  │ - 文件对话框         │      │ - Konva 画布             │    │
│  │ - 文件系统访问       │      │ - 页面组件                │    │
│  └──────────┬──────────┘      └─────────────┬───────────────┘    │
│              │                            ▲               │
│              │                            │               │
│              │                   Preload (IPC 桥接)   │
│              │                            │               │
│              ▼                            │               │
│  ┌─────────────────────┐              │               │
│  │   Python 后端        │              │               │
│  │   (FastAPI)          │──────────────┘               │
│  │                      │    HTTP/WebSocket               │
│  │ - 模型推理           │                                  │
│  │ - 模型下载管理       │──────────────────────────────────┘
│  │ - AI 核心算法        │    Base64 图像传输
│  └─────────────────────┘
│                                                                     │
└─────────────────────────────────────────────────────────────────────────┘
```

### 2.2 进程模型

```
┌────────────────┐
│  Electron Main  │
│    Process      │
│                │
│  BackendMgr   │← 管理 Python 后端生命周期
│  IPC Handlers  │← 处理渲染进程请求
└───────┬────────┘
         │ spawn/kill
         ▼
┌────────────────┐
│  Python        │
│  Backend       │
│  (FastAPI)    │
│                │
│  Uvicorn Server│← 端口 8787
└───────┬────────┘
         │ HTTP API
         ▼
┌────────────────┐
│  Electron      │
│  Renderer      │
│  (React)      │
│                │
│  Zustand Store│← 状态管理
│  Pages/Components│
└────────────────┘
```

### 2.3 目录结构

```
HiImage/
├── backend/                  # Python FastAPI 后端
│   ├── app/
│   │   ├── main.py          # FastAPI 入口
│   │   ├── routers/         # API 路由（inpaint / upscale / synthesis / settings / logs / system / models）
│   │   ├── websocket/      # 进度推送 WebSocket
│   │   ├── config.py        # 配置读取
│   │   └── logging_manager.py # 日志管理
│   ├── core/               # AI 推理核心
│   │   ├── model_registry.py # 模型注册表（models.yaml 驱动）
│   │   ├── model_checker.py # 模型完整性检查
│   │   ├── download_queue.py # 下载队列调度器
│   │   ├── downloaders.py   # 各种下载器（HF / rembg / direct）
│   │   ├── upscaler.py      # Real-ESRGAN 超分辨率
│   │   ├── inpainter.py     # IOPaint 去水印
│   │   ├── synthesizer.py    # 智能合成核心
│   │   ├── model_executor.py # 模型执行器工厂
│   │   └── ...             # 其他执行器
│   └── tests/
├── frontend/                # Electron + React 前端
│   ├── src/
│   │   ├── main/           # Electron 主进程
│   │   │   ├── index.ts          # 主进程入口
│   │   │   └── backend-manager.ts # 后端进程管理
│   │   ├── preload/        # IPC 桥接
│   │   └── renderer/       # React 渲染进程
│   │       ├── App.tsx       # 主应用组件
│   │       ├── pages/        # 页面组件
│   │       ├── components/   # 复用组件
│   │       ├── stores/       # Zustand 状态管理
│   │       ├── hooks/        # 自定义 Hooks
│   │       ├── constants.ts   # 常量定义
│   │       └── types/        # TypeScript 类型
│   └── electron.vite.config.ts
├── config/settings.json     # 用户配置（不在 git 中）
├── models/                 # AI 模型文件（自动下载，.gitignore）
├── output/                 # 处理结果输出目录
└── docs/                  # 文档
```

---

## 3. 后端设计

### 3.1 FastAPI 应用结构

**入口**: `backend/app/main.py`

```python
app = FastAPI(
    title="HiImage API",
    version="2.0.0",
    lifespan=lifespan,  # 使用 lifespan 管理生命周期
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
```

### 3.2 生命周期管理

**lifespan 事件**:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup:
    #   1. 初始化日志系统
    #   2. 设置 HuggingFace 端点和 Token
    #   3. 应用默认环境变量
    #   4. 为 IOPaint server 模式模型补全 hub/ 软链接
    
    yield
    
    # Shutdown:
    #   1. 停止 IOPaint Server（如果在运行）
```

### 3.3 API 路由设计

#### 3.3.1 系统路由 (`/api/system`)

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/health` | GET | 健康检查 |
| `/api/devices` | GET | 检测可用计算设备（CPU/MPS/CUDA） |
| `/api/models/inpaint` | GET | 获取去水印模型列表（旧接口） |
| `/api/models/upscale` | GET | 获取超分辨率模型列表（旧接口） |

**设备检测逻辑**:

```python
@router.get("/devices")
async def available_devices():
    # 1. 检测 MPS 可用性（Apple Silicon）
    #    - 检查 torch.backends.mps.is_available()
    #    - 不可用时返回原因（需要 Apple Silicon + macOS 12.3+）
    
    # 2. 检测 CUDA 可用性（NVIDIA GPU）
    #    - 检查 torch.cuda.is_available()
    #    - 区分「PyTorch CPU-only 版」vs「驱动/硬件问题」
    
    return {
        "devices": [
            {"id": "mps", "label": "MPS", "available": mps_available, "reason": mps_reason},
            {"id": "cpu", "label": "CPU", "available": True, "reason": ""},
            {"id": "cuda", "label": "CUDA", "available": cuda_available, ...},
        ]
    }
```

#### 3.3.2 去水印路由 (`/api/inpaint`)

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/models/inpaint` | GET | 返回所有去水印模型（按 display_group 分组） |
| `/api/detect` | POST | 自动检测水印区域 |
| `/api/inpaint` | POST | 使用 ROI 列表去除水印 |
| `/api/inpaint/with-mask` | POST | 使用自定义掩码去除水印 |

**请求模型**:

```python
class InpaintRequest(BaseModel):
    image: str              # Base64 encoded PNG/JPG
    rois: List[List[int]]  # [[x1, y1, x2, y2], ...]
    model: str = default_factory=lambda: get_default_model("watermark_removal")
    device: str = "mps"
    dilation: int = 10
    disable_nsfw: bool = False
```

**处理流程**:

```python
@router.post("/inpaint")
async def inpaint_with_rois(req: InpaintRequest):
    # 1. 解码 Base64 图像 → numpy array
    image = decode_image(req.image)
    
    # 2. 检查并确保模型已下载（支持进度推送）
    success, error_msg = await _ensure_model_ready_with_progress(req.model, "水印去除")
    if not success:
        return JSONResponse(status_code=422, content={"detail": error_msg})
    
    # 3. 使用模型执行器工厂创建执行器
    executor = ModelExecutorFactory.create_executor(model_config, req.device)
    
    # 4. 在线程池中执行推理（避免阻塞事件循环）
    result = await loop.run_in_executor(executor, _process)
    
    # 5. 编码结果图像 → Base64
    return {"image": encode_image(result)}
```

#### 3.3.3 超分辨率路由 (`/api/upscale`)

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/models/upscale` | GET | 返回所有超分辨率模型（按 display_group 分组） |
| `/api/upscale` | POST | 超分辨率处理（单张） |
| `/api/upscale/batch` | POST | 批量超分辨率处理 |

**批量处理优化**:

```python
@router.post("/upscale/batch")
async def upscale_image_batch(req: BatchUpscaleRequest):
    # 1. 循环外创建 Upscaler 实例，批量复用
    shared_upscaler = Upscaler(
        model_name=req.model,
        device=req.device,
        outscale=req.outscale,
        tile=tile,
    )
    
    # 2. 预加载模型（循环前就加载好，避免每张图都触发加载）
    shared_upscaler._ensure_model_loaded()
    
    # 3. 循环处理每张图片，复用 shared_upscaler
    for idx, img_data in enumerate(req.images):
        # 读取图片（支持文件路径或 Base64）
        # 处理：复用 shared_upscaler
        # 保存或编码结果
        
    # 4. 完成，释放模型和显存
    del shared_upscaler
    if req.device == 'cuda':
        torch.cuda.empty_cache()
```

#### 3.3.4 智能合成路由 (`/api/synthesis`)

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/synthesis/modes` | GET | 获取所有合成模式（含描述、模型、参考图需求） |
| `/api/synthesis/models` | GET | 获取所有可用模型（带功能标签与描述） |
| `/api/synthesis/run` | POST | 执行合成处理 |

**合成模式**:

```python
SYNTHESIS_MODE_GROUPS = [
    {"id": "background_replace", "name": "换背景", "requires_reference": True, ...},
    {"id": "outfit_swap", "name": "换装模拟", ...},
    {"id": "face_swap", "name": "换脸模拟", ...},
    {"id": "virtual_tryon", "name": "虚拟试穿", ...},
    {"id": "prompt_inpaint", "name": "精准替换", ...},
    {"id": "auto_segment_edit", "name": "智能定位编辑", ...},
    {"id": "instruction_edit", "name": "自由编辑", ...},
]
```

#### 3.3.5 模型管理路由 (`/api/models`)

**新下载队列 API（推荐）**:

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/models/health` | GET | 检测所有注册模型的完整性 |
| `/api/models/health/{model_id}` | GET | 检测单个模型的完整性 |
| `/api/models/list` | GET | 返回所有注册模型的元数据（含当前状态） |
| `/api/models/download/{model_id}` | POST | 提交单模型下载任务（返回任务状态） |
| `/api/models/download/bulk` | POST | 批量提交（一键下载），返回任务列表 |
| `/api/models/download/{model_id}` | DELETE | 取消下载任务 |
| `/api/models/subscribe/{model_id}` | GET | SSE 订阅单个模型的实时状态变更 |
| `/api/models/queue` | GET | 查询当前队列中所有活跃任务 |

**SSE 订阅示例**:

```python
@router.get("/subscribe/{model_id}")
async def subscribe_model(model_id: str, request: Request):
    async def _generate():
        async for event in queue.subscribe(model_id):
            if await request.is_disconnected():
                break
            if event.get("heartbeat"):
                yield ": heartbeat\n\n"
            else:
                yield _sse("status", event)
    
    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
```

### 3.4 AI 推理核心

#### 3.4.1 模型注册表 (`core/model_registry.py`)

**设计理念**: 所有模型与模式配置均在 `core/models.yaml` 中维护，代码只负责读取和解析。

```python
# models.yaml 结构
models:
  - id: "wm_lama"
    name: "LaMa"
    provider: "IOPaint"
    iopaint_mode: "cli"
    tags: ["watermark_removal"]
    description: "快速本地模型，无需下载"
    badge: "快速"
    ...

mode_groups:
  - id: "watermark_removal"
    name: "去水印"
    default_model: "wm_lama"
    ...

# Python 代码动态加载
MODELS, MODE_GROUPS = _load()
MODEL_BY_ID: dict[str, dict] = {m["id"]: m for m in MODELS}
MODE_BY_ID:  dict[str, dict] = {g["id"]: g for g in MODE_GROUPS}
```

**工具函数**:

```python
def get_default_model(mode_id: str) -> str:
    """返回指定模式的默认模型 ID"""

def get_models_for_mode(mode_id: str) -> list[dict]:
    """返回指定模式下的所有模型"""

def get_model(model_id: str) -> dict:
    """按 ID 获取模型配置"""

def get_mode(mode_id: str) -> dict:
    """按 ID 获取模式配置"""
```

#### 3.4.2 模型执行器框架

**工厂模式**: `core/model_executor.py`

```python
class ModelExecutorFactory:
    @staticmethod
    def create_executor(model_config: dict, device: str):
        provider = model_config.get("provider")
        
        if provider == "IOPaint":
            return IOPaintExecutor(model_config, device)
        elif provider == "diffusers":
            return DiffusersExecutor(model_config, device)
        elif provider == "rembg":
            return RembgExecutor(model_config, device)
        elif provider == "facexlib":
            return FacexlibExecutor(model_config, device)
        elif provider == "realesrgan":
            return RealESRGANExecutor(model_config, device)
        elif provider == "HiImage":
            return HiImageExecutor(model_config, device)
        else:
            raise ValueError(f"未知 provider: {provider}")
```

**统一接口**:

```python
class BaseExecutor(ABC):
    @abstractmethod
    def execute(self, image, **kwargs):
        """执行推理"""
        
    @abstractmethod
    def supports_mask(self) -> bool:
        """是否支持掩码输入"""
        
    def set_progress_callback(self, callback):
        """设置进度回调"""
```

#### 3.4.3 下载队列调度器 (`core/download_queue.py`)

**设计目标**: 统一管理所有模型下载任务，支持并发控制和排队等待。

**核心类**:

```python
@dataclass
class DownloadTask:
    """单个下载任务的状态快照"""
    model_id: str
    model_name: str
    status: str = "queued"     # queued / downloading / done / error / cancelled
    position: int = 0           # queued 时的排队序号（1-based）
    message: str = "等待下载..."
    speed: str = ""
    downloaded: str = ""
    total_size: str = ""
    created_at: float
    updated_at: float
    
    # 内部控制（不序列化给前端）
    _cancel_flag: bool = False
    _future: Any = None
    
    def to_dict(self) -> dict:
        """转为字典（供 SSE 推送）"""

class DownloadQueue:
    """全局下载队列（单例）"""
    
    def __init__(self):
        self._active: Dict[str, DownloadTask] = {}    # 活跃任务（downloading）
        self._pending: deque[str] = deque()          # 等待队列（queued）
        self._tasks: Dict[str, DownloadTask] = {}     # 所有任务
        self._subscribers: Dict[str, List[asyncio.Queue]] = {}  # SSE 订阅者
        self._slot_event: Optional[asyncio.Event] = None
        self._scheduler_started = False
    
    def submit(self, model_id: str) -> DownloadTask:
        """提交单个模型下载任务"""
        # 1. 去重：已有活跃任务则直接返回
        # 2. 有空槽 → 立即开始
        # 3. 无空槽 → 进入 pending 队列排队
        
    def cancel(self, model_id: str) -> bool:
        """取消下载任务"""
        # 1. queued → 从 pending 移除
        # 2. downloading → 设置取消标志，下载线程会检查
        
    async def subscribe(self, model_id: str) -> AsyncIterator[dict]:
        """SSE 订阅：持续推送指定 model_id 的状态变更事件"""
        
    def _scheduler(self) -> None:
        """后台调度协程：持续检查是否有空槽和等待任务"""
        
    async def _run_download(self, model_id: str) -> None:
        """在线程池中执行下载，通过 asyncio.Queue 接收进度"""
```

**并发控制**:

```python
@property
def max_concurrent(self) -> int:
    """从配置中读取最大并发下载数（默认 3）"""
    from app.config import get as get_config
    return int(get_config("download.max_concurrent", 3))
```

---

## 4. 前端设计

### 4.1 Electron 主进程

**入口**: `frontend/src/main/index.ts`

**职责**:
1. 窗口管理（创建、显示、关闭）
2. 后端生命周期管理（启动、停止、配置更新）
3. IPC 通信处理（文件对话框、后端 URL、文件读写等）

**后端管理器**: `frontend/src/main/backend-manager.ts`

```typescript
export class BackendManager {
  private process: ChildProcess | null = null
  private localPort: number = 8787
  private isReady: boolean = false
  private config: BackendConfig = {
    mode: 'local',
    remoteHost: '127.0.0.1',
    remotePort: 8787,
  }
  
  private queue: Promise<void> = Promise.resolve()
  private abortController: AbortController = new AbortController()
  
  // 串行队列：所有操作都排进这条链，保证一次只有一个操作在执行
  private enqueue(task: () => Promise<void>): Promise<void> {
    const result = this.queue.then(() => task())
    this.queue = result.catch(() => {})
    return result
  }
  
  // Public API
  async start(): Promise<void> {
    return this.enqueue(() => this._start())
  }
  
  async stop(): Promise<void> {
    return this.enqueue(() => this._stop())
  }
  
  async forceQuit(): Promise<void> {
    // 强制退出：跳过队列、取消所有等待中的操作、直接 kill 进程
  }
  
  async updateConfig(newConfig: Partial<BackendConfig>): Promise<void> {
    return this.enqueue(() => this._updateConfig(newConfig))
  }
  
  getBaseURL(): string {
    if (this.config.mode === 'remote') {
      return `http://${this.config.remoteHost}:${this.config.remotePort}`
    }
    return `http://127.0.0.1:${this.localPort}`
  }
}
```

**进程管理**:

- **启动**: 使用 `spawn` 创建 Python 进程，设置 `detached: true`（macOS/Linux）以便 kill 整个进程组
- **停止**:
  - Windows: 使用 `taskkill /F /T /PID <pid>` 杀掉整个进程树
  - macOS/Linux: 先 `SIGTERM` 优雅终止，等待 5 秒；如果还存活，再 `SIGKILL` 强制终止
- **端口清理**: 启动前先清理占用指定端口的所有进程

**IPC 处理**:

```typescript
function setupIPC(): void {
  // File dialog
  ipcMain.handle('dialog:openFile', async () => { ... })
  ipcMain.handle('dialog:openFiles', async () => { ... })
  ipcMain.handle('dialog:openDirectory', async () => { ... })
  ipcMain.handle('dialog:saveFile', async () => { ... })
  
  // Backend URL
  ipcMain.handle('backend:getURL', () => {
    return backendManager.getBaseURL()
  })
  
  // Connection config
  ipcMain.handle('backend:getConfig', () => {
    return backendManager.getConfig()
  })
  
  ipcMain.handle('backend:updateConfig', async (_, config) => {
    await backendManager.updateConfig(config)
    return backendManager.getBaseURL()
  })
  
  // File write/read
  ipcMain.handle('file:save', async (_, filePath, base64Data) => { ... })
  ipcMain.handle('file:read', async (_, filePath) => { ... })
  
  // Window controls
  ipcMain.on('window:minimize', () => { ... })
  ipcMain.on('window:maximize', () => { ... })
  ipcMain.on('window:close', () => { ... })
}
```

### 4.2 渲染进程

**入口**: `frontend/src/renderer/main.tsx`

**主组件**: `frontend/src/renderer/App.tsx`

```tsx
function App() {
  const { isConnected } = useBackendStore()
  const { start, cancelDownload, resume } = useDownloadManager()
  
  useEffect(() => {
    // 1. 检查后端连接
    checkHealth()
    
    // 2. 恢复进行中的 SSE 连接
    resume()
  }, [])
  
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Layout />}>
          <Route index element={<Navigate to="/watermark" />} />
          {/* 实际由 MainLayout 常驻挂载，此处仅注册路径 */}
          <Route path="/watermark" element={null} />
          <Route path="/cutout" element={null} />
          <Route path="/upscale" element={null} />
          <Route path="/synthesis" element={null} />
          <Route path="/settings" element={null} />
          <Route path="/logs" element={null} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
```

### 4.3 页面组件

#### 4.3.1 去水印页面 (`pages/WatermarkRemoval.tsx`)

**功能**:
1. 打开图片（拖拽或文件对话框）
2. 在画布上框选水印区域（支持多个 ROI，可缩放/平移画布）
3. 选择模型（快速模型推荐 LaMa；文字水印用 AnyText）
4. 点击「开始处理」，实时查看进度
5. 对比原图与结果，保存为 PNG/JPG

**核心状态**:

```typescript
// useImageStore
const {
  sourceImage, resultImage, rois, selectedROIs,
  setSourceImage, setResultImage, addROI, removeROI, clearROIs,
  ...
} = useImageStore()

// useSettingsStore
const {
  device, inpaintModel, defaultDilation, sensitivity, disableNsfw,
  setDevice, setInpaintModel, setDilation, setSensitivity, setDisableNsfw,
  ...
} = useSettingsStore()

// useProcessStore
const {
  isProcessing, progress, statusMessage,
  startProcess, updateProgress, finishProcess, setError, reset,
} = useProcessStore()

// useBackendAPI
const { detectWatermark, inpaint, inpaintWithMask } = useBackendAPI()
```

**WebSocket 进度推送**:

```typescript
useEffect(() => {
  const connectProgressWS = async () => {
    const backendURL = window.electronAPI
      ? await window.electronAPI.getBackendURL()
      : 'http://127.0.0.1:8787'
    const wsURL = backendURL.replace('http', 'ws') + '/api/ws/progress'
    
    const ws = new WebSocket(wsURL)
    ws.onmessage = (event) => {
      const msg = JSON.parse(event.data)
      if (msg.type === 'progress') {
        updateProgress(msg.percent, msg.message || '')
      } else if (msg.type === 'complete') {
        finishProcess(msg.message || '处理完成')
      } else if (msg.type === 'error') {
        setError(msg.message || '处理出错')
      }
    }
    ws.onclose = () => {
      // Reconnect after 3s
      setTimeout(connectProgressWS, 3000)
    }
  }
  
  connectProgressWS()
}, [])
```

#### 4.3.2 超分辨率页面 (`pages/SuperResolution.tsx`)

**功能**:
1. 打开图片
2. 选择放大倍率（2x / 4x）和模型
3. 点击「开始处理」
4. 预览对比结果并保存

**批量处理**:

```typescript
// 切换到「批量模式」
// 1. 点击「添加」或拖拽文件夹，一次性导入多张图片
// 2. 选择输出目录
// 3. 点击「开始批量处理」
// 4. 实时查看每张图片的处理进度，完成后结果保存在输出目录

const handleBatchProcess = async () => {
  const result = await batchUpscale({
    images: imagePaths,  // 文件路径列表（Electron 模式）
    model: upscaleModel,
    device,
    outscale: upscaleTile || undefined,
    output_dir: outputDir,
    is_file_path: true,  // 告知后端直接读取文件
  })
  
  // 处理结果
  // result.results: [{ index, status, outputPath, width, height, fileName }]
  // result.summary: { total, success, failed }
}
```

#### 4.3.3 智能合成页面 (`pages/SmartSynthesis.tsx`)

**功能**:
1. 选择合成模式（换背景 / 换装 / 换脸 / 虚拟试穿 / 精准替换 / 智能定位编辑 / 自由编辑）
2. 上传源图像和参考图像（如果需要）
3. 框选 ROI（如果需要）
4. 输入提示词（SD 系列模型）
5. 点击「开始合成」，实时查看进度
6. 预览结果并保存

#### 4.3.4 模型管理页面 (`pages/ModelManager.tsx`)

**功能**:
1. 查看所有注册模型的元数据（名称、描述、状态、大小等）
2. 下载缺失的模型（单个或一键下载全部）
3. 查看下载进度（排队位置、下载速度、已下载大小等）
4. 取消下载任务
5. 删除模型文件

**模型状态**:

```typescript
// 模型文件状态（来自后端 /api/models/health）
type ModelStatus = 'ok' | 'missing' | 'partial' | 'corrupted' | 'unknown'

// 下载任务状态（来自前端 useDownloadStore）
type DownloadStatus = 'queued' | 'downloading' | 'done' | 'error' | 'cancelled' | 'skipped'
```

**下载管理器 Hook**: `hooks/useDownloadManager.ts`

```typescript
export function useDownloadManager() {
  const startDownload = useCallback(async (modelId: string) => {
    // 1. 检查当前状态（已在队列/下载中则只恢复 SSE）
    // 2. 清除取消标记（用户重新发起下载）
    // 3. 先重置为 queued 状态，给用户即时反馈
    // 4. POST /api/models/download/{modelId}
    // 5. 建立 SSE 连接（ensureSubscribed）
  }, [getURL])
  
  const cancelDownload = useCallback(async (modelId: string) => {
    // 1. 标记为已取消，防止 startDownload 的 POST 回调覆盖状态
    // 2. 关闭 SSE 连接
    // 3. 清除重试计数器
    // 4. 更新前端状态
    // 5. 通知后端取消
  }, [getURL])
  
  const startBulkDownload = useCallback(async () => {
    // 1. POST /api/models/download/bulk
    // 2. 批量提交，已在队列中的自动去重
    // 3. 为每个 queued/downloading 的任务建立 SSE 连接
  }, [getURL])
  
  const cancelBulkDownload = useCallback(async () => {
    // 1. 遍历 bulkSession.modelIds
    // 2. 取消每个 queued/downloading 的任务
    // 3. 清除 bulkSession
  }, [cancelDownload])
  
  const resume = useCallback(async () => {
    // 1. 遍历所有 tasks
    // 2. 为 queued/downloading 状态的任务恢复 SSE 连接
  }, [getURL])
  
  return {
    startDownload,
    cancelDownload,
    startBulkDownload,
    cancelBulkDownload,
    resume,
    syncModelStatus,
  }
}
```

**SSE 连接管理**:

```typescript
// 模块级单例（跨组件共享，整个应用生命周期只有一份）
const _sseMap = new Map<string, EventSource>()
const _cancelledSet = new Set<string>()
const _sseRetryCount = new Map<string, number>()

function ensureSubscribed(modelId: string, url: string) {
  // 1. 检查是否已有连接（复用）
  // 2. 创建新的 EventSource 连接
  // 3. 监听 onopen / onmessage / onerror
  
  es.addEventListener('status', (e: MessageEvent) => {
    const patch = normalizeTask(data)
    useDownloadStore.getState().setTask(modelId, patch)
    
    if (patch.status === 'done') {
      // 下载完成：关闭连接，删除 map，同步模型状态，触发回调
    }
    
    if (patch.status === 'error') {
      // 下载失败：关闭连接，删除 map，显示 Toast
    }
    
    if (patch.status === 'cancelled') {
      // 下载取消：关闭连接，删除 map
    }
  })
  
  es.onerror = (e) => {
    // 对于 queued/downloading 状态的任务，尝试重连而不是立即标记为 error
    if (current?.status === 'downloading' || current?.status === 'queued') {
      const retries = _sseRetryCount.get(modelId) ?? 0
      if (retries < SSE_MAX_RETRIES) {
        // 重试
        setTimeout(() => {
          ensureSubscribed(modelId, url)
        }, SSE_RETRY_DELAY)
      } else {
        // 超过重试次数，标记为 error
      }
    }
  }
}
```

---

## 5. 数据流设计

### 5.1 图像数据传输

**方案**: Base64 编码传输（简单、跨进程兼容）

**编码** (后端 → 前端):

```python
def encode_image(image_rgb: np.ndarray) -> str:
    """RGB numpy array → Base64 PNG"""
    img_bgr = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2BGR)
    _, buffer = cv2.imencode(".png", img_bgr)
    return base64.b64encode(buffer).decode("utf-8")
```

**解码** (前端 → 后端):

```python
def decode_image(base64_str: str) -> np.ndarray:
    """Base64 → RGB numpy array"""
    if "," in base64_str:
        base64_str = base64_str.split(",", 1)[1]
    img_bytes = base64.b64decode(base64_str)
    nparr = np.frombuffer(img_bytes, np.uint8)
    img_bgr = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    return cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
```

**优化**: Electron 模式下支持直接传递文件路径，后端直接读取文件（避免 Base64 编码解码开销）

### 5.2 进度推送

**方案**: WebSocket（实时、双向）

**后端**:

```python
# app/websocket/progress.py
class ProgressManager:
    def __init__(self):
        self.connections: Set[WebSocket] = set()
    
    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.connections.add(websocket)
    
    def remove(self, websocket: WebSocket):
        self.connections.discard(websocket)
    
    async def send_progress(self, percent: int, message: str):
        """推送进度"""
        for ws in self.connections:
            await ws.send_json({
                "type": "progress",
                "percent": percent,
                "message": message,
            })
    
    async def send_complete(self, message: str = "完成"):
        """推送完成"""
        for ws in self.connections:
            await ws.send_json({
                "type": "complete",
                "message": message,
            })
    
    async def send_error(self, message: str):
        """推送错误"""
        for ws in self.connections:
            await ws.send_json({
                "type": "error",
                "message": message,
            })
```

**前端**:

```typescript
// 建立 WebSocket 连接
const ws = new WebSocket(wsURL)

ws.onmessage = (event) => {
  const msg = JSON.parse(event.data)
  if (msg.type === 'progress') {
    updateProgress(msg.percent, msg.message || '')
  } else if (msg.type === 'complete') {
    finishProcess(msg.message || '处理完成')
  } else if (msg.type === 'error') {
    setError(msg.message || '处理出错')
  }
}

ws.onclose = () => {
  // Reconnect after 3s
  setTimeout(connectProgressWS, 3000)
}
```

### 5.3 模型下载进度

**方案**: SSE（Server-Sent Events，单向推送）

**后端**:

```python
@app.get("/subscribe/{model_id}")
async def subscribe_model(model_id: str, request: Request):
    async def _generate():
        async for event in queue.subscribe(model_id):
            if await request.is_disconnected():
                break
            if event.get("heartbeat"):
                yield ": heartbeat\n\n"
            else:
                yield _sse("status", event)
    
    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
    )
```

**前端**:

```typescript
function ensureSubscribed(modelId: string, url: string) {
  const es = new EventSource(`${url}/api/models/subscribe/${modelId}`)
  
  es.addEventListener('status', (e: MessageEvent) => {
    const patch = JSON.parse(e.data)
    useDownloadStore.getState().setTask(modelId, patch)
  })
  
  es.onerror = (e) => {
    // 重试逻辑
  }
}
```

---

## 6. 核心功能实现

### 6.1 去水印

**流程**:

1. 用户打开图片 → 前端解码并显示在 Konva 画布上
2. 用户框选水印区域 → 生成 ROIs（Region of Interest）
3. 用户点击「开始处理」→ 前端将图像（Base64）和 ROIs 发送到后端
4. 后端检查模型是否已下载 → 如果未下载，先下载模型
5. 后端使用模型执行器执行推理 → 在线程池中运行，避免阻塞事件循环
6. 后端通过 WebSocket 推送进度
7. 推理完成 → 后端将结果图像（Base64）返回给前端
8. 前端显示结果图像 → 用户保存

**自动水印检测**:

```python
@router.post("/detect")
async def detect_watermark(req: DetectRequest):
    """自动检测水印区域"""
    from core.watermark_detector import WatermarkDetector
    
    image = decode_image(req.image)
    
    def _detect():
        detector = WatermarkDetector(sensitivity=req.sensitivity)
        return detector.detect(image)
    
    loop = asyncio.get_event_loop()
    regions = await loop.run_in_executor(executor, _detect)
    
    return {
        "regions": [[int(x1), int(y1), int(x2), int(y2)] for x1, y1, x2, y2 in regions]
    }
```

### 6.2 超分辨率

**流程**:

1. 用户打开图片 → 前端解码并显示
2. 用户选择放大倍率和模型 → 前端更新设置
3. 用户点击「开始处理」→ 前端将图像（Base64）和参数发送到后端
4. 后端加载 Real-ESRGAN 模型 → 如果未加载，先加载模型到显存
5. 后端执行超分辨率 → 在线程池中运行
6. 后端通过 WebSocket 推送进度
7. 推理完成 → 后端将结果图像（Base64）返回给前端
8. 前端显示结果图像 → 用户保存

**批量处理优化**:

- **复用 Upscaler 实例**: 循环外创建 Upscaler 实例，批量复用，避免每张图重复加载模型到显存
- **预加载模型**: 循环前就加载好模型，避免每张图都触发加载
- **Electron 模式优化**: 传递文件路径而非 Base64，后端直接读取文件，减少内存开销
- **显存管理**: 处理完成后释放模型和显存（`del upscaler; torch.cuda.empty_cache()`）

### 6.3 智能合成

**流程**:

1. 用户选择合成模式 → 前端更新 UI（显示/隐藏参考图上传、ROI 框选、提示词输入等）
2. 用户上传源图像和参考图像（如果需要）→ 前端解码并显示
3. 用户框选 ROI（如果需要）→ 生成 ROIs
4. 用户输入提示词（SD 系列模型）→ 前端更新设置
5. 用户点击「开始合成」→ 前端将图像（Base64）和参数发送到后端
6. 后端检查模型是否已下载 → 如果未下载，先下载模型
7. 后端使用合成器执行推理 → 在线程池中运行
8. 后端通过 WebSocket 推送进度
9. 推理完成 → 后端将结果图像（Base64）返回给前端
10. 前端显示结果图像 → 用户保存

**合成模式**:

| 模式 | 说明 | 是否需要参考图 |
|------|------|----------------|
| 换背景 | 上传背景图，自动抠图合成 | 是 |
| 换装模拟 | 上传服装图，试穿效果 | 是 |
| 换脸模拟 | 上传目标脸，合成到原图 | 是 |
| 虚拟试穿 | 综合换装 + 换背景 | 是 |
| 精准替换 | 框选 ROI + 文字描述，按描述重绘 | 否 |
| 智能定位编辑 | 输入中文指令（如"将上衣换成红色"） | 否 |
| 自由编辑 | 自然语言指令全图语义编辑 | 否 |

---

## 7. 状态管理设计

### 7.1 Zustand Store 结构

**设计理念**: 按职责分离 Store，避免单一 Store 过大；Store 之间可以互相访问（通过 `getState()`）。

#### 7.1.1 `useBackendStore` - 后端连接状态

```typescript
interface BackendState {
  isConnected: boolean
  backendURL: string
  wsConnected: boolean
  
  setConnected: (connected: boolean) => void
  setBackendURL: (url: string) => void
  setWsConnected: (connected: boolean) => void
  checkHealth: () => Promise<boolean>
}
```

#### 7.1.2 `useDownloadStore` - 下载状态管理

```typescript
// 单个模型下载任务状态
export type DownloadTask = {
  modelId: string
  modelName: string
  status: TaskStatus  // 'queued' | 'downloading' | 'done' | 'error' | 'cancelled'
  position: number      // queued 时的排队序号（1-based），downloading/done 时为 0
  message: string
  speed: string         // 下载速度，如 "1.2 MB/s"
  downloaded: string    // 已下载大小，如 "32 MB"
  totalSize: string     // 总大小，如 "65 MB"
  updatedAt: number     // 最近更新时间戳（ms）
}

// 一键下载批次会话
export type BulkSession = {
  active: boolean
  modelIds: string[]   // 本次一键下载包含的全部 modelId
}

interface DownloadStoreState {
  tasks: Record<string, DownloadTask>  // 所有活跃/已完成任务，key = modelId
  bulkSession: BulkSession
  
  setTask: (modelId: string, patch: Partial<DownloadTask>) => void
  removeTask: (modelId: string) => void
  clearFinished: () => void
  setBulkSession: (patch: Partial<BulkSession>) => void
  clearBulkSession: () => void
  getTask: (modelId: string) => DownloadTask | undefined
}
```

#### 7.1.3 `useSettingsStore` - 用户设置

```typescript
interface SettingsState {
  // Global
  device: string
  disableNsfw: boolean
  
  // Inpaint
  inpaintModel: string
  defaultDilation: number
  sensitivity: number
  
  // Postprocess
  postprocessMethod: string
  postprocessEnabled: boolean
  
  // Upscale
  upscaleModel: string
  upscaleEnabled: boolean
  upscaleTile: number
  
  // Server
  serverPort: number
  keepaliveSeconds: number
  startupTimeout: number
  
  // Memory optimization
  lowMem: boolean
  cpuOffload: boolean
  cpuTextencoder: boolean
  
  // Network
  hfEndpoint: string
  hfToken: string
  githubMirror: string
  
  // Download
  downloadMaxConcurrent: number
  
  isLoading: boolean
  defaultsLoaded: boolean
  
  // Actions
  setDevice: (device: string) => void
  setInpaintModel: (model: string) => void
  setUpscaleModel: (model: string) => void
  setUpscaleTile: (tile: number) => void
  setDilation: (dilation: number) => void
  setSensitivity: (sensitivity: number) => void
  setDisableNsfw: (v: boolean) => void
  setPostprocessMethod: (method: string) => void
  setPostprocessEnabled: (enabled: boolean) => void
  setUpscaleEnabled: (enabled: boolean) => void
  setSettings: (settings: Partial<SettingsState>) => void
  loadSettings: (backendURL: string) => Promise<void>
  saveSettings: (backendURL: string) => Promise<void>
  loadDefaults: (backendURL: string) => Promise<void>
}
```

#### 7.1.4 工作区与工具状态（v1.4）

去水印 / 超分 / 合成共享 `useWorkspaceStore`（原图、结果、`inputSource`），ROI 与画笔分别在 `useWatermarkToolStore` / `useSynthesisToolStore`。超分与合成默认处理上一页结果，可切回原图。抠图使用独立的 `useCutoutStore`。

进度 WebSocket 在 App 级通过 `useProgressWebSocket` 连接一次，写入全局 `useProcessStore`。

#### 7.1.5 `useModelStore` - 模型列表

```typescript
interface ModelStoreState {
  inpaintGroups: Array<...>
  upscaleGroups: Array<...>
  
  loadModels: () => Promise<void>
  loadUpscaleModels: () => Promise<void>
}
```

#### 7.1.6 `useProcessStore` - 处理状态

```typescript
interface ProcessState {
  isProcessing: boolean
  progress: number
  statusMessage: string
  
  startProcess: () => void
  updateProgress: (progress: number, message: string) => void
  finishProcess: (message: string) => void
  setError: (message: string) => void
  reset: () => void
}
```

### 7.2 常量管理

**文件**: `frontend/src/renderer/constants.ts`

**设计理念**: 统一管理所有魔术字符串，避免散落各处；与后端常量保持一致。

```typescript
// 模型文件检测状态
export const ModelStatus = {
  OK:        'ok',
  MISSING:   'missing',
  PARTIAL:   'partial',
  CORRUPTED: 'corrupted',
  UNKNOWN:   'unknown',
} as const

// 下载任务状态
export const DownloadStatus = {
  QUEUED:     'queued',
  DOWNLOADING: 'downloading',
  DONE:        'done',
  ERROR:       'error',
  CANCELLED:   'cancelled',
  SKIPPED:     'skipped',
} as const

// 模型 Provider
export const Provider = {
  REMBG:     'rembg',
  IOPAINT:   'IOPaint',
  DIFFUSERS: 'diffusers',
  FACEXLIB:  'facexlib',
  REALESRGAN: 'realesrgan',
  HIIMAGE:   'HiImage',
} as const

// API 路径
export const ApiPath = {
  MODELS_LIST:       '/api/models/list',
  MODELS_HEALTH:     '/api/models/health',
  MODELS_SUBSCRIBE:  '/api/models/subscribe',
  MODELS_DOWNLOAD:   '/api/models/download',
  HEALTH:            '/api/health',
  WS_PROGRESS:       '/api/ws/progress',
  ...
} as const
```

---

## 8. API 设计

### 8.1 RESTful API

**Base URL**: `http://127.0.0.1:8787` (local) 或 `http://<remote_host>:<remote_port>` (remote)

**认证**: 无（本地应用，或远程连接时用户自行保证网络安全）

#### 8.1.1 系统

| 端点 | 方法 | 说明 | 请求体 | 响应 |
|------|------|------|---------|------|
| `/api/health` | GET | 健康检查 | - | `{"status": "ok", "version": "2.0.0"}` |
| `/api/devices` | GET | 检测可用计算设备 | - | `{"devices": [{"id": "mps", "label": "MPS", "available": true, ...}]}` |

#### 8.1.2 去水印

| 端点 | 方法 | 说明 | 请求体 | 响应 |
|------|------|------|---------|------|
| `/api/models/inpaint` | GET | 返回所有去水印模型（按 display_group 分组） | - | `{"groups": [{"label": "快速本地模型", "models": [...]}]}` |
| `/api/detect` | POST | 自动检测水印区域 | `{"image": "base64...", "sensitivity": 0.5}` | `{"regions": [[x1,y1,x2,y2], ...]}` |
| `/api/inpaint` | POST | 使用 ROI 列表去除水印 | `{"image": "base64...", "rois": [[x1,y1,x2,y2]], "model": "wm_lama", ...}` | `{"image": "base64..."}` |
| `/api/inpaint/with-mask` | POST | 使用自定义掩码去除水印 | `{"image": "base64...", "mask": "base64...", ...}` | `{"image": "base64..."}` |

#### 8.1.3 超分辨率

| 端点 | 方法 | 说明 | 请求体 | 响应 |
|------|------|------|---------|------|
| `/api/models/upscale` | GET | 返回所有超分辨率模型（按 display_group 分组） | - | `{"groups": [...]}` |
| `/api/upscale` | POST | 超分辨率处理（单张） | `{"image": "base64...", "model": "RealESRGAN_x4plus", ...}` | `{"image": "base64...", "width": 1024, "height": 1024}` |
| `/api/upscale/batch` | POST | 批量超分辨率处理 | `{"images": [...], "model": "...", ...}` | `{"results": [...], "summary": {...}}` |

#### 8.1.4 智能合成

| 端点 | 方法 | 说明 | 请求体 | 响应 |
|------|------|------|---------|------|
| `/api/synthesis/modes` | GET | 获取所有合成模式 | - | `{"modes": [...]}` |
| `/api/synthesis/models` | GET | 获取所有可用模型 | - | `{"models": [...]}` |
| `/api/synthesis/run` | POST | 执行合成处理 | `{"source_image": "base64...", "mode": "background_replace", ...}` | `{"image": "base64...", "width": 1024, "height": 1024}` |

#### 8.1.5 模型管理

| 端点 | 方法 | 说明 | 请求体 | 响应 |
|------|------|------|---------|------|
| `/api/models/health` | GET | 检测所有注册模型的完整性 | - | `{"models": [...], "summary": {...}}` |
| `/api/models/list` | GET | 返回所有注册模型的元数据 | - | `{"models": [...], "mode_groups": [...]}` |
| `/api/models/download/{model_id}` | POST | 提交单模型下载任务 | - | `{"modelId": "...", "status": "downloading", ...}` |
| `/api/models/download/bulk` | POST | 批量提交（一键下载） | - | `{"submitted": 5, "tasks": [...]}` |
| `/api/models/download/{model_id}` | DELETE | 取消下载任务 | - | `{"ok": true, "message": "已取消: ..."}` |
| `/api/models/subscribe/{model_id}` | GET | SSE 订阅单个模型的实时状态变更 | - | `text/event-stream` |
| `/api/models/queue` | GET | 查询当前队列中所有活跃任务 | - | `{"active": [...], "max_concurrent": 3}` |
| `/api/models/{model_id}/files` | DELETE | 删除指定模型的本地权重文件 | - | `{"ok": true, "deleted": [...], "not_found": [...]}` |

#### 8.1.6 设置

| 端点 | 方法 | 说明 | 请求体 | 响应 |
|------|------|------|---------|------|
| `/api/settings` | GET | 获取当前设置 | - | `{"device": "mps", "server_port": 51821, ...}` |
| `/api/settings` | PUT | 更新设置 | `{"device": "cuda", ...}` | `{"ok": true}` |

### 8.2 WebSocket

**端点**: `/api/ws/progress`

**用途**: 实时推送处理进度

**消息格式**:

```json
// 进度
{
  "type": "progress",
  "percent": 50,
  "message": "正在处理..."
}

// 完成
{
  "type": "complete",
  "message": "处理完成"
}

// 错误
{
  "type": "error",
  "message": "处理失败：..."
}
```

### 8.3 SSE

**端点**: `/api/models/subscribe/{model_id}`

**用途**: 实时推送模型下载状态变更

**事件类型**: `status`

**消息格式**:

```json
{
  "modelId": "wm_lama",
  "modelName": "LaMa",
  "status": "downloading",
  "position": 0,
  "message": "正在下载...",
  "speed": "1.2 MB/s",
  "downloaded": "32 MB",
  "totalSize": "65 MB"
}
```

---

## 9. 模型管理系统

### 9.1 模型注册表

**设计理念**: 所有模型与模式配置均在 `core/models.yaml` 中维护，代码只负责读取和解析。**新增模型只需编辑 YAML 文件，无需修改任何 Python 代码。**

**models.yaml 结构**:

```yaml
# 参数规格模板（锚点，供各模型引用以避免重复）
_templates:
  sd_inpaint: &sd_inpaint
    num_inference_steps:
      type: int
      default: 50
      range: [1, 150]
      desc: 扩散去噪步数；步数越多越精细，但耗时增加
    guidance_scale:
      type: float
      default: 7.5
      range: [1.0, 20.0]
      desc: 提示词引导强度；值越高越贴近 prompt，但可能过拟合
    ...

# 模型列表
models:
  - id: "wm_lama"
    name: "LaMa"
    provider: "IOPaint"
    iopaint_mode: "cli"
    iopaint_model_id: "lama"
    tags: ["watermark_removal"]
    description: "快速本地模型，无需下载"
    badge: "快速"
    size_mb: 0

  - id: "RealESRGAN_x4plus"
    name: "4x 通用照片（推荐）"
    provider: "realesrgan"
    local_path: "realesrgan/RealESRGAN_x4plus.pth"
    download_url: "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth"
    tags: ["upscale"]
    description: "通用照片超分辨率"
    scale: 4
    weight_filename: "RealESRGAN_x4plus.pth"
    arch: "RRDBNet"
    num_block: 23
    badge: ""
    size_mb: 65

  # ... 更多模型

# 模式分组
mode_groups:
  - id: "watermark_removal"
    name: "去水印"
    default_model: "wm_lama"
    ...

  # ... 更多模式
```

**字段说明**:

| 字段 | 说明 |
|------|------|
| `id` | 内部 ID，与前端 model_id / 后端 dispatch 一一对应 |
| `name` | 展示名称 |
| `provider` | 模型来源：`rembg` / `IOPaint` / `diffusers` / `HiImage` / `facexlib` / `realesrgan` |
| `tags` | 所属功能模式 ID 列表（一个模型可属于多个模式） |
| `description` | 面向用户的功能描述 |
| `recommended_for` | 适合的场景（用于 UI tooltip） |
| `requires_reference` | 是否需要参考图 |
| `size_mb` | 模型权重大小（MB，估算） |
| `badge` | UI 徽章：推荐 / 快速 / 高质量 / 动漫（空串表示无徽章） |
| `hf_model_id` | HuggingFace Hub model ID（单模型），可直接传给 `snapshot_download()` |
| `hf_models` | HuggingFace Hub 多模型列表（组合模型专用） |
| `download_url` | 模型下载页面 URL（HuggingFace 或 GitHub Release） |
| `local_path` | 相对于 PROJECT_ROOT 的本地权重路径 |
| `rembg_model_name` | rembg 库内部模型名，对应 `~/.u2net/<name>.onnx` |
| `iopaint_model_id` | 传给 iopaint CLI / server 的 --model 参数 |
| `iopaint_mode` | IOPaint 调用方式：`"cli"` → iopaint run 命令；`"server"` → iopaint start HTTP 服务 |
| `device_override` | 强制使用的推理设备，忽略用户在 UI 中的选择 |
| `display_group` | 在 UI 下拉中所属的分组标题（watermark_removal 模式专用） |
| `scale` | 超分倍率（2 或 4） |
| `weight_filename` | 权重文件名 |
| `arch` | 网络架构：RRDBNet（标准大模型）或 SRVGGNetCompact（轻量模型） |
| `num_block` | RRDBNet 专用：dense block 数 |
| `num_conv` | SRVGGNetCompact 专用：conv 层数 |
| `outscale` | 实际输出倍率（可选） |
| `supports_custom_outscale` | 是否支持自定义输出倍率 |
| `supported_params` | 推理参数说明（key → type/default/range/desc） |
| `features` | 能力标签列表 |

### 9.2 模型完整性检查

**模块**: `core/model_checker.py`

**检查逻辑**:

```python
class ModelChecker:
    def check_model(self, model_id: str) -> ModelCheckResult:
        """检测单个模型的文件完整性"""
        cfg = MODEL_BY_ID.get(model_id)
        if not cfg:
            return ModelCheckResult(model_id=model_id, status=MS.UNKNOWN, message=f"未知模型 ID: {model_id}")
        
        provider = cfg.get("provider")
        
        if provider == Provider.REMBG:
            # 检查 ~/.u2net/<rembg_model_name>.onnx 是否存在
            ...
            
        elif provider == Provider.IOPAINT:
            # 检查 IOPaint 模型是否已下载
            ...
            
        elif provider == Provider.DIFFUSERS:
            # 检查 HuggingFace 缓存中对应的 repo 目录是否存在
            ...
            
        elif provider == Provider.REALESRGAN:
            # 检查 models/realesrgan/<weight_filename> 是否存在
            ...
            
        # ... 其他 provider
        
        return ModelCheckResult(
            model_id=model_id,
            name=cfg.get("name", model_id),
            provider=provider,
            status=status,  # OK / MISSING / PARTIAL / CORRUPTED / UNKNOWN
            message=message,
            file_path=file_path,
            size_bytes=size_bytes,
        )
    
    def check_all(self) -> List[ModelCheckResult]:
        """检测所有注册模型的文件完整性"""
        return [self.check_model(m["id"]) for m in MODELS]
```

### 9.3 模型下载

**下载器**: `core/downloaders.py`

**支持的下载方式**:

```python
def download_rembg(cfg: dict, progress_cb=None, cancel_check=None):
    """下载 rembg 模型（ONNX 权重）"""
    # 使用 rembg 内置的下载逻辑
    ...

def download_hf(cfg: dict, progress_cb=None, cancel_check=None):
    """从 HuggingFace Hub 下载模型"""
    from huggingface_hub import snapshot_download
    snapshot_download(
        repo_id=cfg["hf_model_id"],
        cache_dir=str(get_hf_hub_dir()),
        ...
    )

def download_hf_multi(cfg: dict, progress_cb=None, cancel_check=None):
    """从 HuggingFace Hub 下载组合模型（多个子模型）"""
    for sub in cfg["hf_models"]:
        snapshot_download(repo_id=sub["id"], ...)

def download_direct(cfg: dict, progress_cb=None, cancel_check=None):
    """直接下载权重文件（从 download_url）"""
    url = cfg["download_url"]
    dest = resolve_model_cache_path(cfg)
    # 使用 requests 或 urllib 下载，支持进度回调
    ...
```

**下载队列调度器**: `core/download_queue.py`

（详见 [3.4.3](#343-下载队列调度器-coredownload_queuepy)）

### 9.4 模型执行器

**工厂模式**: `core/model_executor.py`

（详见 [3.4.2](#342-模型执行器框架)）

---

## 10. 部署与打包

### 10.1 开发模式

**启动**:

```bash
cd frontend
npm run dev
# → Vite dev server @ http://localhost:5173
# → FastAPI backend 自动管理生命周期
```

**后端管理**:
- 前端启动时会自动启动后端进程（通过 `BackendManager`）
- 后端进程会在前端关闭时自动停止
- 支持远程连接（输入 IP + 端口）

### 10.2 生产构建

**构建**:

```bash
cd frontend
npm run build     # 输出 frontend/dist/
npm run package   # 打包 Electron 应用（.dmg / .exe）
```

**打包配置**: `frontend/electron-builder.config.ts`

```typescript
export default {
  appId: 'com.hiimage.app',
  productName: 'HiImage',
  directories: {
    output: 'dist',
  },
  mac: {
    target: 'dmg',
    hardenedRuntime: true,
    gatekeeperAssess: false,
    icon: 'assets/icons/icon.icns',
  },
  win: {
    target: 'nsis',
    icon: 'assets/icons/icon.ico',
  },
  linux: {
    target: 'AppImage',
    icon: 'assets/icons/icon-512.png',
  },
  files: [
    'dist/**/*',
    'assets/**/*',
    'backend/**/*',
    'models/**/*',
    'node_modules/**/*',
  ],
  extraResources: [
    {
      from: 'backend',
      to: 'backend',
    },
    {
      from: 'models',
      to: 'models',
    },
  ],
}
```

**注意事项**:
- macOS 打包需要 Apple Developer Program 证书（$99/年），否则无法分发给他人的。
- 开发模式下无需签名，直接 `npm run dev` 即可使用全部功能。

### 10.3 Docker 部署

**Docker Compose**: `docker-compose.yml`

```yaml
version: "3.9"

services:
  backend:
    build:
      context: ./backend
      dockerfile: Dockerfile
    container_name: hiimage-backend
    ports:
      - "${BACKEND_PORT:-8787}:8787"
    volumes:
      # 持久化模型（避免每次重新下载）
      - ./models:/app/models
      # 配置文件
      - ./config:/app/config
      # 临时文件
      - ./tmp:/app/tmp
    environment:
      - PYTHONUNBUFFERED=1
      - HF_HOME=/app/models/huggingface
      - TORCH_HOME=/app/models/torch
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8787/api/health"]
      interval: 10s
      timeout: 5s
      retries: 3
      start_period: 30s
```

**GPU 版本（NVIDIA GPU）**:

```yaml
  # backend-gpu:
  #   build:
  #     context: ./backend
  #     dockerfile: Dockerfile
  #   container_name: hiimage-backend-gpu
  #   ports:
  #     - "${BACKEND_PORT:-8787}:8787"
  #   volumes:
  #     - ./models:/app/models
  #     - ./config:/app/config
  #     - ./tmp:/app/tmp
  #   environment:
  #     - PYTHONUNBUFFERED=1
  #     - HF_HOME=/app/models/huggingface
  #     - TORCH_HOME=/app/models/torch
  #     - NVIDIA_VISIBLE_DEVICES=all
  #   deploy:
  #     resources:
  #       reservations:
  #         devices:
  #           - driver: nvidia
  #             count: 1
  #             capabilities: [gpu]
  #   restart: unless-stopped
```

---

## 11. 扩展性与维护

### 11.1 新增模型

**步骤**:

1. 编辑 `backend/core/models.yaml`，在 `models` 列表末尾追加一条记录
2. 如果新模型属于现有模式，只需在 `tags` 中指定模式 ID
3. 如果新模型需要新的 provider，需实现对应的执行器（继承 `BaseExecutor`）
4. 重启应用（或调用 `reload()` 热重载配置）

**示例**:

```yaml
  - id: "new_model"
    name: "New Model"
    provider: "IOPaint"
    iopaint_mode: "server"
    iopaint_model_id: "org/repo"
    tags: ["watermark_removal"]
    description: "新模型描述"
    badge: "推荐"
    size_mb: 100
    hf_model_id: "org/repo"
```

### 11.2 新增功能模式

**步骤**:

1. 编辑 `backend/core/models.yaml`，在 `mode_groups` 列表中追加一条记录
2. 实现后端路由（如 `backend/app/routers/new_mode.py`）
3. 实现前端页面组件（如 `frontend/src/renderer/pages/NewMode.tsx`）
4. 注册路由（后端 `main.py` 和前端 `App.tsx`）
5. 如需新的模型执行器，实现对应的执行器类

### 11.3 代码规范

**后端**:

- 使用类型提示（Type Hints）
- 使用 `pydantic` 定义请求/响应模型
- 使用 `async/await` 处理异步操作
- 使用 `asyncio.run_in_executor` 执行 CPU/GPU 密集型任务（避免阻塞事件循环）
- 使用 `log_manager` 记录日志（统一日志管理）

**前端**:

- 使用 TypeScript（类型安全）
- 使用函数组件 + Hooks（避免 class 组件）
- 使用 Zustand 管理全局状态（避免 PropTypes）
- 使用 `clsx` 处理条件类名（避免模板字符串）
- 使用 `lucide-react` 图标（避免自定义图标）

### 11.4 测试

**后端测试**: `backend/tests/`

```python
# 使用 pytest
def test_inpaint():
    response = client.post("/api/inpaint", json={
        "image": "...",
        "rois": [[0, 0, 100, 100]],
        "model": "wm_lama",
    })
    assert response.status_code == 200
    assert "image" in response.json()
```

**前端测试**: （待补充）

```typescript
// 使用 vitest
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import WatermarkRemoval from './WatermarkRemoval'

describe('WatermarkRemoval', () => {
  it('should render', () => {
    render(<WatermarkRemoval />)
    expect(screen.getByText('去水印')).toBeInTheDocument()
  })
})
```

---

## 附录

### A. 常见问题

**Q: 模型下载速度慢？**

A: 在设置页将 `network.hf_endpoint` 修改为 `https://hf-mirror.com`。

**Q: 处理时出现 "Out of Memory" 错误？**

A: 开启设置页的「低内存模式」，或进一步开启「CPU 显存卸载」。

**Q: 后端启动失败，提示 "Address already in use"？**

A: 端口 8787 被占用。在设置页修改 IOPaint 端口，或手动清理占用进程。

### B. 参考文档

- [产品需求文档](./PRD.md)
- [用户手册](./USER_MANUAL.md)（待创建）
- [API 参考](./API_REFERENCE.md)（待创建）
- [开发者指南](./DEVELOPER_GUIDE.md)（待创建）

---

**文档版本**: 1.0  
**最后更新**: 2026-05-26  
**作者**: Hikari
