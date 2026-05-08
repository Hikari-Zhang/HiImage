# 下载状态流程图与决策树

## 修复前后对比

修复前：
- canDownload = !isBuiltin && model.status !== 'ok' && rowDownload.status !== 'downloading'
- 问题：当 rowDownload.status = 'done' 时，仍会显示下载按钮

修复后：
- canDownload = !isBuiltin && model.status !== 'ok' && rowDownload.status === 'idle'
- 解决：只有 idle 状态时才显示下载按钮

## 状态转移表

┌──────────────────┬────────────────────┬──────────────────┬──────────────────┐
│ model.status     │ rowDownload.status │ 修复前(错误)      │ 修复后(正确)      │
├──────────────────┼────────────────────┼──────────────────┼──────────────────┤
│ missing          │ idle               │ true ✓            │ true ✓            │
│ missing          │ downloading        │ false ✓           │ false ✓           │
│ missing          │ done               │ true ❌           │ false ✓           │
│ missing          │ error              │ true ❌           │ false ✓           │
│ ok               │ idle               │ false ✓           │ false ✓           │
│ ok               │ downloading        │ false ✓           │ false ✓           │
│ ok               │ done               │ false ✓           │ false ✓           │
│ ok               │ error              │ false ✓           │ false ✓           │
└──────────────────┴────────────────────┴──────────────────┴──────────────────┘

## 下载流程时间线

T0: 用户点击下载
    → rowDownload.status = 'downloading'
    → 隐藏下载按钮，显示取消按钮

T1-T∞: 下载进行中
    → 显示进度条

T_finish: 后端发送 finish 事件
    → rowDownload.status = 'done'（即刻）
    → 显示 "下载完成" 文案

T_finish + 500ms: loadModels() 执行
    ✓ 成功：model.status = 'ok'
      → canDownload = false
      → 隐藏下载按钮，显示删除按钮
    
    ✗ 失败：model.status 仍为 'missing'
      修复前：canDownload = true （BUG！）
      修复后：canDownload = false ✓

## 关键代码位置

ModelManager.tsx:
  第 672 行: canDownload 定义 ← 问题所在
  第 793-801 行: 下载按钮条件渲染
  第 145-158 行: 下载完成回调（可选增强）

useSettingsStore.ts:
  第 18-24 行: RowDownloadState 类型
  第 69 行: rowDownloads 状态
  第 154-164 行: setRowDownload() 方法

## 修复建议

最简单方案（推荐）：
文件: ModelManager.tsx, 第 672 行
改: const canDownload = !isBuiltin && model.status !== 'ok' && rowDownload.status !== 'downloading'
为: const canDownload = !isBuiltin && model.status !== 'ok' && rowDownload.status === 'idle'

