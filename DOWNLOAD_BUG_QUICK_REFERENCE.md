# 下载按钮 BUG - 快速参考指南

## 问题
✗ 下载完成后，下载按钮仍然显示，状态没有刷新

## 根本原因
条件判断不完整。`canDownload` 只检查了 `status !== 'downloading'`，但没有排除 `'done'` 和 `'error'` 状态。

## 找到的文件

### 1. 主要文件：ModelManager.tsx
**路径**: `frontend/src/renderer/pages/ModelManager.tsx`
- **673行**: 下载按钮条件定义（问题所在）
- **793-801行**: 下载按钮JSX渲染

### 2. 状态管理：useSettingsStore.ts  
**路径**: `frontend/src/renderer/stores/useSettingsStore.ts`
- **18-24行**: RowDownloadState 类型定义
- **69行**: 全局 rowDownloads 状态
- **154-164行**: setRowDownload 方法

---

## 🔴 问题详解

### 当前代码（第672行）
```typescript
const canDownload = !isBuiltin && model.status !== 'ok' && rowDownload.status !== 'downloading'
```

### 问题分析
| 状态 | model.status | rowDownload.status | 应该显示下载按钮 | 实际显示 | 备注 |
|-----|---|---|---|---|---|
| 未下载 | missing | idle | ✓ 是 | ✓ 是 | ✓ 正确 |
| 下载中 | missing | downloading | ✗ 否 | ✗ 否 | ✓ 正确 |
| **下载完成** | **missing** | **done** | **✗ 否** | **✓ 是** | **❌ BUG** |
| 下载失败 | missing | error | ✗ 否 | ✓ 是 | ❌ BUG |
| 已下载 | ok | idle | ✗ 否 | ✗ 否 | ✓ 正确 |

### 为什么会出现BUG
1. 下载完成后，后端发送 `finish` 事件
2. 前端立即设置 `rowDownload.status = 'done'`
3. 此时条件检查：
   - `!isBuiltin` = true ✓
   - `model.status !== 'ok'` = true ✓（因为 loadModels() 还没刷新）
   - `rowDownload.status !== 'downloading'` = true ✓（状态是 'done' 而不是 'downloading'）
4. **结果**: `canDownload = true` → **显示下载按钮** ❌

---

## ✅ 修复方案

### 方案 A（推荐）- 最简单
**文件**: `ModelManager.tsx`  
**第672行修改**:
```typescript
// 修改前
const canDownload = !isBuiltin && model.status !== 'ok' && rowDownload.status !== 'downloading'

// 修改后
const canDownload = !isBuiltin && model.status !== 'ok' && rowDownload.status === 'idle'
```

**优点**:
- ✓ 最简洁（1行修改）
- ✓ 完全解决问题
- ✓ 逻辑清晰（只有 idle 状态才显示）

---

### 方案 B（可选）- 增强健壮性
在 `ModelManager.tsx` 第145-158行的 `handlers.finish` 中，下载完成后主动清除行内状态：

```typescript
handlers.finish = (e) => {
  const data = JSON.parse((e as MessageEvent).data)
  const finalStatus = data.failed > 0 ? 'error' : 'done'
  useSettingsStore.getState().setRowDownload(mid, {
    status: finalStatus, message: data.message, speed: '', downloaded: '', total_size: '',
  })
  es.close(); delete globalRowEsRef[mid]; delete globalRowEsHandlers[mid]
  if (finalStatus === 'done') {
    showToast('success', data.message)
    setTimeout(() => {
      loadModelsRef.current()
      // 新增：下载完成且状态已刷新后，清除行内状态
      setTimeout(() => {
        useSettingsStore.getState().clearRowDownload(mid)
      }, 1000)
    }, 500)
  } else {
    showToast('error', data.message)
  }
}
```

**优点**:
- ✓ 防止状态刷新失败的情况
- ✓ 清理行内下载记录

---

## 📋 完整的下载状态流

```
┌─ RowDownloadState ─────────────┐
│                                 │
│  status: 'idle'                │  初始状态，无下载
│          'downloading'         │  正在下载中
│          'done'                │  下载完成
│          'error'               │  下载失败
│                                 │
│  message: 状态消息              │
│  speed: 下载速度                │
│  downloaded: 已下载字节         │
│  total_size: 总大小             │
└─────────────────────────────────┘

状态流转:
idle → downloading → done ✓
     ↘             ↘ error ✓

下载按钮显示规则:
- 仅当 rowDownload.status === 'idle' 时显示
- 其他所有状态（downloading, done, error）都隐藏
```

---

## 🔍 下载按钮相关的完整条件

```typescript
// ModelRow 组件中的按钮渲染逻辑

{/* 下载按钮 */}
{canDownload && !isConfirming && <button>...</button>}

{/* 取消按钮（下载中） */}
{isRowDownloading && <button>...</button>}

{/* 删除按钮（已下载） */}
{canDelete ? <button>...</button> : null}

其中：
- canDownload = !isBuiltin && model.status !== 'ok' && rowDownload.status === 'idle'
- isRowDownloading = rowDownload.status === 'downloading'
- canDelete = !isBuiltin && model.status === 'ok'
```

---

## 📍 关键代码位置导航

```
frontend/src/renderer/pages/ModelManager.tsx
├─ Line 35-41    : STATUS_ICON 图标映射
├─ Line 43-49    : STATUS_LABEL 状态标签
├─ Line 172      : ModelManager 组件开始
├─ Line 197-213  : loadModels() 加载模型列表
├─ Line 287-296  : handleDownloadSingle() 单模型下载
├─ Line 300-313  : handleDownloadAll() 一键下载
├─ Line 335-343  : handleCancelSingle() 取消单模型下载
├─ Line 672      : ★ canDownload 条件定义（BUG位置）
├─ Line 673      : isRowDownloading 条件定义
├─ Line 793-801  : ★ 下载按钮渲染逻辑（依赖第672行）
├─ Line 804-812  : 取消按钮渲染逻辑
├─ Line 833-841  : 删除按钮渲染逻辑
└─ Line 656-845  : ModelRow 子组件

frontend/src/renderer/stores/useSettingsStore.ts
├─ Line 1-3      : 类型导入
├─ Line 4-25     : DownloadRunStatus 和 ModelDownloadItem 类型
├─ Line 18-24    : ★ RowDownloadState 类型定义
├─ Line 62-66    : 一键下载状态字段
├─ Line 68-69    : ★ 行内下载状态字段
├─ Line 85-95    : 一键下载 actions
├─ Line 94-95    : ★ 行内下载 actions
├─ Line 154-164  : ★ setRowDownload() 状态更新方法
└─ Line 165-170  : clearRowDownload() 清除方法
```

---

## 测试用例

修复前（有BUG）:
```
1. 点击下载 → 显示取消按钮 ✓
2. 等待下载完成 → 显示"下载完成"和... ❌ 下载按钮（BUG！）
3. 如果 loadModels() 刷新失败，按钮保持显示 ❌
```

修复后（正确）:
```
1. 点击下载 → 显示取消按钮 ✓
2. 等待下载完成 → 显示"下载完成"，隐藏下载按钮 ✓
3. model.status 更新后 → 显示删除按钮 ✓
4. 即使 loadModels() 失败 → 仍不显示下载按钮 ✓
```

---

## 关键总结

| 项目 | 详情 |
|-----|------|
| **问题** | 下载完成后下载按钮仍显示 |
| **原因** | `canDownload` 条件缺少对非idle状态的排除 |
| **文件** | `ModelManager.tsx` 第672行 |
| **修复** | 改 `!== 'downloading'` 为 `=== 'idle'` |
| **复杂度** | 1行代码修改 |
| **风险** | 极低（只修改条件判断） |

