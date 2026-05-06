# HiImage

<p align="center">
  <strong>🎨 AI 驱动的图像处理工具</strong>
</p>

<p align="center">
  支持去水印、超分辨率、智能合成三大核心功能
</p>

## ✨ 功能特性

| 功能 | 说明 | 适用场景 |
|------|------|----------|
| 🖼️ **去水印** | 基于 IOPaint（LaMa / SD / PowerPaint 等 15+ 模型）智能修复指定区域 | 移除图片中的水印、logo、不需要的物体 |
| 🔍 **超分辨率** | 基于 Real-ESRGAN 2x / 4x 倍率放大重建 | 提升低分辨率图片质量 |
| ✨ **智能合成** | 换背景、换装模拟、换脸模拟、虚拟试穿，支持 ROI 区域选择 | 创意合成、虚拟试穿、背景替换 |

## 🏗️ 技术架构

### 前端技术栈
- **框架**: Electron 28 + React 18 + TypeScript
- **构建工具**: Vite
- **状态管理**: Zustand
- **UI 组件**: Tailwind CSS + Lucide React 图标
- **画布交互**: HTML Canvas API（ROI 区域绘制）

### 后端技术栈
- **API 框架**: FastAPI + Uvicorn
- **AI 推理引擎**:
  - IOPaint（LaMa / SD / PowerPaint / ZITS 等 15+ 模型）
  - Real-ESRGAN（超分辨率）
  - rembg（抠图，用于智能合成）
  - GFPGAN（人脸增强）
- **进程管理**: 支持模型服务长驻内存，自动空闲关闭

### 部署方式
- **开发模式**: 前端 Vite dev server + 后端 FastAPI
- **生产模式**: Electron 打包，后端作为子进程启动
- **容器化**: Docker / Docker Compose 部署
- **灵活配置**: 前端设置页支持切换本地或远程后端地址

## 📁 项目结构

```
HiImage/
├── backend/                 # FastAPI 后端服务
│   ├── app/
│   │   ├── main.py         # FastAPI 应用入口
│   │   ├── config.py      # 配置加载
│   │   ├── logging_manager.py  # 日志管理
│   │   ├── models/        # Pydantic 数据模型
│   │   ├── routers/       # API 路由
│   │   │   ├── inpaint.py      # 去水印接口
│   │   │   ├── upscale.py      # 超分辨率接口
│   │   │   ├── synthesis.py    # 智能合成接口
│   │   │   ├── settings.py     # 配置管理接口
│   │   │   ├── logs.py         # 日志查询接口
│   │   │   └── system.py      # 系统状态接口
│   │   └── websocket/
│   │       └── progress.py  # 进度推送 WebSocket
│   ├── core/              # 核心处理逻辑
│   │   ├── inpainter.py   # IOPaint 调用封装
│   │   ├── upscaler.py    # Real-ESRGAN 超分辨率
│   │   ├── synthesizer.py  # 智能合成核心
│   │   ├── model_server.py # IOPaint HTTP Server 管理
│   │   └── watermark_detector.py  # 自动水印检测
│   ├── requirements.txt    # Python 依赖
│   ├── Dockerfile         # Docker 镜像构建
│   └── run.py            # 后端启动脚本
├── frontend/               # Electron + React 前端
│   ├── src/
│   │   ├── main/          # Electron 主进程
│   │   │   ├── index.ts         # 主进程入口
│   │   │   └── backend-manager.ts  # 后端进程管理
│   │   ├── preload/       # 预加载脚本（桥接主进程和渲染进程）
│   │   └── renderer/      # React 渲染进程
│   │       ├── App.tsx          # 应用根组件
│   │       ├── pages/           # 页面组件
│   │       │   ├── WatermarkRemoval.tsx  # 去水印页面
│   │       │   ├── SuperResolution.tsx   # 超分辨率页面
│   │       │   ├── SmartSynthesis.tsx    # 智能合成页面
│   │       │   ├── Logs.tsx              # 日志页面
│   │       │   └── Settings.tsx         # 设置页面
│   │       ├── components/      # 复用组件
│   │       │   ├── ImageCanvas.tsx     # 图片画布（ROI 绘制）
│   │       │   ├── ImageCompare.tsx    # 图片对比组件
│   │       │   ├── layout/             # 布局组件
│   │       │   └── ui/                # 基础 UI 组件
│   │       ├── stores/           # Zustand 状态管理
│   │       ├── hooks/            # 自定义 React Hooks
│   │       └── types/           # TypeScript 类型定义
│   ├── package.json
│   ├── electron.vite.config.ts
│   └── tailwind.config.mjs
├── config/                  # 配置文件
│   ├── settings.json       # 用户配置（模型路径、设备选择等）
│   └── __init__.py        # 配置加载模块
├── models/                 # AI 模型文件（自动下载，不入库）
│   ├── huggingface/       # HuggingFace 模型缓存
│   ├── torch/             # PyTorch 模型缓存
│   └── realesrgan/       # Real-ESRGAN 模型权重
├── output/                 # 处理结果输出目录
├── docker-compose.yml     # Docker Compose 配置
└── README.md
```

## 🚀 快速开始

### 环境要求

- **Python**: 3.9+（推荐 3.11）
- **Node.js**: v18+（推荐 v24）
- **操作系统**: macOS / Linux / Windows
- **硬件加速**（可选）:
  - Apple Silicon (M1/M2/M3/M4) - MPS 加速
  - NVIDIA GPU - CUDA 加速

### 安装依赖

**1. 克隆项目**

```bash
git clone https://github.com/yourusername/HiImage.git
cd HiImage
```

**2. 安装后端依赖**

```bash
cd backend
pip install -r requirements.txt
```

**3. 安装前端依赖**

```bash
cd frontend
npm install
```

### 开发模式

**方式一：同时启动前端 + 后端（推荐）**

```bash
cd frontend
npm run dev
```

此命令会：
- 启动 Vite 开发服务器（前端 @ http://localhost:5173）
- 自动启动后端 FastAPI 服务（@ http://localhost:8787）
- 支持热更新

**方式二：分别启动**

```bash
# 终端 1：启动后端
cd backend
python run.py --reload

# 终端 2：启动前端
cd frontend
npm run dev:renderer
```

### 生产构建

```bash
cd frontend
npm run build          # 输出到 frontend/dist/
npm run package        # 打包 Electron 应用
```

### Docker 部署

```bash
# 构建并启动后端服务
docker-compose up -d

# 查看日志
docker-compose logs -f

# 停止服务
docker-compose down
```

## 📖 使用指南

### 1. 去水印

**操作步骤**：
1. 点击「打开图片」或拖拽图片到画布
2. 在画布上框选水印区域（支持多个 ROI）
3. 选择去水印模型（推荐 LaMa 快速模型）
4. 点击「开始处理」
5. 处理完成后，对比原图和处理结果
6. 保存结果图片

**模型选择建议**：
- **快速处理**: LaMa、MiGAN、ZITS（CPU 可用）
- **高质量**: MAT、PowerPaintV2（需要 GPU）
- **文字水印**: AnyText
- **复杂背景**: SD Inpainting、SDXL Inpainting

### 2. 超分辨率

**操作步骤**：
1. 打开图片
2. 选择放大倍率（2x 或 4x）
3. 选择 Real-ESRGAN 模型
4. 点击「开始处理」
5. 等待处理完成（进度条显示）
6. 保存放大后的图片

**注意事项**：
- 4x 放大需要更多内存和时间
- 推荐使用 GPU 加速

### 3. 智能合成

**支持模式**：
- **换背景**: 上传背景图，自动抠图合成
- **换装模拟**: 上传服装图，试穿效果
- **换脸模拟**: 上传目标脸，合成到原图
- **虚拟试穿**: 综合换装 + 换背景

**操作步骤**：
1. 选择合成模式
2. 上传原图和参考图
3. 绘制 ROI 区域（可选，用于精确定位）
4. 选择模型
5. 点击「开始合成」
6. 查看并保存合成结果

### 4. 设置页

**可配置项**：
- **后端地址**: 本地（127.0.0.1:8787）或远程 IP
- **模型设备**: CPU / MPS (Apple Silicon) / CUDA (NVIDIA)
- **模型下载源**: 官方 HuggingFace 或国内镜像
- **HuggingFace Token**: 用于下载 gated 模型（如 PowerPaintV2）
- **NSFW 检测**: 开关 NSFW 内容检测

## ⚙️ 配置说明

配置文件位置：`config/settings.json`

```json
{
  "server": {
    "keepalive_seconds": 300,
    "port": 8787,
    "startup_timeout": 1800
  },
  "inpaint": {
    "default_dilation": 10,
    "default_device": "mps"
  },
  "network": {
    "hf_endpoint": "https://huggingface.co",
    "hf_token": ""
  }
}
```

**配置项说明**：
- `server.keepalive_seconds`: 模型服务空闲多久后自动关闭（秒）
- `server.port`: 后端服务监听端口
- `inpaint.default_dilation`: 掩码扩张像素数（影响修复范围）
- `inpaint.default_device`: 默认计算设备（cpu/mps/cuda）
- `network.hf_endpoint`: HuggingFace 镜像地址（国内用户可设为镜像站）
- `network.hf_token`: HuggingFace Access Token（下载 gated 模型需要）

## 🔧 开发指南

### 项目架构

**前端（Electron + React）**：
- 主进程（`src/main/`）：管理应用生命周期、启动后端子进程、处理系统事件
- 渲染进程（`src/renderer/`）：React 应用，负责 UI 交互
- 预加载脚本（`src/preload/`）：安全地暴露主进程 API 给渲染进程

**后端（FastAPI）**：
- `app/main.py`: FastAPI 应用创建、CORS 配置、生命周期管理
- `app/routers/`: API 路由定义
- `app/core/`: 核心 AI 处理逻辑
- `app/websocket/`: WebSocket 实时进度推送

**进程通信**：
- HTTP REST API：前端通过 axios 调用后端 API
- WebSocket：后端通过 WebSocket 推送处理进度
- IPC（主进程 ↔ 渲染进程）：Electron IPC 通信

### 添加新功能

**1. 添加新的 API 接口**：

后端：`backend/app/routers/` 下创建新路由文件

```python
from fastapi import APIRouter

router = APIRouter()

@router.post("/api/new-feature")
async def new_feature():
    return {"status": "success"}
```

前端：在 `frontend/src/renderer/hooks/useBackendAPI.ts` 中添加 API 调用函数

**2. 添加新的页面**：

1. 在 `frontend/src/renderer/pages/` 下创建页面组件
2. 在 `frontend/src/renderer/App.tsx` 中添加路由
3. 在 `frontend/src/renderer/components/layout/Sidebar.tsx` 中添加侧边栏入口

### 调试技巧

**后端调试**：
```bash
cd backend
python run.py --reload  # --reload 启用热重载
```

**前端调试**：
- 打开 Electron 开发者工具：Ctrl+Shift+I (Windows/Linux) 或 Cmd+Option+I (macOS)
- Vite 热更新：修改前端代码后自动刷新

**查看日志**：
- 后端日志：通过「日志」页面查看
- 或查看 `output/` 目录下的日志文件

## ⚠️ 注意事项

1. **模型下载**：
   - 首次使用某模型时，IOPaint 会自动下载模型权重（存放于 `models/` 目录），需保持网络畅通
   - 扩散模型（如 Stable Diffusion）体积较大（3-7GB），下载时间较长
   - 可提前通过设置页配置 HuggingFace 镜像加速下载

2. **硬件要求**：
   - **CPU 模式**：所有模型均可运行，但速度较慢
   - **Apple Silicon (MPS)**：推荐 M1/M2/M3/M4，可加速大部分模型
   - **NVIDIA GPU (CUDA)**：推荐 8GB+ 显存，可运行所有模型
   - **内存要求**：
     - 快速模型（LaMa 等）：2-4GB 内存
     - 扩散模型（SD 等）：8-16GB 内存

3. **Apple Silicon Mac 优化**：
   - 建议使用 MPS 加速（设置页选择 "mps" 设备）
   - 某些模型可能不支持 MPS，会自动回退到 CPU

4. **文件管理**：
   - `models/` 目录下的模型文件体积较大，已加入 `.gitignore`，不纳入版本控制
   - `output/` 目录存放处理结果，建议定期清理
   - `tmp/` 目录存放临时文件，处理成功后自动清理

## 📝 后续计划

- [ ] **自动水印检测**：基于 CV 的智能水印区域检测
- [ ] **批量处理**：支持同时处理多张图片
- [ ] **更多图像操作功能**：滤波、调色、裁剪等基础图像编辑功能
- [ ] **视频处理支持**：扩展至视频去水印、超分辨率
- [ ] **模型管理界面**：可视化模型下载、删除、更新
- [ ] **处理历史记录**：保存处理记录，支持撤销/重做
- [ ] **云端处理**：支持调用云端 GPU 服务器进行推理

## 🐛 常见问题

**Q1: 后端启动失败，提示 "Address already in use"？**

A: 端口 8787 已被占用。修改 `config/settings.json` 中的 `server.port`，或关闭占用端口的进程。

**Q2: 模型下载速度慢？**

A: 在设置页将 `network.hf_endpoint` 修改为国内镜像站地址，例如：`https://hf-mirror.com`

**Q3: 处理时出现 "Out of Memory" 错误？**

A: 
- 尝试使用更小的模型（如 LaMa 代替 SD Inpainting）
- 切换设备到 CPU（虽然慢但内存占用小）
- 减小输入图片分辨率

**Q4: Electron 应用打包失败？**

A: 
- 确保 `backend/dist/` 目录存在（后端已编译）
- 确保 `models/` 目录存在（可以是空的）
- 检查 `frontend/package.json` 中的 `build.extraResources` 路径是否正确

## 📄 许可证

MIT License

## 🙏 致谢

- [IOPaint](https://github.com/Sanster/IOPaint) - 提供去水印核心算法
- [Real-ESRGAN](https://github.com/xinntao/Real-ESRGAN) - 提供超分辨率算法
- [rembg](https://github.com/danielgatis/rembg) - 提供抠图功能
- [GFPGAN](https://github.com/TencentARC/GFPGAN) - 提供人脸增强功能

---

<p align="center">
  Made with ❤️ by Hikari
</p>
