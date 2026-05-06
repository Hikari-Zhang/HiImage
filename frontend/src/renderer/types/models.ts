/**
 * 模型配置类型定义
 * 与后端 /api/models/* 返回结构对齐
 */

/** 单个模型选项 */
export interface ModelOption {
  /** 模型 ID（传给后端的值） */
  value: string
  /** 显示名称 */
  label: string
  /** 模型描述（tooltip 或说明） */
  description?: string
}

/** 分组模型选项（用于 Select 组件的 groups prop） */
export interface ModelGroup {
  label: string
  options: ModelOption[]
}

/** 后端 /api/models/inpaint 返回结构 */
export interface InpaintModelsResponse {
  groups: Array<{
    label: string
    models: Array<{
      id: string
      name: string
      description: string
    }>
  }>
}

/** 后端 /api/models/upscale 返回结构 */
export interface UpscaleModelsResponse {
  groups: Array<{
    label: string
    models: Array<{
      id: string
      name: string
      description: string
      scale: number
    }>
  }>
}
