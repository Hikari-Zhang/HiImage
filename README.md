# HiImage

HiImage 是面向浏览器的 AI 图像处理 Web 应用，提供去水印、超分辨率、抠图、智能合成、模型管理和实时日志。React/Vite 前端通过 HTTP、WebSocket 与 SSE 访问 FastAPI；生产环境由 Nginx 提供静态资源并把 `/api/` 同源代理到后端。

## 运行边界

- 当前部署目标是可信局域网内的单用户实例，不含账号和租户隔离。
- 本地开发与 Docker 默认只监听 `127.0.0.1`。
- 暴露到公网前必须另行配置身份认证、TLS、请求限流、上传策略和审计；不要直接映射现有服务。
- 浏览器批量队列最多 100 张图片，单文件最多 20 MB、输入总量最多 128 MB；前后端按 `宽 × 高 × 倍率² × 4` 限制预计输出 RGBA 为 256 MB，浏览器累计结果也限制为 256 MB。ZIP 另设 64 MB 上限，每个成功结果均可逐张下载。后端单请求解码后限制为 30 MB。图片解码、Base64、预览、结果与 ZIP 会产生额外内存，不提供流式 ZIP。

## 环境要求

- Node.js 22（容器构建使用 `node:22-alpine`）
- Python 3.10+，推荐 3.11/3.12
- npm
- 可选：Docker + Docker Compose v2
- 可选：NVIDIA 驱动与 NVIDIA Container Toolkit（GPU 容器）

MPS 仅适用于直接在 Apple Silicon macOS 上本地运行后端，并只在该平台默认启用；Intel Mac 默认使用 CPU，Linux 容器不提供 MPS。

## 本地开发

```bash
npm run setup
npm run dev
```

`setup` 安装根目录、前端和 Python 环境依赖；`dev` 启动 FastAPI（默认 `127.0.0.1:8787`）与 Vite（默认 `127.0.0.1:5173`）。浏览器开发请求通过 Vite 代理访问后端。

常用命令：

```bash
npm run dev:check       # 只检查环境和启动命令，不启动服务
npm run backend:dev     # 只启动后端
npm run frontend:dev    # 只启动 Vite
npm run typecheck
npm test
```

## 构建与预览

```bash
npm run build           # 生成 frontend/dist
npm run preview         # 构建后启动本地后端与 Vite preview
```

`npm start` 与 `npm run preview` 使用同一预览模式。生产部署应使用 Nginx 容器，不把 Vite preview 当作生产服务器。

## Docker

CPU 是默认栈：

```bash
docker compose up --build
```

访问 <http://127.0.0.1:8080>。GPU 栈必须明确选择 profile 和服务，避免同时启动 CPU 栈：

```bash
docker compose --profile gpu up --build backend-gpu web-gpu
```

更改本地绑定可设置：

```bash
WEB_HOST=192.168.1.10 WEB_PORT=8080 docker compose up --build
```

Windows PowerShell：

```powershell
$env:WEB_HOST="192.168.1.10"
$env:WEB_PORT="8080"
docker compose up --build
```

仅应绑定可信局域网地址。后端不发布主机端口，只通过 Nginx 同源代理访问。

### 卷与环境变量

CPU 与 GPU 栈使用彼此独立的命名卷，分别持久化：

- `/app/config`：设置
- `/app/cache`：Hugging Face、PyTorch 等缓存
- `/app/tmp`：临时文件
- `/app/output`：处理结果
- `/app/models`：模型权重

后端容器设置 `HIIMAGE_PROJECT_ROOT`、`HIIMAGE_CONFIG_DIR`、`HIIMAGE_CACHE`、`HIIMAGE_TMP_DIR`、`HIIMAGE_OUTPUT_DIR`、`HIIMAGE_MODELS_DIR`。`HIIMAGE_CORS_ORIGINS` 默认为空，表示生产同源部署不开放跨域。

## 验证

```bash
npm test
npm run typecheck
npm run build
python scripts/check_docker_config.py --static-only
python scripts/check_docs.py
python scripts/smoke_web.py --check
```

Docker 可用时执行规范化 Compose 验证：

```bash
python scripts/check_docker_config.py
```

对已运行的 Web 服务执行真实 smoke（检查首页、健康接口、SPA 回退和 WebSocket 代理）：

```bash
python scripts/smoke_web.py --url http://127.0.0.1:8080
```

`--check` 只校验 smoke 参数与目标，不接触服务，不能代表运行态通过。Docker 不可用时只能报告静态检查结果。

## 架构与文档

- [系统架构](docs/SYSTEM_ARCHITECTURE.md)
- [系统设计](docs/system-design.md)
- [产品需求](docs/PRD.md)
- [脚本参考](docs/SCRIPTS_REFERENCE.md)

## 许可证

MIT
