# 降噪专用模型（SCUNet / NAFNet / SwinIR）设计规格

> 日期：2026-08-19  
> 状态：待实现  
> 范围：在已有降噪页上增加专用复原模型，按下拉分组标注适合的噪声类型。  
> 前置：`docs/superpowers/specs/2026-08-17-image-denoise-design.md`（页面、批量、工作区已落地）

## 1. 背景与结论

降噪页目前只有 `denoise-general` / `denoise-anime`：Real-ESRGAN `outscale=1`，先 4x 再缩回。适合当「快速清晰化」，不是盲去噪。用户选择补齐常用专用模型，并标明各自适合的类型。

结论：

- 默认改为 **SCUNet real-psnr**。
- 新增 SCUNet-gan、NAFNet-SIDD width32、SwinIR color JPEG40。
- 现有两条 ESRGAN 保留，分组改为「快速清晰化」。
- 新网络用 **spandrel** 加载官方 `.pth`，不手写架构，不修复 Restormer。

## 2. 目标

用户在降噪页能按噪声类型选模型；默认是真去噪；清晰化仍可用且不必再下载。

成功标准：

- 下拉按类型分组，每条能看出适合什么噪声。
- 默认 `denoise-scunet-psnr`；权重未下载时走现有下载队列，下完才能跑。
- `provider: restoration` 与 `provider: realesrgan` 都能走 `/api/denoise` 与 `/batch`。
- 批量循环外只加载一次模型；单张失败不中断。
- 超分页清晰化条目与 tags 不变。

非目标：

- 修复或启用 `restormer_model.py` / `image_restoration` 模式。
- NAFNet width64、SwinIR jpeg10/20/30、降噪强度滑块。
- 抽出超分/降噪共用 Enhance 页。
- 修改超分 API 语义。
- 为 `download_direct` 增加 HF 镜像改写（NAFNet 用 HF resolve 直链即可）。

## 3. 架构

```
Denoise.tsx
  → GET  /api/models/denoise
  → POST /api/denoise | /api/denoise/batch
      → _require_denoise_model（tags 含 denoise；不再要求 realesrgan）
      → ModelExecutorFactory
           provider=restoration → RestorationExecutor（spandrel + tile）
           provider=realesrgan  → Upscaler（outscale 由 YAML，降噪为 1）
```

`RestorationExecutor` 实现 `BaseModelExecutor`：RGB uint8 进、RGB uint8 出。加载：`spandrel.ModelLoader().load_from_file(weight_path)`。进程启动后、首次加载前调用一次 `spandrel_extra_arches.install()`（NAFNet 在 extra 包）。不实现 SCUNet / NAFNet / SwinIR 网络代码。

## 4. 模型目录

在 `models.yaml` 里把新四条插在现有 `denoise-general` **之前**（同一 denoise 注释块内），保证 `list_denoise_models` 的分组顺序为：照片 / 截图 → 观感更干净 → 相机 ISO → 去压缩伪影 → 快速清晰化。`mode_groups.denoise.default_model` 改为 `denoise-scunet-psnr`。

`denoise-general` / `denoise-anime`：改 `display_group` 为 `快速清晰化`，改描述标明「超分权重复用，偏锐化，不是专用去噪」。`tags` 仍为 `[denoise]`，`provider` 仍为 `realesrgan`。

### 4.1 新增条目

| id | name | provider | display_group | suitable_for | badge | size_mb | weight_filename |
|----|------|----------|---------------|--------------|-------|---------|-----------------|
| denoise-scunet-psnr | SCUNet 盲去噪（推荐） | restoration | 照片 / 截图 | 盲去噪（压缩颗粒 + 一般照片噪声，保纹理） | 推荐 | 72 | scunet_color_real_psnr.pth |
| denoise-scunet-gan | SCUNet 观感去噪 | restoration | 观感更干净 | 更干净，可能略抹纹理 | | 72 | scunet_color_real_gan.pth |
| denoise-nafnet-sidd | NAFNet 相机降噪 | restoration | 相机 ISO | 手机 / 相机传感器噪声 | | 117 | NAFNet-SIDD-width32.pth |
| denoise-swinir-jpeg | SwinIR 去压缩 | restoration | 去压缩伪影 | JPEG 色块 / 振铃（按常见网图质量 Q≈40） | | 98 | 006_colorCAR_DFWB_s126w7_SwinIR-M_jpeg40.pth |

公共字段（四条相同，除非上表另列）：

- `tags: [denoise]`
- `requires_reference: false`
- `supports_custom_outscale: false`
- `features: [denoise]`
- `local_path: models/restoration/<weight_filename>`（供 checker；实际落盘见 §6）

下载地址（写死在 YAML `download_url`）：

- SCUNet psnr：`https://github.com/cszn/KAIR/releases/download/v1.0/scunet_color_real_psnr.pth`
- SCUNet gan：`https://github.com/cszn/KAIR/releases/download/v1.0/scunet_color_real_gan.pth`
- NAFNet：`https://huggingface.co/nyanko7/nafnet-models/resolve/main/NAFNet-SIDD-width32.pth`（官方权重镜像，117 MB，SHA256 `89c70e80…26f4`）
- SwinIR：`https://github.com/JingyunLiang/SwinIR/releases/download/v0.0/006_colorCAR_DFWB_s126w7_SwinIR-M_jpeg40.pth`

SwinIR 官方按 Q10/20/30/40 分权重。产品只挂 **jpeg40** 一条，避免四个同名选项。

`suitable_for` 是新字段：给 UI 的短类型说明，不参与路由分发。

## 5. 后端分发

### 5.1 Provider 与路径

- `core/constants.py`：`Provider.RESTORATION = "restoration"`。不启用已有空壳 `Provider.NAFNET` 当路由 provider。
- `resolve_model_cache_path`：`provider == "restoration"` → `MODELS_CACHE_DIR / "restoration" / weight_filename`（即 `~/.cache/hiimage/models/restoration/`）。
- 下载：已有 `local_path` + `download_url` → `download_direct`，无需新下载器。
- `model_checker`：已有 `local_path` / cache path 分支，restoration 走同一套。

### 5.2 Factory

`ModelExecutorFactory.create_executor`：

- `provider == "restoration"` → `RestorationExecutor`
- `provider == "realesrgan"` 保持现有 `RealESRGANExecutor`；降噪路由对清晰化两条仍直接用 `Upscaler`（与现逻辑一致，见 §5.4）

`get_supported_providers()` 增加 `"restoration"`。保留列表中的 `"nafnet"` / `"restormer"` 字面量也可以，但本规格不实现它们。

### 5.3 RestorationExecutor

文件：`backend/core/restoration_executor.py`。

```
load_model():
  路径 = resolve_model_cache_path(cfg)
  不存在 → FileNotFoundError（文案：请先在模型管理中下载）
  确保 extra arches 已 install
  self._model = ModelLoader().load_from_file(path)
  将模块 .to(device).eval()

execute(image, **kwargs):
  tile = kwargs.get("tile", 0)
  自动 tile（tile==0）对齐 Upscaler._auto_tile 的设备策略（MPS 偏保守）
  RGB uint8 → 按 tile 推理 → RGB uint8
  OOM：tile > 256 则减到 256 重试一次；否则 RuntimeError 显存不足

unload_model():
  释放 self._model；cuda 时 empty_cache
```

输入输出尺寸应相同。路由层仍调用已有 `_match_input_size` 兜底。

依赖（写入 `backend/requirements.txt`）：

- `spandrel>=0.4.0`（含 SCUNet、SwinIR）
- `spandrel-extra-arches>=0.2.0`（NAFNet）

spandrel 要求 Python ≥ 3.10。与 README 推荐 3.11 一致；3.9 环境装不上属环境问题，本规格不回退手写网络。

### 5.4 降噪路由

`_require_denoise_model`：只要求 `denoise` in tags，且 `provider` 属于 `{restoration, realesrgan}`。不再要求必须 realesrgan。

`DenoiseRequest.model` / `BatchDenoiseRequest.model` 默认值改为 `denoise-scunet-psnr`。

单张 `_run_denoise` 与批量共享执行器创建：

```
cfg = get_model(model_id)
if cfg.provider == "realesrgan":
    用 Upscaler(model_name, device, tile)（与现逻辑相同，不传 outscale）
elif cfg.provider == "restoration":
    RestorationExecutor(cfg, device)，execute(..., tile=tile)
```

批量：循环外创建并 `load_model()` / `_ensure_model_loaded()`；循环内只 `execute`/`upscale`；结束 `unload`。线程池仍 `max_workers=1`。

`list_denoise_models` 每条增加 `suitable_for`（缺省空串）。

## 6. 前端

`useSettingsStore.denoiseModel` 与 `loadDefaults` 默认改为 `defaults.denoise ?? 'denoise-scunet-psnr'`。

`DEFAULT_DENOISE_GROUPS` 与 `GET /api/models/denoise` 对齐为五组六条。`ModelOption` 增加可选 `suitableFor?: string`。`ModelSelect` 选项主行是名称，其下第二行用 10px 次要色显示 `suitableFor`（无则显示 `description` 单行截断）。分组标题用 API 的 `display_group`。

下载状态按模型 ID 区分；SCUNet 两条文件不同，各下一份。清晰化两条仍与超分权重复用同一 `weight_filename` 落盘路径（现有 realesrgan 目录）。

## 7. 错误处理

| 场景 | 行为 |
|------|------|
| 未知 ID / 无 denoise tag / provider 非法 | HTTP 400，detail 列出 `get_models_for_mode("denoise")` 的 ID |
| 权重缺失 | 与超分相同：未下载不得开始；选中可入队下载 |
| 单张推理失败 | WebSocket `send_error` + 页面 toast |
| 批量单张失败 | 该条 `status: error`，继续下一张 |
| OOM | RestorationExecutor / Upscaler 各按现约定减小 tile 重试 |
| 输出尺寸偏差 | `_match_input_size` |
| 并发 | `useProcessStore` 全局只一个推理任务 |

## 8. 测试

不加载真实权重、不跑推理。

1. `tests/test_denoise_registry.py`（扩展）
   - `get_models_for_mode("denoise")` ID 顺序：`denoise-scunet-psnr`、`denoise-scunet-gan`、`denoise-nafnet-sidd`、`denoise-swinir-jpeg`、`denoise-general`、`denoise-anime`
   - `get_default_model("denoise") == "denoise-scunet-psnr"`
   - 前四条 `provider == "restoration"`；后两条 `realesrgan` 且 `outscale == 1`
   - 各条 `suitable_for` 非空；`display_group` 符合 §4
2. `tests/test_denoise_router_helpers.py`
   - `_require_denoise_model` 接受 `denoise-scunet-psnr` 与 `denoise-general`
   - 拒绝 `RealESRGAN_x4plus`（无 denoise tag）
   - `list_denoise_models` 分组标签顺序与 §4 一致，模型含 `suitable_for`、不含 `outscale`
3. `tests/test_restoration_executor.py`
   - mock `get_model` 为 restoration 配置时，Factory 返回 `RestorationExecutor`
   - `provider` 不是 restoration/realesrgan 的 denoise 路由仍 400（若测路由）
   - 不调用 `load_model()`

## 9. 文档同步（随实现改）

- `docs/PRD.md`：降噪支持模型表换成 §4 六条，默认 SCUNet。
- `docs/SYSTEM_ARCHITECTURE.md`：推理层补 RestorationExecutor / spandrel。
- `README.md`：降噪使用指南写明按类型选模型；默认需下载 SCUNet。
- 2026-08-17 降噪规格保持历史，不改写其「不引入 NAFNet」原文；本规格覆盖其后的模型集。

## 10. 文件清单

新建：

- `backend/core/restoration_executor.py`
- `backend/tests/test_restoration_executor.py`

修改：

- `backend/core/models.yaml`
- `backend/core/constants.py`
- `backend/core/paths.py`
- `backend/core/model_executor.py`
- `backend/app/routers/denoise.py`
- `backend/requirements.txt`（及 locked 文件若仓库惯例要同步）
- `backend/tests/test_denoise_registry.py`
- `backend/tests/test_denoise_router_helpers.py`
- `frontend/src/renderer/stores/useSettingsStore.ts`
- `frontend/src/renderer/stores/useModelStore.ts`
- `frontend/src/renderer/components/ModelSelect/ModelSelect.tsx`
- `docs/PRD.md` / `docs/SYSTEM_ARCHITECTURE.md` / `README.md`
