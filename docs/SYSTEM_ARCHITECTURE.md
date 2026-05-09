# HiImage 系统架构设计文档

## 1. 项目概述

### 1.1 项目简介
HiImage 是一款 AI 驱动的图像处理桌面工具，基于 Electron + FastAPI 构建。提供去水印、超分辨率、智能合成等功能。

### 1.2 技术栈

| 层级 | 技术栈 |
|------|----------|
| 前端 | Electron 28 + React 18 + TypeScript + Vite |
| 状态管理 | Zustand |
| UI | Tailwind CSS（暗色主题）+ Lucide React 图标 |
| 画布 | Konva.js（ROI 区域绘制） |
| 后端 | FastAPI + Uvicorn |
| AI 推理 | IOPaint / Real-ESRGAN / rembg / GFPGAN |
| 进程管理 | 本地后端作为子进程管理 |
| 部署 | 本地启动 / 远程连接 / Docker Compose |

---

## 2. 系统架构设计

### 2.1 整体架构图

```
┌─────────────────────────────────────────────────────────────────┐
│                      HiImage 桌面应用                          │
├─────────────────────────────────────────────────────────────────┤
│                                                               │
│  ┌────────────────────────────────────────────────────────┐    │
│  │             前端层 (Electron + React)                  │    │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────────┐     │    │
│  │  │ 主进程    │  │ 预加载    │  │ 渲染进程      │     │    │
│  │  │ (Main)   │  │ (Preload)│  │ (Renderer)   │     │    │
│  │  │           │  │           │  │               │     │    │
│  │  │ - 窗口管理│  │ - IPC桥接 │  │ - React页面   │     │    │
│  │  │ - 后端管理│  │ - 安全沙箱│  │ - Zustand    │     │    │
│  │  │ - IPC处理 │  │           │  │ - Konva画布   │     │    │
│  │  └──────────┘  └──────────┘  └──────────────┘     │    │
│  └────────────────────────────────────────────────────────┘    │
│                          ↕ IPC                                │
│  ┌────────────────────────────────────────────────────────┐    │
│  │             后端层 (FastAPI + Uvicorn)                │    │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────────┐     │    │
│  │  │ FastAPI   │  │  路由     │  │  核心处理     │     │    │
│  │  │ 应用入口  │  │ 模块     │  │  模块         │     │    │
│  │  │           │  │          │  │               │     │    │
│  │  │ - 生命周期│  │ - inpaint│  │ - Inpainter  │     │    │
│  │  │ - CORS    │  │ - upscale│  │ - Upscaler   │     │    │
│  │  │ - WebSocket│  │ - synthesis│ │ - Synthesizer│    │    │
│  │  └──────────┘  └──────────┘  └──────────────┘     │    │
│  └────────────────────────────────────────────────────────┘    │
│                          ↕ HTTP/WebSocket                     │
│  ┌────────────────────────────────────────────────────────┐    │
│  │              AI 模型层                                  │    │
│  │  - IOPaint (去水印模型)                                │    │
│  │  - Real-ESRGAN (超分辨率)                             │    │
│  │  - rembg (抠图模型)                                   │    │
│  │  - GFPGAN (人脸增强)                                  │    │
│  │  - Diffusers (扩散模型)                               │    │
│  └────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 核心设计原则

1. **模型配置外部化**：所有模型配置在 `backend/core/models.yaml` 维护，无需修改代码即可新增模型
2. **进程隔离**：后端作为独立子进程运行，前端退出时自动清理
3. **模型懒加载**：首次使用时自动下载模型权重，支持进度回调
4. **双模式调用**：快速模型使用 CLI 批处理，扩散模型使用 HTTP Server 保活
5. **跨平台支持**：支持 macOS / Windows / Linux，兼容 Apple Silicon MPS 和 NVIDIA CUDA

---

## 3. 前端架构

### 3.1 目录结构

```
frontend/src/
├── main/                    # Electron 主进程
│   ├── index.ts            # 主进程入口
│   └── backend-manager.ts  # 后端进程管理（启动/停止/清理）
├── preload/                # 预加载脚本（IPC 桥接）
└── renderer/              # React 渲染进程
    ├── App.tsx                 # 应用根组件
    ├── pages/                 # 页面组件
    │   ├── WatermarkRemoval.tsx
    │   ├── SuperResolution.tsx
    │   ├── SmartSynthesis.tsx
    │   ├── Settings.tsx
    │   └── Logs.tsx
    ├── components/            # 复用组件
    │   ├── layout/           # 布局组件
    │   ├── ui/              # UI 组件
    │   ├── ImageCanvas.tsx   # 图像画布（Konva.js）
    │   └── ImageCompare.tsx  # 图像对比组件
    ├── stores/               # Zustand 状态管理
    │   ├── useBackendStore.ts
    │   └── useModelStore.ts
    ├── hooks/                # 自定义 React Hooks
    └── types/               # TypeScript 类型定义
```

### 3.2 主进程 (Main Process)

**职责**：
- 创建和管理应用窗口
- 管理后端进程生命周期
- 处理 IPC 通信
- 文件系统操作（打开/保存对话框）

**核心类**：`BackendManager`
- 支持本地模式和远程连接模式
- 串行队列调度，避免并发竞态
- 跨平台进程清理（macOS/Linux: SIGTERM + SIGKILL；Windows: taskkill）
- 端口冲突自动清理

### 3.3 渲染进程 (Renderer Process)

**技术栈**：React 18 + TypeScript + Vite

**状态管理**：Zustand
- `useBackendStore`：管理后端连接状态
- `useModelStore`：管理模型列表（从后端动态加载）

**路由**：React Router (HashRouter)
- `/watermark` - 去水印
- `/upscale` - 超分辨率
- `/synthesis` - 智能合成
- `/settings` - 设置页
- `/logs` - 日志页

### 3.4 IPC 通信

**预加载脚本**暴露安全的方法给渲染进程：

| IPC 通道 | 说明 |
|---------|------|
| `dialog:openFile` | 打开文件对话框 |
| `dialog:saveFile` | 保存文件对话框 |
| `backend:getURL` | 获取后端 URL |
| `backend:getConfig` | 获取连接配置 |
| `backend:updateConfig` | 更新连接配置 |
| `file:save` | 保存图像数据到磁盘 |
| `file:read` | 从磁盘读取图像文件 |
| `window:minimize` | 最小化窗口 |
| `window:maximize` | 最大化/还原窗口 |
| `window:close` | 关闭窗口 |

---

## 4. 后端架构

### 4.1 目录结构

```
backend/
├── app/
│   ├── main.py              # FastAPI 应用入口、生命周期管理
│   ├── config.py           # 配置加载（config/settings.json）
│   ├── logging_manager.py   # 日志管理 + WebSocket 推送
│   ├── routers/            # API 路由
│   │   ├── inpaint.py     # 去水印接口
│   │   ├── upscale.py     # 超分辨率接口
│   │   ├── synthesis.py   # 智能合成接口
│   │   ├── settings.py    # 配置管理接口
│   │   ├── logs.py        # 日志查询接口
│   │   ├── system.py      # 系统状态接口
│   │   ├── models.py      # 模型列表接口
│   │   └── postprocess.py # 后处理接口
│   └── websocket/
│       └── progress.py     # 进度推送 WebSocket
├── core/                   # 核心 AI 处理逻辑
│   ├── inpainter.py       # IOPaint 调用封装（CLI + HTTP Server 双模式）
│   ├── model_server.py    # IOPaint HTTP Server 进程管理（单例）
│   ├── upscaler.py       # Real-ESRGAN 超分辨率
│   ├── synthesizer.py    # 智能合成核心
│   ├── model_registry.py  # 模型注册表（models.yaml 驱动）
│   ├── watermark_detector.py  # 自动水印检测
│   └── models.yaml       # 统一模型配置文件
├── requirements.txt        # 依赖声明（松散版本）
├── requirements-locked.txt # 锁定版本（pip freeze）
├── Dockerfile
└── run.py                # 后端启动脚本
```

### 4.2 FastAPI 应用入口

**文件**：`backend/app/main.py`

**生命周期管理**：
- **启动时**：初始化日志系统、设置环境变量（HF_ENDPOINT、HF_TOKEN、HF_HOME、TORCH_HOME）
- **关闭时**：停止 IOPaint Server（如果在运行）

**中间件**：
- CORS 中间件（允许 Electron renderer 访问）

**路由注册**：
- `/api/system` - 系统状态
- `/api/inpaint` - 去水印
- `/api/upscale` - 超分辨率
- `/api/synthesis` - 智能合成
- `/api/settings` - 配置管理
- `/api/logs` - 日志查询
- `/api/models` - 模型列表
- `/api/ws/progress` - 进度推送 WebSocket

### 4.3 配置管理

**文件**：`backend/app/config.py`

**配置来源**：`config/settings.json`

**默认配置**：
```python
DEFAULTS = {
    "server.keepalive_seconds": 300,
    "server.port": 51821,
    "server.startup_timeout": 1800,
    "server.low_mem": True,
    "server.cpu_offload": False,
    "server.cpu_textencoder": False,
    "inpaint.default_dilation": 10,
    "inpaint.default_device": "mps",
    "network.hf_endpoint": "https://huggingface.co",
    "network.hf_token": "",
}
```

**配置加载**：扁平化 key（如 `server.port`）自动转换为嵌套结构

### 4.4 核心 AI 处理模块

#### 4.4.1 去水印模块 (Inpainter)

**文件**：`backend/core/inpainter.py`

**双模式调用**：
1. **快速模型**（LaMa/MiGAN/ZITS 等）：CLI 批处理模式，按需调用
2. **扩散模型**（AnyText/SD 系列）：HTTP Server 模式，保活 5 分钟

**处理流程**：
1. 接收 Base64 编码的图像和 ROI 区域列表
2. 创建掩码（Mask）
3. 根据模型类型选择调用模式
4. 返回 Base64 编码的处理结果

#### 4.4.2 超分辨率模块 (Upscaler)

**文件**：`backend/core/upscaler.py`

**使用 Real-ESRGAN 库**

**支持模型类型**：
- RRDBNet（标准大模型）：`RealESRGAN_x4plus`、`RealESRGAN_x2plus` 等
- SRVGGNetCompact（轻量模型）：`realesr-general-x4v3`、`realesr-animevideov3` 等

**处理流程**：
1. 检查权重文件是否存在，不存在则自动下载
2. 根据 `arch` 字段构建网络模型
3. 执行超分辨率放大
4. 返回结果图像

#### 4.4.3 模型注册表 (ModelRegistry)

**文件**：`backend/core/model_registry.py`

**数据来源**：`backend/core/models.yaml`

**功能**：
- 读取并解析 YAML 配置
- 展开 `mode_groups.models`（按 tags 自动关联）
- 提供对外接口：`MODELS`、`MODE_GROUPS`、`MODEL_BY_ID`、`MODE_BY_ID`
- 提供工具函数：`get_model()`、`get_mode()`、`get_models_for_mode()`

**新增模型**：只需编辑 `models.yaml`，无需修改代码

---

## 5. 模型管理系统

### 5.1 模型配置结构

**文件**：`backend/core/models.yaml`

**核心字段**：
- `id` - 内部 ID
- `name` - 展示名称
- `provider` - 模型来源（IOPaint、realesrgan、rembg、diffusers、HiImage、facexlib）
- `tags` - 所属功能模式 ID 列表
- `iopaint_model_id` - 传给 iopaint 的参数
- `iopaint_mode` - 调用方式（cli / server）
- `hf_model_id` - HuggingFace Hub model ID
- `download_url` - 模型下载页面 URL
- `local_path` - 本地权重路径
- `display_group` - UI 分组标题

### 5.2 模型分类

#### 5.2.1 去水印模型

**快速修复模型**（CLI 模式）：
- LaMa（推荐·通用）
- MiGAN（GAN·快速）
- ZITS（边缘感知）
- FCF（快速填充）
- MAT（精细修复）
- LDM（轻量扩散）
- Manga（漫画专用）
- CV2（传统算法）

**专用扩散模型**（Server 模式，保活 5 分钟）：
- AnyText（文字水印专用）
- PowerPaint V1（最强通用）
- SD 1.5 Inpainting（通用）
- Realistic Vision V5.1（写实照片）
- DreamShaper（艺术风格）
- Anything V4（动漫/插画）
- SDXL Inpainting（高分辨率）

#### 5.2.2 超分辨率模型

**通用超分辨率**：
- RealESRGAN_x4plus（4x 通用照片，推荐）
- RealESRGAN_x2plus（2x 通用照片）

**精细化增强**：
- realesr-general-x4v3（4x 精细化增强·轻量，推荐）
- RealESRNet_x4plus（4x 自然细化，无 GAN）

**动漫/插画**：
- RealESRGAN_x4plus_anime_6B（4x 动漫/插画，静图）
- realesr-animevideov3（4x 动漫视频/帧序列）

#### 5.2.3 智能合成模型

**换背景**：BiRefNet-General、RMBG 2.0、IS-Net General 等

**换装/换脸/试穿**：FLUX.1-Fill-dev、SDXL、PowerPaint v2、MAT、LaMa 等

**精准替换**：FLUX.1-Fill-dev、SDXL Inpainting、PowerPaint v2、SD 1.5 Inpainting

**智能定位**：GDINO + SAM + FLUX.1-Fill、GDINO + SAM + SDXL、SegFormer + HSV 换色等

**自由编辑**：FLUX.1-dev、SDXL Img2Img、MagicBrush、InstructPix2Pix

### 5.3 模型下载管理

**自动下载**：
- 首次使用某模型时自动下载权重
- 快速模型权重存于 `~/.cache/torch/hub/checkpoints/`
- 扩散模型权重存于 HuggingFace 缓存

**下载进度**：
- 支持进度回调
- 通过 WebSocket 推送到前端

**镜像加速**：
- 国内用户可设置 `network.hf_endpoint` 为 `https://hf-mirror.com`

---

## 6. 进程管理

### 6.1 后端进程管理

**类**：`BackendManager`（前端主进程）

**职责**：
- 启动本地后端进程（Python FastAPI）
- 停止后端进程（优雅终止 + 强制清理）
- 检查远程后端可达性
- 管理连接配置（本地/远程模式切换）

**进程清理策略**：
1. 发送 SIGTERM（macOS/Linux）或 taskkill（Windows）
2. 等待 5 秒优雅退出
3. 如果仍未退出，发送 SIGKILL 强制终止进程组

**端口管理**：
- 启动前自动清理占用指定端口的残留进程
- 避免端口冲突导致启动失败

### 6.2 IOPaint Server 管理

**文件**：`backend/core/model_server.py`

**职责**：
- 管理 IOPaint HTTP Server 进程（扩散模型使用）
- 单例模式，避免重复启动
- 保活机制（默认 5 分钟无请求自动停止）
- 线程安全的启动/停止操作

---

## 7. 通信机制

### 7.1 HTTP REST API

**基础 URL**：`http://127.0.0.1:8787`（本地模式）

**主要端点**：

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/health` | GET | 健康检查 |
| `/api/models/inpaint` | GET | 获取去水印模型列表 |
| `/api/models/upscale` | GET | 获取超分辨率模型列表 |
| `/api/inpaint` | POST | 去水印处理 |
| `/api/inpaint/detect` | POST | 自动检测水印区域 |
| `/api/upscale` | POST | 超分辨率处理 |
| `/api/synthesis/*` | POST | 智能合成处理 |
| `/api/settings` | GET/POST | 配置管理 |
| `/api/logs` | GET | 日志查询 |

### 7.2 WebSocket 进度推送

**端点**：`/api/ws/progress`

**功能**：
- 实时推送处理进度（百分比 + 消息）
- 处理完成通知
- 错误处理通知

**实现**：
- `backend/app/websocket/progress.py`
- 使用 `progress_manager` 管理 WebSocket 连接
- 线程安全的进度推送（子线程 → 主线程事件循环）

### 7.3 IPC 通信

**渲染进程 → 主进程**：
- 通过 `window.electronAPI` 调用预加载脚本暴露的方法
- 所有文件操作通过 IPC 委托给主进程

**主进程 → 后端进程**：
- 标准输出/错误流转发
- 进程生命周期管理

---

## 8. 数据流动

### 8.1 去水印流程

```
用户选择图像
    ↓
前端：框选 ROI 区域（Konva.js 画布）
    ↓
前端：将图像和 ROI 转换为 Base64
    ↓
HTTP POST /api/inpaint
    ↓
后端：解码 Base64 → NumPy 数组
    ↓
后端：创建掩码（Mask）
    ↓
后端：根据模型类型选择调用模式
    ├─ 快速模型 → CLI 模式（iopaint run）
    └─ 扩散模型 → HTTP Server 模式（iopaint start）
    ↓
后端：执行推理处理
    ↓
WebSocket：推送进度（0-100%）
    ↓
后端：编码结果图像为 Base64
    ↓
HTTP Response：返回 Base64 图像
    ↓
前端：解码 Base64 → 显示结果
    ↓
用户：保存处理结果
```

### 8.2 超分辨率流程

```
用户选择图像
    ↓
前端：选择模型和放大倍率
    ↓
HTTP POST /api/upscale
    ↓
后端：解码 Base64 → NumPy 数组
    ↓
后端：懒加载模型（检查权重 → 自动下载 → 构建模型）
    ↓
后端：执行超分辨率放大（Real-ESRGAN）
    ↓
WebSocket：推送进度
    ↓
后端：编码结果图像为 Base64
    ↓
HTTP Response：返回 Base64 图像 + 新尺寸
    ↓
前端：显示对比结果
```

---

## 9. 部署方案

### 9.1 本地部署

**开发模式**：
```bash
cd frontend
npm run dev
# → Vite dev server @ http://localhost:5173
# → FastAPI backend @ http://localhost:8787
```

**生产构建**：
```bash
cd frontend
npm run build    # 输出 frontend/dist/
npm run package  # 打包 Electron 应用（.dmg / .exe）
```

### 9.2 Docker 部署

**文件**：`docker-compose.yml`

**服务**：
- `backend` - FastAPI 后端服务
- `backend-gpu`（可选）- NVIDIA GPU 加速版本

**配置**：
- 持久化模型目录（`./models:/app/models`）
- 配置文件目录（`./config:/app/config`）
- 临时文件目录（`./tmp:/app/tmp`）
- 健康检查（`curl -f http://localhost:8787/api/health`）

**启动**：
```bash
docker compose up -d
docker compose logs -f
docker compose down
```

### 9.3 远程连接

**配置**：
- 在设置页选择"远程连接"模式
- 输入远程服务器 IP 和端口
- 前端直接连接远程后端，不启动本地进程

---

## 10. 性能优化

### 10.1 内存优化

**低内存模式**：
- 开启后可将扩散模型内存占用减半
- 设置：`server.low_mem = True`

**CPU 显存卸载**：
- 将部分模型权重卸载到 CPU 内存
- 设置：`server.cpu_offload = True`

**Text Encoder CPU 运行**：
- 将 Text Encoder 在 CPU 上运行
- 设置：`server.cpu_textencoder = True`

### 10.2 计算设备优化

**Apple Silicon Mac**：
- 选择 MPS 设备获得显著加速
- 某些模型自动回退到 CPU（MPS 不支持的操作）

**NVIDIA GPU**：
- 选择 CUDA 设备
- 支持半精度加速（fp16）

**CPU 模式**：
- 速度慢但内存占用小
- 无 GPU 时的 fallback

### 10.3 模型缓存

**IOPaint Server 保活**：
- 扩散模型使用 HTTP Server 模式
- 保活 5 分钟（可配置 `server.keepalive_seconds`）
- 避免重复加载模型权重

**权重文件缓存**：
- HuggingFace 缓存：`~/.cache/huggingface/`
- Torch Hub 缓存：`~/.cache/torch/hub/checkpoints/`
- 避免重复下载

---

## 11. 安全设计

### 11.1 IPC 安全

**预加载脚本**：
- 使用 `contextBridge.exposeInMainWorld` 暴露有限的方法
- 不直接暴露 Node.js API 给渲染进程
- 设置 `sandbox: false`（需要 Node.js 集成）

### 11.2 输入验证

**后端**：
- 使用 Pydantic 模型验证请求数据
- Base64 解码错误处理
- 图像解码错误处理

**前端**：
- 文件类型过滤（仅允许图像文件）
- ROI 区域边界检查

### 11.3 NSFW 过滤

**可选功能**：
- SD 类模型支持 NSFW 安全检查
- 可通过 `disable_nsfw` 参数控制

---

## 12. 错误处理

### 12.1 后端错误处理

**异常捕获**：
- 所有路由处理函数都有 try-except
- 返回友好的错误信息（不包含堆栈跟踪）
- 记录详细错误到日志

**进程管理**：
- 后端进程意外退出时自动清理
- 端口冲突时自动清理残留进程

### 12.2 前端错误处理

**错误边界**：
- React 错误边界捕获渲染错误
- 显示友好的错误提示

**Toast 通知**：
- 使用 `ToastContainer` 显示操作反馈
- 成功/错误/警告三种状态

---

## 13. 日志系统

### 13.1 日志管理

**文件**：`backend/app/logging_manager.py`

**功能**：
- 日志记录（支持多个来源：backend、inpaint、upscale、synthesis 等）
- 日志查询（通过 `/api/logs` 端点）
- 日志推送（通过 WebSocket 实时推送）

**日志级别**：
- INFO：正常操作记录
- ERROR：错误信息
- WARNING：警告信息

### 13.2 日志存储

**存储位置**：
- 日志存储在内存中（最近 1000 条）
- 可通过前端日志页查看

**日志格式**：
```json
{
  "timestamp": "2026-05-09T01:36:00Z",
  "level": "INFO",
  "source": "inpaint",
  "message": "水印去除完成"
}
```

---

## 14. 扩展性设计

### 14.1 新增模型

**步骤**：
1. 编辑 `backend/core/models.yaml`
2. 添加模型配置（id、name、provider、tags 等）
3. 重启后端（或调用热重载接口）
4. 前端自动加载新模型列表

**无需修改代码**！

### 14.2 新增功能模式

**步骤**：
1. 在 `models.yaml` 中添加新的 `mode_groups`
2. 添加该模式下的模型配置（设置 tags）
3. 后端添加新的路由模块（`backend/app/routers/`）
4. 前端添加新的页面组件（`frontend/src/renderer/pages/`）
5. 添加新的状态管理（如需要）

### 14.3 插件机制（未来规划）

**设想**：
- 支持第三方插件扩展
- 插件可以添加新的模型、新的功能模式
- 插件通过标准接口与核心系统交互

---

## 15. 测试策略

### 15.1 后端测试

**目录**：`backend/tests/`

**测试框架**：pytest

**测试内容**：
- API 端点测试
- 模型加载测试
- 图像处理测试
- 配置管理测试

### 15.2 前端测试（未来规划）

**测试框架**：Jest + React Testing Library

**测试内容**：
- 组件渲染测试
- 状态管理测试
- IPC 通信测试
- 端到端测试（Playwright）

---

## 16. 未来优化方向

### 16.1 性能优化

- 支持批量处理（Batch Processing）
- 支持 GPU 多卡并行推理
- 优化模型加载速度（模型预热）
- 支持分布式推理（多机协同）

### 16.2 功能扩展

- 支持视频处理（超分辨率、去水印）
- 支持批量处理（拖拽多个文件）
- 支持处理历史记录
- 支持云端处理（连接远程 GPU 服务器）

### 16.3 用户体验

- 支持拖拽上传
- 支持快捷键操作
- 支持多语言（i18n）
- 支持主题切换（亮色/暗色）

---

## 17. 总结

HiImage 是一个设计良好的 AI 图像处理桌面应用，具有以下特点：

1. **模块化设计**：前端、后端、AI 模型层分离，易于维护和扩展
2. **模型配置外部化**：新增模型无需修改代码，只需编辑 YAML 配置文件
3. **跨平台支持**：支持 macOS、Windows、Linux，兼容多种计算设备
4. **性能优化**：支持内存优化、计算设备加速、模型缓存等
5. **用户体验**：直观的 UI、实时的进度推送、友好的错误处理

该系统为 AI 图像处理提供了一个可扩展、高性能、易使用的桌面解决方案。
