import { create } from 'zustand'
import type { EditorSessionId } from '../types/editor'
import { useSettingsStore } from './useSettingsStore'

function createEditorSettings() {
  // Global settings seed a new editor; editing one session never rewrites another.
  const defaults = useSettingsStore.getState()
  return create<{
    device: string
    inpaintModel: string
    defaultDilation: number
    sensitivity: number
    disableNsfw: boolean
    postprocessMethod: string
    postprocessEnabled: boolean
    upscaleModel: string
    upscaleEnabled: boolean
    upscaleTile: number
    cutoutModel: string
    outscale: number | null
    tile: number | null
    synthesisMode: string
    synthesisModel: string
    prompt: string
    referenceImage: string | null
    referenceFileName: string
    setDevice: (value: string) => void
    setInpaintModel: (value: string) => void
    setDilation: (value: number) => void
    setSensitivity: (value: number) => void
    setPostprocessMethod: (value: string) => void
    setPostprocessEnabled: (value: boolean) => void
    setUpscaleModel: (value: string) => void
    setUpscaleEnabled: (value: boolean) => void
    setCutoutModel: (value: string) => void
    setOutscale: (value: number | null) => void
    setTile: (value: number | null) => void
    setSynthesisMode: (value: string) => void
    setSynthesisModel: (value: string) => void
    setPrompt: (value: string) => void
    setReferenceImage: (value: string | null) => void
    setReferenceFileName: (value: string) => void
  }>((set) => ({
    device: defaults.device,
    inpaintModel: defaults.inpaintModel,
    defaultDilation: defaults.defaultDilation,
    sensitivity: defaults.sensitivity,
    disableNsfw: defaults.disableNsfw,
    postprocessMethod: defaults.postprocessMethod,
    postprocessEnabled: defaults.postprocessEnabled,
    upscaleModel: defaults.upscaleModel,
    upscaleEnabled: defaults.upscaleEnabled,
    upscaleTile: defaults.upscaleTile,
    cutoutModel: defaults.cutoutModel,
    outscale: null,
    tile: null,
    synthesisMode: '',
    synthesisModel: '',
    prompt: '',
    referenceImage: null,
    referenceFileName: '',
    setDevice: (device) => set({ device }),
    setInpaintModel: (inpaintModel) => set({ inpaintModel }),
    setDilation: (defaultDilation) => set({ defaultDilation }),
    setSensitivity: (sensitivity) => set({ sensitivity }),
    setPostprocessMethod: (postprocessMethod) => set({ postprocessMethod }),
    setPostprocessEnabled: (postprocessEnabled) => set({ postprocessEnabled }),
    setUpscaleModel: (upscaleModel) => set({ upscaleModel }),
    setUpscaleEnabled: (upscaleEnabled) => set({ upscaleEnabled }),
    setCutoutModel: (cutoutModel) => set({ cutoutModel }),
    setOutscale: (outscale) => set({ outscale }),
    setTile: (tile) => set({ tile }),
    setSynthesisMode: (synthesisMode) => set({ synthesisMode }),
    setSynthesisModel: (synthesisModel) => set({ synthesisModel }),
    setPrompt: (prompt) => set({ prompt }),
    setReferenceImage: (referenceImage) => set({ referenceImage }),
    setReferenceFileName: (referenceFileName) => set({ referenceFileName }),
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
