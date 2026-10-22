import { create } from 'zustand'
import { useSettingsStore } from './useSettingsStore'

function savedFields() {
  const saved = useSettingsStore.getState()
  return {
    device: saved.device,
    port: String(saved.serverPort),
    keepalive: String(saved.keepaliveSeconds),
    startupTimeout: String(saved.startupTimeout),
    hfEndpoint: saved.hfEndpoint,
    hfToken: '',
    githubMirror: saved.githubMirror,
    dilation: String(saved.defaultDilation),
    disableNsfw: saved.disableNsfw,
    lowMem: saved.lowMem,
    cpuOffload: saved.cpuOffload,
    cpuTextencoder: saved.cpuTextencoder,
    downloadMaxConcurrent: String(saved.downloadMaxConcurrent),
    upscaleTile: String(saved.upscaleTile),
  }
}

type Fields = ReturnType<typeof savedFields> & {
  activeTab: 'general' | 'models'
  isSaving: boolean
  settingsLoadError: string
}
type Setters = { [Key in keyof Fields as `set${Capitalize<Key>}`]: (value: Fields[Key]) => void }
type DraftState = Fields & Setters & {
  dirty: boolean
  revision: number
  syncFromSettings: () => void
  markSaved: (revision: number) => void
  reset: () => void
}

function initialFields(): Fields {
  return { ...savedFields(), activeTab: 'general', isSaving: false, settingsLoadError: '' }
}

function createDraftStore() {
  return create<DraftState>((set, get) => {
    const values = initialFields()
    const setters = Object.fromEntries(Object.keys(values).map((key) => [
      `set${key[0].toUpperCase()}${key.slice(1)}`,
      (value: Fields[keyof Fields]) => set((state) => ({
        [key]: value,
        ...(['activeTab', 'isSaving', 'settingsLoadError'].includes(key)
          ? {} : { dirty: true, revision: state.revision + 1 }),
      })),
    ])) as Setters
    return {
      ...values, ...setters, dirty: false, revision: 0,
      syncFromSettings: () => {
        if (!get().dirty && !get().isSaving) set(savedFields())
      },
      markSaved: (revision) => {
        if (get().revision === revision) set({ dirty: false, hfToken: '' })
      },
      reset: () => set({ ...initialFields(), dirty: false, revision: get().revision + 1 }),
    }
  })
}

// Memory only. Each backend has its own draft; no token is written to browser storage.
const drafts = new Map<string, ReturnType<typeof createDraftStore>>()

export function getSettingsDraftStore(backendURL: string) {
  let store = drafts.get(backendURL)
  if (!store) {
    store = createDraftStore()
    drafts.set(backendURL, store)
  }
  return store
}

export function useSettingsDraft(backendURL: string) {
  return getSettingsDraftStore(backendURL)()
}
