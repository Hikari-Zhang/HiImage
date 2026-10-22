import type { ApiClient } from '../apiClient'

export interface RawModeGroup {
  id: string
  name: string
  icon_name: string
  description: string
  default_model: string
  needs_reference: boolean
  reference_label?: string
  reference_hint?: string
  needs_roi: boolean
  needs_prompt: boolean
  prompt_required?: boolean
  prompt_label?: string
  prompt_hint?: string
}

export interface SynthesisModel {
  id: string
  name: string
  tags: string[]
  description: string
  badge: string
}

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

export function createCatalogAPI(client: ApiClient) {
  return {
    getInpaintModels: (signal?: AbortSignal) => client.json<GroupedModels>('/api/models/inpaint', signal ? { signal } : undefined, '读取修复模型失败'),
    getUpscaleModels: (signal?: AbortSignal) => client.json<GroupedModels>('/api/models/upscale', signal ? { signal } : undefined, '读取超分模型失败'),
    getCutoutModels: (signal?: AbortSignal) => client.json<GroupedModels>('/api/cutout/models', signal ? { signal } : undefined, '读取抠图模型失败'),
    getSynthesisModes: (signal?: AbortSignal) => client.json<{ modes: RawModeGroup[] }>('/api/synthesis/modes', signal ? { signal } : undefined, '读取合成模式失败'),
    getSynthesisModels: (signal?: AbortSignal) => client.json<{ models: SynthesisModel[] }>('/api/synthesis/models', signal ? { signal } : undefined, '读取合成模型失败'),
    getPostprocessMethods: (signal?: AbortSignal) => client.json<{
      groups: Array<{ group: string; methods: Array<{ id: string; name: string; description: string }> }>
    }>('/api/postprocess/methods', signal ? { signal } : undefined, '读取后处理方法失败'),
    getDevices: (signal?: AbortSignal) => client.json<Devices>('/api/devices', signal ? { signal } : undefined, '读取计算设备失败'),
  }
}
