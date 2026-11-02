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

应用入口是 `frontend/src/main.tsx`，`App.tsx` 组织页面。页面首次可见时才加载所需数据，以减少初始请求。Zustand store 分离后端状态、设置、图像、模型、下载与处理进度。去水印、超分、智能合成和抠图各有独立编辑会话；会话保存原图、结果、遮罩及编辑参数，应用设置仅作为新会话的初始默认值。

`createApiClient()` 接受可选基址：

- 空基址：浏览器同源，生产首选。
- 显式 HTTP(S) 基址：本地调试或受控跨域环境。
- WebSocket：从 HTTP(S) 映射到 WS(S)，相对地址使用当前页面 host。

文件处理只使用 Web API。Web API 已无服务器路径读写参数：浏览器仅上传内容，导入结果由 `File`/`Blob` 表示；保存使用浏览器下载。

### 2.2 批量队列

批量约束由 `batchLimits.ts` 集中定义：

- 最多 100 张；
- 每张不超过 20 MB，后端每张图片或遮罩的压缩文件字节数上限为 30 MB，解码前检查 256 MB 输入 RGBA 预算；
- 输入总量不超过 128 MB；开始处理前读取真实像素尺寸，按 `宽 × 高 × 倍率² × 4` 聚合的输出 RGBA 与累计结果均不超过 256 MB；
- 不支持的文件在进入队列前拒绝；
- 同一队列顺序处理，不并行触发多个重推理任务；
- UI 展示估算峰值内存；ZIP 独立上限为 64 MB，每个成功项提供逐张下载，不声称流式 ZIP。

源文件大小不是运行时内存。浏览器解码后的 RGBA 图像约占 `宽 × 高 × 4` 字节，预览、Base64、结果图与模型请求还会产生额外副本，因此大图批次应分组导入。

### 2.3 FastAPI

API 层负责输入模型、错误响应和任务调度；`backend/app/services/` 负责图像处理编排、模型就绪检查、模型分组及进度适配；`backend/core/` 负责模型和算法。配置、缓存、临时、输出和模型目录均从统一路径模块获得，避免工作目录影响。

健康语义分离：

- `/api/health` 表示 HTTP 进程可响应。
- `/api/ready` 检查运行目录可写，并验证用户配置的计算设备实际可用；响应同时给出配置设备、平台默认设备和不可用原因。

设置读取或保存失败必须向前端返回可显示的错误，前端在设置页保留持久错误提示，并可独立刷新健康状态。

### 2.4 实时通道

- WebSocket：处理百分比、完成、失败和日志；所有握手先校验 Origin。
- SSE：模型下载状态，适合服务器单向推送。
- HTTP：配置、模型清单和处理请求/结果。

前端按 `task_id` 保存任务记录，并将任务绑定到编辑会话；进度、失败和完成只更新所属会话。超分单图与批量任务使用独立任务槽，互不替代。换图会使本会话旧任务失效，迟到响应不得写入新图；这不会取消后端已经开始的推理。任务终态历史最多保留约 100 条，运行中的其他会话任务保留。

所有推理请求模型都要求 1-128 字符的 `task_id`，进度 WebSocket 以相同 ID 建立每任务频道，不存在全局匿名进度订阅。Nginx 对 `/api/` 禁用代理缓冲和缓存，传递 WebSocket 升级头，并覆盖客户端提供的 `X-Forwarded-*`。客户端断开后，订阅逻辑负责清理连接。

### 2.5 设置与调度

设置读取按响应白名单组装，HF token 只显示 `hf_token_configured`。更新时，省略或空白 `hf_token` 保留旧值，非空值替换；`clear_hf_token=true` 才清除持久值和运行时环境。

推理调度器在每个后端进程内固定 1 个执行槽。`HIIMAGE_MAX_PENDING_INFERENCE` 默认 `4`、范围 `0-32`，满载返回结构化 `429`。`GET /api/ready` 返回队列 `running`、`pending`、`capacity`、`accepting` 快照。队列不跨进程共享，因此当前只支持单 worker GPU 调度边界。

关闭时调度器先停止接纳并排空任务，然后由调度器幂等关闭工作线程，再停止 IOPaint model server；调度器关闭失败时额外尝试关闭线程，清理异常会记录且后续服务清理仍执行。

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

生产页面和 API 同源，因此 Compose 将 `HIIMAGE_CORS_ORIGINS` 和 `HIIMAGE_WS_ORIGINS` 都设为空。对 WebSocket，`HIIMAGE_WS_ORIGINS` 未设置时允许同源和两个默认 Vite 开发源，空值仅同源，非空值是替代默认开发源的逗号分隔显式白名单。`HIIMAGE_TRUST_PROXY_HEADERS` 默认 `false`，仅 `1`/`true` 启用；Docker 后端在会覆盖转发头的 Nginx 后设为 `true`。通配 CORS 不允许凭据。

系统仅面向可信局域网单用户。默认绑定 `127.0.0.1`。若把 `WEB_HOST` 改为局域网地址，应确认主机防火墙和访问者范围。

LAN 部署仍没有内建用户认证。第一阶段仅关闭服务器路径读写、凭据泄露和跨站 WebSocket，不等价于公网安全。公网入口至少需要认证、TLS、限流、上传检查和审计，并应评估模型下载接口与计算资源滥用风险。

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
- HF token 脱敏、保留、替换和显式清除；
- 所有推理请求的 `task_id`、所有 WebSocket 的 Origin 校验，以及所有 AI 路由的 admission 矩阵；
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

- HTTP 非 2xx 由前端统一解析为 `ApiError`，保留状态码、错误码和可重试标志；响应体只读取一次，支持 JSON 错误、FastAPI 校验数组及纯文本，HTML 网关错误使用简短提示。
- 设置加载失败同时显示页面内提示和 toast。
- 下载通过 SSE 暴露队列状态、进度和失败原因。
- 推理通过 WebSocket 暴露进度和错误。
- 容器 healthcheck 分别检查 Nginx 首页与 FastAPI health。
- Smoke 严格要求首页 HTML shell、健康 `status=ok`、未知路由 HTML shell及 WebSocket 101 握手，避免把 404 或错误页误判为通过。

## 9. 图像校验与模型会话

Web 图片和遮罩由 `backend/app/services/image_codec.py` 统一编解码。`image.max_decode_mb`（默认 30）限制 Base64 代表的压缩文件字节数；`image.max_input_rgba_mb`（默认 256）限制按 `宽 × 高 × 4` 估算的单张输入像素量。读取图片头部后、OpenCV 解码前执行预算检查。该预算不代表进程总内存，模型、临时数组和多个输入仍会产生额外占用。

独立超分与 Pipeline 在模型执行前共用输出倍率和 `image.max_output_mb`（默认 256）校验。遮罩、精修画笔及后处理结果必须与原图尺寸一致，不匹配返回 422；无效图像返回 422，超预算返回 413。

修复、抠图、Pipeline 和合成中的模型执行器使用 `core/model_session.py` 管理一次使用的资源，成功或失败都会调用卸载方法；若推理与清理同时失败，记录清理错误并保留原始推理异常。Pipeline 超分步骤同样显式关闭模型。

## 10. 编辑会话与工具之间传图

`useImageStore.ts` 为去水印、超分、智能合成分别创建图像 store，抠图由 `useCutoutStore.ts` 管理。换图统一清除结果、遮罩、ROI 和旧尺寸，并增加原图版本，旧尺寸加载回调不能覆盖新图。超分结果同时保存实际输出尺寸，抠图精修会清除先前的背景合成结果。

`useEditorSettings.ts` 按会话保存模型、设备和编辑参数；智能合成的参考图与提示词同样保存在会话中。`useUpscaleBatchStore.ts` 保存单图/批量模式、队列及批处理位置。编辑内容在当前浏览器应用生命周期内保留；刷新后可以查询最近任务并下载仍在服务端暂存的成功结果，不恢复原图、编辑内容和后续批量队列，详见第 15–16 节。

页面提供“发送结果到…”选择框，用户选择目标工具后才替换该工具原图并导航过去；发送到超分时切换至单图模式，正在执行的批量任务仍保持独立。非当前路由的页面会卸载，不处理编辑快捷键。画布在原图替换或卸载后忽略迟到的图片加载回调。

## 11. 服务边界与 API 响应处理

后端主要图像处理路径遵循 `router → app.services.image_processing → core`。路由保留请求校验、共享推理调度、HTTP 结果序列化和任务终态上报；服务接收图像数组、参数和进度回调，负责模型调用及处理链编排。抠图精修与背景合成仍在线程中执行，不占用 GPU 推理队列。

`model_readiness.py` 管理模型就绪检查的应用层进度，`model_catalog.py` 为独立和聚合模型接口提供一致分组；路由不再相互导入公共函数。`progress.py` 将同步算法回调线程安全地适配到指定任务的 WebSocket 频道，并观察异步发送错误；`core/utils.py` 不再依赖 WebSocket。Pipeline 的实际阶段进度通过该适配器发送。

前端 `services/api/processing.ts` 与 `services/api/catalog.ts` 定义推理和能力查询接口，`useBackendAPI` 只选择后端地址并缓存 API 函数。设置、模型、日志、下载和推理共用 `apiClient.ts` 的响应解析。普通 JSON 接口拒绝非成功状态和无效成功 JSON，空内容修改操作通过 `ensureApiSuccess` 校验；429 保持统一队列满提示，不自动重试。请求取消保持原始 `AbortError`，错误响应不再先读 JSON 后尝试第二次读取文本。

## 12. 页面加载与资源生命周期

`AppRoutes.tsx` 使用动态 import，`MainLayout` 通过 Outlet 只挂载当前路由。设置中的模型管理页签单独按需加载。`PageBoundary` 提供加载提示、错误提示及重试，重试不刷新整个应用，保留 store 中的会话。

页面卸载时取消设备、能力、模型列表与日志初始读取，释放日志 WebSocket 和页面订阅。ModelManager 的请求标识隔离旧请求的结束回调，保证 React StrictMode 的挂载检查和快速切换不会锁死新请求。

已开始的单图/批量推理保留原会话归属和异步回调，离开页面后继续执行并写回 store，返回时恢复结果和队列。批量处理在异步尺寸预检前就锁定提交，避免切页返回后重复启动。下载 SSE 仍由应用级下载管理器管理。

遮罩 Worker 按导出任务创建，无操作的页面不创建 Worker；切页不终止已开始的导出，成功、错误或 60 秒超时都会清理 Worker。Worker 内部的异步图像编码失败通过 export-error 返回。

`useSettingsDraft.ts` 按后端地址保存内存草稿、页签和保存状态，避免切页丢失输入或重复提交。草稿修改带版本号，旧保存完成不会清除之后输入的新内容；Token 只在内存中暂存，成功保存对应版本后清空。浏览器刷新后的状态恢复不属于本次改造。

## 13. 任务连接与失败重试

`createTaskProgressSession` 只在 run 时创建连接，一个会话只能执行一次。任务在执行前或连接打开前被替代时不提交推理；连接创建异常、握手失败及 HTTP 失败统一进入调用方错误处理，并在 finally 中释放连接。失败后用户再次操作会创建新的 task_id，不自动重放推理 POST。

进度通道与 HTTP 结果分开处理：WebSocket 的 complete/error 仅更新进度提示，任务终态和结果由 HTTP 调用方确认。运行中的进度连接可有限重连，重连不会再次提交 HTTP 请求。会话结束或被替代后的消息会被忽略；旧 socket 的 open/close/message/error 事件不能影响当前连接，释放连接时清除回调和计时器。

进度载荷须为对象，progress 百分比须为 0–100 的有限数字；非字符串提示不传入界面，携带不同 task_id 的消息被忽略。非法 JSON 由会话捕获记录，不中断当前 HTTP 请求。当前仍使用同步 HTTP 返回图像结果，不将网络断开等同于后端任务取消。任务状态查询和成功结果的临时取回见后续章节。

## 14. 后端任务状态查询

所有 11 个图像 POST 接口使用 `TaskRoute`，统一记录真实 HTTP 生命周期，包括直接返回错误 JSONResponse 的路径。共用推理调度器的请求先记为 queued，获得执行槽后切为 running；CPU 精修和背景合成直接进入 running。缺失或无效 task_id 仍按原请求验证规则返回 422。具有有效 ID、但其他参数校验失败的请求会留下 failed/422 记录。

`GET /api/tasks/{task_id}` 返回 `task_id`、`operation`、`status`、`progress`、`message`、`created_at`、`updated_at`、`finished_at`、`http_status`、`result_available`。时间戳为 Unix 秒；未结束时 finished_at 和 http_status 为 null。status 为 queued/running/succeeded/failed/interrupted。查询返回 no-store，未知、过期、容量淘汰或服务重启丢失的 ID 返回 404，不能据此断言任务从未执行。

`services/task_registry.py` 提供线程安全的内存记录，默认最多 200 条，终态 TTL 为 600 秒。容量不足时优先淘汰最早结束的记录，不淘汰 queued/running。全部为活动记录且容量已满时返回 `503 TASK_REGISTRY_FULL`（retryable=true）。进度通过 ProgressManager 在广播前更新，无 WebSocket 订阅者时也可查询；迟到的更新不能覆盖终态。记录不含输入、结果 Base64 或异常正文，进度提示最多保留 500 字符。

HTTP 返回 2xx/3xx 时记录 succeeded，错误响应或异常记录 failed；批量接口的 succeeded 表示 HTTP 请求处理成功，单项失败仍以原响应的 summary/results 为准。取消请求记为 interrupted，已启动的线程计算结束前仍保留 running，避免虚假表示 GPU 已释放。记录的是服务端处理结果，不保证浏览器已收到完整响应。

保留期内重复 task_id 返回 `409 TASK_ID_CONFLICT`（retryable=false），不会再次执行或返回旧图像结果。推理队列满的 429 删除临时记录，不占用 task_id，允许用户显式重试。前端 `services/api/tasks.ts` 提供带取消信号、禁用缓存的 getTask/getTaskResult，复用统一错误解析。刷新后的查询提示与结果暂存见后续章节。部署仍要求单进程、单 worker，记录不跨进程共享。

## 15. 刷新后的状态提示

任务开始时，`useProcessStore` 通过 `taskRecoveryStorage.ts` 写入当前标签页 sessionStorage 的 `hiimage.task-recovery.v1`。字段仅限 taskId、sessionId、规范化后的有效 backendURL 和 createdAt（Unix 毫秒）。最多保留最近 20 条，读取时验证字段、限制长度并去重；损坏、不可用或配额不足的存储不阻断正常任务。图片、遮罩、提示词、参数与结果均不持久化。

成功、失败或被替代的任务均保留最近元数据，用户移除提示或数量淘汰时删除，以支持处理完成后再刷新并取回结果。失败不能证明后端未执行，查询时也不自动重试。恢复提示按后端地址隔离；同一应用中仍在运行的任务不作为刷新遗留任务查询，不修改其编辑会话或任务终态。

`TaskRecoveryNotice` 在应用启动和后端地址切换时自动查询一次，提供手动刷新；不持续轮询，不发送推理 POST。请求总等待限制为 10 秒，超时或网络失败后可重试。卸载和后端切换取消在途读取，并忽略迟到响应；支持 StrictMode 的挂载检查。有效状态同时校验任务 ID，异常响应不误判为成功。

排队和运行提示等待，避免重复提交；成功且 result_available=true 时提供下载结果，否则明确提示无法取回；失败和中断提示重新选择图片处理；404 只表示记录不可查询，可能过期或服务重启，不能当作执行失败。用户移除提示只删除当前标签页元数据，不取消后端任务。状态查询不恢复原图、编辑参数或尚未提交的后续批量队列。

## 16. 成功结果暂存与取回

`task_results.py` 将 TaskRoute 已序列化的成功图像响应字节写入独立随机临时目录，目录位于 HIIMAGE_TMP_DIR，文件名使用随机 UUID，不由 task_id 或客户端文件名拼接。不复制请求正文，不缓存检测接口或错误响应；保留原始 JSON 输出，因此抠图包含 mask，批量保留原 results/summary。

默认单项上限 64 MiB、缓存总量 256 MiB、TTL 600 秒。访问时清理过期项；容量不足按写入顺序淘汰，状态记录过期/淘汰也移除关联结果。单项超限、磁盘不可写或缓存异常时，仍正常返回原处理响应，result_available=false。文件读写使用工作线程；状态响应不包含 Base64。正常进程结束清理临时目录，强制终止可能遗留临时文件；没有重启后的任务索引和恢复能力，缓存不是长期输出归档。

`GET /api/tasks/{task_id}/result` 返回原成功 JSON，禁用缓存。任务仍在运行或未成功时返回 409，记录缺失返回 404，结果未保存/过期/淘汰返回 410。状态查询与结果读取之间允许缓存被淘汰，前端按 404/410 提示失效，不自动重新推理。已取消并记录为 interrupted 的请求不提供结果。

用户点击下载后才取回 JSON，单张输出保存为 PNG，多张或 image+mask 打包 ZIP。文件名由客户端固定生成，不采信服务端文件路径；下载前验证 PNG 签名、批量最多 100 项、总解码预算 64 MiB。下载过程最多等待 60 秒，防止重复点击；后端切换、卸载或移除提示会取消取回，并在 ZIP 生成结束前再次检查取消状态。下载不会覆盖当前编辑会话；提示仅说明已发起浏览器下载，不把程序触发下载视为用户已成功保存。
