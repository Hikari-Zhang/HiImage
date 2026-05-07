# HiImage

AI 驱动的图像处理桌面工具，基于 Electron + FastAPI 构建。

- **去水印** — 15+ 模型（LaMa / SD / PowerPaint / AnyText 等），支持 ROI 框选和自动检测
- **超分辨率** — Real-ESRGAN 2x / 4x 放大，通用照片和动漫专属模型
- **智能合成** — 7 种合成模式：换背景 / 换装 / 换脸 / 虚拟试穿 / 指令编辑
- **内存优化** — 低内存模式、CPU 显存卸载、Text Encoder 分离，可在设置页配置

---

## 技术架构

| 层级 | 技术栈 |
|------|----------|
| 前端 | Electron 28 + React 18 + TypeScript + Vite |
| 状态管理 | Zustand |
| UI | Tailwind CSS（暗色主题）+ Lucide React 图标 |
| 画布 | Konva.js（ROI 区域绘制） |
| 后端 | FastAPI + Uvicorn |
| AI 推理 | IOPaint（15+ 模型）/ Real-ESRGAN / rembg / GFPGAN |
| 进程管理 | 本地后端作为子进程管理，退出时自动清理 |
| 部署 | 本地启动 / 远程连接 / Docker Compose |

---

## 快速开始

### 环境要求

- **Python** 3.9+（推荐 3.11）
- **Node.js** v18+（推荐 v24，nvm 管理）
- **操作系统** macOS / Windows / Linux
- **加速**（可选）：Apple Silicon MPS / NVIDIA CUDA

### 安装

```bash
git clone <repo-url> HiImage
cd HiImage

# 后端依赖
cd backend
pip install -r requirements-locked.txt   #  reproducable，含所有锁定版本

# 前端依赖
cd ../frontend
npm install
```

### 开发模式

```bash
# 同时启动前端 + 后端（推荐）
cd frontend
npm run dev
# → Vite dev server @ http://localhost:5173
# → FastAPI backend @ http://localhost:8787
# → 自动管理后端子进程生命周期

# 分别启动
cd backend && python run.py --reload   # 终端 1
cd frontend && npm run dev:renderer        # 终端 2
```

### 生产构建

```bash
cd frontend
npm run build    # 输出 frontend/dist/
npm run package  # 打包 Electron 应用（.dmg / .exe）
```

### Docker 部署

```bash
docker compose up -d      # 启动后端服务
docker compose logs -f   # 查看日志
docker compose down       # 停止服务
```

---

## 使用指南

### 去水印

1. 打开图片（拖拽或文件对话框，支持 PNG/JPG/BMP/WebP/TIFF）
2. 在画布上框选水印区域（支持多个 ROI，可缩放/平移画布）
3. 选择模型（快速模型推荐 LaMa；文字水印用 AnyText）
4. 点击「开始处理」，实时查看进度
5. 对比原图与结果，保存为 PNG/JPG

**模型选择建议：**

| 场景 | 推荐模型 |
|------|----------|
| 快速处理 | LaMa、MiGAN、ZITS（CPU 可用） |
| 文字/字幕水印 | AnyText |
| 复杂渐变背景 | SD Inpainting、SDXL Inpainting |
| 最强通用效果 | PowerPaint V1 |

### 超分辨率

1. 打开图片
2. 选择放大倍率（2x / 4x）和 Real-ESRGAN 模型
3. 点击「开始处理」
4. 预览对比结果并保存

### 智能合成

支持 7 种模式，覆盖创意合成和虚拟试穿场景：

| 模式 | 说明 |
|------|------|
| 换背景 | 上传背景图，自动抠图合成 |
| 换装模拟 | 上传服装图，试穿效果 |
| 换脸模拟 | 上传目标脸，合成到原图 |
| 虚拟试穿 | 综合换装 + 换背景 |
| 精准替换 | 框选 ROI + 文字描述，按描述重绘 |
| 智能定位编辑 | 输入中文指令（如"将上衣换成红色"） |
| 自由编辑 | 自然语言指令全图语义编辑 |

### 设置页

- **连接方式**：本地启动（自动管理后端进程）/ 远程连接（输入 IP + 端口）
- **计算设备**：CPU / MPS（Apple Silicon）/ CUDA（NVIDIA）
- **内存优化**：低内存模式（推荐开启）、CPU 显存卸载、Text Encoder CPU 运行
- **服务器配置**：IOPaint 端口、保活时间、启动超时
- **网络**：HuggingFace Endpoint（国内可填 `https://hf-mirror.com` 加速下载）、HF Token
- **修复参数**：默认遮罩扩张像素数、NSFW 安全检查开关

---

## 项目结构

```
HiImage/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI 应用入口、生命周期管理
│   │   ├── config.py           # 配置加载（config/settings.json）
│   │   ├── logging_manager.py   # 日志管理 + WebSocket 推送
│   │   ├── routers/            # API 路由
│   │   │   ├── inpaint.py     # 去水印接口
│   │   │   ├── upscale.py     # 超分辨率接口
│   │   │   ├── synthesis.py   # 智能合成接口
│   │   │   ├── settings.py    # 配置管理接口
│   │   │   ├── logs.py        # 日志查询接口
│   │   │   └── system.py      # 系统状态接口
│   │   └── websocket/
│   │       └── progress.py     # 进度推送 WebSocket
│   ├── core/                   # 核心 AI 处理逻辑
│   │   ├── inpainter.py      # IOPaint 调用封装（CLI + HTTP Server 双模式）
│   │   ├── model_server.py    # IOPaint HTTP Server 进程管理（单例）
│   │   ├── upscaler.py       # Real-ESRGAN 超分辨率
│   │   ├── synthesizer.py    # 智能合成核心
│   │   ├── model_registry.py  # 模型注册表（models.yaml 驱动）
│   │   └── watermark_detector.py  # 自动水印检测
│   ├── requirements.txt        # 依赖声明（松散版本）
│   ├── requirements-locked.txt # 锁定版本（pip freeze，可复现）
│   ├── Dockerfile
│   └── run.py                # 后端启动脚本
├── frontend/
│   ├── src/
│   │   ├── main/               # Electron 主进程
│   │   │   ├── index.ts              # 主进程入口
│   │   │   └── backend-manager.ts   # 后端进程管理（启动/停止/清理）
│   │   ├── preload/            # 预加载脚本（IPC 桥接）
│   │   └── renderer/          # React 渲染进程
│   │       ├── App.tsx                 # 应用根组件
│   │       ├── pages/                 # 页面组件
│   │       ├── components/            # 复用组件（ImageCanvas / ImageCompare / layout / ui）
│   │       ├── stores/                # Zustand 状态管理
│   │       ├── hooks/                 # 自定义 React Hooks
│   │       └── types/                # TypeScript 类型定义
│   ├── package.json
│   └── electron.vite.config.ts
├── config/
│   └── settings.json            # 用户配置文件（不在 git 中）
├── models/                     # AI 模型文件（自动下载，.gitignore）
├── output/                     # 处理结果输出目录
├── docker-compose.yml
├── docs/
│   └── PRD.md                 # 产品需求文档
└── README.md
```

---

## 注意事项

### 模型下载

- 首次使用某模型时，IOPaint 会自动下载模型权重（存放于 `models/` 目录），需保持网络畅通
- 扩散模型（SD 等）体积较大（3-7GB），下载时间较长
- 国内用户可在设置页将 HuggingFace Endpoint 改为 `https://hf-mirror.com` 加速下载

### 硬件要求

| 场景 | 最低配置 | 推荐配置 |
|------|----------|----------|
| 快速模型（LaMa 等） | 2GB 内存 | 4GB 内存 |
| 扩散模型（SD / AnyText） | 8GB 内存 | 16GB 内存（开启低内存模式可减半） |
| Real-ESRGAN 4x | 4GB 内存 | 8GB 内存 |

### Apple Silicon Mac 优化

- 选择 MPS 设备可获得显著加速
- 某些模型可能不完全支持 MPS，会自动回退到 CPU
- 开启「低内存模式」可大幅降低统一内存占用

### 进程管理

- 本地模式下，前端退出时会自动终止后端进程，**不会**留下残留进程
- 支持进程组级别的清理（macOS/Linux），避免孤儿进程累积显存
- 端口冲突时自动清理残留进程

---

## 常见问题

**Q：后端启动失败，提示 "Address already in use"？**

A：端口 8787 被占用。在设置页修改 IOPaint 端口，或手动清理占用进程。

**Q：处理时出现 "Out of Memory" 错误？**

A：开启设置页的「低内存模式」，或进一步开启「CPU 显存卸载」。也可以切换设备到 CPU（速度慢但内存占用小）。

**Q：模型下载速度慢？**

A：在设置页将 `network.hf_endpoint` 修改为 `https://hf-mirror.com`。

**Q：Electron 应用打包失败？**

A：确保 `backend/` 下依赖已安装，`models/` 目录已存在（可以是空的）。

---

## 许可证

MIT License

---

Made with ❤️ by Hikari
