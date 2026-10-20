import { useBackendStore } from '../stores/useBackendStore'
import { createApiClient } from '../services/apiClient'

/**
 * Backend API 调用 hook
 */
export function useBackendAPI() {
  const { backendURL } = useBackendStore()
  const client = createApiClient(backendURL)

  /**
   * 水印检测
   */
  const detectWatermark = async (imageBase64: string, sensitivity: number) => {
    const res = await client.fetch('/api/detect', {
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
    const res = await client.fetch('/api/inpaint', {
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
   * 去水印（遮罩模式）
   */
  const inpaintWithMask = async (params: {
    image: string
    mask: string
    model: string
    device: string
    dilation: number
    disable_nsfw: boolean
  }) => {
    const res = await client.fetch('/api/inpaint/with-mask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(params),
    })
    if (!res.ok) {
      try {
        const data = await res.json()
        throw new Error(data.message || data.detail || `遮罩去水印失败 (${res.status})`)
      } catch (jsonErr) {
        if (jsonErr instanceof SyntaxError) {
          throw new Error(`遮罩去水印失败: ${await res.text()}`)
        }
        throw jsonErr
      }
    }
    return res.json() as Promise<{ image: string }>
  }

  /**
   * 超分辨率
   */
  const upscale = async (params: {
    image: string
    model: string
    device: string
    outscale?: number
    tile?: number
  }) => {
    const res = await client.fetch('/api/upscale', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        image: params.image,
        model: params.model,
        device: params.device,
        outscale: params.outscale,
        tile: params.tile,
      }),
    })
    if (!res.ok) {
      const err = await res.text()
      throw new Error(`超分辨率失败: ${err}`)
    }
    return res.json() as Promise<{
      image: string
      width: number
      height: number
    }>
  }

  /**
   * 超分辨率 - 批量处理
   * 支持批量传入 base64 图像或后端可访问的文件路径
   */
  const upscaleBatch = async (params: {
    images: string[]  // base64 数组或文件路径数组
    model: string
    device: string
    outscale?: number
    tile?: number
  }) => {
    const res = await client.fetch('/api/upscale/batch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(params),
    })
    if (!res.ok) {
      const err = await res.text()
      throw new Error(`批量超分辨率失败: ${err}`)
    }
    return res.json() as Promise<{
      results: { index: number; status: 'success' | 'error'; image?: string; width?: number; height?: number; error?: string; fileName?: string }[]
      summary: { total: number; success: number; failed: number }
    }>
  }

  /**
   * 获取模型列表
   */
  const getInpaintModels = async () => {
    const res = await client.fetch('/api/models/inpaint')
    return res.json()
  }

  const getUpscaleModels = async () => {
    const res = await client.fetch('/api/models/upscale')
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
    const res = await client.fetch('/api/pipeline/run', {
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
    const res = await client.fetch('/api/postprocess/methods')
    return res.json()
  }

  /**
   * 获取智能合成模式列表
   */
  const getSynthesisModes = async () => {
    const res = await client.fetch('/api/synthesis/modes')
    return res.json()
  }

  /**
   * 获取智能合成模型列表
   */
  const getSynthesisModels = async () => {
    const res = await client.fetch('/api/synthesis/models')
    return res.json()
  }

  /**
   * 执行智能合成（换背景/换装/换脸/试穿）
   */
  const runSynthesis = async (params: {
    source_image: string
    reference_image?: string
    rois?: number[][]
    mask?: string
    mode: string
    model_id: string
    device: string
    prompt?: string
  }) => {
    const res = await client.fetch('/api/synthesis/run', {
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
    const res = await client.fetch('/api/devices')
    if (!res.ok) return null
    return res.json() as Promise<{
      devices: { id: string; label: string; desc: string; available: boolean; reason?: string; device_count?: number }[]
    }>
  }

  // ── 抠图 API ──────────────────────────────────────────────────────────────

  /**
   * 执行抠图（返回 RGBA 结果 + alpha mask）
   */
  const cutout = async (params: {
    image: string
    model: string
    device: string
  }) => {
    const res = await client.fetch('/api/cutout', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(params),
    })
    if (!res.ok) {
      try {
        const data = await res.json()
        throw new Error(data.message || data.detail || `抠图失败 (${res.status})`)
      } catch (jsonErr) {
        if (jsonErr instanceof SyntaxError) {
          throw new Error(`抠图失败: ${await res.text()}`)
        }
        throw jsonErr
      }
    }
    return res.json() as Promise<{ image: string; mask: string; width: number; height: number }>
  }

  /**
   * 遮罩精细化（羽化/膨胀/腐蚀/画笔编辑）
   */
  const cutoutRefine = async (params: {
    mask: string
    original_image: string
    feather: number
    dilate: number
    brush_add?: string
    brush_remove?: string
  }) => {
    const res = await client.fetch('/api/cutout/refine', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(params),
    })
    if (!res.ok) {
      try {
        const data = await res.json()
        throw new Error(data.message || data.detail || `精细化失败 (${res.status})`)
      } catch (jsonErr) {
        if (jsonErr instanceof SyntaxError) {
          throw new Error(`精细化失败: ${await res.text()}`)
        }
        throw jsonErr
      }
    }
    return res.json() as Promise<{ image: string; mask: string; width: number; height: number }>
  }

  /**
   * 合成到自定义背景色
   */
  const cutoutComposite = async (params: {
    image: string
    background_color: string
  }) => {
    const res = await client.fetch('/api/cutout/composite', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(params),
    })
    if (!res.ok) {
      try {
        const data = await res.json()
        throw new Error(data.message || data.detail || `合成失败 (${res.status})`)
      } catch (jsonErr) {
        if (jsonErr instanceof SyntaxError) {
          throw new Error(`合成失败: ${await res.text()}`)
        }
        throw jsonErr
      }
    }
    return res.json() as Promise<{ image: string; width: number; height: number }>
  }

  /**
   * 获取抠图模型列表
   */
  const getCutoutModels = async () => {
    const res = await client.fetch('/api/cutout/models')
    return res.json()
  }

  return {
    detectWatermark, inpaint, inpaintWithMask, upscale, upscaleBatch, runPipeline,
    getPostprocessMethods, getInpaintModels, getUpscaleModels,
    getSynthesisModes, getSynthesisModels, runSynthesis, getDevices,
    cutout, cutoutRefine, cutoutComposite, getCutoutModels,
  }
}
