import { create } from 'zustand'
import type { ModelGroup } from '../types/models'

/**
 * 默认模型配置 — 后端不可用时的 fallback
 * 
 * 注意：value 必须使用 models.yaml 中的内部 ID（如 'wm_lama'），
 * 而不是 iopaint_model_id（如 'lama'）
 */
const DEFAULT_INPAINT_GROUPS: ModelGroup[] = [
  {
    label: '快速修复（本地推理）',
    options: [
      { value: 'wm_lama', label: 'LaMa（推荐·通用）', description: '综合最佳首选：速度快、质量好' },
      { value: 'wm_migan', label: 'MiGAN（GAN·快速）', description: '基于GAN，速度快' },
      { value: 'wm_zits', label: 'ZITS（边缘感知）', description: '边缘过渡自然' },
      { value: 'wm_fcf', label: 'FCF（快速填充）', description: '背景简单时效果佳' },
      { value: 'wm_mat', label: 'MAT（精细修复）', description: '质量最高但速度较慢' },
      { value: 'wm_ldm', label: 'LDM（轻量扩散）', description: '质量与速度均衡' },
      { value: 'wm_manga', label: 'Manga（漫画专用）', description: '针对漫画/线稿优化' },
      { value: 'wm_cv2', label: 'CV2（传统算法）', description: '速度最快，质量有限' },
    ],
  },
  {
    label: '专用模型（首次使用自动下载）',
    options: [
      { value: 'wm_anytext', label: 'AnyText（文字水印专用）', description: '文字类水印效果显著' },
      { value: 'wm_powerpaint_v1', label: 'PowerPaint V1（最强通用）', description: '支持文字引导，综合效果最强' },
    ],
  },
  {
    label: '扩散模型 SD 1.5（高质量·首次下载较大）',
    options: [
      { value: 'wm_sd15', label: 'SD 1.5 Inpainting（通用）', description: '复杂/渐变背景效果自然' },
      { value: 'wm_realistic_vision', label: 'Realistic Vision V5.1（写实照片）', description: '写实照片真实感最强' },
      { value: 'wm_dreamshaper', label: 'DreamShaper（艺术风格）', description: '艺术感强，人像/插画自然' },
      { value: 'wm_anything_v4', label: 'Anything V4（动漫/插画）', description: '二次元/动漫/CG 插图专用' },
    ],
  },
  {
    label: '扩散模型 SDXL（超高质量）',
    options: [
      { value: 'wm_sdxl', label: 'SDXL Inpainting（高分辨率）', description: '2K+ 图像细节最佳' },
    ],
  },
]

const DEFAULT_UPSCALE_GROUPS: ModelGroup[] = [
  {
    label: '通用超分辨率',
    options: [
      { value: 'RealESRGAN_x4plus', label: '4x 通用照片（推荐）', description: '通用场景，综合细节恢复最佳' },
      { value: 'RealESRGAN_x2plus', label: '2x 通用照片', description: '放大倍率适中，速度更快' },
    ],
  },
  {
    label: '精细化增强（去噪/去模糊）',
    options: [
      { value: 'realesr-general-x4v3', label: '4x 精细化增强·轻量（推荐）', description: '去噪+去模糊+放大，低质量/压缩图首选' },
      { value: 'RealESRNet_x4plus', label: '4x 自然细化（无 GAN）', description: '色彩还原自然，无过度锐化伪影' },
    ],
  },
  {
    label: '动漫/插画',
    options: [
      { value: 'RealESRGAN_x4plus_anime_6B', label: '4x 动漫/插画（静图）', description: '动漫线稿专用，线条锐利' },
      { value: 'realesr-animevideov3', label: '4x 动漫视频/帧序列', description: '视频逐帧专用，时间一致性更好' },
    ],
  },
]

const DEFAULT_CUTOUT_GROUPS: ModelGroup[] = [
  {
    label: '推荐模型',
    options: [
      { value: 'birefnet', label: 'BiRefNet-General', description: '2024 SOTA 抠图，边缘细节极佳' },
    ],
  },
  {
    label: '其他模型',
    options: [
      { value: 'rmbg', label: 'RMBG 2.0', description: '商业级抠图精度，人像/产品均优秀' },
      { value: 'isnet', label: 'IS-Net General', description: '产品图/商品摄影效果极佳' },
      { value: 'u2net', label: 'U2-Net', description: '通用显著目标检测经典模型' },
    ],
  },
  {
    label: '动漫专用',
    options: [
      { value: 'isnet_anime', label: 'IS-Net Anime', description: '针对动漫/插画优化' },
    ],
  },
  {
    label: '快速模型',
    options: [
      { value: 'modnet', label: 'U2-Net 人像分割（快速）', description: '轻量人像抠图，速度极快' },
    ],
  },
]

const DEFAULT_DENOISE_GROUPS: ModelGroup[] = [
  {
    label: '照片 / 截图',
    options: [
      { value: 'denoise-scunet-psnr', label: 'SCUNet 盲去噪（推荐）', description: '盲去噪，适合照片与截图', suitableFor: '盲去噪（压缩颗粒 + 一般照片噪声，保纹理）' },
    ],
  },
  {
    label: '观感更干净',
    options: [
      { value: 'denoise-scunet-gan', label: 'SCUNet 观感去噪', description: '更干净，可能略抹纹理', suitableFor: '更干净，可能略抹纹理' },
    ],
  },
  {
    label: '相机 ISO',
    options: [
      { value: 'denoise-nafnet-sidd', label: 'NAFNet 相机降噪', description: '手机 / 相机传感器噪声', suitableFor: '手机 / 相机传感器噪声' },
    ],
  },
  {
    label: '去压缩伪影',
    options: [
      { value: 'denoise-swinir-jpeg', label: 'SwinIR 去压缩', description: 'JPEG 色块与振铃', suitableFor: 'JPEG 色块 / 振铃（按常见网图质量 Q≈40）' },
    ],
  },
  {
    label: '快速清晰化',
    options: [
      { value: 'denoise-general', label: '快速清晰化·通用', description: '超分权重复用，偏锐化', suitableFor: '要锐化或不想再下大模型' },
      { value: 'denoise-anime', label: '快速清晰化·动漫', description: '超分权重复用，线条更锐', suitableFor: '动漫线稿锐化' },
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
  /** 降噪模型分组列表 */
  denoiseGroups: ModelGroup[]
  /**
   * 每个超分辨率模型的元数据（model_id → meta）
   *   defaultOutscale:         默认输出倍率
   *   supportsCustomOutscale:  是否支持自定义输出倍率（隐藏倍率选项用）
   */
  upscaleModelMeta: Record<string, { defaultOutscale: number; supportsCustomOutscale: boolean }>
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
  denoiseGroups: DEFAULT_DENOISE_GROUPS,
  upscaleModelMeta: {},
  isLoaded: false,
  isLoading: false,
  error: null,

  loadModels: async (backendURL: string) => {
    set({ isLoading: true, error: null })
    try {
      const [inpaintRes, upscaleRes, cutoutRes, denoiseRes] = await Promise.all([
        fetch(`${backendURL}/api/models/inpaint`),
        fetch(`${backendURL}/api/models/upscale`),
        fetch(`${backendURL}/api/cutout/models`),
        fetch(`${backendURL}/api/models/denoise`),
      ])

      if (!inpaintRes.ok || !upscaleRes.ok) {
        throw new Error('模型列表接口返回错误')
      }

      const inpaintData = await inpaintRes.json()
      const upscaleData = await upscaleRes.json()
      const cutoutData = cutoutRes.ok ? await cutoutRes.json() : null

      // 转换后端数据为前端格式（统一去除 ── 装饰符）
      const stripDecorators = (s: string) => s.replace(/^──\s*|\s*──$/g, '').trim()

      const inpaintGroups: ModelGroup[] = inpaintData.groups.map(
        (g: { label: string; models: Array<{ id: string; name: string; description: string }> }) => ({
          label: stripDecorators(g.label),
          options: g.models.map((m) => ({
            value: m.id,
            label: m.name,
            description: m.description,
          })),
        })
      )

      const upscaleModelMeta: Record<string, { defaultOutscale: number; supportsCustomOutscale: boolean }> = {}
      const upscaleGroups: ModelGroup[] = upscaleData.groups.map(
        (g: { label: string; models: Array<{ id: string; name: string; description: string; scale: number; outscale: number; supports_custom_outscale: boolean }> }) => ({
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
        })
      )

      // 抠图模型
      let cutoutGroups: ModelGroup[] = DEFAULT_CUTOUT_GROUPS
      if (cutoutData?.groups) {
        cutoutGroups = cutoutData.groups.map(
          (g: { label: string; models: Array<{ id: string; name: string; description: string }> }) => ({
            label: stripDecorators(g.label),
            options: g.models.map((m) => ({
              value: m.id,
              label: m.name,
              description: m.description,
            })),
          })
        )
      }

      const denoiseData = denoiseRes.ok ? await denoiseRes.json() : null
      let denoiseGroups: ModelGroup[] = DEFAULT_DENOISE_GROUPS
      if (denoiseData?.groups) {
        denoiseGroups = denoiseData.groups.map(
          (g: { label: string; models: Array<{ id: string; name: string; description: string; suitable_for?: string }> }) => ({
            label: stripDecorators(g.label),
            options: g.models.map((m) => ({
              value: m.id,
              label: m.name,
              description: m.description,
              suitableFor: m.suitable_for,
            })),
          })
        )
      }

      set({ inpaintGroups, upscaleGroups, cutoutGroups, denoiseGroups, upscaleModelMeta, isLoaded: true, isLoading: false })
    } catch (err: any) {
      console.warn('[ModelStore] 加载模型列表失败，使用默认配置:', err.message)
      // 失败时保留默认值
      set({ isLoaded: true, isLoading: false, error: err.message })
    }
  },
}))
