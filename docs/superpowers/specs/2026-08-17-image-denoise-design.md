# 图片降噪（单张 + 批量）设计规格

> 日期：2026-08-17  
> 状态：待实现  
> 范围：独立「图片降噪」功能页；复用已有 Real-ESRGAN 清晰化权重；不修复 Restormer。

## 1. 背景与结论

PRD Phase 3 将「图片降噪」列为远期功能。当前配置里：

- `clarity-general` / `clarity-anime` 已启用：`outscale: 1`，同分辨率去噪+锐化，权重复用 `realesr-general-x4v3` 与 `RealESRGAN_x4plus_anime_6B`。它们挂在超分页，不是独立降噪入口。
- `restormer_denoise` 与 `image_restoration` 模式因架构通道不匹配（skip 用相加，官方权重是 concat + 1×1 降维）整段注释，本规格不修复。
- NAFNet 仅出现在 `model_executor.get_supported_providers()` 与常量，无执行器、无 YAML，本规格不引入。
- 超分已有单张 + 批量（虚拟滚动、懒加载、逐张处理、输出目录）。降噪批量复用同一套前端组件与处理节奏。

因此：**不新增权重文件即可交付独立降噪页**；推理走现有 `Upscaler`，输出倍率锁为 1。

## 2. 目标

用户从侧边栏进入「图片降噪」，对单张或一批图片做**保持分辨率**的去噪/去颗粒，结果可写入共享工作区，供后续超分或合成使用。

成功标准：

- 侧边栏有独立入口；单张与批量交互对齐超分页（无放大倍率选择）。
- 默认模型 `denoise-general` 在权重已缓存时无需额外下载（与 `realesr-general-x4v3` / `clarity-general` 同一文件）。
- 批量 10+ 张时模型只加载一次；单张失败不中断整批。
- 超分页的「清晰化增强」条目保持不变。

非目标（明确不做）：

- Restormer / NAFNet / 相机 ISO 专业去噪。
- 降噪强度滑块（`realesr-general-wdn` 插值）。
- 抽出超分/降噪共用的 Enhance 页面。
- 修改超分 API 语义。

## 3. 架构

```
侧边栏 /denoise
    → Denoise.tsx（单张 | 批量）
        → GET  /api/models/denoise
        → POST /api/denoise
        → POST /api/denoise/batch
            → Upscaler(model_name=denoise-*, outscale=YAML 默认 1)
                → Real-ESRGAN 权重（已有文件）
```

配置驱动：`models.yaml` 增加 `denoise` 模式与两条模型；`tags: [denoise]` 使 `get_models_for_mode("denoise")` 自动关联。推理层不新写网络，只让 `Upscaler` 能按任意 `provider: realesrgan` 的模型 ID 解析，而不是只认 `upscale` 模式下的 ID。

## 4. 模型配置

在 `backend/core/models.yaml` 的超分清晰化条目之后、Restormer 注释块之前，新增：

### 4.1 模型条目

| 字段 | `denoise-general` | `denoise-anime` |
|------|-------------------|-----------------|
| name | 通用降噪（推荐） | 动漫/插画降噪 |
| provider | realesrgan | realesrgan |
| tags | `[denoise]` | `[denoise]` |
| badge | 推荐 | 动漫 |
| scale | 4（网络结构要求） | 4 |
| outscale | 1 | 1 |
| supports_custom_outscale | false | false |
| weight_filename | `realesr-general-x4v3.pth` | `RealESRGAN_x4plus_anime_6B.pth` |
| local_path | `models/realesrgan/realesr-general-x4v3.pth` | `models/realesrgan/RealESRGAN_x4plus_anime_6B.pth` |
| arch | SRVGGNetCompact | RRDBNet |
| num_conv / num_block | num_conv: 32 | num_block: 6 |
| display_group | 通用降噪 | 动漫/插画 |
| size_mb | 5 | 18 |
| download_url | 与现有 `realesr-general-x4v3` 相同 | 与现有 `RealESRGAN_x4plus_anime_6B` 相同 |
| features | `[denoise]` | `[denoise]` |

`clarity-general` / `clarity-anime` **不改 tags**，继续只出现在超分页。

描述文案必须写清「保持原始分辨率」，避免用户以为会放大。

### 4.2 模式分组

```yaml
- id: denoise
  name: 图片降噪
  icon: droplet
  icon_name: Droplets
  description: 保持分辨率，去除压缩噪声与颗粒，支持单张与批量
  default_model: denoise-general
  needs_reference: false
  needs_roi: false
  needs_prompt: false
```

`GET /api/models/defaults` 已遍历 `MODE_GROUPS`，新增模式后会自动返回 `"denoise": "denoise-general"`，无需改该接口逻辑。

## 5. Upscaler 查找

现状：`backend/core/upscaler.py` 在模块加载时用 `get_models_for_mode("upscale")` 构建 `_MODEL_SCALE` 等字典；`Upscaler.__init__` 校验 `model_name not in AVAILABLE_UPSCALE_MODELS`。因此 `denoise-general` 会直接 `ValueError`。

改为：

- 超分页模型列表仍用 `get_models_for_mode("upscale")`（`GET /api/models/upscale` 不变）。
- `Upscaler` 构造时通过 `get_model(model_name)` 取配置；若 `provider != "realesrgan"` 则抛 `ValueError`。
- `scale` / `outscale` / `weight_filename` / `arch` / `num_block` / `num_conv` 全部来自该条 YAML，不再依赖模块级 upscale-only 字典。
- `outscale` 参数：调用方传入则覆盖；否则用 YAML `outscale`，再否则用 `scale`。降噪路由不传 `outscale`，因此恒为 1。
- `_get_weight_path` / `_download_weight` 继续按 `provider: realesrgan` + `weight_filename` 解析；`denoise-general` 与 `clarity-general` 落到同一缓存文件，model_checker 与下载队列可共用。

模块级 `UPSCALE_MODEL_LIST` / `AVAILABLE_UPSCALE_MODELS` 仍可留给超分页或兼容代码，但 `Upscaler` 本身不再用它们做准入校验。

## 6. 后端 API

新建 `backend/app/routers/denoise.py`，在 `backend/app/main.py` 注册：`app.include_router(denoise.router, prefix="/api", tags=["denoise"])`。

线程池与超分相同：`ThreadPoolExecutor(max_workers=1)`，与现有推理串行，避免抢显存。进度走现有 `progress_manager` WebSocket。

### 6.1 `GET /api/models/denoise`

从 `get_models_for_mode("denoise")` 按 `display_group` 分组。每条返回：`id`, `name`, `description`, `badge`。不返回倍率字段（前端无倍率 UI）。

### 6.2 `POST /api/denoise`

请求体：

| 字段 | 类型 | 说明 |
|------|------|------|
| image | str | Base64（与超分单张相同，工作区图转 data URL 后上传） |
| model | str | 默认 `denoise-general` |
| device | str | 默认 `mps` |
| tile | int \| null | `0` = 自动；`null` = 按 0 处理 |
| output_path | str \| null | 非空则写文件，不返回 base64 |

处理：`decode_image` → `Upscaler(model_name=req.model, device=req.device, tile=tile)`（不传 outscale）→ `upscale()`。进度文案为「正在加载降噪模型…」「正在降噪…」。成功后 `send_complete`。

响应：与超分单张对齐 — `{ image, width, height }` 或 `{ outputPath, width, height }`。`width`/`height` 必须等于输入尺寸。

### 6.3 `POST /api/denoise/batch`

请求体：

| 字段 | 类型 | 说明 |
|------|------|------|
| images | list[str] | Base64 或文件路径 |
| model | str | 默认 `denoise-general` |
| device | str | |
| tile | int \| null | |
| output_dir | str \| null | 非空则写目录 |
| is_file_path | bool | true 时 `images` 为路径 |

循环外创建并预加载一个 `Upscaler`。逐张：读图失败记 error 后 continue；处理失败记 error 后 continue。输出文件名：`{原主文件名}_denoised.png`（扩展名统一 png）。结束后卸载模型；CUDA 时 `empty_cache`。

响应：`{ results: [...], summary: { total, success, failed } }`，单条字段与 `/api/upscale/batch` 相同（`index`, `status`, `image` 或 `outputPath`, `width`, `height`, `fileName`, `error`）。

### 6.4 校验

未知 `model`：`get_model` 抛 `KeyError`，路由转为 400，正文说明可选 ID 来自 `get_models_for_mode("denoise")`。非 `realesrgan` provider 不允许走本路由。

## 7. 前端

### 7.1 路由与导航

- `App.tsx` 增加 `path="denoise"`。
- `MainLayout.tsx` 常驻挂载 `<Denoise />`，path `/denoise`。
- `Sidebar.tsx` 在「超分辨率」之上插入：`{ path: '/denoise', label: '图片降噪', icon: Droplets }`。

### 7.2 页面 `frontend/src/renderer/pages/Denoise.tsx`

以 `SuperResolution.tsx` 为模板，删除所有 `outscale` 状态、倍率 Select、以及 header 里的 `Nx` 输出文案。保留：

- 单张 / 批量切换。
- 拖拽、文件对话框、文件夹导入（批量）。
- `BatchImageQueue`、`BatchProgress`、`ImageCompare`、`WorkspaceInputBar`、`ModelSelect`。
- tile 控件：复用设置里的 `upscaleTile`（同一套 Real-ESRGAN 分块逻辑，不新增独立 tile 配置）。
- 模型未下载时走现有 `useDownloadManager` / `DownloadStatus`，model id 为 `denoise-general` / `denoise-anime`（下载 URL 与已有权重相同，队列按 id 区分任务但落盘路径相同，可接受）。

单张处理使用共享工作区：输入为 `selectWorkingImage`（与超分相同，可对去水印结果再降噪）。成功后 `setHandoffResult`，对比滑块显示处理前/后。

批量：Electron 选输出目录；结果 `{name}_denoised.png`。完成后 toast：`批量降噪完成: N 成功[, M 失败]`。

### 7.3 Store

`useSettingsStore`：增加 `denoiseModel: string`，默认 `'denoise-general'`；`setDenoiseModel`。`loadDefaults` 增加 `denoiseModel: defaults.denoise ?? 'denoise-general'`。不把 denoiseModel 写入 `/api/settings`（与 `upscaleModel` 一致，仅会话内记住）。

`useModelStore`：增加 `denoiseGroups` 与 fallback `DEFAULT_DENOISE_GROUPS`（两条模型）。`loadModels` 并行请求 `GET /api/models/denoise`。降噪页不需要 outscale meta。

`useBackendAPI`：增加 `denoise` 与 `denoiseBatch`，路径分别为 `/api/denoise`、`/api/denoise/batch`，类型与超分对应函数一致，但不含 `outscale`。

### 7.4 工作区

降噪加入去水印 / 超分 / 合成同一套 `useWorkspaceStore`。抠图仍独立。`SYSTEM_ARCHITECTURE.md` 页面表增加 `/denoise` 一行。

## 8. 错误处理

| 场景 | 行为 |
|------|------|
| 模型未下载 | 与超分相同：按钮旁下载进度；未完成不得开始处理 |
| 单张推理失败 | WebSocket `send_error`；页面 `setError`；toast 失败 |
| 批量单张失败 | 该条 `status: error`，继续下一张；汇总 toast 含失败数 |
| OOM | 沿用 `Upscaler` 已有减小 tile 重试 |
| 并发 | 全局 `useProcessStore` 同时只允许一个推理任务 |
| 读文件失败 | 批量该条 error，不中断 |
| 输出宽高变化 | 视为实现错误；单张接口文档要求宽高等于输入。Real-ESRGAN `outscale=1` 应保持尺寸；若个别尺寸因 padding 差 1～2px，在路由里 `cv2.resize` 回输入尺寸后再返回 |

## 9. 测试

仓库几乎没有功能测试（仅 `backend/tests/test_progress_parsing.py`）。本功能最低测试：

1. `backend/tests/test_denoise_registry.py`
   - `get_models_for_mode("denoise")` 恰好包含 `denoise-general`、`denoise-anime`。
   - `get_default_model("denoise") == "denoise-general"`。
   - 两条模型 `provider == "realesrgan"`、`outscale == 1`、`supports_custom_outscale is False`。
   - `denoise-general` 的 `weight_filename` 与 `realesr-general-x4v3` 相同。
2. `backend/tests/test_upscaler_lookup.py`
   - mock `get_model` 返回 realesrgan 配置时，`Upscaler(model_name="denoise-general")` 的 `outscale == 1`、`scale == 4`。
   - `provider` 不是 realesrgan 时抛 `ValueError`。
   - 不实际加载权重、不跑推理。

手动验收：

- 超分页模型列表仍含「清晰化增强」，不含 `denoise-*`。
- 降噪页只有两条模型，无倍率选择。
- 单张：输出尺寸等于输入；对比滑块可用；结果可交给超分。
- 批量：选目录，生成 `*_denoised.png`；人为弄坏一张路径，其余仍成功。
- 权重已存在时，降噪通用模型显示为已下载。

## 10. 文档同步（随实现改，不单独开需求）

- `docs/PRD.md`：把「图片降噪」从 Phase 3 移到已规划/本版本功能，补单张+批量功能点。
- `docs/SYSTEM_ARCHITECTURE.md`：页面表与工作区说明加入降噪。
- `README.md`：功能列表增加图片降噪。

Restormer 相关注释与禁用代码保持原样。

## 11. 文件清单

新建：

- `backend/app/routers/denoise.py`
- `frontend/src/renderer/pages/Denoise.tsx`
- `backend/tests/test_denoise_registry.py`
- `backend/tests/test_upscaler_lookup.py`

修改：

- `backend/core/models.yaml`
- `backend/core/upscaler.py`
- `backend/app/main.py`
- `frontend/src/renderer/App.tsx`
- `frontend/src/renderer/components/layout/MainLayout.tsx`
- `frontend/src/renderer/components/layout/Sidebar.tsx`
- `frontend/src/renderer/stores/useSettingsStore.ts`
- `frontend/src/renderer/stores/useModelStore.ts`
- `frontend/src/renderer/hooks/useBackendAPI.ts`
- `docs/PRD.md`
- `docs/SYSTEM_ARCHITECTURE.md`
- `README.md`
