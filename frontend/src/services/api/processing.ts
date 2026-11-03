import type { ApiClient } from '../apiClient'

export interface ImageResult { image: string; width: number; height: number }
export interface MaskResult extends ImageResult { mask: string }
export interface RedrawOptions {
  prompt?: string
  seed?: number
  num_inference_steps?: number
  guidance_scale?: number
}

export interface WatermarkCandidate {
  region: number[]
  polygon: number[][] | null
  text: string
  text_confidence: number | null
  watermark_score: number | null
  reasons: string[]
}
export interface WatermarkDetection {
  regions: number[][]
  candidates?: WatermarkCandidate[]
  engine?: string
  warning?: string | null
}

export function createProcessingAPI(client: ApiClient) {
  const detectWatermark = (imageBase64: string, sensitivity: number, taskId: string) => client.json<WatermarkDetection>('/api/detect', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ image: imageBase64, sensitivity, task_id: taskId }),
  }, '检测失败')

  const inpaint = (params: {
    image: string
    rois: number[][]
    model: string
    device: string
    dilation: number
    disable_nsfw: boolean
    task_id: string
  }) => client.json<{ image: string }>('/api/inpaint', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  }, '去水印失败')

  const inpaintWithMask = (params: {
    image: string
    mask: string
    model: string
    device: string
    dilation: number
    disable_nsfw: boolean
    task_id: string
    redraw?: RedrawOptions
  }) => client.json<{ image: string }>('/api/inpaint/with-mask', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  }, '遮罩去水印失败')

  const upscale = (params: {
    image: string
    model: string
    device: string
    outscale?: number
    tile?: number
    task_id: string
  }) => client.json<ImageResult>('/api/upscale', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  }, '超分辨率失败')

  const runPipeline = (params: {
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
    task_id: string
    redraw?: RedrawOptions
  }) => client.json<ImageResult>('/api/pipeline/run', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  }, 'Pipeline 失败')

  const runSynthesis = (params: {
    source_image: string
    reference_image?: string
    rois?: number[][]
    mask?: string
    mode: string
    model_id: string
    device: string
    prompt?: string
    task_id: string
  }) => client.json<ImageResult>('/api/synthesis/run', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  }, '智能合成失败')

  const cutout = (params: {
    image: string
    model: string
    device: string
    task_id: string
  }) => client.json<MaskResult>('/api/cutout', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  }, '抠图失败')

  const cutoutRefine = (params: {
    mask: string
    original_image: string
    feather: number
    dilate: number
    brush_add?: string
    brush_remove?: string
    task_id: string
  }) => client.json<MaskResult>('/api/cutout/refine', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  }, '精细化失败')

  const cutoutComposite = (params: {
    image: string
    background_color: string
    task_id: string
  }) => client.json<ImageResult>('/api/cutout/composite', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  }, '合成失败')

  return { detectWatermark, inpaint, inpaintWithMask, upscale, runPipeline, runSynthesis, cutout, cutoutRefine, cutoutComposite }
}
