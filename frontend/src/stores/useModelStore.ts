import { create } from 'zustand'
import type { ModelGroup } from '../types/models'
import { createApiClient } from '../services/apiClient'

/**
 * 最小化 fallback — 仅在后端完全不可达时使用。
 * 完整模型列表由后端 /api/models/groups 动态返回。
 */
const DEFAULT_INPAINT_GROUPS: ModelGroup[] = []

const DEFAULT_UPSCALE_GROUPS: ModelGroup[] = [
  {
    label: '通用超分辨率',
    options: [
      { value: 'RealESRGAN_x4plus', label: '4x 通用照片（推荐）', description: '后端离线，仅显示默认模型' },
    ],
  },
]

const DEFAULT_CUTOUT_GROUPS: ModelGroup[] = [
  {
    label: '推荐模型',
    options: [
      { value: 'birefnet', label: 'BiRefNet-General', description: '后端离线，仅显示默认模型' },
    ],
  },
]

interface ModelStoreState {
  /** 修复模型分组列表 */
  inpaintGroups: ModelGroup[]
  /** 超分辨率模型分组列表 */
  upscaleGroups: ModelGroup[]
  /** 抠图模型分组列表 */
  cutoutGroups: ModelGroup[]
  /**
   * 每个超分辨率模型的元数据（model_id → meta）
   *   defaultOutscale:         默认输出倍率
   *   supportsCustomOutscale:  是否支持自定义输出倍率（隐藏倍率选项用）
   */
  upscaleModelMeta: Record<string, { defaultOutscale: number; supportsCustomOutscale: boolean }>
  inpaintDeviceOverrides: Record<string, string>
  inpaintPipelines: Record<string, string>
  defaultInpaintModel: string
  /** 是否已加载完成 */
  isLoaded: boolean
  /** 加载中 */
  isLoading: boolean
  /** 加载错误信息 */
  error: string | null

  /** 从后端加载模型配置 */
  loadModels: (backendURL: string) => Promise<void>
}

export const useModelStore = create<ModelStoreState>((set) => ({
  inpaintGroups: DEFAULT_INPAINT_GROUPS,
  upscaleGroups: DEFAULT_UPSCALE_GROUPS,
  cutoutGroups: DEFAULT_CUTOUT_GROUPS,
  upscaleModelMeta: {},
  inpaintDeviceOverrides: {},
  inpaintPipelines: {},
  defaultInpaintModel: '',
  isLoaded: false,
  isLoading: false,
  error: null,

  loadModels: async (backendURL: string) => {
    set({ isLoading: true, error: null })
    try {
      const data = await createApiClient(backendURL).json<{
        inpaint: { default_model?: string; groups: Array<{ label: string; models: Array<{ id: string; name: string; description: string; badge?: string; device_override?: string; pipeline?: string }> }> }
        upscale: { groups: Array<{ label: string; models: Array<{ id: string; name: string; description: string; scale: number; outscale: number; badge?: string; supports_custom_outscale: boolean }> }> }
        cutout: { groups: Array<{ label: string; models: Array<{ id: string; name: string; description: string; recommended_for?: string; size_mb?: number; badge?: string; provider?: string }> }>; default_model: string }
      }>('/api/models/groups', undefined, '读取模型列表失败')

      // 转换后端数据为前端格式（统一去除 ── 装饰符）
      const stripDecorators = (s: string) => s.replace(/^──\s*|\s*──$/g, '').trim()

      const inpaintGroups: ModelGroup[] = data.inpaint.groups.map((g) => ({
        label: stripDecorators(g.label),
        options: g.models.map((m) => ({
          value: m.id,
          label: m.name,
          description: m.description,
        })),
      }))

      const upscaleModelMeta: Record<string, { defaultOutscale: number; supportsCustomOutscale: boolean }> = {}
      const upscaleGroups: ModelGroup[] = data.upscale.groups.map((g) => ({
        label: stripDecorators(g.label),
        options: g.models.map((m) => {
          upscaleModelMeta[m.id] = {
            defaultOutscale: m.outscale ?? m.scale ?? 4,
            supportsCustomOutscale: m.supports_custom_outscale ?? false,
          }
          return {
            value: m.id,
            label: m.name,
            description: m.description,
          }
        }),
      }))

      const cutoutGroups: ModelGroup[] = data.cutout.groups.map((g) => ({
        label: stripDecorators(g.label),
        options: g.models.map((m) => ({
          value: m.id,
          label: m.name,
          description: m.description,
        })),
      }))

      const inpaintDeviceOverrides = Object.fromEntries(data.inpaint.groups.flatMap(g =>
        g.models.filter(m => m.device_override).map(m => [m.id, m.device_override!])))
      const inpaintPipelines = Object.fromEntries(data.inpaint.groups.flatMap(g =>
        g.models.flatMap(m => m.pipeline ? [[m.id, m.pipeline]] : [])))
      set({ inpaintGroups, upscaleGroups, cutoutGroups, upscaleModelMeta, inpaintDeviceOverrides, inpaintPipelines, defaultInpaintModel: data.inpaint.default_model ?? '', isLoaded: true, isLoading: false })
    } catch (err: any) {
      console.warn('[ModelStore] 加载模型列表失败，使用默认配置:', err.message)
      // 失败时保留默认值
      set({ isLoaded: true, isLoading: false, error: err.message })
    }
  },
}))
