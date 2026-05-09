# HiImage 项目完整架构设计文档

**项目类型**: Electron + React/TypeScript 前端 + FastAPI 后端的 AI 图像处理应用  
**项目规模**: 75 个源文件，约 13,229 行代码  
**核心功能**: 水印去除、超分辨率、智能合成  
**部署模式**: 本地模式（Electron 集成）/ 远程模式（Docker）  

---

## 一、整体架构概览

### 1.1 系统分层结构

```
┌─────────────────────────────────────────────────────────┐
│             前端层 (Electron + React)                   │
├─────────────────────────────────────────────────────────┤
│  Browser Window (WebView)  │  Main Process (Node.js)   │
│  ├─ 组件树                 │  ├─ Backend Manager        │
│  ├─ 页面路由               │  ├─ IPC Handler           │
│  ├─ 状态管理(Zustand)     │  ├─ 文件操作              │
│  └─ UI 与交互              │  └─ 进程控制              │
├─────────────────────────────────────────────────────────┤
│  IPC 通信 + HTTP/WebSocket 协议                        │
├─────────────────────────────────────────────────────────┤
│          后端层 (FastAPI + Python)                     │
├─────────────────────────────────────────────────────────┤
│  API Router Layer                                      │
│  ├─ /api/inpaint          (水印去除)                  │
│  ├─ /api/upscale          (超分辨率)                  │
│  ├─ /api/synthesis        (智能合成)                  │
│  ├─ /api/models/*         (模型管理)                  │
│  ├─ /api/settings         (配置管理)                  │
│  └─ /api/ws/progress      (WebSocket 进度)           │
├─────────────────────────────────────────────────────────┤
│  Core Processing Layer                                 │
│  ├─ Inpainter (使用 IOPaint/LaMa/SD)                 │
│  ├─ Upscaler  (Real-ESRGAN)                          │
│  ├─ Synthesizer (rembg/GFPGAN/自定义)                │
│  ├─ Model Registry (集中配置)                        │
│  └─ Model Server (IOPaint HTTP 保活)                │
├─────────────────────────────────────────────────────────┤
│  Infrastructure Layer                                  │
│  ├─ Config Management (config/settings.json)           │
│  ├─ Logging Manager (日志收集 + WebSocket 推送)      │
│  ├─ Model Checker (完整性检测)                       │
│  └─ Model Downloader (SSE 流式下载)                 │
├─────────────────────────────────────────────────────────┤
│          外部依赖与模型库                              │
├─────────────────────────────────────────────────────────┤
│ PyTorch | ONNX Runtime | transformers | diffusers    │
│ iopaint | rembg | gfpgan | realesrgan | basicsr      │
└─────────────────────────────────────────────────────────┘
```

---

## 二、前端架构详解

### 2.1 Electron 主进程架构

**文件**: `frontend/src/main/index.ts`、`frontend/src/main/backend-manager.ts`

#### 核心职责
1. **应用生命周期管理**
   - App 启动时初始化 IPC、启动后端
   - App 关闭时优雅停止后端进程
   - 处理 macOS 特殊事件（activate、window-all-closed）

2. **后端进程管理** (BackendManager 单例)
   - **双模式支持**:
     - 本地模式: 自动启动 Python FastAPI 进程（开发 + 打包版本）
     - 远程模式: 连接外部 Docker/服务器后端
   - **串行队列调度**: 所有异步操作（start/stop/updateConfig）排队执行，避免竞态
   - **优雅关闭流程**:
     ```
     SIGTERM → 等待 5 秒 → SIGKILL → kill 进程组 → 立即检测
     macOS/Linux: 进程树完整清理
     Windows: taskkill /T (树形杀进程)
     ```
   - **端口管理**:
     - 启动前自动清理占用端口的残留进程（netstat + taskkill / lsof + kill）
     - 支持端口冲突自动恢复

3. **IPC 通信接口**
   ```typescript
   // 文件操作
   ipcMain.handle('dialog:openFile')        // 打开文件对话框
   ipcMain.handle('dialog:saveFile')        // 保存文件对话框
   ipcMain.handle('file:save')              // 保存 base64 → 磁盘
   ipcMain.handle('file:read')              // 读文件 → base64

   // 后端管理
   ipcMain.handle('backend:getURL')         // 获取后端 URL
   ipcMain.handle('backend:getConfig')      // 获取连接配置
   ipcMain.handle('backend:updateConfig')   // 更新连接配置

   // 窗口控制（Windows 自定义标题栏）
   ipcMain.on('window:minimize/maximize/close')
   ```

4. **跨平台支持**
   - 图标路径: Windows (.ico) / macOS (.icns) / Linux (.png)
   - 窗口样式: macOS 隐藏标题栏 (hiddenInset) + 红绿黄按钮位置
   - 输出目录: 统一为 `~/Documents/HiImage`

#### BackendManager 技术亮点
- **事件驱动型等待**: `waitForReady()` 使用 interval + abort 信号，支持快速中断
- **配置持久化**: `connection.json` 存储本地/远程连接配置
- **环境变量管理**: Python 环境变量注入（PYTHONUNBUFFERED、HF_HOME 等）
- **启动命令适配**:
  - 开发: `venv/bin/python backend/run.py --port PORT`
  - 打包: `resources/backend/hiimage-backend --port PORT`

### 2.2 预加载脚本

**文件**: `frontend/src/preload/index.ts`

```typescript
electronAPI = {
  // 平台信息
  platform: 'darwin' | 'win32' | 'linux',
  
  // 文件操作（返回 Promise）
  openFile()                              // → filePath | null
  saveFile(defaultPath?)                  // → filePath | null
  saveImageFile(path, base64Data)        // → { success, path?, error? }
  readImageFile(path)                    // → base64 DataURL
  
  // 后端管理
  getBackendURL()                        // → URL string
  getBackendConfig()                     // → { mode, remoteHost, remotePort }
  updateBackendConfig(config)            // → 新的 URL
  
  // 窗口控制
  windowMinimize/Maximize/Close()        // 控制窗口
  
  // 事件监听
  onBackendReady(callback)               // 后端启动完成
  onBackendError(callback)               // 后端启动失败
}
```

特点: 严格的 Context Bridge 隔离，暴露最少必要 API，安全性优先

### 2.3 渲染进程（React 应用）

#### 文件组织
```
frontend/src/renderer/
├── App.tsx                  # 根组件 + 路由配置
├── main.tsx                # React 挂载点
├── pages/                   # 页面组件
│   ├── WatermarkRemoval.tsx # 去水印页面
│   ├── SuperResolution.tsx  # 超分辨率页面
│   ├── SmartSynthesis.tsx   # 智能合成页面
│   ├── Settings.tsx         # 设置页面
│   └── Logs.tsx            # 日志页面
├── components/
│   ├── layout/             # 布局组件
│   │   ├── MainLayout.tsx  # 主布局 (Sidebar + Content)
│   │   ├── PageHeader.tsx  # 页头
│   │   ├── Sidebar.tsx     # 导航栏
│   │   └── SidebarItem.tsx
│   ├── ui/                 # UI 基础组件
│   │   ├── Button.tsx      # 按钮
│   │   ├── Select.tsx      # 下拉选择
│   │   ├── Slider.tsx      # 滑块
│   │   ├── Progress.tsx    # 进度条
│   │   ├── Toast.tsx       # 弹出通知
│   │   └── index.ts        # 导出
│   ├── ImageCanvas.tsx     # 图像画布 (绘制/平移 ROI)
│   └── ImageCompare.tsx    # 对比组件
├── hooks/
│   ├── useBackendAPI.ts    # 后端 API 调用
│   └── useDeviceOptions.ts # 设备选项获取
├── stores/                 # Zustand 状态管理
│   ├── useBackendStore.ts  # 后端连接状态
│   ├── useImageStore.ts    # 图像 + ROI 状态
│   ├── useModelStore.ts    # 模型列表状态
│   ├── useProcessStore.ts  # 处理进度状态
│   └── useSettingsStore.ts # 用户设置状态
├── types/
│   ├── models.ts           # 模型类型定义
│   └── electron.d.ts       # Electron API 类型
└── vite-env.d.ts          # Vite 类型声明
```

#### 路由结构
```typescript
// 使用 HashRouter（避免 SPA 刷新问题）
<HashRouter>
  <Routes>
    <Route path="/" element={<MainLayout />}>
      <Route index element={<Navigate to="/watermark" />} />
      <Route path="watermark" element={<WatermarkRemoval />} />
      <Route path="upscale" element={<SuperResolution />} />
      <Route path="synthesis" element={<SmartSynthesis />} />
      <Route path="settings" element={<Settings />} />
      <Route path="logs" element={<Logs />} />
    </Route>
  </Routes>
</HashRouter>
```

### 2.4 状态管理系统（Zustand）

| 存储名 | 职责 | 关键字段 |
|-------|------|--------|
| **useBackendStore** | 后端连接状态 | isConnected, backendURL, wsConnected, checkHealth() |
| **useImageStore** | 图像 & ROI 管理 | sourceImage, resultImage, rois[], selectedROIs[] |
| **useModelStore** | 模型列表缓存 | inpaintGroups, upscaleGroups, loadModels() |
| **useProcessStore** | 处理进度 | isProcessing, progress[0-100], statusMessage |
| **useSettingsStore** | 用户全局配置 | device, inpaintModel, upscaleModel, loadSettings(), saveSettings() |

**设计模式**:
- 浅响应式更新: 只通知订阅者状态变更部分
- 异步操作: 通过 `async` 方法加载/保存设置
- 持久化: Settings 通过 API 同步到后端 `config/settings.json`

### 2.5 前端数据通信协议

#### HTTP API 调用（useBackendAPI Hook）
```typescript
// 水印检测
POST /api/detect
{
  "image": "data:image/png;base64,...",
  "sensitivity": 0.5
}
→ { "regions": [[x1,y1,x2,y2], ...] }

// 去水印（ROI 模式）
POST /api/inpaint
{
  "image": "base64",
  "rois": [[x1,y1,x2,y2], ...],
  "model": "lama",
  "device": "mps",
  "dilation": 10,
  "disable_nsfw": false
}
→ { "image": "base64" }

// 超分辨率
POST /api/upscale
{
  "image": "base64",
  "model": "RealESRGAN_x4plus",
  "device": "mps"
}
→ { "image": "base64", "width": 1920, "height": 1080 }

// 获取模型列表
GET /api/models/inpaint
GET /api/models/upscale
→ { "groups": [{"label": "分组", "models": [...]}] }
```

#### WebSocket 进度推送（/api/ws/progress）
```typescript
// 连接建立后，后端主动推送：
{
  "type": "progress",
  "percent": 30,
  "message": "正在加载模型..."
}

{
  "type": "complete",
  "percent": 100,
  "message": "处理完成"
}

{
  "type": "error",
  "percent": -1,
  "message": "处理出错: 显存不足"
}
```

---

## 三、后端架构详解

### 3.1 FastAPI 应用入口

**文件**: `backend/app/main.py`

```python
# 应用生命周期管理
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    - 初始化日志系统
    - 设置环境变量 (HF_ENDPOINT, HF_TOKEN, HF_HOME, TORCH_HOME)
    - 打印启动信息
    
    yield  # ← 应用运行中
    
    # Shutdown
    - 停止 IOPaint Server（如果在运行）
    - 清理资源

app = FastAPI(
    title="HiImage API",
    version="2.0.0",
    lifespan=lifespan,
)

# 中间件
CORSMiddleware(allow_origins=["*"])  # Electron renderer 跨域

# 路由注册
/api/health           (系统)
/api/inpaint          (去水印)
/api/upscale          (超分辨率)
/api/synthesis        (智能合成)
/api/models/*         (模型管理)
/api/settings         (配置)
/api/logs             (日志)
/api/ws/progress      (WebSocket 进度)
```

特点: 异步优先，支持 CPU/GPU 混合计算，内存高效

### 3.2 模型管理架构

#### 3.2.1 集中配置体系 (core/model_registry.py)

**关键设计**: 所有模型配置统一存放在 `core/models.yaml` 中，运行时动态加载。

```yaml
# 简化示例
models:
  - id: wm_lama
    name: "LaMa（推荐·通用）"
    description: "综合最佳首选：速度快、质量好"
    provider: "IOPaint"
    tags: ["watermark_removal"]
    iopaint_mode: "cli"
    display_group: "快速修复（本地推理）"
    
  - id: wm_anytext
    name: "AnyText（文字水印专用）"
    description: "文字类水印效果显著"
    provider: "IOPaint"
    tags: ["watermark_removal"]
    iopaint_mode: "server"
    iopaint_model_id: "Sanster/AnyText"
    display_group: "专用模型"
    
  - id: RealESRGAN_x4plus
    name: "4x 通用照片（推荐）"
    description: "通用场景，综合细节恢复最佳"
    provider: "Real-ESRGAN"
    tags: ["upscale"]
    scale: 4
    weight_filename: "RealESRGAN_x4plus.pth"
    download_url: "https://..."
    display_group: "通用超分辨率"

mode_groups:
  - id: watermark_removal
    name: "水印去除"
    # models 字段自动填充：过滤所有 tags 含有该 mode_id 的模型
  - id: upscale
    name: "超分辨率"
```

**API 设计**:
```python
# 统一接口
MODELS: list[dict]                    # 所有模型配置
MODE_GROUPS: list[dict]               # 所有模式分组

MODEL_BY_ID: dict[str, dict]         # 快速查询
MODE_BY_ID: dict[str, dict]

# 工具函数
get_models_for_mode(mode_id: str)    # 获取某模式下的模型
get_model(model_id: str)              # 获取单个模型
get_mode(mode_id: str)                # 获取模式配置
reload()                              # 热重载配置
```

**优势**:
- 扩展性强: 添加新模型仅需修改 YAML，无需改动代码
- 类型安全: 前端通过 TypeScript 类型定义确保 API 结构一致
- 动态分组: 按 `display_group` 字段自动分组，支持模型聚类
- 标签系统: 通过 `tags` 字段自动关联模式

#### 3.2.2 模型运行模式

根据 `iopaint_mode` 字段区分两种执行策略:

| 模式 | 模型例子 | 执行方式 | 内存占用 | 保活时间 |
|------|--------|--------|--------|---------|
| **cli** | LaMa, ZITS, MiGAN | 每次调用独立进程 | 低（进程级卸载） | 立即回收 |
| **server** | SD, AnyText | HTTP Server 长驻 | 高（GPU 显存常驻） | 5 分钟（可配置） |

**决策树** (is_diffusion_model):
1. 查询 model_registry: 该模型的 `iopaint_mode` 字段
2. 反向查询: 通过 `iopaint_model_id` 查找（兼容遗留代码）
3. 前缀匹配: 检查是否为已知扩散模型前缀（fallback）

#### 3.2.3 IOPaint Server 管理 (core/model_server.py)

**单例模式**: 全局唯一 Server 实例

```python
_server: Optional[IOPaintProcess] = None

def get_server() -> Optional[IOPaintProcess]:
    global _server
    return _server

def ensure_server_for_model(model_id: str, device: str):
    global _server
    
    # 判断是否需要 server
    if not is_diffusion_model(model_id):
        return  # CLI 模型不需要 server
    
    # 检查是否需要重启
    if _server and _server.current_model == model_id and _server.current_device == device:
        _server.touch()  # 更新最后活动时间
        return
    
    # 停止旧 server
    if _server:
        _server.stop()
    
    # 启动新 server
    _server = IOPaintProcess(model_id, device)
    _server.start()
```

**保活机制**:
- 时间戳记录: 每次调用时更新 `last_activity`
- 后台线程: 定期检测超时（默认 5 分钟）
- 自动卸载: 超时后优雅停止，释放显存

**配置参数** (来自 config/settings.json):
```json
{
  "server": {
    "keepalive_seconds": 300,      // 保活超时时间
    "port": 51821,                 // IOPaint Server 端口
    "startup_timeout": 1800,       // 启动超时时间
    "low_mem": true,               // 低内存优化
    "cpu_offload": false,          // CPU offload (显存优化)
    "cpu_textencoder": false       // Text Encoder offload
  }
}
```

### 3.3 核心处理模块

#### 3.3.1 去水印模块 (core/inpainter.py)

**入口**: `POST /api/inpaint`

```python
def inpaint(
    image: np.ndarray,           # 源图像 (RGB, uint8)
    rois: List[Tuple[int, int, int, int]],  # ROI 列表
    model: str,                  # 模型 ID
    device: str,                 # mps/cuda/cpu
    dilation: int = 10,          # ROI 膨胀像素
    disable_nsfw: bool = False,
) -> np.ndarray:
    """执行去水印处理"""
```

**处理流程**:
```
1. 膨胀 ROI (dilation 参数控制)
   └─ 避免边界伪影
   
2. 判断模型类型
   ├─ CLI 模型 (lama/zits/...)
   │  └─ subprocess 调用 iopaint 命令行
   │
   └─ Server 模型 (SD/AnyText)
      ├─ 启动/保活 IOPaint Server (HTTP)
      └─ 批量请求填充每个 ROI

3. 模型推理
   └─ 对每个膨胀后的 ROI 区域填充

4. 边界融合（可选后处理）
   └─ Poisson 混合、LaMa 二次精修
```

**模型分组** (从 models.yaml 动态加载):
```
快速修复（本地推理）
  ├─ LaMa (lama) - 默认推荐
  ├─ MiGAN (migan)
  ├─ ZITS (zits)
  └─ ...

专用模型
  └─ AnyText (Sanster/AnyText) - 文字水印

扩散模型（高质量·首次下载较大）
  ├─ SD Inpainting (runwayml/stable-diffusion-inpainting)
  ├─ Realistic Vision
  └─ SDXL Inpainting
```

#### 3.3.2 超分辨率模块 (core/upscaler.py)

**入口**: `POST /api/upscale`

```python
class Upscaler:
    def upscale(
        self,
        image: np.ndarray,        # 源图像
        scale: int = 4,           # 放大倍数
    ) -> np.ndarray:
        """执行超分辨率"""
```

**执行策略**:
```
1. 加载模型权重
   ├─ 本地缓存: models/realesrgan/*.pth
   └─ 首次使用: 自动下载

2. 创建 upsampler 实例
   └─ RRDBNet / SRVGGNet 架构

3. 逐块处理（大图避免 OOM）
   ├─ 分割成 512x512 tiles
   ├─ 各自推理
   └─ 融合结果

4. 后处理
   └─ 色彩空间转换
```

**支持的模型** (按放大倍数分组):
- **2x**: RealESRGAN_x2plus
- **4x**: RealESRGAN_x4plus, realesr-general-x4v3, RealESRNet_x4plus
- **4x 动漫**: RealESRGAN_x4plus_anime_6B
- **4x 视频**: realesr-animevideov3

#### 3.3.3 智能合成模块 (core/synthesizer.py)

**入门**: `POST /api/synthesis/run`

```python
class Synthesizer:
    def run(
        self,
        source_rgb: np.ndarray,           # 主图
        rois: Optional[List[Tuple]] = None,
        reference_rgb: Optional[np.ndarray] = None,
    ) -> np.ndarray:
        """执行合成处理"""
```

**支持的合成模式**:
| 模式 ID | 功能 | 需要参考图 | 核心模型 |
|---------|------|---------|--------|
| `background_replace` | 换背景 | ✅ | rembg (抠图) + LaMa (填充) |
| `outfit_swap` | 换装 | ✅ | Inpaint + 衣物检测 |
| `face_swap` | 换脸 | ✅ | Inpaint + 人脸对齐 |
| `virtual_tryon` | 虚拟试穿 | ✅ | 衣物拟合算法 |
| `prompt_inpaint` | 精准替换 | - | SD Inpainting + 提示词 |
| `auto_segment_edit` | 智能定位 | - | Grounded-SAM + HSV/SD |
| `instruction_edit` | 自由编辑 | - | InstructPix2Pix |

**核心处理链**:
```
1. 图像预处理
   ├─ 色彩空间转换 (BGR → RGB)
   └─ 尺寸归一化

2. 模式判断
   └─ 按模式 ID 调用对应处理器

3. ROI 处理（如有）
   ├─ 如果 rois 为 None：全图处理
   └─ 如果 rois 有值：仅处理指定区域

4. 后处理融合
   └─ Alpha blending / Poisson 混合

5. 输出
   └─ RGB numpy (uint8)
```

### 3.4 API 路由层

#### 3.4.1 水印去除路由 (routers/inpaint.py)

```python
POST /api/detect                  # 水印检测
POST /api/inpaint                 # ROI 模式去水印
POST /api/inpaint/mask            # Mask 模式去水印
GET  /api/models/inpaint          # 获取可用模型
```

**核心数据模型** (Pydantic):
```python
class InpaintRequest(BaseModel):
    image: str                    # Base64 PNG/JPG
    rois: List[List[int]]         # [[x1,y1,x2,y2], ...]
    model: str = "lama"
    device: str = "mps"
    dilation: int = 10
    disable_nsfw: bool = False

class InpaintWithMaskRequest(BaseModel):
    image: str
    mask: str                     # Base64 灰度掩码
    model: str = "lama"
    device: str = "mps"
    dilation: int = 10
    disable_nsfw: bool = False
```

**执行策略**:
- 使用 `ThreadPoolExecutor(max_workers=1)` 串行处理
- 异步包装 (asyncio.run_in_executor)
- 进度通过 WebSocket 实时推送

#### 3.4.2 模型管理路由 (routers/models.py)

```python
GET    /api/models/health              # 检测所有模型完整性
GET    /api/models/health/{model_id}   # 检测单个模型
GET    /api/models/list                # 列出所有模型 + 状态
GET    /api/models/download            # SSE: 一键下载缺失模型
GET    /api/models/download/{model_id} # SSE: 下载指定模型
DELETE /api/models/{model_id}/files    # 删除模型本地文件
```

**模型完整性检测** (ModelChecker):
```python
def check_model(model_id: str) -> ModelCheckResult:
    """
    检测单个模型的文件完整性
    
    返回状态:
    - "ok"         : 文件完整
    - "missing"    : 文件不存在
    - "corrupted"  : 文件损坏（大小不符）
    - "partial"    : 下载不完整
    - "unknown"    : 无法判断
    """
```

**下载流程** (SSE 流):
```
事件流:
  start   → {"total": 26}
  model   → {"id": "rmbg", "status": "downloading", "speed": "1.2 MB/s", ...}
  model   → {"id": "rmbg", "status": "done"}
  finish  → {"ok": 24, "skipped": 1, "failed": 1}
```

支持多种下载源:
- **rembg**: GitHub Releases（支持镜像加速）
- **HF 模型**: HuggingFace Repo（支持 Token 授权）
- **本地文件**: 直接 URL 下载（Real-ESRGAN 等）

### 3.5 基础设施层

#### 3.5.1 配置管理 (app/config.py)

**设计**: 扁平化键值对 (dot-notation)

```python
# 读取
device = config.get("inpaint.default_device", "cpu")
hf_token = config.get("network.hf_token", "")

# 写入
config.save({
    "inpaint.default_device": "mps",
    "server.keepalive_seconds": 300,
})

# 全部读取（合并默认值）
all_settings = config.get_all()
```

**配置文件结构** (config/settings.json):
```json
{
  "server": {
    "keepalive_seconds": 300,
    "port": 51821,
    "startup_timeout": 1800,
    "low_mem": true,
    "cpu_offload": false
  },
  "inpaint": {
    "default_dilation": 10,
    "default_device": "mps",
    "disable_nsfw": true
  },
  "network": {
    "hf_endpoint": "https://huggingface.co",
    "hf_token": "hf_xxx",
    "github_mirror": ""
  }
}
```

**缓存机制**:
- 懒加载 (首次读取时加载)
- 全局缓存 (_cache dict)
- 热重载支持 (reload())

#### 3.5.2 日志管理 (app/logging_manager.py)

**设计**: 内存缓冲 + WebSocket 实时推送

```python
class LogManager:
    MAX_ENTRIES = 500              # 最多保留 500 条
    
    def add(level: str, message: str, source: str = ""):
        """添加日志"""
    
    def info/warning/error/debug():
        """快捷方法"""
    
    def get_all() → List[dict]:
        """获取所有日志"""
    
    def get_errors() → List[dict]:
        """仅获取错误"""
    
    def get_filtered(level=None, limit=100) → List[dict]:
        """按级别和数量过滤"""

# WebSocket 推送
async def ws_connect(ws: WebSocket):
    """客户端连接时自动推送最近 100 条日志"""
```

**日志条目格式**:
```json
{
  "timestamp": "2026-05-09T10:30:45.123456",
  "level": "INFO",
  "message": "开始超分辨率: model=RealESRGAN_x4plus, device=mps",
  "source": "upscale"
}
```

#### 3.5.3 WebSocket 进度管理 (app/websocket/progress.py)

```python
class ProgressManager:
    async def connect(ws: WebSocket):
        """接受连接"""
    
    def disconnect(ws: WebSocket):
        """移除连接"""
    
    async def send_progress(percent: int, message: str):
        """广播进度"""
    
    async def send_complete(message: str):
        """广播完成"""
    
    async def send_error(message: str):
        """广播错误"""

# 全局单例
progress_manager = ProgressManager()

# WebSocket 端点
@router.websocket("/ws/progress")
async def websocket_progress(ws: WebSocket):
    await progress_manager.connect(ws)
    # 保持连接直到客户端断开
```

---

## 四、数据流与核心流程

### 4.1 去水印完整流程

```
┌─────────────────┐
│  用户上传图像    │
└────────┬────────┘
         │
         ▼
┌──────────────────────────────────┐
│ 前端: useImageStore.setSourceImage│
│  - 存储 Base64                    │
│  - 更新图像尺寸                   │
└────────┬─────────────────────────┘
         │
         ▼
┌──────────────────────────────────┐
│ 用户绘制 ROI (画笔模式)          │
│  - ImageCanvas 监听鼠标事件      │
│  - 添加到 useImageStore.rois[]   │
└────────┬─────────────────────────┘
         │
         ▼
┌──────────────────────────────────┐
│ 用户点击"开始处理"               │
└────────┬─────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────────────┐
│ 前端: useProcessStore.startProcess()             │
│  - 设置 isProcessing = true                      │
│  - 显示进度条                                    │
│  - 建立 WebSocket 进度监听                       │
└────────┬──────────────────────────────────────────┘
         │
         ▼
┌────────────────────────────────────────────────────────┐
│ HTTP POST /api/inpaint                                 │
│ {                                                      │
│   image: "data:image/png;base64,...",               │
│   rois: [[x1,y1,x2,y2], ...],                       │
│   model: "lama",                                      │
│   device: "mps",                                      │
│   dilation: 10,                                       │
│   disable_nsfw: false                                │
│ }                                                     │
└────────┬───────────────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────┐
│ 后端: inpaint.py 处理                    │
│                                          │
│ 1. 解码 Base64 → numpy (RGB, uint8)    │
│ 2. 计算膨胀 ROI (dilation 参数)        │
│ 3. 判断模型类型                         │
│    ├─ CLI 模型: subprocess 调用         │
│    └─ Server 模型: HTTP 请求 IOPaint  │
│ 4. 执行推理                             │
│ 5. Base64 编码结果                      │
│                                          │
│ 进度推送:                               │
│  - 10%: 加载模型                        │
│  - 50%: 推理中...                       │
│  - 90%: 编码结果                        │
│  - 100%: 完成                           │
└────────┬──────────────────────────────────┘
         │ (WebSocket 实时推送)
         ▼
┌──────────────────────────────┐
│ 前端: WebSocket 收到消息     │
│  - 更新 useProcessStore      │
│  - 刷新进度条 UI             │
└────────┬─────────────────────┘
         │
         ▼
┌──────────────────────────────┐
│ HTTP 响应返回结果            │
│ {                            │
│   image: "base64"            │
│ }                            │
└────────┬─────────────────────┘
         │
         ▼
┌──────────────────────────────────────┐
│ 前端: 处理响应                       │
│  - Base64 转换为 DataURL             │
│  - useImageStore.setResultImage      │
│  - useProcessStore.finishProcess     │
│  - 显示结果图像                       │
└──────────────────────────────────────┘
```

### 4.2 超分辨率流程

```
上传图像 (512x512)
   ↓
POST /api/upscale (model=RealESRGAN_x4plus)
   ↓
后端处理:
  1. 加载模型权重 (models/realesrgan/RealESRGAN_x4plus.pth)
  2. 分割成 tiles (避免 OOM)
  3. 各自推理 upscale 4x
  4. 融合结果 → 2048x2048
   ↓
HTTP 返回 Base64 + 新尺寸 (2048, 2048)
   ↓
前端显示结果
```

### 4.3 完整 Pipeline 流程

```
用户选择:
  - 去水印: model=lama
  - 后处理: method=poisson
  - 超分辨率: upscale=true, model=RealESRGAN_x4plus
   ↓
POST /api/pipeline
  ├─ 1️⃣ inpaint (lama, ROIs)      → 去水印结果
  ├─ 2️⃣ postprocess (poisson)     → 融合边界
  └─ 3️⃣ upscale (RealESRGAN_x4plus) → 超分辨率
   ↓
返回最终结果 (Base64)
```

---

## 五、技术栈与依赖

### 5.1 前端依赖

| 库 | 版本 | 用途 |
|----|------|------|
| react | ^18.3.1 | 核心框架 |
| react-dom | ^18.3.1 | DOM 渲染 |
| react-router-dom | ^6.26.2 | 路由管理 |
| zustand | ^4.5.5 | 状态管理 |
| axios | ^1.7.7 | HTTP 请求 |
| lucide-react | ^0.447.0 | 图标库 |
| tailwindcss | ^3.4.13 | CSS 框架 |
| electron | ^28.3.3 | 桌面应用 |
| electron-vite | ^2.3.0 | 打包工具 |

### 5.2 后端依赖

| 库 | 版本 | 用途 |
|----|------|------|
| fastapi | ==0.108.0 | Web 框架 |
| uvicorn | >=0.30.0 | ASGI 服务器 |
| pydantic | >=2.5.2 | 数据验证 |
| torch | >=2.1.0 | 深度学习框架 |
| torchvision | >=0.16.0 | 视觉工具 |
| iopaint | ==1.6.0 | **核心**: IOPaint 框架（包含 LaMa/ZITS/MiGAN/SD 等） |
| diffusers | ==0.27.2 | 扩散模型 |
| transformers | >=4.39.1 | NLP 模型库 |
| rembg | >=2.0.50 | 背景去除 |
| gfpgan | >=1.3.8 | 人脸增强 |
| realesrgan | >=0.3.0 | **超分辨率核心** |
| basicsr | >=1.4.2 | Real-ESRGAN 依赖 |
| opencv-python | >=4.10.0 | 图像处理 |
| PyYAML | >=6.0 | YAML 解析 |

### 5.3 模型库架构

```
models/
├── huggingface/
│   ├── hub/                    # HF 标准缓存
│   └── manual/
│       ├── runwayml--stable-diffusion-inpainting/
│       ├── Sanster--AnyText/
│       └── ...
├── torch/
│   ├── checkpoints/           # PyTorch 模型权重
│   └── ...
├── realesrgan/
│   ├── RealESRGAN_x4plus.pth
│   ├── RealESRGAN_x2plus.pth
│   └── ...
├── gfpgan/
│   └── GFPGANv1.3.pth
└── u2net/ (rembg)
    ├── u2net.onnx
    ├── u2netp.onnx
    └── ...
```

---

## 六、设计模式与架构决策

### 6.1 关键设计模式

| 模式 | 实现位置 | 目的 |
|------|--------|------|
| **单例** | BackendManager, ProgressManager, LogManager, IOPaintProcess | 全局唯一资源管理 |
| **工厂** | ModelRegistry.get_models_for_mode() | 动态模型对象创建 |
| **策略** | is_diffusion_model() + inpaint_via_cli/server | 模型执行策略选择 |
| **观察者** | Zustand 状态 + WebSocket | 事件驱动更新 |
| **中介者** | API Router 层 | 前后端通信中介 |
| **模板方法** | Pipeline 类 | inpaint → postprocess → upscale 流程 |

### 6.2 可扩展性设计

#### 6.2.1 添加新模型

**步骤 1**: 编辑 `core/models.yaml`
```yaml
- id: my_new_model
  name: "新模型名称"
  description: "模型描述"
  provider: "IOPaint"  # 或 "rembg", "Real-ESRGAN"
  tags: ["watermark_removal"]  # 关联模式
  iopaint_mode: "cli"  # 或 "server"
  iopaint_model_id: "Repo/ModelName"  # 仅 server 模式需要
  display_group: "分组标签"
  hf_model_id: "user/model"  # 若从 HF 下载
  download_url: "https://..."  # 直接下载链接
  local_path: "models/my_model.pth"  # 本地路径
  size_mb: 150  # 预期大小 (MB)
```

**步骤 2**: 前端自动获取
```typescript
// 无需修改代码，GET /api/models/inpaint 自动包含新模型
```

#### 6.2.2 添加新处理模式

**步骤 1**: 在 `core/models.yaml` 中添加 mode_groups
```yaml
mode_groups:
  - id: my_new_mode
    name: "新模式"
```

**步骤 2**: 标记模型的 tags
```yaml
models:
  - id: some_model
    tags: ["my_new_mode"]  # 关联新模式
```

**步骤 3**: 实现处理器
```python
# core/my_processor.py
class MyProcessor:
    def process(self, image, **kwargs):
        # 处理逻辑
        pass
```

**步骤 4**: 在路由中调用
```python
# routers/my_mode.py
@router.post("/my_mode/run")
async def run_my_mode(req: MyModeRequest):
    processor = MyProcessor(...)
    result = processor.process(...)
    return result
```

### 6.3 性能优化策略

#### 6.3.1 前端优化

| 优化项 | 方案 |
|-------|------|
| 代码分割 | React Router Lazy Loading |
| 状态管理 | Zustand 浅响应式（避免不必要渲染） |
| 图像处理 | Base64 传输（相比二进制序列化更简单） |
| 缓存策略 | 模型列表缓存、设置本地保存 |

#### 6.3.2 后端优化

| 优化项 | 方案 |
|-------|------|
| 模型保活 | IOPaint Server 5 分钟保活（避免重复加载） |
| 内存优化 | `server.low_mem=true` 启用内存节省模式 |
| GPU 优化 | `server.cpu_offload / cpu_textencoder` 显存卸载 |
| 大图处理 | Tile 分割推理（upscale） |
| 进度推送 | WebSocket 异步广播（非阻塞） |
| 并发控制 | ThreadPoolExecutor(max_workers=1) 串行处理 |

#### 6.3.3 模型下载优化

| 优化项 | 方案 |
|-------|------|
| 镜像加速 | 支持 HF 镜像 + GitHub 镜像配置 |
| 断点续传 | 文件完整性检测 + 部分文件重新下载 |
| 流式进度 | SSE 实时推送下载速度/进度 |
| 并行下载 | 多线程下载多个文件（可配置） |

### 6.4 安全性考虑

| 方面 | 措施 |
|------|------|
| IPC | Context Bridge 严格隔离，最少权限原则 |
| 文件操作 | 路径白名单验证，防止目录遍历 |
| 模型验证 | 文件完整性检测（SHA256/大小对比） |
| 环境变量 | HF_TOKEN 仅在后端设置，不暴露给前端 |
| CORS | 生产环境应限制 allow_origins 范围 |

---

## 七、部署架构

### 7.1 打包构建

#### 前端编译
```bash
npm run build
# 输出: frontend/dist/
```

#### 后端打包
```bash
pip install PyInstaller
pyinstaller backend/run.py --onefile --name hiimage-backend
# 输出: backend/dist/hiimage-backend
```

#### Electron 打包
```bash
npm run package
# 输出: frontend/out/HiImage-2.0.0.dmg (macOS)
#      frontend/out/HiImage-2.0.0.exe (Windows)
#      frontend/out/HiImage-2.0.0.AppImage (Linux)
```

### 7.2 资源打包

打包配置 (package.json):
```json
{
  "extraResources": [
    { "from": "assets/icons", "to": "icons" },
    { "from": "backend/dist/hiimage-backend", "to": "backend" },
    { "from": "models", "to": "models" },
    { "from": "config", "to": "config" }
  ]
}
```

### 7.3 Docker 部署

```yaml
# docker-compose.yml
services:
  backend:
    build:
      context: ./backend
      dockerfile: Dockerfile
    volumes:
      - ./models:/app/models           # 模型持久化
      - ./config:/app/config           # 配置持久化
      - ./tmp:/app/tmp                 # 临时文件
    environment:
      - HF_HOME=/app/models/huggingface
      - TORCH_HOME=/app/models/torch
    ports:
      - "8787:8787"
```

---

## 八、故障诊断与日志系统

### 8.1 日志收集

```
后端日志来源:
  ├─ app.main: 应用启动/关闭
  ├─ routers.*: API 处理日志
  ├─ core.*: 模型处理日志
  ├─ BackendManager: 进程管理日志
  └─ IOPaintServer: 扩散模型日志

前端日志来源:
  ├─ BackendManager: 后端进程日志（stdout/stderr）
  └─ 浏览器 console（开发者工具）
```

### 8.2 日志级别与来源标签

```python
log_manager.info("消息", source="upscale")
log_manager.error("错误", source="inpaint")
log_manager.debug("调试", source="model_server")
```

| 级别 | 保留条件 | 用途 |
|------|--------|------|
| DEBUG | 按需保留 (500 条缓冲) | 详细调试信息 |
| INFO | 关键操作 | 工作流进度 |
| WARNING | 所有 | 潜在问题 |
| ERROR | 所有 | 故障排查 |

### 8.3 常见问题诊断

#### 问题: 后端启动失败
```
症状: [Main] Failed to start backend
诊断:
  1. 检查日志: app log 中 Backend 错误消息
  2. 检查端口: netstat/lsof 查看 51821 是否被占用
  3. 检查环境: Python 版本、PyTorch 安装
  4. 重新启动: 关闭旧进程，重启应用
```

#### 问题: 模型加载缓慢
```
症状: 进度条卡在 "正在加载模型..."
诊断:
  1. 检查网络: 首次加载需下载模型权重
  2. 检查显存: nvidia-smi / GPU Memory
  3. 启用低内存模式: Settings → Memory Optimization
  4. 查看后端日志: 是否有 OOM 错误
```

#### 问题: 推理结果质量差
```
症状: 输出图像质量不理想
诊断:
  1. 检查模型选择: 某些模型适合特定场景
  2. 调整参数: dilation、敏感度等
  3. 尝试后处理: Poisson 融合、GFPGAN 人脸增强
  4. 使用超分: 最终质量优化
```

---

## 九、扩展与定制指南

### 9.1 添加自定义模型

```python
# core/my_custom_model.py
class MyCustomModel:
    def __init__(self, device="cpu"):
        self.device = device
        self.model = load_model()
    
    def forward(self, image):
        # 自定义处理
        return result

# routers/my_custom.py
@router.post("/my_custom/process")
async def process_my_custom(req: MyCustomRequest):
    model = MyCustomModel(device=req.device)
    result = await asyncio.run_in_executor(None, model.forward, image)
    return {"result": encode_image(result)}
```

### 9.2 自定义前端组件

```typescript
// renderer/pages/MyCustomPage.tsx
import { useBackendAPI } from '../hooks/useBackendAPI'

export default function MyCustomPage() {
  const { backendURL } = useBackendStore()
  
  const processCustom = async (image: string) => {
    const res = await fetch(`${backendURL}/api/my_custom/process`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ image }),
    })
    return res.json()
  }
  
  return (
    // UI
  )
}
```

### 9.3 定制化部署

```python
# 修改 config/settings.json.example
{
  "custom": {
    "my_param": "my_value"
  }
}

# 在代码中读取
from app.config import get
value = get("custom.my_param")
```

---

## 十、性能基准与优化建议

### 10.1 典型处理时间

| 操作 | 设备 | 时间 | 输入尺寸 |
|------|------|------|--------|
| 去水印 (LaMa) | M1 MPS | 0.8s | 512x512 |
| 去水印 (SD Inpainting) | M1 MPS | 5-10s | 512x512 |
| 超分 (4x) | M1 MPS | 2-3s | 512x512 → 2048x2048 |
| 背景去除 (rembg) | M1 MPS | 0.3s | 512x512 |
| 整个 Pipeline | M1 MPS | 10-15s | 512x512 |

### 10.2 内存占用

| 模型 | 显存 | 系统内存 |
|------|------|--------|
| LaMa (CLI) | - | 2GB |
| SD Inpainting | 4-6GB | 8GB |
| Real-ESRGAN x4 | 2-3GB | 4GB |
| rembg U2Net | 1GB | 2GB |
| **总计（同时运行）** | 8-10GB | 16GB |

### 10.3 优化建议

```
✅ 对用户:
  1. 选择合适的模型（速度 vs 质量权衡）
  2. 启用低内存优化（Settings → Memory）
  3. 使用较小的输入图像（512x512）
  4. 按需开启后处理和超分

✅ 对开发者:
  1. 对扩散模型使用 server 模式（5 分钟保活）
  2. 使用 tile 处理大图像（避免 OOM）
  3. 启用进度推送反馈（提升用户体验）
  4. 定期清理模型缓存（Settings → Model Management）
```

---

## 十一、版本与路线图

### 11.1 当前版本 (v2.0.0)

✅ 核心功能:
- 去水印（LaMa、SD、AnyText）
- 超分辨率（Real-ESRGAN）
- 智能合成（背景替换、换装、换脸等）
- 模型管理与完整性检测
- 配置管理与持久化
- WebSocket 实时进度推送
- 本地 + 远程双模式部署

### 11.2 未来规划

🚀 可能的扩展:
- 批量处理（多图自动化）
- GPU 优化（CUDA/ROCm 支持）
- 预设管理（保存处理参数组合）
- 高级后处理链
- CLI 工具
- REST API 文档生成（Swagger）

---

## 总结

**HiImage** 是一个设计完善的 AI 图像处理应用，具有以下特点:

1. **模块化架构**: 前后端分离，清晰的职责划分
2. **可扩展性强**: 集中配置体系，易于添加新模型/功能
3. **用户体验好**: WebSocket 进度反馈，本地 + 远程部署灵活
4. **技术栈现代**: React + FastAPI + PyTorch，生产级代码质量
5. **性能优化**: 模型保活、内存管理、流式下载等
6. **文档完善**: 代码注释清晰，易于维护和贡献

