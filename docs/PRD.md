# HiImage 产品需求文档

> 版本：3.0
>
> 形态：浏览器 Web 应用
>
> 运行范围：本机或可信局域网单用户

## 1. 产品定位

HiImage 面向个人创作者和设计人员，提供去水印、超分辨率、抠图、智能合成、模型管理和日志查看。用户通过现代浏览器访问 React 页面，FastAPI 在服务器侧执行模型下载和推理。

产品不包含多用户账号、权限或租户隔离。默认仅本机访问；局域网部署由用户显式配置。公网场景不在当前交付范围。

## 2. 核心流程

### 2.1 去水印

1. 从浏览器选择或拖入图片。
2. 在画布绘制 ROI 或遮罩，也可执行自动检测。
3. 选择模型、设备和修复参数。
4. 启动任务；请求携带必填 `task_id`，通过相同 ID 的独立 WebSocket 频道查看进度。
5. 比较前后结果并下载。

### 2.2 超分辨率

- 单张模式支持模型与 2x/4x 等有效倍率。
- 批量模式允许多选或拖入文件。
- 队列逐张串行处理，展示每张状态与汇总结果。
- 最多 100 张，单文件最多 20 MB、输入总量最多 128 MB；前后端按 `宽 × 高 × 倍率² × 4` 限制输出 RGBA 为 256 MB，浏览器累计结果最多 256 MB；后端解码后单请求上限为 30 MB。
- Web API 不接受服务器路径读写参数；浏览器仅上传内容并生成下载文件。ZIP 独立上限为 64 MB，每个成功结果提供逐张下载，不提供流式 ZIP。

### 2.3 抠图与智能合成

- 抠图提供自动分割、遮罩精修和背景合成。
- 智能合成包含背景替换、服装/人脸相关模式、局部提示词修复与指令编辑。
- 页面根据模式要求展示参考图、ROI 或提示词输入。

### 2.4 模型管理

- 展示 `backend/core/models.yaml` 注册模型及本地状态。
- 支持单个和批量提交下载、取消、删除。
- 下载队列限制并发，通过 SSE 展示排队位置、速度和错误。

### 2.5 设置与日志

- 显示同源服务健康状态，并可手动刷新。
- 加载或保存设置失败时提供明确提示。
- 配置 CPU、CUDA、本地 macOS MPS、内存优化、模型服务和镜像地址。
- HF token 读取只返回 `hf_token_configured`；空白更新保留，非空更新替换，`clear_hf_token=true` 显式清除。
- 日志通过 WebSocket 更新，支持级别筛选与清空。

## 3. Web 约束

### 3.1 文件和内存

浏览器侧仅持有用户授权选择的 `File`/`Blob`。源文件、解码像素、预览、Base64 和处理结果可能同时存在，实际峰值内存高于磁盘文件大小。UI 必须展示批量限制和内存估算，不承诺超大批次常驻内存。

### 3.2 网络

- 开发：Vite 将 `/api` 代理到 `127.0.0.1:8787`。
- 生产：Nginx 提供页面，并将 `/api/` 同源代理到 FastAPI。
- HTTP 承载请求；WebSocket 承载处理进度和日志；SSE 承载模型下载。
- 所有 WebSocket 握手必须通过 Origin 校验。`HIIMAGE_WS_ORIGINS` 未设置时允许默认开发源，空值仅同源，非空值按逗号分隔为显式白名单。`HIIMAGE_TRUST_PROXY_HEADERS` 默认关闭且仅 `1`/`true` 启用；受信 Nginx 必须覆盖客户端转发头。
- SPA 未知路由返回 `index.html`。

### 3.3 安全

- 默认绑定 `127.0.0.1`。
- 仅支持可信局域网单用户部署。
- LAN 仍无用户认证；第一阶段关闭高危服务器路径、凭据泄露和跨站 WebSocket，不等价于公网安全。
- 公网部署必须由外部入口增加认证、TLS、限流、上传策略和审计。
- 后端容器不直接发布主机端口。

## 4. 非功能要求

| 领域 | 要求 |
|---|---|
| 可用性 | 健康和 readiness 语义分离；设置错误可见 |
| 性能 | 批量串行，避免多个重模型任务并发 |
| 调度 | 单进程 1 个执行任务；`HIIMAGE_MAX_PENDING_INFERENCE` 默认 4、范围 `0-32`，满载返回 `429` |
| 兼容 | Chrome、Edge、Firefox、Safari 近期版本 |
| 设备 | CPU 必选；CUDA 可选；MPS 仅 Apple Silicon macOS 本地 |
| 部署 | CPU 默认 Compose；GPU 显式 profile 和服务 |
| 测试 | 根 `npm test` 覆盖脚本、前端和后端 |

## 5. 验收标准

### 5.1 开发与构建

```bash
npm run setup
npm run dev:check
npm run build
npm run typecheck
npm test
```

### 5.2 容器

CPU：

```bash
docker compose up --build
```

GPU：

```bash
docker compose --profile gpu up --build backend-gpu web-gpu
```

默认页面地址为 `http://127.0.0.1:8080`。CPU 与 GPU 使用独立网络和卷。

### 5.3 验证

```bash
python scripts/check_docker_config.py --static-only
python scripts/check_docs.py
python scripts/smoke_web.py --check
```

服务运行后：

```bash
python scripts/smoke_web.py --url http://127.0.0.1:8080
```

Live smoke 必须验证首页、`/api/health`、SPA 未知路由与 WebSocket 代理。静态检查和 `--check` 不得表述为运行态通过。

`GET /api/ready` 必须包含推理队列的 `running`、`pending`、`capacity`、`accepting` 快照。当前队列只在单个后端进程内有效，部署边界为单 worker；多 worker 不得被描述为共享一个 GPU 队列。

## 6. 后续范围

- 认证与多用户资源配额；
- 对象存储和服务端任务历史；
- 公网部署模板；
- 大文件流式上传；
- 批量任务持久化与断点恢复。
