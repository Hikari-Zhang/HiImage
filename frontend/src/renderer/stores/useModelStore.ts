import { create } from 'zustand'
import type { ModelGroup, ModelOption } from '../types/models'

/**
 * 默认模型配置 — 后端不可用时的 fallback
 */
const DEFAULT_INPAINT_GROUPS: ModelGroup[] = [
  {
    label: '快速修复（本地推理）',
    options: [
      { value: 'lama', label: 'LaMa（推荐·通用）', description: '综合最佳首选：速度快、质量好' },
      { value: 'migan', label: 'MiGAN（GAN·快速）', description: '基于GAN，速度快' },
      { value: 'zits', label: 'ZITS（边缘感知）', description: '边缘过渡自然' },
      { value: 'fcf', label: 'FCF（快速填充）', description: '背景简单时效果佳' },
      { value: 'mat', label: 'MAT（精细修复）', description: '质量最高但速度较慢' },
      { value: 'ldm', label: 'LDM（轻量扩散）', description: '质量与速度均衡' },
      { value: 'manga', label: 'Manga（漫画专用）', description: '针对漫画/线稿优化' },
      { value: 'cv2', label: 'CV2（传统算法）', description: '速度最快，质量有限' },
    ],
  },
  {
    label: '专用模型（首次使用自动下载）',
    options: [
      { value: 'Sanster/AnyText', label: 'AnyText（文字水印专用）', description: '文字类水印效果显著' },
    ],
  },
  {
    label: '扩散模型（高质量·首次下载较大）',
    options: [
      { value: 'runwayml/stable-diffusion-inpainting', label: 'SD Inpainting（复杂背景）', description: '复杂/渐变背景效果自然' },
      { value: 'andregn/Realistic_Vision_V3.0-inpainting', label: 'Realistic Vision（写实照片）', description: '写实照片真实感最强' },
      { value: 'JunhaoZhuang/PowerPaint-v2-1', label: 'PowerPaintV2（最强通用）', description: '综合效果最强' },
      { value: 'diffusers/stable-diffusion-xl-1.0-inpainting-0.1', label: 'SDXL Inpainting（高分辨率）', description: '2K+ 图像细节最佳' },
    ],
  },
]

const DEFAULT_UPSCALE_OPTIONS: ModelOption[] = [
  { value: 'RealESRGAN_x4plus', label: '4x 通用照片（推荐）', description: '通用场景，细节恢复最佳' },
  { value: 'RealESRGAN_x4plus_anime_6B', label: '4x 动漫/插画', description: '动漫线稿专用，线条更锐利' },
  { value: 'RealESRGAN_x2plus', label: '2x 通用照片', description: '放大倍率适中，速度更快' },
]

interface ModelStoreState {
  /** 修复模型分组列表 */
  inpaintGroups: ModelGroup[]
  /** 超分辨率模型扁平列表 */
  upscaleOptions: ModelOption[]
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
  upscaleOptions: DEFAULT_UPSCALE_OPTIONS,
  isLoaded: false,
  isLoading: false,
  error: null,

  loadModels: async (backendURL: string) => {
    set({ isLoading: true, error: null })
    try {
      const [inpaintRes, upscaleRes] = await Promise.all([
        fetch(`${backendURL}/api/models/inpaint`),
        fetch(`${backendURL}/api/models/upscale`),
      ])

      if (!inpaintRes.ok || !upscaleRes.ok) {
        throw new Error('模型列表接口返回错误')
      }

      const inpaintData = await inpaintRes.json()
      const upscaleData = await upscaleRes.json()

      // 转换后端数据为前端格式
      const inpaintGroups: ModelGroup[] = inpaintData.groups.map(
        (g: { label: string; models: Array<{ id: string; name: string; description: string }> }) => ({
          label: g.label.replace(/^──\s*|\s*──$/g, '').trim(),
          options: g.models.map((m) => ({
            value: m.id,
            label: m.name,
            description: m.description,
          })),
        })
      )

      const upscaleOptions: ModelOption[] = upscaleData.models.map(
        (m: { id: string; name: string; description: string; scale: number }) => ({
          value: m.id,
          label: m.name,
          description: m.description,
        })
      )

      set({ inpaintGroups, upscaleOptions, isLoaded: true, isLoading: false })
    } catch (err: any) {
      console.warn('[ModelStore] 加载模型列表失败，使用默认配置:', err.message)
      // 失败时保留默认值
      set({ isLoaded: true, isLoading: false, error: err.message })
    }
  },
}))
