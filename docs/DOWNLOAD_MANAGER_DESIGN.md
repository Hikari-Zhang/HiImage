# 下载管理器模块设计文档

## 1. 问题描述

### 1.1 当前问题

1. **下载逻辑分散**：下载逻辑分散在多个地方
   - `backend/app/routers/models.py` - `_download_hf()`、`_download_direct()`、`_download_rembg()`
   - `backend/core/upscaler.py` - 独立的 `_download_weight()`
   - `backend/core/background_fixer.py` - 独立的 `_ensure_gfpgan_weights()`

2. **缺少统一下载管理器**：没有集中的下载管理模块，难以：
   - 跟踪下载状态
   - 管理并发下载
   - 提供统一的进度回调接口

3. **没有自动触发下载**：用户选择未下载的模型时，系统不会自动下载

4. **缺少操作拦截**：模型未下载时，用户可以直接点击处理按钮，导致失败后才知道需要下载

### 1.2 需求分析

1. **统一下载管理器**：
   - 集中管理所有模型下载逻辑
   - 支持下载状态跟踪
   - 支持并发控制
   - 提供统一的进度回调接口

2. **自动下载触发**：
   - 当用户选择未下载的模型时，自动触发下载
   - 下载完成后自动执行等待中的操作

3. **操作拦截与提示**：
   - 模型未下载时，禁用处理按钮
   - 显示明确的下载提示
   - 实时显示下载进度

## 2. 系统设计方案

### 2.1 架构设计

```
┌─────────────────────────────────────────────────────────────────┐
│                      前端 (React + TypeScript)                 │
├─────────────────────────────────────────────────────────────────┤
│  │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐   │
│  │  页面组件     │    │  状态管理     │    │  UI 组件      │   │
│  │  (Watermark   │    │  (Zustand)  │    │  (Button/     │   │
│  │   Removal等)  │    │              │    │   Progress/    │   │
│  └──────┬───────┘    └──────┬───────┘    │   Toast)     │   │
│         │                   │               └──────┬───────┘   │
│         │                   │                      │           │
│         └───────────────────┴──────────────────────┘           │
│                            │                                  │
│                            ↓                                  │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │             下载管理器 Hook (useDownloadManager)          │   │
│  │  - 检查模型状态                                         │   │
│  │  - 触发下载                                             │   │
│  │  - 跟踪下载进度                                         │   │
│  │  - 管理下载队列                                         │   │
│  └─────────────────────────────────────────────────────────┘   │
│                            │                                  │
└─────────────────────────────────────────────────────────────────┘
                             ↓ HTTP/SSE
┌─────────────────────────────────────────────────────────────────┐
│                      后端 (FastAPI)                           │
├─────────────────────────────────────────────────────────────────┤
│  │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │            下载管理器 (DownloadManager)                  │   │
│  │  - 统一管理下载任务                                     │   │
│  │  - 下载状态跟踪                                         │   │
│  │  - 并发控制                                             │   │
│  │  - 进度回调                                             │   │
│  └─────────────────────────────────────────────────────────┘   │
│                            │                                  │
│                            ↓                                  │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │          下载执行器 (DownloadExecutor)                   │   │
│  │  - HF 模型下载                                         │   │
│  │  - 直接 URL 下载                                       │   │
│  │  - rembg ONNX 下载                                     │   │
│  └─────────────────────────────────────────────────────────┘   │
│  │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 后端设计

#### 2.2.1 DownloadManager 类

**文件**：`backend/core/download_manager.py`

**职责**：
- 统一管理所有模型下载任务
- 跟踪下载状态（idle、downloading、done、error）
- 提供下载进度回调接口
- 支持并发控制（限制同时下载数量）
- 提供下载任务查询接口

**核心方法**：
```python
class DownloadManager:
    """下载管理器（单例模式）"""
    
    def __init__(self, max_concurrent: int = 3):
        self.max_concurrent = max_concurrent
        self.tasks: Dict[str, DownloadTask] = {}
        self.queue: List[str] = []
        self.active_count: int = 0
    
    def get_task(self, model_id: str) -> Optional[DownloadTask]:
        """获取指定模型的下载任务"""
        
    def start_download(self, model_id: str, progress_callback: Optional[Callable] = None) -> str:
        """启动下载任务，返回任务 ID"""
        
    def cancel_download(self, model_id: str) -> bool:
        """取消下载任务"""
        
    def get_all_tasks(self) -> List[Dict]:
        """获取所有下载任务状态"""
        
    def cleanup(self):
        """清理已完成的任务"""
```

#### 2.2.2 DownloadTask 类

**职责**：
- 表示一个下载任务
- 跟踪任务状态
- 提供进度回调

**核心属性**：
```python
class DownloadTask:
    """下载任务"""
    
    def __init__(self, model_id: str, config: dict):
        self.model_id: str = model_id
        self.config: dict = config
        self.status: str = "idle"  # idle, downloading, done, error, cancelled
        self.progress: Dict = {}
        self.error: Optional[str] = None
        self.created_at: float = time.time()
        self.updated_at: float = time.time()
    
    def to_dict(self) -> Dict:
        """转换为可序列化字典"""
```

#### 2.2.3 下载执行器

**文件**：`backend/core/download_executor.py`

**职责**：
- 执行实际的下载操作
- 支持多种下载方式（HF、直接 URL、rembg）
- 提供统一的进度回调接口

**核心方法**：
```python
class DownloadExecutor:
    """下载执行器"""
    
    @staticmethod
    def download(model_config: dict, progress_callback: Callable) -> None:
        """执行下载"""
        provider = model_config.get("provider", "")
        
        if provider == "rembg":
            DownloadExecutor._download_rembg(model_config, progress_callback)
        elif model_config.get("hf_model_id"):
            DownloadExecutor._download_hf(model_config, progress_callback)
        elif model_config.get("local_path") and model_config.get("download_url"):
            DownloadExecutor._download_direct(model_config, progress_callback)
        else:
            raise ValueError(f"未知的下载方式: {model_config}")
    
    @staticmethod
    def _download_rembg(config: dict, progress_callback: Callable) -> None:
        """下载 rembg ONNX 模型"""
        
    @staticmethod
    def _download_hf(config: dict, progress_callback: Callable) -> None:
        """从 HuggingFace 下载模型"""
        
    @staticmethod
    def _download_direct(config: dict, progress_callback: Callable) -> None:
        """直接下载模型文件"""
```

### 2.3 前端设计

#### 2.3.1 useDownloadManager Hook

**文件**：`frontend/src/renderer/hooks/useDownloadManager.ts`

**职责**：
- 检查模型下载状态
- 触发模型下载
- 跟踪下载进度
- 管理下载队列

**核心方法**：
```typescript
interface UseDownloadManagerReturn {
  // 状态
  isChecking: boolean;
  isDownloading: boolean;
  downloadProgress: ModelDownloadItem[];
  currentModel: string | null;
  
  // 方法
  checkModelStatus: (modelId: string) => Promise<ModelCheckResult>;
  startDownload: (modelId: string) => Promise<void>;
  cancelDownload: (modelId: string) => void;
  waitForDownload: (modelId: string) => Promise<boolean>;
}
```

#### 2.3.2 页面组件修改

**WatermarkRemoval.tsx** 修改：
1. 在选择模型时，检查模型下载状态
2. 如果模型未下载，显示下载提示
3. 禁用处理按钮，直到模型下载完成
4. 显示下载进度条

**修改点**：
```typescript
// 1. 检查模型状态
const [modelStatus, setModelStatus] = useState<ModelCheckResult | null>(null);

// 2. 选择模型时检查
const handleModelChange = async (modelId: string) => {
  setInpaintModel(modelId);
  const status = await checkModelStatus(modelId);
  setModelStatus(status);
  
  if (status.status !== 'ok') {
    // 显示下载提示
    setShowDownloadPrompt(true);
  }
};

// 3. 处理按钮禁用逻辑
const isProcessDisabled = useMemo(() => {
  return isProcessing 
    || rois.length === 0 
    || !sourceImage 
    || (modelStatus !== null && modelStatus.status !== 'ok');
}, [isProcessing, rois.length, sourceImage, modelStatus]);

// 4. 处理按钮点击
const handleProcess = async () => {
  // 检查模型状态
  const status = await checkModelStatus(inpaintModel);
  
  if (status.status !== 'ok') {
    // 触发下载
    await startDownload(inpaintModel);
    // 等待下载完成
    const success = await waitForDownload(inpaintModel);
    if (!success) return;
  }
  
  // 执行处理
  // ...
};
```

#### 2.3.3 UI 组件

**DownloadPrompt 组件**：
- 显示模型未下载提示
- 提供下载按钮
- 显示下载进度

**ModelStatusBadge 组件**：
- 显示模型状态（已下载、未下载、下载中、错误）
- 提供快速下载按钮

## 3. 实现计划

### 3.1 后端实现

1. **创建 `backend/core/download_manager.py`**
   - 实现 `DownloadManager` 类（单例）
   - 实现 `DownloadTask` 类

2. **创建 `backend/core/download_executor.py`**
   - 实现 `DownloadExecutor` 类
   - 整合现有下载逻辑

3. **修改 `backend/app/routers/models.py`**
   - 使用 `DownloadManager` 管理下载
   - 提供下载状态查询接口

4. **添加 API 端点**
   - `GET /api/models/status/{model_id}` - 获取模型状态
   - `GET /api/download/status` - 获取所有下载任务状态
   - `POST /api/download/{model_id}/start` - 启动下载
   - `POST /api/download/{model_id}/cancel` - 取消下载

### 3.2 前端实现

1. **创建 `frontend/src/renderer/hooks/useDownloadManager.ts`**
   - 实现 `useDownloadManager` Hook

2. **修改页面组件**
   - `WatermarkRemoval.tsx`
   - `SuperResolution.tsx`
   - `SmartSynthesis.tsx`

3. **创建 UI 组件**
   - `DownloadPrompt.tsx`
   - `ModelStatusBadge.tsx`

4. **修改状态管理**
   - 在 `useSettingsStore.ts` 中添加下载相关状态

### 3.3 测试计划

1. **后端测试**
   - 测试 `DownloadManager` 功能
   - 测试并发下载控制
   - 测试下载状态跟踪

2. **前端测试**
   - 测试 `useDownloadManager` Hook
   - 测试下载提示 UI
   - 测试操作拦截逻辑

## 4. 时间估算

| 任务 | 预估时间 |
|------|----------|
| 后端 DownloadManager 实现 | 2 小时 |
| 后端 DownloadExecutor 实现 | 2 小时 |
| 后端 API 端点实现 | 1 小时 |
| 前端 useDownloadManager Hook | 2 小时 |
| 前端页面组件修改 | 3 小时 |
| 前端 UI 组件创建 | 2 小时 |
| 测试与调试 | 3 小时 |
| **总计** | **15 小时** |

## 5. 风险评估

| 风险 | 影响 | 应对措施 |
|------|------|----------|
| 下载逻辑整合复杂 | 高 | 逐步迁移，保持 backward compatibility |
| 并发下载控制 | 中 | 使用 asyncio.Queue 实现 |
| 前端状态同步 | 中 | 使用 WebSocket 实时推送状态 |
| 下载中断恢复 | 低 | 暂不实现，后续版本支持 |

## 6. 后续优化方向

1. **断点续传**：支持下载中断后继续下载
2. **下载队列持久化**：保存下载队列，重启后恢复
3. **下载速度限制**：允许用户设置下载速度限制
4. **下载调度优化**：根据模型依赖关系优化下载顺序
5. **P2P 分发**：使用 P2P 技术加速模型分发
