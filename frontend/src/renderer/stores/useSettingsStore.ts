import { create } from 'zustand'

// ── 下载相关类型 ──────────────────────────────────────────────────────────────
export type DownloadRunStatus = 'idle' | 'running' | 'done' | 'error'
export type ModelDownloadItem = {
  id: string
  name: string
  index: number
  total: number
  status: 'checking' | 'skipped' | 'downloading' | 'done' | 'error'
  message: string
  speed?: string       // 下载速度，如 "1.2 MB/s"
  downloaded?: string  // 已下载大小，如 "32 MB"
  total_size?: string  // 文件总大小，如 "65 MB"
}

/** 单模型行内下载进度（跨页签持久） */
export type RowDownloadState = {
  status: 'idle' | 'downloading' | 'done' | 'error'
  message: string
  speed: string
  downloaded: string
  total_size: string
}

interface SettingsState {
  // Global
  device: string
  disableNsfw: boolean

  // Inpaint
  inpaintModel: string
  defaultDilation: number
  sensitivity: number

  // Postprocess
  postprocessMethod: string   // none / poisson / gfpgan / lama_refine
  postprocessEnabled: boolean

  // Upscale
  upscaleModel: string
  upscaleEnabled: boolean

  // Server
  serverPort: number
  keepaliveSeconds: number
  startupTimeout: number

  // Memory optimization (diffusion models)
  lowMem: boolean
  cpuOffload: boolean
  cpuTextencoder: boolean

  // Network
  hfEndpoint: string
  hfToken: string
  githubMirror: string

  isLoading: boolean

  // ── 一键下载全部状态（跨页签持久化）────────────────────────────────────────
  downloadStatus: DownloadRunStatus
  downloadModels: ModelDownloadItem[]
  downloadSummary: string
  downloadTotal: number
  downloadPanelOpen: boolean

  // ── 单模型行内下载状态（跨页签持久化）──────────────────────────────────────
  rowDownloads: Record<string, RowDownloadState>

  // Actions
  setDevice: (device: string) => void
  setInpaintModel: (model: string) => void
  setUpscaleModel: (model: string) => void
  setDilation: (dilation: number) => void
  setSensitivity: (sensitivity: number) => void
  setDisableNsfw: (v: boolean) => void
  setPostprocessMethod: (method: string) => void
  setPostprocessEnabled: (enabled: boolean) => void
  setUpscaleEnabled: (enabled: boolean) => void
  setSettings: (settings: Partial<SettingsState>) => void
  loadSettings: (backendURL: string) => Promise<void>
  saveSettings: (backendURL: string) => Promise<void>

  // 一键下载 actions
  setDownloadStatus: (s: DownloadRunStatus) => void
  setDownloadModels: (updater: ModelDownloadItem[] | ((prev: ModelDownloadItem[]) => ModelDownloadItem[])) => void
  setDownloadSummary: (s: string) => void
  setDownloadTotal: (n: number) => void
  setDownloadPanelOpen: (v: boolean) => void
  resetDownload: () => void

  // 单模型行内下载 actions
  setRowDownload: (modelId: string, patch: Partial<RowDownloadState>) => void
  clearRowDownload: (modelId: string) => void
}

export const useSettingsStore = create<SettingsState>((set, get) => ({
  device: 'cpu',
  disableNsfw: true,
  inpaintModel: 'lama',
  defaultDilation: 10,
  sensitivity: 50,
  postprocessMethod: 'none',
  postprocessEnabled: false,
  upscaleModel: 'RealESRGAN_x4plus',
  upscaleEnabled: false,
  serverPort: 51821,
  keepaliveSeconds: 300,
  startupTimeout: 1800,
  lowMem: true,
  cpuOffload: false,
  cpuTextencoder: false,
  hfEndpoint: 'https://huggingface.co',
  hfToken: '',
  githubMirror: '',
  isLoading: false,

  // 一键下载状态初始值
  downloadStatus: 'idle',
  downloadModels: [],
  downloadSummary: '',
  downloadTotal: 0,
  downloadPanelOpen: false,

  // 单模型行内下载状态初始值
  rowDownloads: {},

  setDevice: (device) => set({ device }),
  setInpaintModel: (model) => set({ inpaintModel: model }),
  setUpscaleModel: (model) => set({ upscaleModel: model }),
  setDilation: (dilation) => set({ defaultDilation: dilation }),
  setSensitivity: (sensitivity) => set({ sensitivity }),
  setDisableNsfw: (v) => set({ disableNsfw: v }),
  setPostprocessMethod: (method) => set({ postprocessMethod: method }),
  setPostprocessEnabled: (enabled) => set({ postprocessEnabled: enabled }),
  setUpscaleEnabled: (enabled) => set({ upscaleEnabled: enabled }),

  setSettings: (settings) => set(settings),

  // 一键下载 actions
  setDownloadStatus: (s) => set({ downloadStatus: s }),
  setDownloadModels: (updater) =>
    set((state) => ({
      downloadModels:
        typeof updater === 'function' ? updater(state.downloadModels) : updater,
    })),
  setDownloadSummary: (s) => set({ downloadSummary: s }),
  setDownloadTotal: (n) => set({ downloadTotal: n }),
  setDownloadPanelOpen: (v) => set({ downloadPanelOpen: v }),
  resetDownload: () =>
    set({ downloadStatus: 'idle', downloadModels: [], downloadSummary: '', downloadTotal: 0 }),

  // 单模型行内下载 actions
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
  clearRowDownload: (modelId) =>
    set((state) => {
      const next = { ...state.rowDownloads }
      delete next[modelId]
      return { rowDownloads: next }
    }),

  loadSettings: async (backendURL) => {
    set({ isLoading: true })
    try {
      const res = await fetch(`${backendURL}/api/settings`)
      const data = await res.json()
      set({
        device: data.device ?? 'cpu',
        serverPort: data.server_port ?? 51821,
        keepaliveSeconds: data.server_keepalive ?? 300,
        startupTimeout: data.server_startup_timeout ?? 1800,
        hfEndpoint: data.hf_endpoint ?? 'https://huggingface.co',
        hfToken: data.hf_token ?? '',
        githubMirror: data.github_mirror ?? '',
        defaultDilation: data.default_dilation ?? 10,
        disableNsfw: data.disable_nsfw ?? true,
        lowMem: data.low_mem ?? true,
        cpuOffload: data.cpu_offload ?? false,
        cpuTextencoder: data.cpu_textencoder ?? false,
      })
    } catch (err) {
      console.error('Failed to load settings:', err)
    } finally {
      set({ isLoading: false })
    }
  },

  saveSettings: async (backendURL) => {
    const state = get()
    try {
      await fetch(`${backendURL}/api/settings`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          device: state.device,
          server_port: state.serverPort,
          server_keepalive: state.keepaliveSeconds,
          server_startup_timeout: state.startupTimeout,
          hf_endpoint: state.hfEndpoint,
          hf_token: state.hfToken,
          github_mirror: state.githubMirror,
        default_dilation: state.defaultDilation,
        disable_nsfw: state.disableNsfw,
        low_mem: state.lowMem,
        cpu_offload: state.cpuOffload,
        cpu_textencoder: state.cpuTextencoder,
        }),
      })
    } catch (err) {
      console.error('Failed to save settings:', err)
      throw err
    }
  },
}))

