# HiImage 系统架构文档

> **版本**: 1.4 · **更新**: 2026-08-17  
> 以当前代码为准。`docs/system-design.md` 为更细的设计说明，若与本文冲突，以本文与代码为准。

## 1. 概述

HiImage 是 **Electron + FastAPI** 的跨平台 AI 图像处理桌面应用。核心功能：去水印、抠图、超分辨率、智能合成。

设计原则：

- **配置驱动**：模型元数据集中在 `backend/core/models.yaml`，同 provider 新增模型通常只改 YAML。
- **端云协同**：Electron 管理本地 FastAPI 生命周期，也支持连接远程后端。
- **工作区流水线**：去水印 / 超分 / 合成共享「工作图」，选区与画笔按页隔离，便于先去水印再超分。抠图使用独立 store（RGBA + mask 管线不同）。

## 2. 高层架构

```
┌─────────────────────────────────────────────────────────────┐
│                     Electron 应用                             │
│  Main: 窗口 / IPC / BackendManager                           │
│  Preload: contextBridge                                      │
│  Renderer: React + Zustand + Canvas/Konva                    │
│       │ HTTP REST          │ WebSocket 进度                   │
│       │ SSE 下载进度        │                                 │
└───────┼────────────────────┼─────────────────────────────────┘
        ▼                    ▼
┌─────────────────────────────────────────────────────────────┐
│  FastAPI (Uvicorn :8787)                                     │
│  routers → (线程池) → Executor / Upscaler / Synthesizer      │
│  DownloadQueue · ModelRegistry(models.yaml) · ModelServer    │
└─────────────────────────────────────────────────────────────┘
```

三层：

| 层 | 职责 |
|----|------|
| 前端 | UI、画布、工作区状态、后端进程拉起 |
| 后端 | 推理调度、模型下载、进度推送 |
| 模型 | YAML 注册、本地缓存、CPU/MPS/CUDA |

## 3. 前端

### 3.1 进程

- **Main** (`frontend/src/main/`)：窗口、文件对话框、`backend-manager.ts`（local / remote）。
- **Preload**：`electronAPI`（openFile / saveFile / getBackendURL / 读写图片）。
- **Renderer**：React 18 + Vite + Zustand + Tailwind。

`MainLayout` 将功能页 **CSS `hidden` 常驻挂载**，切页不卸载，工作区状态得以保留。

### 3.2 页面

| 路由 | 页面 | 图像状态 |
|------|------|----------|
| `/watermark` | 去水印 | 工作区 source；本页 ROI/画笔 |
| `/cutout` | 抠图 | **独立** `useCutoutStore` |
| `/upscale` | 超分辨率 | 工作区 working image（原图或上一页结果） |
| `/synthesis` | 智能合成 | 同上 + 本页 ROI/画笔 |
| `/logs` `/settings` | 日志 / 设置 | 无工作图 |

### 3.3 工作区与工具状态（v1.4）

**共享工作图** `useWorkspaceStore`：

- `sourceImage` / `sourceFilePath`：打开或「将结果设为原图」后的底图
- `resultImage`：最近一次去水印 / 超分 / 合成的成功结果（跨页交接）
- `inputSource`: `'original' | 'result'`：超分、合成默认用结果，可切回原图
- 去水印始终编辑 `sourceImage`（在结果上再去水印需点「将结果设为原图」）

**按页隔离** `useWatermarkToolStore` / `useSynthesisToolStore`：

- ROI、选中项、画笔笔划、画布工具
- 切页不会把去水印选区带到合成页

`useProcessStore` 全局一份：同一时刻只跑一个推理任务。进度 WebSocket 在 **App 级单例**连接 `/api/ws/progress`，所有处理页都能更新进度条。

### 3.4 画布

| 组件 | 技术 | 用途 |
|------|------|------|
| `ImageCanvas` | 原生 Canvas 2D | ROI 框选 / 只读预览 |
| `MaskCanvas` | react-konva | 画笔 + ROI |
| `CutoutPage` 内联 Stage | react-konva | 抠图前景/背景画笔（未并入 MaskCanvas） |

### 3.5 通信

- REST：`useBackendAPI` 发处理指令
- WebSocket：`useProgressWebSocket`（App 挂载一次）推推理进度
- SSE：`useDownloadManager` 推模型下载进度

## 4. 后端

```
backend/
├── app/main.py              # FastAPI 入口、lifespan、路由注册
├── app/routers/             # inpaint / cutout / upscale / synthesis / models / …
├── app/websocket/progress.py
└── core/
    ├── models.yaml          # 模型与 mode_groups
    ├── model_registry.py
    ├── model_executor.py    # Factory（inpaint / cutout 主路径）
    ├── inpainter.py         # IOPaint CLI + HTTP Server
    ├── upscaler.py          # Real-ESRGAN（upscale 路由直接调用）
    ├── synthesizer.py       # 智能合成编排（不走 Factory）
    ├── download_queue.py
    └── model_server.py      # IOPaint 扩散模型保活进程
```

### 4.1 分发

- 去水印、抠图：`ModelExecutorFactory`（provider → Executor）
- 超分：router / pipeline 直接 `Upscaler`（不走 Factory）
- 合成：`Synthesizer` 按 mode 编排

### 4.2 图像 I/O

多数接口仍收发 Base64。超分支持 `output_path` / `is_file_path`（Electron 批量避免撑爆内存）。其余接口的路径化是后续优化。

`/cutout` 推理在 `ThreadPoolExecutor` 中执行；`/cutout/refine` 与 `/cutout/composite` 同样进线程池，避免堵住事件循环。

### 4.3 进度

`ProgressManager` 向所有 WebSocket 连接广播 `progress` / `complete` / `error`。前端以 HTTP 响应作为任务结束依据，WebSocket 只驱动百分比。

## 5. 数据流（跨页流水线）

1. 用户在去水印页打开图片 → `workspace.sourceImage`
2. 去水印成功 → `workspace.resultImage`，`inputSource = 'result'`
3. 切到超分 / 合成 → 画布与请求使用 **处理结果**；可切回原图
4. 「将结果设为原图」→ 结果提升为 source，清空结果与各页选区，可继续下一轮

抠图不参与该工作区，避免 RGBA/mask 与 RGB 工作图混用。

## 6. 技术栈

| 维度 | 选型 |
|------|------|
| 外壳 | Electron + electron-vite |
| UI | React 18 + TypeScript + Zustand + Tailwind |
| 画布 | Canvas 2D + Konva |
| 后端 | FastAPI + Uvicorn |
| 推理 | IOPaint / Real-ESRGAN / rembg / diffusers 等 |
