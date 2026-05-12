import { create } from 'zustand'

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

  // Download
  downloadMaxConcurrent: number

  isLoading: boolean
  defaultsLoaded: boolean   // 默认配置是否已从后端加载

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
  loadDefaults: (backendURL: string) => Promise<void>  // 从后端加载默认模型配置
}

export const useSettingsStore = create<SettingsState>((set, get) => ({
  device: 'cpu',
  disableNsfw: true,
  inpaintModel: 'wm_lama',  // 硬编码兜底值，将从后端API覆盖
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
  downloadMaxConcurrent: 3,
  isLoading: false,
  defaultsLoaded: false,

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
        downloadMaxConcurrent: data.download_max_concurrent ?? 3,
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
          download_max_concurrent: state.downloadMaxConcurrent,
        }),
      })
    } catch (err) {
      console.error('Failed to save settings:', err)
      throw err
    }
  },

  loadDefaults: async (backendURL) => {
    try {
      const res = await fetch(`${backendURL}/api/models/defaults`)
      const defaults = await res.json()
      set({
        inpaintModel: defaults.watermark_removal ?? 'wm_lama',
        defaultsLoaded: true,
      })
    } catch (err) {
      console.error('Failed to load defaults:', err)
      set({ defaultsLoaded: true })  // 使用硬编码兜底值
    }
  },
}))
