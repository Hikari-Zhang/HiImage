/**
 * 项目级常量 —— 统一管理所有魔术字符串，避免散落各处。
 *
 * 使用方式：
 *   import { ModelStatus, DownloadStatus, Provider } from '../constants'
 */

// ── 模型文件检测状态 ─────────────────────────────────────────────────────────
// 与后端 ModelStatus 保持一致

export const ModelStatus = {
  /** 文件完整，可直接使用 */
  OK:        'ok',
  /** 文件缺失，需要下载 */
  MISSING:   'missing',
  /** 部分文件缺失（多文件模型） */
  PARTIAL:   'partial',
  /** 文件存在但损坏 */
  CORRUPTED: 'corrupted',
  /** 无法判断 */
  UNKNOWN:   'unknown',
} as const

export type ModelStatusValue = typeof ModelStatus[keyof typeof ModelStatus]

// ── 下载任务状态 ─────────────────────────────────────────────────────────────
// 与后端 DownloadStatus 和 useDownloadStore.TaskStatus 对齐

export const DownloadStatus = {
  /** 已入队，等待槽位 */
  QUEUED:      'queued',
  /** 正在下载 */
  DOWNLOADING: 'downloading',
  /** 下载完成 */
  DONE:        'done',
  /** 下载失败 */
  ERROR:       'error',
  /** 已取消 */
  CANCELLED:   'cancelled',
  /** 文件已存在，跳过（旧 SSE 接口专用） */
  SKIPPED:     'skipped',
} as const

export type DownloadStatusValue = typeof DownloadStatus[keyof typeof DownloadStatus]

// ── 模型 Provider ────────────────────────────────────────────────────────────
// 与后端 Provider 保持一致

export const Provider = {
  REMBG:      'rembg',
  IOPAINT:    'IOPaint',
  DIFFUSERS:  'diffusers',
  FACEXLIB:   'facexlib',
  REALESRGAN: 'realesrgan',
  HIIMAGE:    'HiImage',
} as const

export type ProviderValue = typeof Provider[keyof typeof Provider]

// ── 设备标识 ─────────────────────────────────────────────────────────────────

export const Device = {
  CPU:  'cpu',
  CUDA: 'cuda',
  MPS:  'mps',
} as const

export type DeviceValue = typeof Device[keyof typeof Device]

// ── 后处理方法 ───────────────────────────────────────────────────────────────

export const PostprocessMethod = {
  NONE:        'none',
  POISSON:     'poisson',
  GFPGAN:      'gfpgan',
  LAMA_REFINE: 'lama_refine',
} as const

export type PostprocessMethodValue = typeof PostprocessMethod[keyof typeof PostprocessMethod]

// ── 合成模式 Tag ─────────────────────────────────────────────────────────────

export const SynthesisTag = {
  BACKGROUND_REPLACE: 'background_replace',
  OUTFIT_SWAP:        'outfit_swap',
  FACE_SWAP:          'face_swap',
  VIRTUAL_TRYON:      'virtual_tryon',
  PROMPT_INPAINT:     'prompt_inpaint',
  AUTO_SEGMENT_EDIT:  'auto_segment_edit',
  INSTRUCTION_EDIT:   'instruction_edit',
} as const

export type SynthesisTagValue = typeof SynthesisTag[keyof typeof SynthesisTag]

// ── 连接模式 ─────────────────────────────────────────────────────────────────

export const ConnectionMode = {
  LOCAL:  'local',
  REMOTE: 'remote',
} as const

export type ConnectionModeValue = typeof ConnectionMode[keyof typeof ConnectionMode]

// ── 连接状态 ─────────────────────────────────────────────────────────────────

export const ConnectionStatus = {
  IDLE:         'idle',
  TESTING:      'testing',
  CONNECTED:    'connected',
  DISCONNECTED: 'disconnected',
} as const

export type ConnectionStatusValue = typeof ConnectionStatus[keyof typeof ConnectionStatus]

// ── IOPaint 运行模式 ──────────────────────────────────────────────────────────

export const IOPaintMode = {
  CLI:    'cli',
  SERVER: 'server',
} as const

export type IOPaintModeValue = typeof IOPaintMode[keyof typeof IOPaintMode]

// ── API 路径 ─────────────────────────────────────────────────────────────────

export const ApiPath = {
  MODELS_LIST:          '/api/models/list',
  MODELS_HEALTH:        '/api/models/health',
  MODELS_SUBSCRIBE:     '/api/models/subscribe',
  MODELS_DOWNLOAD:      '/api/models/download',
  MODELS_INPAINT:       '/api/models/inpaint',
  MODELS_UPSCALE:       '/api/models/upscale',
  HEALTH:               '/api/health',
  WS_PROGRESS:          '/api/ws/progress',
  SYNTHESIS_MODES:      '/api/synthesis/modes',
  SYNTHESIS_MODELS:     '/api/synthesis/models',
} as const
