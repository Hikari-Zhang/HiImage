# HiImage 系统设计

## 1. 设计目标

HiImage 以 Web 方式交付本地 AI 图像能力。设计优先级是：

1. 浏览器内完成文件选择、画布编辑、批量队列和结果下载。
2. FastAPI 统一承载模型管理与推理。
3. 开发和生产保持相同的 `/api` 同源调用约定。
4. CPU 默认可运行，NVIDIA GPU 显式启用。
5. 对内存、上传和网络暴露给出明确边界。

## 2. 组件设计

### 2.1 React/Vite

应用入口是 `frontend/src/main.tsx`，`App.tsx` 组织页面。页面首次可见时才加载所需数据，以减少初始请求。Zustand store 分离后端状态、设置、图像、模型、下载与处理进度。

`createApiClient()` 接受可选基址：

- 空基址：浏览器同源，生产首选。
- 显式 HTTP(S) 基址：本地调试或受控跨域环境。
- WebSocket：从 HTTP(S) 映射到 WS(S)，相对地址使用当前页面 host。

文件处理只使用 Web API。导入结果由 `File`/`Blob` 表示；保存使用浏览器下载，不依赖服务器看到客户端文件路径。

### 2.2 批量队列

批量约束由 `batchLimits.ts` 集中定义：

- 最多 100 张；
- 每张不超过 20 MB，后端解码后单请求上限为 30 MB；
- 输入总量不超过 128 MB；开始处理前读取真实像素尺寸，按 `宽 × 高 × 倍率² × 4` 聚合的输出 RGBA 与累计结果均不超过 256 MB；
- 不支持的文件在进入队列前拒绝；
- 同一队列顺序处理，不并行触发多个重推理任务；
- UI 展示估算峰值内存；ZIP 独立上限为 64 MB，每个成功项提供逐张下载，不声称流式 ZIP。

源文件大小不是运行时内存。浏览器解码后的 RGBA 图像约占 `宽 × 高 × 4` 字节，预览、Base64、结果图与模型请求还会产生额外副本，因此大图批次应分组导入。

### 2.3 FastAPI

API 层负责输入模型、错误响应和任务调度；`backend/core/` 负责模型和算法。配置、缓存、临时、输出和模型目录均从统一路径模块获得，避免工作目录影响。

健康语义分离：

- `/api/health` 表示 HTTP 进程可响应。
- `/api/ready` 检查运行目录可写，并验证用户配置的计算设备实际可用；响应同时给出配置设备、平台默认设备和不可用原因。

设置读取或保存失败必须向前端返回可显示的错误，前端在设置页保留持久错误提示，并可独立刷新健康状态。

### 2.4 实时通道

- WebSocket：处理百分比、完成、失败和日志。
- SSE：模型下载状态，适合服务器单向推送。
- HTTP：配置、模型清单和处理请求/结果。

Nginx 对 `/api/` 禁用代理缓冲和缓存，并传递 WebSocket 升级头。客户端断开后，订阅逻辑负责清理连接。

## 3. 开发运行

首次安装：

```bash
npm run setup
```

启动完整开发环境：

```bash
npm run dev
```

只做启动前检查：

```bash
npm run dev:check
```

按组件启动：

```bash
npm run backend:dev
npm run frontend:dev
```

后端默认 `127.0.0.1:8787`，Vite 默认 `127.0.0.1:5173`。MPS 只在 Apple Silicon macOS 本地后端可选并默认启用；Intel Mac 默认使用 CPU。

## 4. 构建与预览

```bash
npm run build
npm run preview
```

构建产物位于 `frontend/dist/`。`preview` 用于本机验收，不是公网生产服务器。生产入口由 `frontend/Dockerfile` 的非 root Nginx 镜像提供。

## 5. 容器设计

### 5.1 CPU 默认栈

```bash
docker compose up --build
```

包含 `backend` 与 `web`。Web 发布 `${WEB_HOST:-127.0.0.1}:${WEB_PORT:-8080}:80`；后端仅在 `cpu` 网络暴露 8787。

### 5.2 GPU 栈

```bash
docker compose --profile gpu up --build backend-gpu web-gpu
```

GPU 命令明确选择两个 GPU 服务，避免 profile 启用后顺带运行默认 CPU 服务。`backend-gpu` 使用 CUDA 12.4 运行时并申请一块 NVIDIA GPU；主机需安装 NVIDIA Container Toolkit。

### 5.3 卷

CPU：`cpu_config`、`cpu_cache`、`cpu_tmp`、`cpu_output`、`cpu_models`。

GPU：`gpu_config`、`gpu_cache`、`gpu_tmp`、`gpu_output`、`gpu_models`。

目标分别为 `/app/config`、`/app/cache`、`/app/tmp`、`/app/output`、`/app/models`。删除卷会删除对应持久化数据。

## 6. 网络与安全设计

生产页面和 API 同源，因此 Compose 将 `HIIMAGE_CORS_ORIGINS` 设为空。未设置该变量的本地开发后端只允许两个 Vite localhost 来源。通配 CORS 不允许凭据。

系统仅面向可信局域网单用户。默认绑定 `127.0.0.1`。若把 `WEB_HOST` 改为局域网地址，应确认主机防火墙和访问者范围。

当前实现不能直接安全暴露公网。公网入口至少需要认证、TLS、限流、上传检查和审计，并应评估模型下载接口与计算资源滥用风险。

## 7. 测试策略

根命令必须运行脚本、前端和后端全部测试：

```bash
npm test
```

前端使用 Vitest、jsdom 与 Testing Library，保留以下回归面：

- API URL、WebSocket 与 SSE URL；
- 浏览器文件读写；
- 超分倍率；
- 批量串行、100 张、20 MB 单文件及输入/结果总预算限制；
- 设置读取、保存与错误反馈；
- 关键设置 UI 流程。

后端覆盖路径、配置、CORS、readiness 和 Docker 配置变异。静态与运行态命令：

```bash
npm run typecheck
npm run build
python scripts/check_docker_config.py --static-only
python scripts/check_docker_config.py
python scripts/check_docs.py
python scripts/smoke_web.py --check
python scripts/smoke_web.py --url http://127.0.0.1:8080
```

静态 Docker 检查不等价于镜像构建或容器运行。Smoke 的 `--check` 不发送网络请求，也不等价于 live smoke。

## 8. 可观测性与失败处理

- HTTP 非 2xx 返回可读错误正文。
- 设置加载失败同时显示页面内提示和 toast。
- 下载通过 SSE 暴露队列状态、进度和失败原因。
- 推理通过 WebSocket 暴露进度和错误。
- 容器 healthcheck 分别检查 Nginx 首页与 FastAPI health。
- Smoke 严格要求首页 HTML shell、健康 `status=ok`、未知路由 HTML shell及 WebSocket 101 握手，避免把 404 或错误页误判为通过。
