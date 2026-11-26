import type { ApiClient } from '../apiClient'

interface GroupedModels {
  groups: Array<{
    label: string
    models: Array<{
      id: string; name: string; description: string; scale?: number; outscale?: number
      badge?: string; supports_custom_outscale?: boolean; recommended_for?: string
      size_mb?: number | null; provider?: string
    }>
  }>
  default_model?: string
}

interface Devices {
  devices: Array<{
    id: string; label: string; desc: string; available: boolean
    reason?: string; device_count?: number
  }>
}

export interface PromptEditStatus {
  available: boolean
  reason: string | null
  model: string | null
  default_model?: string | null
  models?: Array<{
    id: string
    name: string
    available: boolean
    reason: string | null
    supports_mask?: boolean
  }>
}

export function createCatalogAPI(client: ApiClient) {
  return {
    getInpaintModels: (signal?: AbortSignal) => client.json<GroupedModels>('/api/models/inpaint', signal ? { signal } : undefined, '读取修复模型失败'),
    getUpscaleModels: (signal?: AbortSignal) => client.json<GroupedModels>('/api/models/upscale', signal ? { signal } : undefined, '读取超分模型失败'),
    getCutoutModels: (signal?: AbortSignal) => client.json<GroupedModels>('/api/cutout/models', signal ? { signal } : undefined, '读取抠图模型失败'),
    getPromptEditStatus: (signal?: AbortSignal) => client.json<PromptEditStatus>('/api/prompt-edit/status', signal ? { signal } : undefined, '读取图片编辑状态失败'),
    getPostprocessMethods: (signal?: AbortSignal) => client.json<{
      groups: Array<{ group: string; methods: Array<{ id: string; name: string; description: string }> }>
    }>('/api/postprocess/methods', signal ? { signal } : undefined, '读取后处理方法失败'),
    getDevices: (signal?: AbortSignal) => client.json<Devices>('/api/devices', signal ? { signal } : undefined, '读取计算设备失败'),
  }
}
