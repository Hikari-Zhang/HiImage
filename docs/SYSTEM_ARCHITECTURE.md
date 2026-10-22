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
- `stores/`：Zustand 状态；编辑会话分别保存图片、结果、遮罩和参数，任务按 `task_id` 记录并绑定所属会话，超分单图与批量任务独立。
- `services/apiClient.ts`：统一生成同源 HTTP、WebSocket 和 SSE URL，并统一解析 API 响应与错误。
- `services/api/`：推理接口和模型/设备能力查询；React hook 只负责选择后端并缓存函数。
- `services/fileService.ts`：浏览器 File、Blob 和下载能力。
- `services/batchLimits.ts`：100 张、单文件 20 MB、输入总量 128 MB、累计结果 256 MB 与 ZIP 64 MB 限制。
- `services/imageDimensions.ts`：浏览器解码尺寸并按 `宽 × 高 × 倍率² × 4` 执行 256 MB 输出 RGBA 硬预算。

`AppRoutes.tsx` 按路由动态加载页面，`MainLayout` 只挂载当前页面；模型管理在设置页选择对应页签后才加载。页面切换释放画布、页面订阅和日志 WebSocket，并取消尚未完成的页面元数据请求。加载中与加载失败由 `PageBoundary` 提供状态和重试入口。

编辑会话与批量队列由 store 保留，已开始的推理和模型下载独立于页面继续执行。遮罩导出由 `services/maskExport.ts` 为每个任务创建 Worker，导出完成、失败或 60 秒超时后释放，页面卸载不会中断导出。设置草稿按后端地址保存在内存中，保留未保存输入和保存中状态；保存响应不会清除提交后新输入的内容。上述状态不支持浏览器刷新恢复。

任务开始时额外把至多 20 条任务 ID、工具会话、有效后端地址和开始时间写入当前标签页的 sessionStorage，不保存图像、遮罩、提示词或结果。成功、失败及被替代的任务都保留最近的元数据，用户移除提示或超过数量上限时删除，以支持完成后再刷新的结果取回。刷新后 `TaskRecoveryNotice` 按后端地址查询一次状态，可手动刷新或移除提示；可下载仍在服务端暂存的结果，不自动重新提交推理、覆盖编辑器或启动剩余批量队列。404 表示无法确认；成功但缓存不可用时明确提示无法取回。存储不可用不阻断处理。

前端不拥有后端进程生命周期，也不依赖操作系统文件路径。Web API 已无服务器路径读写模式：浏览器仅上传内容，选择的文件保存在内存中，结果由浏览器下载。批量处理按顺序发送，避免并发放大模型显存和页面内存。

## 3. Nginx 同源网关

`frontend/nginx.conf` 承担：

- 返回 `frontend/dist` 静态文件。
- `try_files $uri $uri/ /index.html` 支持浏览器未知路由回退。
- 把 `/api/` 代理到内部别名 `backend:8787`。
- 传递 `Upgrade` / `Connection` 以支持 WebSocket。
- 覆盖客户端传入的 `X-Forwarded-*`，再传递外部协议与保留端口的 host，让后端按浏览器看到的外部源校验 WebSocket Origin。
- 禁用 API 缓冲和缓存，保证 SSE/流式响应及时到达。
- 将请求体上限设为 80 MB；前端执行 20 MB 单文件限制，后端每张图片或遮罩执行 30 MB 压缩文件字节数限制，并在解码前检查 256 MB 输入 RGBA 预算。
- 为带哈希的静态资源设置长期不可变缓存。

## 4. FastAPI 后端

`backend/app/main.py` 注册系统、设置、模型、修复、超分、抠图、合成、日志、后处理和 WebSocket 路由。主要接口：

- `GET /api/health`：进程健康。
- `GET /api/ready`：配置、缓存、临时和输出目录可写性，以及用户配置设备的实际可用性。
- `GET /api/devices`：CPU、CUDA、MPS 能力。
- `GET /api/tasks/{task_id}`：当前进程内的任务状态、进度和 HTTP 终态，不返回图像结果。
- `GET /api/tasks/{task_id}/result`：取回仍在保留期内的成功响应 JSON，不重新执行推理。
- `GET/PUT /api/settings`：持久化设置。
- `POST /api/inpaint*`、`/api/upscale*`、`/api/cutout*`、`/api/synthesis/*`：图像任务。
- `/api/models/*`：模型状态、下载和 SSE 进度。
- `/api/ws/progress`、`/api/ws/logs`：处理进度和日志。

`backend/app/services/` 承担主要图像处理编排、模型就绪检查、统一模型分组及进度适配。主要推理路径为 `router → service → core`，路由之间不互相导入公共函数，算法通过传入回调报告进度。`backend/core/` 负责路径、模型注册、下载队列和推理执行器。耗时推理在线程或子进程中运行，避免阻塞 ASGI 事件循环。

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

11 个图像 POST 接口通过 `TaskRoute` 统一记录任务。状态为 `queued`、`running`、`succeeded`、`failed`、`interrupted`，以 HTTP 处理结果确认终态，WebSocket 的阶段完成不会提前结束记录。抠图精修、背景合成仍不占推理槽。任务记录默认最多 200 条，终态保留最多 600 秒；容量不足可提前淘汰最早结束的记录，但不淘汰活动任务。记录全部活动且已满时返回可重试 503。

同一 task_id 在记录保留期间重复提交返回 `409 TASK_ID_CONFLICT`，不重放结果；推理队列拒绝的 429 不保留记录，该 ID 可显式重试。查询未知或已淘汰任务返回 404，并设置 `Cache-Control: no-store`。状态记录仅包含任务元数据和 result_available，不包含请求正文、图像结果或异常正文。请求取消会等待已启动的阻塞工作退出，再记为 interrupted。服务重启丢失状态记录，暂存结果不支持跨服务重启恢复。

成功图像响应在返回前通过工作线程暂存到 `HIIMAGE_TMP_DIR` 下独立的随机临时目录；单份 JSON 最多 64 MiB、当前进程缓存总量最多 256 MiB，TTL 600 秒。访问时惰性清理过期文件，容量不足淘汰最早写入项，状态记录淘汰时同步清理关联文件。文件名不含 task_id；缓存超预算或写入失败不影响原 HTTP 成功响应。正常进程退出会清理临时目录，异常强制终止可能遗留临时文件；这些文件不能在重启后通过接口取回。客户端一次下载单张 PNG；图片与遮罩或批量成功项打包 ZIP，解码下载预算为 64 MiB，不覆盖当前编辑内容。

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
