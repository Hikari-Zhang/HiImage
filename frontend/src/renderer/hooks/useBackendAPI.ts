import { useBackendStore } from '../stores/useBackendStore'

/**
 * Backend API 调用 hook
 */
export function useBackendAPI() {
  const { backendURL } = useBackendStore()

  const getURL = () => {
    // Prefer Electron IPC if available
    if (window.electronAPI) {
      return window.electronAPI.getBackendURL()
    }
    return Promise.resolve(backendURL)
  }

  /**
   * 水印检测
   */
  const detectWatermark = async (imageBase64: string, sensitivity: number) => {
    const url = await getURL()
    const res = await fetch(`${url}/api/detect`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ image: imageBase64, sensitivity }),
    })
    if (!res.ok) throw new Error(`检测失败: ${res.statusText}`)
    return res.json() as Promise<{ regions: number[][] }>
  }

  /**
   * 去水印（ROI 模式）
   */
  const inpaint = async (params: {
    image: string
    rois: number[][]
    model: string
    device: string
    dilation: number
    disable_nsfw: boolean
  }) => {
    const url = await getURL()
    const res = await fetch(`${url}/api/inpaint`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(params),
    })
    if (!res.ok) {
      // 尝试从 JSON body 提取友好错误信息
      try {
        const data = await res.json()
        throw new Error(data.message || data.detail || `去水印失败 (${res.status})`)
      } catch (jsonErr) {
        if (jsonErr instanceof SyntaxError) {
          throw new Error(`去水印失败: ${await res.text()}`)
        }
        throw jsonErr
      }
    }
    return res.json() as Promise<{ image: string }>
  }

  /**
   * 超分辨率
   */
  const upscale = async (params: { image: string; model: string; device: string; outscale?: number }) => {
    const url = await getURL()
    const res = await fetch(`${url}/api/upscale`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(params),
    })
    if (!res.ok) {
      const err = await res.text()
      throw new Error(`超分辨率失败: ${err}`)
    }
    return res.json() as Promise<{ image: string; width: number; height: number }>
  }

  /**
   * 获取模型列表
   */
  const getInpaintModels = async () => {
    const url = await getURL()
    const res = await fetch(`${url}/api/models/inpaint`)
    return res.json()
  }

  const getUpscaleModels = async () => {
    const url = await getURL()
    const res = await fetch(`${url}/api/models/upscale`)
    return res.json()
  }

  /**
   * 完整 Pipeline：inpaint → postprocess → upscale
   */
  const runPipeline = async (params: {
    image: string
    rois?: number[][]
    mask?: string
    inpaint_model: string
    device: string
    dilation: number
    disable_nsfw: boolean
    postprocess_method: string
    postprocess_enabled: boolean
    upscale_enabled: boolean
    upscale_model: string
  }) => {
    const url = await getURL()
    const res = await fetch(`${url}/api/pipeline/run`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(params),
    })
    if (!res.ok) {
      try {
        const data = await res.json()
        throw new Error(data.message || data.detail || `Pipeline 失败 (${res.status})`)
      } catch (jsonErr) {
        if (jsonErr instanceof SyntaxError) {
          throw new Error(`Pipeline 失败: ${await res.text()}`)
        }
        throw jsonErr
      }
    }
    return res.json() as Promise<{ image: string; width: number; height: number }>
  }

  /**
   * 获取后处理方法列表
   */
  const getPostprocessMethods = async () => {
    const url = await getURL()
    const res = await fetch(`${url}/api/postprocess/methods`)
    return res.json()
  }

  /**
   * 获取智能合成模式列表
   */
  const getSynthesisModes = async () => {
    const url = await getURL()
    const res = await fetch(`${url}/api/synthesis/modes`)
    return res.json()
  }

  /**
   * 获取智能合成模型列表
   */
  const getSynthesisModels = async () => {
    const url = await getURL()
    const res = await fetch(`${url}/api/synthesis/models`)
    return res.json()
  }

  /**
   * 执行智能合成（换背景/换装/换脸/试穿）
   */
  const runSynthesis = async (params: {
    source_image: string
    reference_image?: string
    rois?: number[][]
    mode: string
    model_id: string
    device: string
    prompt?: string
  }) => {
    const url = await getURL()
    const res = await fetch(`${url}/api/synthesis/run`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(params),
    })
    if (!res.ok) {
      try {
        const data = await res.json()
        throw new Error(data.message || data.detail || `智能合成失败 (${res.status})`)
      } catch (jsonErr) {
        if (jsonErr instanceof SyntaxError) {
          throw new Error(`智能合成失败: ${await res.text()}`)
        }
        throw jsonErr
      }
    }
    return res.json() as Promise<{ image: string; width: number; height: number }>
  }

  /**
   * 获取计算设备可用性
   */
  const getDevices = async () => {
    const url = await getURL()
    const res = await fetch(`${url}/api/devices`)
    if (!res.ok) return null
    return res.json() as Promise<{
      devices: { id: string; label: string; desc: string; available: boolean; reason?: string; device_count?: number }[]
    }>
  }

  return {
    detectWatermark, inpaint, upscale, runPipeline,
    getPostprocessMethods, getInpaintModels, getUpscaleModels,
    getSynthesisModes, getSynthesisModels, runSynthesis, getDevices,
  }
}
