# HiImage 系统架构

## 1. 架构概览

HiImage 是浏览器访问的单用户 AI 图像处理系统：

```text
Browser
  └─ HTTP / WebSocket / SSE
       └─ Nginx :80
            ├─ /、静态资源、SPA 回退 → React/Vite 构建产物
            └─ /api/* → FastAPI :8787
                           ├─ 路由与任务编排
                           ├─ 模型注册、下载与缓存
                           └─ CPU / CUDA / 本地 macOS MPS 推理
```

开发环境由 Vite 提供页面和 `/api` 代理；生产容器由 Nginx 提供同源入口。浏览器不直接访问容器内的后端端口。

## 2. 前端

`frontend/src/` 是 React 18 + TypeScript 应用，Vite 负责开发和构建：

- `pages/`：去水印、超分辨率、抠图、智能合成、模型、日志和设置。
- `components/`：画布、遮罩工具、批量队列、进度和通用 UI。
- `stores/`：Zustand 状态。
- `services/apiClient.ts`：统一生成同源 HTTP、WebSocket 和 SSE URL。
- `services/fileService.ts`：浏览器 File、Blob 和下载能力。
- `services/batchLimits.ts`：100 张、单文件 20 MB、输入总量 128 MB、累计结果 256 MB 与 ZIP 64 MB 限制。
- `services/imageDimensions.ts`：浏览器解码尺寸并按 `宽 × 高 × 倍率² × 4` 执行 256 MB 输出 RGBA 硬预算。

前端不拥有后端进程生命周期，也不依赖操作系统文件路径。Web API 已无服务器路径读写模式：浏览器仅上传内容，选择的文件保存在内存中，结果由浏览器下载。批量处理按顺序发送，避免并发放大模型显存和页面内存。

## 3. Nginx 同源网关

`frontend/nginx.conf` 承担：

- 返回 `frontend/dist` 静态文件。
- `try_files $uri $uri/ /index.html` 支持浏览器未知路由回退。
- 把 `/api/` 代理到内部别名 `backend:8787`。
- 传递 `Upgrade` / `Connection` 以支持 WebSocket。
- 覆盖客户端传入的 `X-Forwarded-*`，再传递外部协议与保留端口的 host，让后端按浏览器看到的外部源校验 WebSocket Origin。
- 禁用 API 缓冲和缓存，保证 SSE/流式响应及时到达。
- 将请求体上限设为 80 MB；前端执行 20 MB 单文件限制，后端执行 30 MB 解码后单请求限制。
- 为带哈希的静态资源设置长期不可变缓存。

## 4. FastAPI 后端

`backend/app/main.py` 注册系统、设置、模型、修复、超分、抠图、合成、日志、后处理和 WebSocket 路由。主要接口：

- `GET /api/health`：进程健康。
- `GET /api/ready`：配置、缓存、临时和输出目录可写性，以及用户配置设备的实际可用性。
- `GET /api/devices`：CPU、CUDA、MPS 能力。
- `GET/PUT /api/settings`：持久化设置。
- `POST /api/inpaint*`、`/api/upscale*`、`/api/cutout*`、`/api/synthesis/*`：图像任务。
- `/api/models/*`：模型状态、下载和 SSE 进度。
- `/api/ws/progress`、`/api/ws/logs`：处理进度和日志。

`backend/core/` 负责路径、模型注册、下载队列和推理执行器。耗时推理在线程或子进程中运行，避免阻塞 ASGI 事件循环。

## 5. 通信与数据流

1. 浏览器读取用户选择的文件，执行类型、20 MB 单文件、128 MB 输入总量和批量数量验证；发起超分 API 前解码尺寸并聚合检查 256 MB 输出 RGBA 预算。
2. 前端把图像和参数通过同源 `/api/` 发送给 Nginx。
3. FastAPI 解析请求、检查模型并调用对应执行器。
4. 每个推理请求携带必填 `task_id`；前端以同一 ID 订阅独立 WebSocket 频道，任务进度不会全局匿名广播。模型下载状态通过 SSE 返回。
5. 结果以图像数据返回浏览器，累计结果限制为 256 MB；ZIP 独立限制为 64 MB，超限时使用每个成功项的逐张下载。

WebSocket URL 根据页面协议自动选择 `ws:` 或 `wss:`。SSE 与普通 API 使用相同的同源基址。

所有 WebSocket 在 `accept` 前校验 Origin。`HIIMAGE_WS_ORIGINS` 未设置时允许同源和两个默认 Vite 开发源；空值表示仅同源；非空值按逗号分隔为显式白名单并替代默认开发源。`HIIMAGE_TRUST_PROXY_HEADERS` 默认 `false`，仅 `1`/`true` 启用；Docker 后端在受控 Nginx 后设为 `true`，直连时使用 ASGI scheme 与 `Host`。

## 6. 路径与持久化

后端路径由 `backend/core/paths.py` 和环境变量统一解析：

| 环境变量 | 容器路径 | 用途 |
|---|---|---|
| `HIIMAGE_CONFIG_DIR` | `/app/config` | 设置 |
| `HIIMAGE_CACHE` | `/app/cache` | 框架缓存 |
| `HIIMAGE_TMP_DIR` | `/app/tmp` | 临时文件 |
| `HIIMAGE_OUTPUT_DIR` | `/app/output` | 输出 |
| `HIIMAGE_MODELS_DIR` | `/app/models` | 权重 |

Compose 为 CPU 与 GPU 栈使用不同命名卷和网络，避免配置、缓存和模型状态交叉污染。

## 7. 部署拓扑

默认 CPU：

```bash
docker compose up --build
```

NVIDIA GPU：

```bash
docker compose --profile gpu up --build backend-gpu web-gpu
```

两种栈都只发布 Web 端口，默认 `127.0.0.1:8080`。`backend` / `backend-gpu` 仅在各自内部网络暴露 8787。Web 服务等待后端健康后启动。

MPS 不能在当前 Linux 容器镜像中使用，仅支持 Apple Silicon macOS 本地后端并在该平台默认启用；Intel Mac 默认使用 CPU。

## 8. 安全边界

当前系统按可信局域网、单用户设计，没有内建登录、权限隔离或租户配额。默认回环绑定和空 CORS 来源减少意外暴露，但不构成公网安全方案。

设置接口使用敏感字段白名单：`GET /api/settings` 只返回 `hf_token_configured`，不返回 HF token；更新时空值或省略保持原 token，非空值替换，只有 `clear_hf_token=true` 显式清除。第一阶段关闭服务器路径读写、凭据泄露和跨站 WebSocket，不等价于公网安全。

公网部署必须在入口前增加：

- 身份认证和授权；
- TLS 终止；
- 请求/上传限流与大小控制；
- 日志审计和敏感信息处理；
- 受控的模型与输出存储权限。

## 9. 推理调度边界

每个后端进程只有 1 个推理执行槽，`HIIMAGE_MAX_PENDING_INFERENCE` 默认允许 4 个等待任务，可配置 `0-32`。容量为“1 执行 + N 等待”，满载返回结构化、可重试的 `429`；`GET /api/ready` 暴露 `running`、`pending`、`capacity`、`accepting` 快照。

队列和单线程执行器仅在当前进程内共享。多 worker 会创建多个独立队列并可能同时占用同一 GPU，因此当前部署边界是单进程、单 worker。

生命周期关闭顺序为：调度器停止接纳并排空已接纳任务、停止 IOPaint model server、幂等关闭工作线程。调度器或 server 清理异常会记录，但不会阻断后续清理。

## 10. 运行与验证

```bash
npm run setup
npm run dev
npm run build
npm run preview
npm test
npm run typecheck
python scripts/check_docker_config.py --static-only
python scripts/check_docs.py
python scripts/smoke_web.py --check
```

真实运行态验证需要服务已启动：

```bash
python scripts/smoke_web.py --url http://127.0.0.1:8080
```
