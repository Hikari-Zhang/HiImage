# 下载界面代码关键片段总结

## 📍 相关文件路径

| 文件 | 用途 | 行数 |
|-----|------|------|
| `frontend/src/renderer/pages/ModelManager.tsx` | 下载界面主页面 | 1-846 |
| `frontend/src/renderer/stores/useSettingsStore.ts` | 下载状态管理 | 1-225 |

---

## 1️⃣ 下载按钮显示逻辑（❌ 有 BUG）

**文件:** `ModelManager.tsx`  
**位置:** 第 793-801 行

### 当前代码（有问题）
```typescript
{/* 单独下载按钮 */}
{canDownload && !isConfirming && (
  <button
    onClick={onDownload}
    title="下载此模型"
    className="p-1.5 rounded text-fg-secondary hover:text-fg-accent hover:bg-fg-accent/10 transition-colors"
  >
    <Download size={13} />
  </button>
)}
```

### canDownload 的条件定义（第 672 行）
```typescript
// ❌ 问题在这里！缺少对 'done' 和 'error' 的检查
const canDownload = !isBuiltin && model.status !== 'ok' && rowDownload.status !== 'downloading'
```

### 修复方案
```typescript
// ✅ 方案 1：最完整
const canDownload = !isBuiltin && model.status !== 'ok' && rowDownload.status === 'idle'

// ✅ 方案 2：显式排除所有非 idle 状态
const canDownload = !isBuiltin && model.status !== 'ok' && !['downloading', 'done', 'error'].includes(rowDownload.status)

// ✅ 方案 3：添加缺失的条件
const canDownload = !isBuiltin && model.status !== 'ok' && rowDownload.status !== 'downloading' && rowDownload.status !== 'done' && rowDownload.status !== 'error'
```

---

## 2️⃣ 下载完成后的回调逻辑

**文件:** `ModelManager.tsx`  
**位置:** 第 145-158 行

```typescript
handlers.finish = (e) => {
  const data = JSON.parse((e as MessageEvent).data)
  const finalStatus = data.failed > 0 ? 'error' : 'done'
  
  // ✅ 第 1 步：立即设置状态为 'done'
  useSettingsStore.getState().setRowDownload(mid, {
    status: finalStatus, 
    message: data.message, 
    speed: '', 
    downloaded: '', 
    total_size: '',
  })
  
  es.close(); 
  delete globalRowEsRef[mid]; 
  delete globalRowEsHandlers[mid]
  
  if (finalStatus === 'done') {
    showToast('success', data.message)
    
    // ⚠️ 第 2 步：延迟 500ms 后刷新模型列表
    // 问题：如果这个刷新失败或延迟，model.status 不会变为 'ok'，
    //      导致 canDownload 条件仍然会显示下载按钮！
    setTimeout(() => loadModelsRef.current(), 500)
  } else {
    showToast('error', data.message)
  }
}
```

---

## 3️⃣ 状态定义（Store）

**文件:** `useSettingsStore.ts`  
**位置:** 第 18-24 行

```typescript
/** 单模型行内下载进度（跨页签持久） */
export type RowDownloadState = {
  status: 'idle' | 'downloading' | 'done' | 'error'
  message: string
  speed: string
  downloaded: string
  total_size: string
}
```

**全局状态:**
```typescript
// 第 69 行
rowDownloads: Record<string, RowDownloadState>
```

**状态更新方法:**
```typescript
// 第 154-164 行
setRowDownload: (modelId, patch) =>
  set((state) => ({
    rowDownloads: {
      ...state.rowDownloads,
      [modelId]: {
        ...(state.rowDownloads[modelId] ?? { status: 'idle' as const, message: '', speed: '', downloaded: '', total_size: '' }),
        ...patch,
      },
    },
  })),
```

---

## 4️⃣ 下载按钮取消逻辑

**文件:** `ModelManager.tsx`  
**位置:** 第 804-812 行

```typescript
{/* 下载中：取消按钮 */}
{isRowDownloading && (
  <button
    onClick={onCancel}
    title="取消下载"
    className="p-1.5 rounded text-fg-secondary hover:text-status-error hover:bg-status-error/10 transition-colors"
  >
    <XCircle size={13} />
  </button>
)}
```

其中 `isRowDownloading` 定义（第 673 行）：
```typescript
const isRowDownloading = rowDownload.status === 'downloading'
```

---

## 5️⃣ 下载完成/失败/正常状态的显示

**文件:** `ModelManager.tsx`  
**位置:** 第 752-787 行

```typescript
{/* 显示逻辑是正确的，问题在按钮条件，不在显示 */}
} else rowDownload.status === 'error' ? (
  <div className="flex items-center gap-3 mt-1 flex-wrap">
    <span className="text-[10px] font-medium text-status-error">下载失败</span>
    <span className="text-[10px] text-fg-secondary truncate max-w-[240px]">{rowDownload.message}</span>
  </div>
) : rowDownload.status === 'done' ? (
  <div className="flex items-center gap-3 mt-1 flex-wrap">
    <span className="text-[10px] font-medium text-status-success">下载完成</span>
  </div>
) : (
  // 显示模型整体状态（已下载/未下载/损坏等）
  <div className="flex items-center gap-3 mt-1 flex-wrap">
    <span className={clsx('text-[10px] font-medium', ...)}>
      {STATUS_LABEL[model.status] ?? '未知'}
    </span>
    {/* ... 其他信息 */}
  </div>
)
```

---

## 6️⃣ 下载进度中的显示（正确）

**文件:** `ModelManager.tsx`  
**位置:** 第 725-751 行

```typescript
{isRowDownloading ? (
  <div className="mt-1">
    <div className="flex items-center gap-2 flex-wrap">
      {rowDownload.speed && (
        <span className="text-[10px] text-fg-accent font-mono">{rowDownload.speed}</span>
      )}
      {rowDownload.downloaded && (
        <span className="text-[10px] text-fg-secondary font-mono">
          {rowDownload.downloaded}{rowDownload.total_size ? ` / ${rowDownload.total_size}` : ''}
        </span>
      )}
      {pct !== null && (
        <span className="text-[10px] text-fg-secondary">{pct}%</span>
      )}
      {rowDownload.message && (
        <span className="text-[10px] text-fg-secondary truncate max-w-[160px]">{rowDownload.message}</span>
      )}
    </div>
    {pct !== null && (
      <div className="mt-1 h-0.5 bg-bg-hover rounded-full overflow-hidden">
        <div
          className="h-full bg-fg-accent rounded-full transition-all duration-500"
          style={{ width: `${pct}%` }}
        />
      </div>
    )}
  </div>
```

---

## 🔴 问题发生的确切时刻

```
时间线：
├─ T0: 用户点击下载 → rowDownload.status = 'downloading'
├─ T1: 显示进度条和取消按钮（下载按钮被隐藏）
│      canDownload = false ✓（因为 status !== 'downloading'）
│
├─ T2: 下载完成 → backend 发送 'finish' 事件
│
├─ T3: handlers.finish 执行（即刻）
│      ├─ rowDownload.status = 'done'
│      ├─ canDownload = false ✓（因为 status !== 'downloading' 成立）
│      └─ 显示"下载完成"文案
│
└─ T4: 如果 loadModels() 刷新失败或 model.status 仍为 'missing'
       ├─ model.status = 'missing'（未更新）
       ├─ rowDownload.status = 'done'
       └─ ❌ canDownload = true！（因为 model.status !== 'ok'，且 status !== 'downloading'）
          → 🐛 BUG：下载按钮重新显示！
```

---

## ✅ 解决方案对比

| 方案 | 代码改动 | 复杂度 | 建议 |
|-----|--------|--------|------|
| A | 第 672 行改 `canDownload` | 1 行 | ⭐⭐⭐⭐⭐ 推荐 |
| B | 第 145-158 行添加延迟清除 | 10 行 | ⭐⭐ 可选 |
| C | 两者结合 | 11 行 | ⭐⭐⭐ 最稳健 |

