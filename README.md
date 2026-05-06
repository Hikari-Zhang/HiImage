# ClearWaterMark

AI 驱动的图像处理工具，支持去水印、超分辨率、智能合成三大核心功能。

## 功能特性

| 功能 | 说明 |
|------|------|
| 🖼️ 去水印 | 基于 IOPaint（LaMa / SD / PowerPaint 等 15+ 模型）智能修复指定区域 |
| 🔍 超分辨率 | 基于 Real-ESRGAN 2x / 4x 倍率放大重建 |
| ✨ 智能合成 | 换背景、换装模拟、换脸模拟、虚拟试穿，支持 ROI 区域选择 |

## 技术栈

**前端**
- Electron + React 18 + TypeScript
- Vite 构建
- Zustand 状态管理
- Lucide React 图标
- Canvas ROI 绘制

**后端**
- FastAPI + Uvicorn
- IOPaint（LaMa / SD / PowerPaint / ZITS 等）
- Real-ESRGAN（超分辨率）
- rembg（抠图，用于智能合成）
- GFPGAN（人脸增强）

**部署**
- Docker / 本地进程两种启动模式
- 前端设置页支持切换本地或远程后端地址

## 项目结构

```
ClearWaterMark/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI 入口
│   │   └── routers/            # API 路由（inpaint/upscale/synthesis/settings/logs）
│   ├── core/
│   │   ├── inpainter.py        # IOPaint 调用封装
│   │   ├── upscaler.py         # Real-ESRGAN 超分辨率
│   │   └── synthesizer.py      # 智能合成核心
│   └── requirements.txt
├── frontend/
│   ├── src/renderer/
│   │   ├── pages/              # 去水印 / 超分辨率 / 智能合成 / 日志 / 设置
│   │   ├── components/         # ImageCanvas / ImageCompare / Sidebar 等
│   │   └── hooks/              # useBackendAPI / useSystemLog 等
│   └── src/main/               # Electron 主进程
├── config/                     # 配置文件
├── models/                     # AI 模型文件（自动下载，不入库）
├── output/                     # 处理结果输出目录
├── docker-compose.yml
└── main.py                     # 应用启动入口（Electron + 后端）
```

## 快速开始

### 环境要求

- Python 3.9+
- Node.js v18+（推荐 v24）
- macOS / Linux / Windows

### 安装依赖

```bash
# 后端
cd backend
pip install -r requirements.txt

# 前端
cd frontend
npm install
```

### 开发模式

```bash
# 同时启动前端 + 后端（推荐）
npm run dev

# 或分别启动
npm run backend:dev   # 后端 FastAPI @ http://localhost:8787
npm run frontend:dev  # 前端 Vite @ http://localhost:5173
```

### 生产构建

```bash
cd frontend
npm run build          # 输出到 frontend/dist/
```

### Docker 部署

```bash
docker-compose up -d
```

## 使用说明

1. **去水印**：打开图片 → 在画布上框选水印区域 → 选择模型 → 点击处理
2. **超分辨率**：打开图片 → 选择放大倍率（2x/4x）→ 点击处理
3. **智能合成**：选择模式（换背景/换装/换脸/试穿）→ 上传参考图 → 绘制 ROI（可选）→ 选择模型 → 点击合成
4. **设置页**：配置后端地址（本地 / 远程 IP）、模型下载源、NSFW 检测开关等

## 注意事项

- 首次使用某模型时，IOPaint 会自动下载模型权重（存放于 `models/` 目录），需保持网络畅通
- Apple Silicon Mac（M1/M2/M3/M4）建议使用 MPS 加速
- `models/` 目录下的模型文件体积较大，已加入 `.gitignore`，不纳入版本控制

## 后续计划

- [ ] 自动水印检测
- [ ] 批量处理
- [ ] 更多图像操作功能扩展
- [ ] 视频处理支持
