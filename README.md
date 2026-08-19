# HiImage

AI 驱动的图像处理桌面工具，基于 Electron + React + FastAPI 构建。

## 功能一览

| 功能 | 说明 |
|------|------|
| **去水印** | 框选水印区域，AI 修复，支持 30+ 模型（快速模型无需下载，扩散模型首次自动下载） |
| **抠图** | BiRefNet / rembg 等模型抠图，画笔微调 + 羽化/膨胀精修，合成自定义背景 |
| **超分辨率** | Real-ESRGAN 2x/4x 放大，支持通用/精细化/动漫三类模型，**支持批量处理** |
| **图片降噪** | 保持分辨率去噪；默认 SCUNet，可按类型选相机 / JPEG / 快速清晰化 |
| **智能合成** | 7 种模式：换背景 / 换装 / 换脸 / 虚拟试穿 / 精准替换 / 智能定位 / 自由编辑 |
| **跨页流水线** | 去水印 / 降噪 / 超分 / 合成共享工作图：一页的结果可直接作为下一页输入（可选回原图） |
| **批量处理** | 一次性导入多张图片，逐张处理，实时显示进度，支持 Electron 直接读取本地文件 |

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
pip install -r requirements-locked.txt

# 前端依赖
cd ../frontend
npm install
```

### 开发模式

```bash
cd frontend
npm run dev
# → Vite dev server @ http://localhost:5173
# → FastAPI backend 自动管理生命周期
```

### 生产构建

```bash
cd frontend
npm run build     # 输出 frontend/dist/
npm run package   # 打包 Electron 应用（.dmg / .exe）
```

> **注**：macOS 打包需要 Apple Developer Program 证书（$99/年），否则无法分发给他人的。
> 开发模式下无需签名，直接 `npm run dev` 即可使用全部功能。

---

## 使用指南

### 去水印

1. 打开图片（拖拽或文件对话框，支持 PNG/JPG/BMP/WebP/TIFF）
2. 在画布上框选水印区域（支持多个 ROI，可缩放/平移画布）
3. 选择模型（快速模型推荐 LaMa；文字水印用 AnyText）
4. 点击「开始处理」，实时查看进度
5. 对比原图与结果，保存为 PNG/JPG

**快速模型（无需下载）**：LaMa（推荐通用）/ MiGAN / ZITS / FCF / MAT / LDM / Manga / CV2

**扩散模型（首次自动下载）**：AnyText / PowerPaint V1 / SD 1.5 Inpainting / Realistic Vision / DreamShaper / Anything V4 / SDXL Inpainting

### 抠图

1. 打开图片
2. 选择模型（推荐 BiRefNet），点击抠图
3. 用画笔增减前景，必要时调节羽化 / 膨胀
4. 可合成到自定义背景后保存 PNG

抠图不占用去水印/超分/合成的工作图，避免透明通道结果和 RGB 流水线混在一起。

### 超分辨率

#### 单张处理

1. 打开图片
2. 选择放大倍率（2x / 4x）和模型
3. 点击「开始处理」
4. 预览对比结果并保存

#### 批量处理

1. 切换到「批量模式」
2. 点击「添加」或拖拽文件夹，一次性导入多张图片
3. 选择输出目录
4. 点击「开始批量处理」
5. 实时查看每张图片的处理进度，完成后结果保存在输出目录

> 批量模式支持虚拟滚动，导入 10,000+ 张图片也不会卡死。

**模型选择建议：**

| 类别 | 推荐模型 | 适用场景 |
|------|----------|----------|
| 通用 | RealESRGAN_x4plus | 通用照片 |
| 精细化增强 | realesr-general-x4v3 | 去噪+去模糊+放大（仅 17MB） |
| 动漫/插画 | RealESRGAN_x4plus_anime_6B | 线条锐利（仅 18MB） |

### 图片降噪

1. 打开图片（可与去水印结果衔接）
2. 按噪声类型选模型（默认 SCUNet 盲去噪；首次约 72 MB。快速清晰化可复用已有超分权重）
3. 单张点击「开始降噪」，或切换批量后选择输出目录
4. 对比预览并保存为 `{原名}_denoised.png`

专用复原模型依赖 spandrel，需要 Python 3.10+。

### 智能合成

支持 7 种模式：

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
- **网络**：HuggingFace Endpoint（国内填 `https://hf-mirror.com` 加速）、HF Token

---

## 技术架构

```
HiImage/
├── backend/                  # Python FastAPI 后端
│   ├── app/
│   │   ├── main.py          # FastAPI 入口
│   │   ├── routers/         # API 路由（inpaint / cutout / upscale / synthesis / models / …）
│   │   └── websocket/      # 进度推送 WebSocket
│   └── core/               # AI 推理核心
│       ├── inpainter.py     # IOPaint 调用封装
│       ├── upscaler.py      # Real-ESRGAN 超分辨率
│       ├── synthesizer.py    # 智能合成核心
│       └── model_registry.py # 模型注册表（models.yaml 驱动）
├── frontend/                # Electron + React 前端
│   ├── src/
│   │   ├── main/           # Electron 主进程（后端进程管理）
│   │   ├── preload/        # IPC 桥接
│   │   └── renderer/       # React 渲染进程
│   │       ├── pages/       # 页面组件
│   │       ├── components/  # 复用组件
│   │       ├── stores/      # Zustand 状态管理
│   │       └── hooks/       # 自定义 Hooks
│   └── electron.vite.config.ts
├── config/settings.json     # 用户配置（不在 git 中）
├── models/                 # AI 模型文件（自动下载，.gitignore）
├── output/                 # 处理结果输出目录
└── docs/PRD.md            # 产品需求文档
```

| 层级 | 技术栈 |
|------|----------|
| 前端 | Electron + React 18 + TypeScript + Vite |
| 状态管理 | Zustand |
| UI | Tailwind CSS（暗色主题）+ Lucide React 图标 |
| 画布 | Konva.js（ROI 区域绘制） |
| 后端 | FastAPI + Uvicorn |
| AI 推理 | IOPaint / Real-ESRGAN / rembg / GFPGAN |
| 打包 | electron-builder |

---

## 硬件要求

| 场景 | 最低配置 | 推荐配置 |
|------|----------|----------|
| 快速模型（LaMa 等） | 2GB 内存 | 4GB 内存 |
| 扩散模型（SD 等） | 8GB 内存 | 16GB 内存（开启低内存模式可减半） |
| Real-ESRGAN 4x | 4GB 内存 | 8GB 内存 |

### Apple Silicon Mac 优化

- 选择 MPS 设备可获得显著加速
- 开启「低内存模式」可大幅降低统一内存占用

---

## 常见问题

**Q：模型下载速度慢？**

A：在设置页将 `network.hf_endpoint` 修改为 `https://hf-mirror.com`。

**Q：处理时出现 "Out of Memory" 错误？**

A：开启设置页的「低内存模式」，或进一步开启「CPU 显存卸载」。

**Q：后端启动失败，提示 "Address already in use"？**

A：端口 8787 被占用。在设置页修改 IOPaint 端口，或手动清理占用进程。

---

## 许可证

MIT License

---

Made with ❤️ by Hikari
