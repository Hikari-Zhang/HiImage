# 代码审查修复清单

> 来源：2026-05-28 代码审查，重点覆盖模型管理与前端显示逻辑。
> 状态标记：⬜ 待修复 · ✅ 已修复 · 🚫 已搁置（附原因）

---

## P0 — 立刻修复（功能性 Bug）

### ✅ Fix-01 · Lambda 闭包捕获 Bug

**文件**：`backend/app/routers/models.py`

**问题**：`for` 循环中所有 `lambda` 共享同一个变量引用，循环结束后 `model_cfg` 指向最后一个模型，导致一键下载时所有并发任务实际下载的是同一个模型。

**问题代码**：
```python
for idx, model_cfg in enumerate(downloadable):
    future = loop.run_in_executor(None, lambda: download_hf(model_cfg, ...))
    # 所有 lambda 捕获的是同一个 model_cfg 变量引用
```

**修复方案**：用默认参数在每次迭代时固定捕获值：
```python
for idx, model_cfg in enumerate(downloadable):
    future = loop.run_in_executor(
        None,
        lambda cfg=model_cfg: download_hf(cfg, ...)
    )
```

---

### ✅ Fix-02 · CSS `group` 类缺失导致下载按钮永久隐藏

**文件**：`frontend/src/renderer/components/ModelSelect/ModelSelect.tsx`

**问题**：模型缺失时，下载图标按钮使用 `group-hover:opacity-100` 控制显隐，但父元素没有 `group` 类，导致 hover 事件无法传递，按钮永远不可见，用户无法从下拉框触发下载。

**问题代码**：
```tsx
// 父元素缺少 group 类
<div onClick={...} className="flex items-center gap-2 px-3 py-1.5 ...">
  <button className="... opacity-0 group-hover:opacity-100 ...">
    <Download />
  </button>
</div>
```

**修复方案**：在父元素 `className` 中加入 `group`：
```tsx
<div className="group flex items-center gap-2 px-3 py-1.5 ...">
```

---

### ✅ Fix-03 · `cancel()` 提前调用导致 `AttributeError`

**文件**：`backend/core/download_queue.py`（约 line 190）

**问题**：`_slot_event` 在第一次 `submit()` 触发 `_scheduler_loop` 后才初始化。若在任何任务入队前就调用 `cancel()`，`self._slot_event.set()` 会抛出 `AttributeError`。

**修复方案**：在 `__init__` 中预建 event，或在使用前判空：
```python
# 方案 A：__init__ 中预初始化（推荐）
def __init__(self, ...):
    ...
    self._slot_event: asyncio.Event = asyncio.Event()

# 方案 B：调用前判空
async def cancel(self, model_id: str):
    ...
    if self._slot_event is not None:
        self._slot_event.set()
```

---

## P1 — 近期改进（稳定性 / 可维护性）

### ✅ Fix-04 · 前端硬编码模型列表与后端 `models.yaml` 双重维护

**文件**：`frontend/src/renderer/stores/useModelStore.ts`（约 line 10–99）

**问题**：`DEFAULT_INPAINT_GROUPS` / `DEFAULT_UPSCALE_GROUPS` / `DEFAULT_CUTOUT_GROUPS` 共约 80 行硬编码数据，与 `backend/core/models.yaml` 完全重复。每次在 YAML 中新增模型，必须同步手动更新此 TS 文件，极易遗漏且没有类型检查保障。

**修复方案**：
1. 将 fallback 精简到最核心的 1–2 个模型（如只保留 `wm_lama`）
2. 其余数据完全依赖后端动态返回
3. 如果后端不可用，显示提示信息而非过期的硬编码列表

```typescript
// 精简后的 fallback（仅用于后端完全不可达时）
const MINIMAL_FALLBACK: ModelGroup[] = [
  {
    label: '快速修复',
    options: [
      { value: 'wm_lama', label: 'LaMa（推荐）', description: '后端离线，仅显示默认模型' },
    ],
  },
]
```

---

### ✅ Fix-05 · `_syncDone` 标志阻止后端重启后状态刷新

**文件**：`frontend/src/renderer/hooks/useDownloadManager.ts`（约 line 27–29，363–368）

**问题**：`_syncDone` 是模块级布尔值，进程内只会从 `false` 变为 `true`，永不重置。后端重启、模型被手动删除后，前端模型状态始终停留在首次启动时的快照，已删除的模型仍显示为"已下载"。

**修复方案**：将 `_syncDone` 改为组件内 ref，并监听 Electron 后端就绪事件重新 sync：

```typescript
// useDownloadManager.ts 内部
const syncDoneRef = useRef(false)

useEffect(() => {
    if (!syncDoneRef.current) {
        syncDoneRef.current = true
        syncModelStatus()
    }
    resume()

    // 监听后端重启事件，重新同步状态
    const unsub = window.electronAPI?.onBackendReady?.(() => {
        syncDoneRef.current = false
        syncModelStatus()
    })
    return () => unsub?.()
}, [])
```

---

### ✅ Fix-06 · `registerDownloadDoneCallback` 全局可变状态反模式

**文件**：`frontend/src/renderer/hooks/useDownloadManager.ts`（约 line 29，32–34）

**问题**：模块级的 `_onDownloadDone` 变量只能持有一个回调，多个组件注册时后者静默覆盖前者。HMR 热更新时变量不会重置，容易产生过期闭包。

**修复方案**：改为使用 Zustand 的 `subscribe` 在需要响应的组件内直接订阅状态变化：

```typescript
// ModelManager.tsx 内部（替代 registerDownloadDoneCallback）
useEffect(() => {
    return useDownloadStore.subscribe(
        (state) => state.tasks,
        (tasks, prev) => {
            const hasNewDone = Object.entries(tasks).some(
                ([id, t]) => t.status === 'ok' && prev[id]?.status === 'done'
            )
            if (hasNewDone) loadModelsRef.current()
        }
    )
}, [])
```

同时删除 `useDownloadManager.ts` 中的 `registerDownloadDoneCallback` 导出函数及 `_onDownloadDone` 变量。

---

## P2 — 架构优化

### ✅ Fix-07 · 统一三个模型列表接口

**文件**：`frontend/src/renderer/stores/useModelStore.ts`（约 line 137–141） + 对应后端路由

**问题**：前端同时调用三个路径风格不一致的接口：
- `/api/models/inpaint`
- `/api/models/upscale`
- `/api/cutout/models`（命名空间与前两者不同）

三次并发 HTTP 请求可合并为一次，且 `/api/cutout/models` 的路由前缀与其他模型接口不一致。

**修复方案**：
1. 后端新增 `/api/models/groups` 接口，一次返回所有功能的模型分组
2. 将 `/api/cutout/models` 迁移至 `/api/models/cutout`（或在 cutout 路由中用 `include_router` 挂到 `/api/models`）
3. 前端 `loadModels` 改为单次请求

```typescript
// 修改后：单次请求
const res = await fetch(`${backendURL}/api/models/groups`)
const data = await res.json()
// data: { inpaint: ModelGroup[], upscale: ModelGroup[], cutout: ModelGroup[] }
```

---

### ✅ Fix-08 · 删除 Legacy SSE 下载重复代码

**文件**：`backend/app/routers/models.py`

**问题**：`_download_generator()` 和 `_download_single_generator()` 合计约 200 行，与 `download_queue.py` 中的调度、进度推送、错误处理逻辑高度重复。两套路径并存，行为不一致（legacy 路径无并发限制），维护成本翻倍。

**修复方案**：
1. 确认前端已完全切换到 queue-based 端点（`POST /api/models/download/{id}`）
2. 将 `GET /download`、`GET /download/{model_id}` 标记为 `@deprecated`
3. 验证无调用方后彻底删除这两个端点及 `_download_generator` / `_download_single_generator` 函数

---

### ✅ Fix-09 · 启动时并发请求过多（页面惰性初始化）

**文件**：`frontend/src/renderer/components/layout/MainLayout.tsx`

**问题**：6 个页面全部在应用启动时挂载，每个页面的 `useEffect` 同时触发，导致启动瞬间发出 10+ 个并发请求，加重后端初始化压力。

**修复方案**（方案 A，改动最小）：新增 `useFirstVisible` hook，非当前页面的初始化请求延迟到首次变为可见时再触发：

```typescript
// hooks/useFirstVisible.ts
export function useFirstVisible(isVisible: boolean) {
    const hasBeenVisible = useRef(false)
    if (isVisible && !hasBeenVisible.current) {
        hasBeenVisible.current = true
    }
    return hasBeenVisible.current
}

// 各页面的 useEffect 改为：
const ready = useFirstVisible(isCurrentPage)
useEffect(() => {
    if (!ready) return
    // 原初始化逻辑
}, [ready])
```

---

### ✅ Fix-10 · `model_checker.py` 职责越界

**文件**：`backend/core/model_checker.py`（`ensure_iopaint_hub_links` 函数）

**问题**：`ensure_iopaint_hub_links()` 在"检查器"模块中执行了创建目录、建立符号链接、写入 JSON 文件等副作用操作，违反单一职责原则。

**修复方案**：将链接创建逻辑迁移至 `backend/core/model_setup.py`（新建）或 `paths.py`，`model_checker` 只返回"模型是否可用"的状态枚举，不产生副作用。

---

## P3 — 小优化（代码质量）

### ✅ Fix-11 · `parseMB` 函数重复定义

**文件**：`frontend/src/renderer/pages/ModelManager.tsx`

**问题**：`parseMB` 函数在 `BulkTaskRow` 和 `ModelRow` 两个组件中各定义了一次，实现完全相同。

**修复**：提取到文件顶层或 `src/renderer/utils/format.ts`：
```typescript
export function parseMB(s: string): number {
    const m = s.match(/([\d.]+)\s*(GB|MB|KB)?/i)
    if (!m) return 0
    const v = parseFloat(m[1])
    const unit = (m[2] ?? 'MB').toUpperCase()
    return unit === 'GB' ? v * 1024 : unit === 'KB' ? v / 1024 : v
}
```

---

### ✅ Fix-12 · `autoDownloadOnSelect` 死代码

**文件**：`frontend/src/renderer/components/ModelSelect/ModelSelect.tsx`

**问题**：`autoDownloadOnSelect` prop 在 3 处被传入 `true`，但函数体为空（注释"此处不立即触发，留给调用方决定"），实际毫无效果。

**修复**：二选一——
- **删除**该 prop 及所有调用处的传参
- **实现**它：在 `handleSelect` 中检测到模型 missing 时直接调用 `startDownload(value)`

---

### ✅ Fix-13 · `handleSelect` 因 `tasks` 引用频繁重建

**文件**：`frontend/src/renderer/components/ModelSelect/ModelSelect.tsx`

**问题**：`tasks` 作为 `useCallback` 依赖，任意模型状态变化都会导致 `handleSelect` 重建，引发不必要的子组件 re-render。

**修复**：在回调内直接读 store 状态，去除 `tasks` 依赖：
```typescript
const handleSelect = useCallback((value: string) => {
    const task = useDownloadStore.getState().getTask(value)
    ...
}, [onChange, autoDownloadOnSelect, startDownload])
```

---

### ✅ Fix-14 · `asyncio.get_event_loop()` 弃用警告

**文件**：`backend/app/routers/models.py`、`backend/core/download_queue.py`

**问题**：Python 3.10+ 中 `asyncio.get_event_loop()` 在没有运行中事件循环时会发出 DeprecationWarning。

**修复**：全部替换为 `asyncio.get_running_loop()`（在 async 上下文内调用时等价且不产生警告）。

---

### ✅ Fix-15 · `model_registry.py` 缺少 IO 错误处理 + `reload()` 线程安全

**文件**：`backend/core/model_registry.py`

**问题 1**：`_load()` 在模块导入时执行，无 `try/except`，YAML 损坏会导致整个后端启动失败且报错信息不友好。

**问题 2**：`reload()` 用 `global` 赋值，多线程环境下读写非原子，存在竞态风险。

**修复**：
```python
# 问题 1：添加友好报错
def _load():
    try:
        with open(_YAML_PATH, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except FileNotFoundError:
        raise RuntimeError(f"models.yaml 不存在: {_YAML_PATH}")
    except yaml.YAMLError as e:
        raise RuntimeError(f"models.yaml 格式错误: {e}")
    ...

# 问题 2：reload() 加锁
import threading
_lock = threading.Lock()

def reload():
    models, modes = _load()
    with _lock:
        global MODELS, MODE_GROUPS, MODEL_BY_ID, MODE_BY_ID
        MODELS, MODE_GROUPS = models, modes
        MODEL_BY_ID = {m["id"]: m for m in MODELS}
        MODE_BY_ID  = {g["id"]: g for g in MODE_GROUPS}
```

---

## 修复进度汇总

| ID | 描述 | 优先级 | 状态 |
|----|------|--------|------|
| Fix-01 | Lambda 闭包捕获 Bug（一键下载） | P0 | ✅ |
| Fix-02 | CSS group 类缺失（下载按钮不可见） | P0 | ✅ |
| Fix-03 | cancel() AttributeError | P0 | ✅ |
| Fix-04 | 前端硬编码模型列表双重维护 | P1 | ✅ |
| Fix-05 | _syncDone 阻止后端重启后状态刷新 | P1 | ✅ |
| Fix-06 | registerDownloadDoneCallback 全局可变状态 | P1 | ✅ |
| Fix-07 | 统一三个模型列表接口 | P2 | ✅ |
| Fix-08 | 删除 Legacy SSE 下载重复代码 | P2 | ✅ |
| Fix-09 | 启动时并发请求过多 | P2 | ✅ |
| Fix-10 | model_checker.py 职责越界 | P2 | ✅ |
| Fix-11 | parseMB 函数重复定义 | P3 | ✅ |
| Fix-12 | autoDownloadOnSelect 死代码 | P3 | ✅ |
| Fix-13 | handleSelect 因 tasks 引用频繁重建 | P3 | ✅ |
| Fix-14 | asyncio.get_event_loop() 弃用 | P3 | ✅ |
| Fix-15 | model_registry.py IO 错误处理 + 线程安全 | P3 | ✅ |
