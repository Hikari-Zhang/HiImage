import { create } from 'zustand'
import { createApiClient } from '../services/apiClient'
import { clearHfTokenRequest, saveSettingsRequest } from '../services/settingsService'

interface SettingsResponse {
  device?: string
  hf_endpoint?: string
  hf_token_configured?: boolean
  github_mirror?: string
  default_dilation?: number
  disable_nsfw?: boolean
  download_max_concurrent?: number
  upscale_tile?: number
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
  postprocessMethod: string   // none / poisson / gfpgan
  postprocessEnabled: boolean

  // Upscale
  upscaleModel: string
  upscaleEnabled: boolean
  upscaleTile: number  // 0 = 自动计算；>0 = 固定 tile 大小

  // Cutout
  cutoutModel: string

  // Network
  hfEndpoint: string
  hfToken: string
  hfTokenConfigured: boolean
  githubMirror: string

  // Download
  downloadMaxConcurrent: number

  isLoading: boolean
  defaultsLoaded: boolean   // 默认配置是否已从后端加载

  // Actions
  setDevice: (device: string) => void
  setInpaintModel: (model: string) => void
  setUpscaleModel: (model: string) => void
  setUpscaleTile: (tile: number) => void
  setCutoutModel: (model: string) => void
  setDilation: (dilation: number) => void
  setSensitivity: (sensitivity: number) => void
  setDisableNsfw: (v: boolean) => void
  setPostprocessMethod: (method: string) => void
  setPostprocessEnabled: (enabled: boolean) => void
  setUpscaleEnabled: (enabled: boolean) => void
  setSettings: (settings: Partial<SettingsState>) => void
  loadSettings: (backendURL: string, signal?: AbortSignal) => Promise<void>
  saveSettings: (backendURL: string) => Promise<void>
  clearHfToken: (backendURL: string) => Promise<void>
  loadDefaults: (backendURL: string) => Promise<void>  // 从后端加载默认模型配置
}

export const useSettingsStore = create<SettingsState>((set, get) => ({
  device: 'cpu',
  disableNsfw: true,
  inpaintModel: '',  // 无可用修复模型时保持为空
  defaultDilation: 10,
  sensitivity: 50,
  postprocessMethod: 'none',
  postprocessEnabled: false,
  upscaleModel: 'RealESRGAN_x4plus',
  upscaleEnabled: false,
  upscaleTile: 0,  // 0 = 自动计算
  cutoutModel: 'birefnet',
  hfEndpoint: 'https://huggingface.com',
  hfToken: '',
  hfTokenConfigured: false,
  githubMirror: '',
  downloadMaxConcurrent: 3,
  isLoading: false,
  defaultsLoaded: false,

  setDevice: (device) => set({ device }),
  setInpaintModel: (model) => set({ inpaintModel: model }),
  setUpscaleModel: (model) => set({ upscaleModel: model }),
  setUpscaleTile: (tile) => set({ upscaleTile: tile }),
  setCutoutModel: (model) => set({ cutoutModel: model }),
  setDilation: (dilation) => set({ defaultDilation: dilation }),
  setSensitivity: (sensitivity) => set({ sensitivity }),
  setDisableNsfw: (v) => set({ disableNsfw: v }),
  setPostprocessMethod: (method) => set({ postprocessMethod: method }),
  setPostprocessEnabled: (enabled) => set({ postprocessEnabled: enabled }),
  setUpscaleEnabled: (enabled) => set({ upscaleEnabled: enabled }),

  setSettings: (settings) => set(settings),

  loadSettings: async (backendURL, signal) => {
    set({ isLoading: true })
    try {
      const data = await createApiClient(backendURL).json<SettingsResponse>('/api/settings', signal ? { signal } : undefined, '读取设置失败')
        if (signal?.aborted) throw new DOMException('Settings request cancelled', 'AbortError')
      set({
        device: data.device ?? 'cpu',
        hfEndpoint: data.hf_endpoint ?? 'https://huggingface.com',
        hfToken: '',
        hfTokenConfigured: data.hf_token_configured ?? false,
        githubMirror: data.github_mirror ?? '',
        defaultDilation: data.default_dilation ?? 10,
        disableNsfw: data.disable_nsfw ?? true,
        downloadMaxConcurrent: data.download_max_concurrent ?? 3,
        upscaleTile: data.upscale_tile ?? 0,  // 0 = 自动计算
      })
    } catch (err) {
      if (!signal?.aborted) console.error('Failed to load settings:', err)
      throw err
    } finally {
      set({ isLoading: false })
    }
  },

  saveSettings: async (backendURL) => {
    const state = get()
    try {
      const payload: Record<string, unknown> = {
          device: state.device,
          hf_endpoint: state.hfEndpoint,
          github_mirror: state.githubMirror,
          default_dilation: state.defaultDilation,
          disable_nsfw: state.disableNsfw,
          download_max_concurrent: state.downloadMaxConcurrent,
          upscale_tile: state.upscaleTile,
      }
      if (state.hfToken.trim()) payload.hf_token = state.hfToken.trim()
      await saveSettingsRequest(createApiClient(backendURL), payload)
      if (state.hfToken.trim()) {
        set({ hfToken: '', hfTokenConfigured: true })
      }
    } catch (err) {
      console.error('Failed to save settings:', err)
      throw err
    }
  },

  clearHfToken: async (backendURL) => {
    try {
      await clearHfTokenRequest(createApiClient(backendURL))
      set({ hfToken: '', hfTokenConfigured: false })
    } catch (err) {
      console.error('Failed to clear HuggingFace token:', err)
      throw err
    }
  },

  loadDefaults: async (backendURL) => {
    try {
      const defaults = await createApiClient(backendURL).json<Record<string, string>>('/api/models/defaults', undefined, '读取默认模型失败')
      set({
        inpaintModel: defaults.watermark_removal ?? '',
        cutoutModel: defaults.cutout ?? 'birefnet',
        defaultsLoaded: true,
      })
    } catch (err) {
      console.error('Failed to load defaults:', err)
      set({ defaultsLoaded: true })  // 使用硬编码兜底值
    }
  },
}))
