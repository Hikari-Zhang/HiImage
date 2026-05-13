# HiImage 脚本参考文档

本文档详细列出了 HiImage 项目中的所有脚本及其作用。

---

## 目录

1. [项目根目录脚本](#1-项目根目录脚本)
2. [scripts/ 目录脚本](#2-scripts-目录脚本)
3. [backend/ 目录脚本](#3-backend-目录脚本)
4. [frontend/ 目录脚本](#4-frontend-目录脚本)
5. [核心执行器脚本](#5-核心执行器脚本)
6. [使用场景示例](#6-使用场景示例)

---

## 1. 项目根目录脚本

### package.json 脚本命令

| 命令 | 作用 | 实现方式 |
|------|------|----------|
| `npm run dev` | 启动完整开发模式（后端 + 前端） | 调用 `scripts/dev.js` |
| `npm run dev:sh` | 启动完整开发模式（Bash 版本） | 调用 `scripts/dev.sh` |
| `npm run backend:dev` | 仅启动后端开发服务器 | 直接运行 uvicorn |
| `npm run frontend:dev` | 仅启动前端开发服务器 | 调用 `frontend/package.json` 的 `dev` 命令 |

**示例：**
```bash
# 启动完整开发环境（推荐）
npm run dev

# 仅启动后端（用于 API 调试）
npm run backend:dev

# 仅启动前端（后端已手动启动）
npm run frontend:dev
```

---

## 2. scripts/ 目录脚本

### 2.1 dev.js

**文件路径：** `scripts/dev.js`

**作用：** 跨平台开发模式启动脚本（Node.js 实现）

**功能特性：**
- 自动检测 Python 环境（Windows/Linux/macOS 多路径查找）
- 自动创建 Python 虚拟环境（`venv/`）
- 自动检测 GPU 环境并安装对应版本的 PyTorch：
  - CUDA 12.8+ → cu128（Blackwell / RTX 50 系）
  - CUDA 12.0+ → cu124
  - CUDA 11.8+ → cu118
  - 无 GPU → CPU 版本
  - macOS → 标准 pip（自动使用 MPS / CPU）
- 安装后端依赖（自动处理版本冲突）
- 应用 post-install 补丁
- 启动后端（FastAPI on port 8787）
- 启动前端（Electron + React）
- 清理子进程（退出时自动杀掉整个进程树）

**使用方法：**
```bash
# 通过 npm（推荐）
npm run dev

# 直接运行
node scripts/dev.js
```

**适用平台：** Windows, macOS, Linux

---

### 2.2 dev.sh

**文件路径：** `scripts/dev.sh`

**作用：** Bash 版本的开发模式启动脚本

**功能特性：**
- 启动后端（FastAPI on port 8787）
- 启动前端（Electron + React）
- 等待后端就绪（健康检查）
- 退出时清理子进程

**使用方法：**
```bash
# 通过 npm
npm run dev:sh

# 直接运行
bash scripts/dev.sh
```

**适用平台：** macOS, Linux

---

### 2.3 check_models.py

**文件路径：** `scripts/check_models.py`

**作用：** HiImage 模型完整性检测 CLI

**功能特性：**
- 检测所有模型（或指定模型/模式）的完整性
- 支持多种状态：`ok`, `missing`, `partial`, `corrupted`, `unknown`
- 按功能模式分组显示（如 `watermark_removal`, `upscale` 等）
- 支持表格格式或 JSON 格式输出
- 提供彩色输出（支持终端颜色）

**使用方法：**
```bash
# 检测全部模型
python scripts/check_models.py

# 只检测某功能模式的模型
python scripts/check_models.py --mode outfit_swap

# 只检测指定模型
python scripts/check_models.py --model birefnet

# JSON 格式输出（用于程序解析）
python scripts/check_models.py --json
```

**退出码：**
- `0` - 所有被检测的模型均为 `ok`
- `1` - 存在 `missing` / `corrupted` / `unknown` 状态的模型

---

### 2.4 check_hf_repo_size.py

**文件路径：** `scripts/check_hf_repo_size.py`

**作用：** 查询 HuggingFace 仓库的实际大小（包括 Git LFS 文件）

**功能特性：**
- 通过 HF API 获取仓库所有文件的大小
- 过滤不需要的文件（`.msgpack`, `.h5`, `flax_model`, `tf_model`）
- 显示每个文件的大小和总大小
- 建议 `size_mb` 值（用于 `models.yaml` 配置）

**使用方法：**
```bash
# 查询指定仓库的大小
python scripts/check_hf_repo_size.py Sanster/PowerPaint_v2

# 使用 HF Token（用于访问门控模型）
export HF_TOKEN="your_token_here"
python scripts/check_hf_repo_size.py Sanster/some_gated_model
```

**输出示例：**
```
[仓库] Sanster/PowerPaint_v2
File                                                             Size
---------------------------------------------------------------------------
config.json                                           1.2 MB
model.safetensors                                     1.5 GB
...
---------------------------------------------------------------------------
[文件数] 10 (忽略 2 个)
[总大小] 1.5 GB (1572864 MB)
[建议 size_mb] 1572864
```

---

### 2.5 install_deps.py

**文件路径：** `scripts/install_deps.py`

**作用：** HiImage 依赖安装脚本

**解决的问题：**
- `iopaint 1.6.0` 硬锁 `diffusers==0.27.2`
- HiImage 的 FLUX 系列功能需要 `diffusers>=0.32.0`
- 两者存在版本冲突

**安装策略（两步绕过冲突）：**
1. **Step 1：** 正常安装 `requirements.txt`（`iopaint` 会拉取 `diffusers==0.27.2`）
2. **Step 2：** 使用 `--no-deps` 强制覆盖为高版本（跳过 `iopaint` 的版本约束检查）

**强制升级的包：**
- `diffusers>=0.32.0`
- `transformers>=4.47.0,<5.0`
- `huggingface-hub>=0.27.0,<1.0`
- `peft>=0.9.0`

**使用方法：**
```bash
# 激活虚拟环境
source venv/bin/activate  # macOS/Linux
venv\Scripts\activate      # Windows

# 运行安装脚本
python scripts/install_deps.py
```

**验证：**
脚本会自动验证 `FluxImg2ImgPipeline` 和 `FluxFillPipeline` 是否可导入。

---

### 2.6 post_install.py

**文件路径：** `scripts/post_install.py`

**作用：** Post-install 补丁脚本

**解决的问题：**
- `basicsr 1.4.2` 从已删除的 `torchvision.transforms.functional_tensor` 导入
- 需要修补为从新位置 `torchvision.transforms.functional` 导入

**补丁列表：**
| 补丁描述 | 目标文件 | 修改内容 |
|----------|----------|----------|
| Patch basicsr functional_tensor import | `basicsr/data/degradations.py` | `try: from torchvision.transforms.functional_tensor import rgb_to_grayscale` <br> `except ImportError:` <br> `from torchvision.transforms.functional import rgb_to_grayscale` |

**使用方法：**
```bash
# 激活虚拟环境
source venv/bin/activate

# 运行补丁脚本
python scripts/post_install.py
```

**输出示例：**
```
[INFO] Using site-packages: /path/to/venv/lib/python3.12/site-packages

[PATCHED] Patch basicsr functional_tensor import for torchvision >= 0.16
```

---

### 2.7 generate_icons.py

**文件路径：** `scripts/generate_icons.py`

**作用：** 生成 HiImage 应用图标

**功能特性：**
- 生成 macOS 图标集（`.iconset/`）
- 生成 Windows ICO 文件（多尺寸）
- 生成 standalone PNG 文件（64, 128, 256, 512, 1024）
- 尝试生成 macOS ICNS 文件（Pillow 支持）
- 使用紫色渐变背景 + 大号 "H" + "IMAGE" 副标题设计

**设计的配色方案：**
- 顶部紫色：`#7F77DD`
- 底部紫色：`#534AB7`
- "H" 字母：`#EEEDFE`
- "IMAGE" 副标题：`#CECBF6`

**使用方法：**
```bash
# 激活虚拟环境（需要 PIL/Pillow）
source venv/bin/activate

# 运行图标生成脚本
python scripts/generate_icons.py
```

**输出文件：**
```
assets/icons/
├── HiImage.iconset/
│   ├── icon_16x16.png
│   ├── icon_16x16@2x.png
│   ├── ...
│   └── icon_512x512@2x.png
├── icon.ico              # Windows 图标
├── icon.icns             # macOS 图标
├── icon-64.png
├── icon-128.png
├── icon-256.png
├── icon-512.png
└── icon-1024.png
```

---

## 3. backend/ 目录脚本

### 3.1 run.py

**文件路径：** `backend/run.py`

**作用：** HiImage Backend - FastAPI 服务入口

**功能特性：**
- 解析命令行参数（`--host`, `--port`, `--reload`）
- 启动 uvicorn 服务器
- 支持自动重载（开发模式）

**使用方法：**
```bash
# 基本启动
python backend/run.py

# 指定主机和端口
python backend/run.py --host 0.0.0.0 --port 9000

# 启用自动重载（开发模式）
python backend/run.py --reload
```

**作为模块运行：**
```bash
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8787
```

---

### 3.2 app/main.py

**文件路径：** `backend/app/main.py`

**作用：** FastAPI 应用定义

**功能特性：**
- 定义 FastAPI 应用实例
- 配置 CORS 中间件
- 注册路由（系统、修复、超分、设置、日志、后处理、合成、模型管理）
- 注册 WebSocket 路由（进度推送）
- 生命周期管理（`lifespan`）：
  - 启动时：设置环境变量（`HF_ENDPOINT`, `HF_TOKEN`）、初始化日志系统
  - 启动时：应用默认环境变量（`apply_default_env_vars`）
  - 启动时：为 IOPaint 模型补全 `hub/` 软链接

**路由列表：**
| 路由模块 | 前缀 | 功能 |
|----------|------|------|
| `system` | `/api/system` | 系统信息（GPU、Python 版本等） |
| `inpaint` | `/api` | 去水印/修复接口 |
| `upscale` | `/api` | 超分辨率接口 |
| `settings` | `/api/settings` | 系统配置接口 |
| `logs` | `/api/logs` | 日志查询接口 |
| `postprocess` | `/api` | 后处理方法接口 |
| `synthesis` | `/api` | 智能合成接口 |
| `models` | `/api/models` | 模型管理接口 |
| `websocket` | `/ws` | WebSocket 进度推送 |

---

### 3.3 core/model_server.py

**文件路径：** `backend/core/model_server.py`

**作用：** IOPaint Server 进程管理器（单例）

**功能特性：**
- 针对扩散模型（AnyText / SD 系列）使用 `iopaint start` HTTP 服务模式
- 首次调用时按需启动，模型常驻内存，避免每次重载
- 5 分钟内无调用自动关闭，释放显存/内存
- 切换模型或设备时立即重启
- 通过 HTTP 请求触发推理（`/api/v1/inpaint`）

**配置参数（通过 `config/settings.json`）：**
- `server.port` - IOPaint 服务端口（默认 51821）
- `server.keepalive_seconds` - 保活时间（默认 300 秒）

**使用方法：**
（通常由 `inpainter.py` 自动调用，无需手动操作）

---

## 4. frontend/ 目录脚本

### 4.1 package.json 脚本命令

| 命令 | 作用 | 实现方式 |
|------|------|----------|
| `npm run dev` | 启动 Electron 前端开发模式 | `electron-vite dev` |
| `npm run build` | 构建 Electron 应用程序 | `electron-vite build` |
| `npm run preview` | 预览构建结果 | `electron-vite preview` |
| `npm run package` | 打包应用程序（DMG/EXE/AppImage） | `electron-builder` |

**使用方法：**
```bash
cd frontend

# 开发模式
npm run dev

# 构建
npm run build

# 打包
npm run package
```

---

## 5. 核心执行器脚本

这些脚本位于 `backend/core/`，是实际调用 AI 模型进行推理的模块。

### 5.1 inpainter.py

**文件路径：** `backend/core/inpainter.py`

**作用：** 去水印/修复执行器

**功能特性：**
- 支持双模式调用：
  1. **CLI 模式：** 针对 LaMa, MiGAN, ZITS 等快速模型，直接调用 `iopaint run` 命令（子进程）
  2. **Server 模式：** 针对 SD, SDXL, FLUX 等扩散模型，先启动 `iopaint start` 常驻服务，再通过 HTTP 请求触发推理
- 自动处理 `device_override`（如 MPS 不兼容时强制回退 CPU）
- 支持 ROI（感兴趣区域）和 Mask（遮罩）输入

---

### 5.2 upscaler.py

**文件路径：** `backend/core/upscaler.py`

**作用：** 超分辨率执行器

**功能特性：**
- 使用 **Real-ESRGAN** 库
- 根据 `arch`（`RRDBNet` or `SRVGGNetCompact`）动态构建神经网络
- 支持 `scale`（放大倍率）和 `outscale`（输出倍率，用于同分辨率增强）
- 自动检测模型权重是否存在，不存在则抛出 `FileNotFoundError`

---

### 5.3 restormer_executor.py

**文件路径：** `backend/core/restormer_executor.py`

**作用：** 图像复原执行器

**功能特性：**
- 使用 **Restormer**（Transformer-based）模型
- 支持多种任务类型：`denoise`, `deblur`, `derain`, `dehaze`
- 自动加载预训练权重

---

### 5.4 synthesizer.py

**文件路径：** `backend/core/synthesizer.py`

**作用：** 智能合成执行器

**功能特性：**
- 支持多种合成任务（如虚拟试穿、人脸交换等）
- 调用不同的底层模型执行器

---

### 5.5 其他执行器

| 文件路径 | 作用 |
|----------|------|
| `backend/core/background_fixer.py` | 背景修复执行器 |
| `backend/core/color_replacer.py` | 颜色替换执行器 |
| `backend/core/diffusers_executor.py` | Diffusers 模型执行器 |
| `backend/core/facexlib_executor.py` | FaceXLib 执行器（人脸相关） |
| `backend/core/flux_filler.py` | FLUX Filler 执行器 |
| `backend/core/grounded_segmenter.py` | Grounded Segmenter 执行器 |
| `backend/core/hiimage_executor.py` | HiImage 自定义执行器 |
| `backend/core/human_parser.py` | 人体解析执行器 |
| `backend/core/instruction_editor.py` | 指令编辑执行器 |
| `backend/core/intent_parser.py` | 意图解析器 |
| `backend/core/iopaint_executor.py` | IOPaint 执行器 |
| `backend/core/model_executor.py` | 通用模型执行器 |
| `backend/core/realesrgan_executor.py` | Real-ESRGAN 执行器 |
| `backend/core/rembg_executor.py` | RemBG 执行器（背景移除） |
| `backend/core/watermark_detector.py` | 水印检测器 |

---

## 6. 使用场景示例

### 场景 1：首次克隆项目，启动开发环境

```bash
# 克隆项目
git clone https://github.com/yourusername/HiImage.git
cd HiImage

# 一键启动开发环境（推荐）
npm run dev
```

`dev.js` 会自动完成：
1. 检测 Python 环境
2. 创建虚拟环境
3. 检测 GPU 并安装对应 PyTorch
4. 安装后端依赖
5. 应用 post-install 补丁
6. 启动后端（FastAPI）
7. 启动前端（Electron）

---

### 场景 2：仅启动后端（用于 API 调试）

```bash
npm run backend:dev
```

或

```bash
cd backend
../venv/bin/python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8787
```

---

### 场景 3：仅启动前端（后端已手动启动）

```bash
npm run frontend:dev
```

或

```bash
cd frontend
npm run dev
```

---

### 场景 4：检测模型完整性

```bash
# 激活虚拟环境
source venv/bin/activate

# 检测全部模型
python scripts/check_models.py

# 检测指定功能模式的模型
python scripts/check_models.py --mode watermark_removal

# JSON 格式输出
python scripts/check_models.py --json > model_status.json
```

---

### 场景 5：重新安装依赖（解决版本冲突）

```bash
# 激活虚拟环境
source venv/bin/activate

# 运行依赖安装脚本
python scripts/install_deps.py
```

---

### 场景 6：生成应用图标

```bash
# 激活虚拟环境
source venv/bin/activate

# 运行图标生成脚本
python scripts/generate_icons.py
```

---

### 场景 7：查询 HuggingFace 仓库大小

```bash
# 查询仓库大小
python scripts/check_hf_repo_size.py Sanster/PowerPaint_v2

# 使用 HF Token（访问门控模型）
export HF_TOKEN="your_token_here"
python scripts/check_hf_repo_size.py Sanster/some_gated_model
```

---

## 附录：脚本依赖关系图

```
npm run dev
  └─> scripts/dev.js
        ├─> 创建 venv/
        ├─> 安装 PyTorch（根据 GPU 环境）
        ├─> scripts/install_deps.py
        ├─> scripts/post_install.py
        ├─> 启动后端 (uvicorn)
        └─> 启动前端 (electron-vite dev)

npm run backend:dev
  └─> backend/run.py
        └─> uvicorn (app.main:app)

npm run frontend:dev
  └─> frontend/package.json
        └─> electron-vite dev

python scripts/check_models.py
  └─> backend/core/model_checker.py
  └─> backend/core/model_registry.py

python scripts/install_deps.py
  └─> pip install -r backend/requirements.txt
  └─> pip install <conflict_packages> --no-deps

python scripts/post_install.py
  └─> 修补 site-packages/basicsr/data/degradations.py

python scripts/generate_icons.py
  └─> PIL (Pillow)

python scripts/check_hf_repo_size.py
  └─> huggingface_hub (HfApi)
```

---

**文档版本：** 1.0  
**最后更新：** 2026-05-13  
**维护者：** HiImage Team
