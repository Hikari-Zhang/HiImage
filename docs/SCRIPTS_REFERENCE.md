# HiImage 脚本参考

本文只记录当前 `package.json` 与 `scripts/` 中可执行的命令。

## 1. 根 npm 命令

| 命令 | 作用 |
|---|---|
| `npm run setup` | 安装/校验前端和 Python 环境 |
| `npm run dev` | 启动 FastAPI 与 Vite 开发服务 |
| `npm run dev:check` | 显示环境和启动命令，不启动服务 |
| `npm run backend:dev` | 只启动 FastAPI 开发服务 |
| `npm run frontend:dev` | 只启动 Vite 开发服务 |
| `npm run build` | 构建 `frontend/dist` |
| `npm run typecheck` | 执行前端 TypeScript 检查 |
| `npm test` | 依次运行脚本、前端、后端测试 |
| `npm run preview` | 构建后启动本地预览栈 |
| `npm start` | 启动本地预览栈 |
| `npm run backend:start` | 只启动预览模式后端 |
| `npm run frontend:preview` | 只启动 Vite preview |

常用流程：

```bash
npm run setup
npm run dev
```

提交前：

```bash
npm test
npm run typecheck
npm run build
```

## 2. 前端 npm 命令

在 `frontend/` 内执行：

| 命令 | 作用 |
|---|---|
| `npm run dev` | Vite 开发服务器 |
| `npm run build` | Vite 生产构建 |
| `npm test` | Vitest 全量测试 |
| `npm run typecheck` | `tsc --noEmit` |
| `npm run preview` | Vite 构建预览 |

前端是浏览器应用，生产构建由 Nginx 提供，不生成操作系统安装包。

## 3. 开发启动脚本

### `scripts/setup.js`

跨平台环境安装入口。使用 `scripts/lib/setup-runner.js` 与 `scripts/lib/python-env.js` 检测 Node、Python 3.10+、PyTorch 目标和安装指纹；低于 Python 3.10 的宿主解释器会被拒绝，项目 `venv/` 若仍使用旧版本则安全删除并以已检测到的 3.10+ 宿主重建。

### `scripts/dev.js`

跨平台开发/预览编排器：

- 检查环境，不满足时调用 setup；
- 在 `backend/` 目录使用 `python -m uvicorn app.main:app` 启动后端（开发模式附加 `--reload`）；
- 等待 `/api/health`；
- 启动 Vite dev 或 preview；
- 收到退出信号后清理子进程树。

只检查、不启动：

```bash
npm run dev:check
```

### `scripts/dev.sh`

适用于 macOS/Linux 的 Bash 辅助启动脚本。跨平台使用优先选择根 `npm run dev`。

### `scripts/python.js`

选择项目虚拟环境 Python，并透传参数。根后端测试通过它执行：

```bash
node scripts/python.js -m pytest tests backend/tests
```

## 4. 验证脚本

### `scripts/check_docker_config.py`

验证 Dockerfile、Nginx 和 Compose 不变量。静态检查同时要求 `.env.example` 与 Compose 将 `HIIMAGE_MAX_PENDING_INFERENCE` 设为默认 `4`（允许范围由后端限制为 `0-32`）、将 `HIIMAGE_WS_ORIGINS` 设为空值同源模式；`.env.example` 中 `HIIMAGE_TRUST_PROXY_HEADERS=false`，Docker 后端设为 `true`，且 Nginx 必须覆盖转发头。Docker 不可用时只运行静态检查：

```bash
python scripts/check_docker_config.py --static-only
```

Docker 可用时再验证 Compose CPU/GPU 规范化输出：

```bash
python scripts/check_docker_config.py
```

静态通过不代表镜像构建或容器运行成功。

### `scripts/check_docs.py`

检查活跃文档的本地链接、`npm run` 命令、Web 产品定位和第一阶段安全契约。它会拒绝服务器路径模式、全局匿名进度、HF token 明文读取和无界执行器等旧描述，并要求文档保留以下事实：

- 浏览器仅上传内容，推理请求 `task_id` 必填且按任务订阅进度；
- HF token GET 脱敏、非空替换、`clear_hf_token` 显式清除；
- WebSocket Origin 的未设置/空值/逗号白名单三态语义；
- 单进程 1 执行 + 默认 4 等待、满载 `429` 和 `/api/ready` 快照；
- LAN 无用户认证，第一阶段不等价于公网安全。

```bash
python scripts/check_docs.py
```

### `scripts/smoke_web.py`

对已经运行的 Web 服务检查：

- `/` 返回 HiImage HTML shell；
- `/api/health` 返回 `status=ok`；
- 随机未知路由回退到 HTML shell；
- `/api/ws/progress` 完成 WebSocket 101 握手。

真实运行：

```bash
python scripts/smoke_web.py --url http://127.0.0.1:8080
```

只校验 URL 和探测配置：

```bash
python scripts/smoke_web.py --check
```

`--check` 不访问网络，不能宣称 live smoke 通过。只有明确不需要检查代理 WebSocket 时才使用 `--skip-websocket`。

## 5. 模型与依赖工具

### `scripts/check_models.py`

检查模型完整性：

```bash
python scripts/check_models.py
python scripts/check_models.py --mode watermark_removal
python scripts/check_models.py --model birefnet
python scripts/check_models.py --json
```

### `scripts/check_hf_repo_size.py`

查询 Hugging Face 仓库文件与建议大小：

```bash
python scripts/check_hf_repo_size.py Sanster/PowerPaint_v2
```

### `scripts/install_deps.py`

安装后端依赖并处理 IOPaint 与 FLUX 依赖组合。通常由 setup 调用，不应替代正常的 `npm run setup`。

### `scripts/post_install.py`

对已安装依赖应用兼容性补丁。Dockerfile 和 setup 都会执行。

### `scripts/generate_icons.py`

生成品牌图片资源，需要 Pillow；它不参与 Web 构建或部署。

## 6. Docker 命令

CPU 默认栈：

```bash
docker compose up --build
```

GPU 栈：

```bash
docker compose --profile gpu up --build backend-gpu web-gpu
```

Web 默认绑定 `127.0.0.1:8080`。调整 `WEB_HOST` 仅用于可信局域网；公网需要认证、TLS 与限流。
