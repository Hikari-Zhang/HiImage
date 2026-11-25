export const EDITOR_SESSION_IDS = ['watermark', 'upscale', 'upscale-batch', 'cutout', 'prompt-edit'] as const
export type EditorSessionId = typeof EDITOR_SESSION_IDS[number]
