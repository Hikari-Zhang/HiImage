import { create } from 'zustand'
import type { EditorSessionId } from '../types/editor'
import { useSettingsStore } from './useSettingsStore'

function createEditorSettings() {
  // Global settings seed a new editor; editing one session never rewrites another.
  const defaults = useSettingsStore.getState()
  return create<{
    device: string
    inpaintModel: string
    inpaintDilationOverrides: Record<string, number | undefined>
    sensitivityOverride: number | undefined
    disableNsfw: boolean
    postprocessMethod: string
    postprocessEnabled: boolean
    upscaleModel: string
    upscaleEnabled: boolean
    upscaleTile: number
    cutoutModel: string
    outscale: number | null
    tile: number | null
    setDevice: (value: string) => void
    setInpaintModel: (value: string) => void
    setInpaintDilation: (modelId: string, value: number | undefined) => void
    setSensitivity: (value: number | undefined) => void
    setPostprocessMethod: (value: string) => void
    setPostprocessEnabled: (value: boolean) => void
    setUpscaleModel: (value: string) => void
    setUpscaleEnabled: (value: boolean) => void
    setCutoutModel: (value: string) => void
    setOutscale: (value: number | null) => void
    setTile: (value: number | null) => void
  }>((set) => ({
    device: defaults.device,
    inpaintModel: defaults.inpaintModel,
    inpaintDilationOverrides: {},
    sensitivityOverride: undefined,
    disableNsfw: defaults.disableNsfw,
    postprocessMethod: defaults.postprocessMethod,
    postprocessEnabled: defaults.postprocessEnabled,
    upscaleModel: defaults.upscaleModel,
    upscaleEnabled: defaults.upscaleEnabled,
    upscaleTile: defaults.upscaleTile,
    cutoutModel: defaults.cutoutModel,
    outscale: null,
    tile: null,
    setDevice: (device) => set({ device }),
    setInpaintModel: (inpaintModel) => set({ inpaintModel }),
    setInpaintDilation: (modelId, dilation) => set(({ inpaintDilationOverrides }) => ({
      inpaintDilationOverrides: { ...inpaintDilationOverrides, [modelId]: dilation },
    })),
    setSensitivity: (sensitivityOverride) => set({ sensitivityOverride }),
    setPostprocessMethod: (postprocessMethod) => set({ postprocessMethod }),
    setPostprocessEnabled: (postprocessEnabled) => set({ postprocessEnabled }),
    setUpscaleModel: (upscaleModel) => set({ upscaleModel }),
    setUpscaleEnabled: (upscaleEnabled) => set({ upscaleEnabled }),
    setCutoutModel: (cutoutModel) => set({ cutoutModel }),
    setOutscale: (outscale) => set({ outscale }),
    setTile: (tile) => set({ tile }),
  }))
}

const editors = new Map<EditorSessionId, ReturnType<typeof createEditorSettings>>()

export function getEditorSettingsStore(sessionId: EditorSessionId) {
  const id = sessionId === 'upscale-batch' ? 'upscale' : sessionId
  let store = editors.get(id)
  if (!store) {
    store = createEditorSettings()
    editors.set(id, store)
  }
  return store
}

export function useEditorSettings(sessionId: EditorSessionId) {
  return getEditorSettingsStore(sessionId)()
}
