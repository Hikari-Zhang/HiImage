# HiImage 系统架构文档

## 1. 概述 (Overview)
HiImage 是一个基于 **Electron + FastAPI** 构建的跨平台 AI 图像处理桌面应用程序。它通过集成多种先进的 AI 模型（如 IOPaint, Real-ESRGAN, rembg 等），为用户提供了一站式的图像修复、超分辨率、去水印及智能合成功能。

该系统的核心设计理念是 **“配置驱动”** 与 **“端云协同”**：通过配置文件动态扩展模型能力，并通过 Electron 进程管理实现后端 AI 推理引擎的自动化生命周期管理。

## 2. 高层架构 (High-Level Architecture)

系统采用 **客户端-服务器 (Client-Server)** 架构，分为三个主要层级：

*   **前端层 (Frontend/Client):** 基于 Electron 的桌面客户端。负责用户交互（UI）、图像渲染（Canvas）、任务监控以及后端服务的启动与维护。
*   **后端层 (Backend/Server):** 基于 FastAPI 的 Python 服务。作为 AI 推议引擎的核心，负责接收指令、调度模型、执行复杂的 AI 算法并管理下载队列。
*   **模型层 (Model Layer):** 这是一个由配置文件驱动的逻辑层。负责 AI 权重的下载、本地缓存管理以及针对不同硬件环境（CPU/GPU）的推理优化。

## 3. 前端层架构 (Frontend Architecture)

HiImage 前端基于 **Electron** 构建，采用 **主进程 (Main Process) + 预加载脚本 (Preload Script) + 渲染进程 (Renderer Process)** 的三层架构，确保安全性与模块化。

### 3.1 主进程 (`src/main/`)

主进程是 Electron 应用的入口点，负责管理应用生命周期、原生窗口及与操作系统的交互。

*   **`index.ts` (应用入口):**
    *   **窗口管理:** 创建 `BrowserWindow` 实例，配置跨平台窗口样式（如 macOS 的 `hiddenInset` 标题栏）。
    *   **IPC 通信:** 通过 `ipcMain.handle` 注册系统级操作，如打开文件对话框 (`dialog:openFile`)、保存文件 (`dialog:saveFile`)、读写本地文件 (`file:save`, `file:read`)。
    *   **生命周期管理:** 监听 `whenReady`, `window-all-closed`, `before-quit` 等事件，确保应用平滑启动与退出。
*   **`backend-manager.ts` (后端进程管理器):**
    *   **核心职责:** 管理 Python FastAPI 后端进程的生命周期（启动、停止、重启）。
    *   **双模式支持:** 支持 `local` (本地启动 Python 进程) 和 `remote` (连接远程服务器) 两种模式。
    *   **进程守护:** 监听进程退出事件，清理残留进程，确保端口不被占用。
    *   **健康检查:** 通过轮询 `/api/health` 接口确认后端服务是否就绪。

### 3.2 预加载脚本 (`src/preload/`)

作为主进程与渲染进程之间的安全桥梁，使用 `contextBridge` 仅暴露必要的 API。

*   **`index.ts`:**
    *   **API 暴露:** 将 `electronAPI` 对象注入到 `window` 中，提供 `openFile`, `saveFile`, `getBackendURL` 等方法。
    *   **安全保障:** 避免直接在渲染进程中启用 Node.js 集成，防止 XSS 攻击。

### 3.3 渲染进程 (`src/renderer/`)

渲染进程负责 UI 展示与用户交互，采用 **React + TypeScript + Vite** 构建。

#### 3.3.1 组件层 (`components/`)
*   **`ImageCanvas.tsx` (核心画布组件):**
    *   **Canvas 渲染:** 基于原生 `HTML5 Canvas` 实现高性能图像渲染。
    *   **交互逻辑:** 支持拖拽平移 (Pan)、滚轮缩放 (Zoom)、以及 ROI (感兴趣区域) 的绘制与选择。
    *   **坐标转换:** 实现屏幕坐标与图像坐标的精确转换，确保不同缩放比例下的交互准确性。
*   **布局组件 (`layout/`):** 实现应用的主界面布局（侧边栏、工具栏、状态栏）。
*   **UI 组件 (`ui/`):** 基于 Tailwind CSS 封装的通用 UI 组件（按钮、输入框、滑块等）。
*   **模型选择 (`ModelSelect/`):** 动态加载并展示后端支持的 AI 模型列表。

#### 3.3.2 状态管理层 (`stores/`)
采用 **Zustand** 进行轻量级状态管理，各 Store 职责单一：

| Store 文件 | 职责描述 |
| :--- | :--- |
| `useImageStore.ts` | 管理原图/结果图的 Base64 数据、图像尺寸、ROI 区域列表及选中状态。 |
| `useBackendStore.ts` | 管理后端连接状态 (`isConnected`)、后端 URL、WebSocket 连接状态。 |
| `useProcessStore.ts` | 管理当前处理任务的状态（空闲、运行中、完成、失败）及进度百分比。 |
| `useDownloadStore.ts` | 管理模型下载任务队列、下载进度、暂停/恢复/取消操作。 |
| `useModelStore.ts` | 缓存模型列表、当前选中的模型及其配置参数。 |
| `useSettingsStore.ts` | 管理应用设置（如默认输出路径、主题颜色等）。 |

#### 3.3.3 自定义 Hooks (`hooks/`)
封装业务逻辑，供组件调用：

*   **`useBackendAPI.ts`:** 统一封装所有后端 API 调用（如 `inpaint`, `upscale`, `runPipeline`），处理 URL 拼接、请求发送、错误拦截。
*   **`useDownloadManager.ts`:** 管理下载任务的并发控制，调用 `useDownloadStore` 更新状态。
*   **`useDeviceOptions.ts`:** 获取并向用户展示可用的计算设备（CPU/GPU）。

#### 3.3.4 通信机制
*   **HTTP 请求:** 使用原生 `fetch` API 向后端发送处理指令（RESTful 风格）。
*   **WebSocket:** 监听后端推送的实时进度与日志（如 `progress`, `log` 事件）。

### 3.2 后端模块 (Backend)
*   **API 路由 (`backend/app/routers/`):** 定义了业务接口，如 `inpaint.py` (修复)、`upscale.py` (超分) 等，将前端请求转化为具体的模型执行指令。
*   **模型注册中心 (`backend/core/model_registry.py`):** 系统的“大脑”，根据 `models.yaml` 动态加载和管理支持的所有模型及其对应的执行器。
*   **AI 执行器 (`backend/core/`):**
    *   `inpainter.py`: 封装了 IOPaint 推理逻辑，支持复杂的图像修复任务。
    *   `upscaler.py`: 负责 Real-ESRGAN 等超分辨率模型的执行。
    *   `model_server.py`: 提供模型推理的 HTTP 服务能力。
*   **任务与下载管理 (`backend/core/download_queue.py`):** 管理并发的模型下载任务，支持断点续传与队列调度，确保大规模模型下载时的系统稳定性。

## 4. 后端层架构 (Backend Architecture)

HiImage 后端基于 **FastAPI** 构建，采用 **配置驱动 (Configuration-Driven)** 的设计理念，通过 `models.yaml` 统一管理所有 AI 模型，实现了高度的可扩展性。

### 4.1 目录结构与核心模块

```
backend/
├── app/
│   ├── routers/          # FastAPI 路由（API 端点）
│   │   ├── inpaint.py       # 去水印接口
│   │   ├── upscale.py       # 超分辨率接口
│   │   ├── synthesis.py     # 智能合成接口
│   │   ├── models.py        # 模型管理接口（下载、状态查询）
│   │   ├── postprocess.py   # 后处理方法接口
│   │   ├── settings.py      # 系统配置接口
│   │   └── system.py       # 系统信息接口
│   ├── config.py         # 配置管理（读取 settings.json）
│   └── websocket/       # WebSocket 进度推送
├── core/
│   ├── model_registry.py    # 模型注册表（读取 models.yaml）
│   ├── model_checker.py     # 模型完整性检查
│   ├── download_queue.py    # 下载队列调度器
│   ├── model_download_helper.py  # 模型下载辅助函数
│   ├── paths.py            # 路径管理（缓存目录、模型路径）
│   ├── inpainter.py        # 去水印执行器（IOPaint）
│   ├── upscaler.py         # 超分辨率执行器（Real-ESRGAN）
│   ├── restormer_executor.py  # 图像复原执行器
│   ├── synthesizer.py      # 智能合成执行器
│   ├── iopaint_executor.py # IOPaint 执行器
│   ├── model_server.py     # 模型 HTTP 服务管理
│   └── models.yaml        # 统一模型配置文件
└── run.py               # 后端启动入口
```

### 4.2 模型注册与配置系统 (`core/models.yaml` + `model_registry.py`)

这是 HiImage 后端最核心的设计之一：**所有模型配置集中管理，代码无需修改即可扩展新模型。**

*   **`models.yaml` (配置清单):**
    *   定义了所有支持模型的元数据：`id`, `name`, `provider`, `tags`, `description`, `size_mb`, `supported_params` 等。
    *   支持多种 `provider` 类型：`rembg`, `IOPaint`, `diffusers`, `HiImage`, `facexlib`, `realEserGan`, `restormer`。
    *   通过 `tags` 字段将模型关联到功能模式（如 `watermark_removal`, `upscale`）。
*   **`model_registry.py` (运行时加载器):**
    *   在进程启动时读取 `models.yaml`。
    *   提供全局变量 `MODELS` (模型列表) 和 `MODE_GROUPS` (模式分组)。
    *   提供查询接口：`get_model(id)`, `get_models_for_mode(mode_id)`。

### 4.3 模型下载与队列管理 (`download_queue.py`)

为了避免多个模型同时下载导致网络阻塞或磁盘 IO 瓶颈，后端实现了 **智能下载队列**。

*   **`DownloadQueue` (全局单例):**
    *   **并发控制:** 通过 `max_concurrent` 限制同时下载的任务数（默认 3）。
    *   **去重机制:** 如果模型已在队列中或正在下载，再次提交会直接返回现有任务。
    *   **排队机制:** 超出并发数的任务进入 `pending` 队列，当前任务完成后自动启动排队任务。
    *   **取消支持:** 支持取消 `queued` 或 `downloading` 状态的任务。
    *   **实时订阅:** 支持多个客户端通过 SSE (Server-Sent Events) 订阅同一个模型的下载进度。
*   **下载策略选择 (`_run_download`):**
    *   根据 `provider` 类型自动选择下载方式：
        *   `rembg`: 使用 `rembg` 库内置的下载逻辑。
        *   `diffusers` (单模型): 使用 `huggingface_hub.snapshot_download`。
        *   `diffusers` (多模型): 遍历 `hf_models` 列表，逐个下载。
        *   `realEserGan` / `facexlib`: 使用 `urllib.request` 直接下载 `.pth` 文件。
    *   下载过程中通过 `progress_callback` 实时汇报进度（速度、已下载大小）。

### 4.4 AI 执行器模块

这是实际调用 AI 模型进行推理的层。每个执行器封装了一种特定的 AI 能力。

*   **`inpainter.py` (去水印/修复):**
    *   支持 **双模式调用**：
        1.  **CLI 模式:** 针对 LaMa, MiGAN, ZITS 等快速模型，直接调用 `iopaint run` 命令（子进程）。
        2.  **Server 模式:** 针对 SD, SDXL, FLUX 等扩散模型，先启动 `iopaint start` 常驻服务（保活 5 分钟），再通过 HTTP 请求触发推理。
    *   自动处理 `device_override`（如 MPS 不兼容时强制回退 CPU）。
*   **`upscaler.py` (超分辨率):**
    *   使用 **Real-ESRGAN** 库。
    *   根据 `arch` (`RRDBNet` or `SRVGGNetCompact`) 动态构建神经网络。
    *   支持 `scale` (放大倍率) 和 `outscale` (输出倍率，用于同分辨率增强)。
*   **`restormer_executor.py` (图像复原):**
    *   使用 **Restormer** (Transformer-based) 模型。
    *   支持多种任务类型：`denoise`, `deblur`, `derain`, `dehaze`。

### 4.5 API 路由层 (`app/routers/`)

定义了 RESTful API 端点，负责接收前端请求、解析参数、调用核心执行器、返回结果。

*   **`inpaint.py`:**
    *   `POST /api/detect`: 水印检测（返回疑似区域坐标）。
    *   `POST /api/inpaint`: 执行去水印（接收 ROI 坐标，返回修复后图像）。
*   **`upscale.py`:**
    *   `POST /api/upscale`: 执行超分辨率（接收图像和模型 ID，返回放大后图像）。
*   **`models.py`:**
    *   `GET /api/models/health`: 检测所有模型完整性。
    *   `POST /api/models/download/{model_id}`: 提交模型下载任务。
    *   `GET /api/models/subscribe/{model_id}`: SSE 订阅下载进度。
*   **通信机制:**
    *   **HTTP:** 用于发送指令和接收结果（同步）。
    *   **WebSocket:** 用于实时推送处理进度（异步）。后端通过 `websocket.progress.ProgressManager` 向前端发送百分比进度和日志。

### 4.6 路径与缓存管理 (`core/paths.py`)

统一管理所有文件 I/O 路径，确保跨平台兼容性（Windows/macOS/Linux）。

*   **`PROJECT_ROOT`:** 项目根目录。
*   **`MODELS_CACHE_DIR`:** 模型缓存根目录（默认 `~/.cache/hiimage/models/`）。
*   **`resolve_model_cache_path(config)`:** 根据模型配置自动解析完整的权重文件路径（如 `~/.cache/hiimage/models/realesrgan/RealESRGAN_x4plus.pth`）。

## 5. 数据流向 (Data Flow)

### 4.1 图像处理请求流
1.  **用户触发:** 用户在前端画布上绘制 Mask 或选择 ROI，并点击“开始处理”。
2.  **请求发送:** 前端通过 HTTP POST 请求将图像路径/数据及处理参数发送至 FastAPI 路由。
3.  **任务调度:** 后端 Router 识别模型类型，调用 `model_registry` 获取对应的执行器（如 `inpainter`）。
4.  **模型推理:** 执行器加载本地模型权重，调用 AI 引擎进行计算。
5.  **进度推送:** 在推理过程中，后端通过 WebSocket 实时向前端推送 `progress` 事件（百分比、当前步骤）。
6.  **结果反馈:** 处理完成后，后端返回处理后的文件路径，前端 Canvas 自动刷新显示处理结果。

## 5. 技术栈 (Technology Stack)

| 维度 | 技术选型 | 说明 |
| :--- | :--- | :--- |
| **应用外壳** | Electron 28 | 实现跨平台桌面应用能力 |
| **前端框架** | React 18 + TypeScript | 构建高性能、类型安全的 UI |
| **前端画布** | Konva.js | 高性能 Canvas 交互与图形绘制 |
| **后端框架** | FastAPI + Uvicorn | 高性能异步 Python Web 框架 |
| **AI 推理** | IOPaint, Real-ESRGAN, rembg | 核心 AI 算法实现 |
| **状态管理** | Zustand | 轻量级前端状态管理 |
| **样式处理** | Tailwind CSS | 响应式、原子化 CSS 框架 |
| **通信协议** | HTTP (REST) + WebSocket | 指令控制与实时进度推送 |

## 6. 模型管理机制 (Model Management)

*   **动态扩展:** 所有支持的模型及其参数均定义在 `backend/core/models.yaml` 中。新增模型无需修改核心代码，只需更新配置文件。
*   **智能下载:** 系统具备自动下载能力。当检测到本地缓存缺失时，会自动触发下载任务，并支持使用 `hf-mirror.com` 镜像站以优化国内下载体验。
*   **路径标准化:** 统一使用 `paths.py` 进行模型路径解析，确保模型权重、缓存目录与项目根目录的逻辑一致性。
