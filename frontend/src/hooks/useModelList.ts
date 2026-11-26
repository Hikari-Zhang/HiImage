import { useCallback, useSyncExternalStore } from 'react'
import { ApiPath, ModelStatus, type ModelStatusValue } from '../constants'
import { createApiClient } from '../services/apiClient'
import { useBackendStore } from '../stores/useBackendStore'
import { useDownloadStore } from '../stores/useDownloadStore'

export interface ModelEntry {
  id: string
  name: string
  provider: string
  tags: string[]
  description?: string
  badge?: string
  size_mb?: number
  hf_model_id?: string
  download_url?: string
  setup_hint?: string
  display_group?: string
  status: ModelStatusValue
  message: string
  has_local_path?: boolean
}

interface ModelListData {
  models: ModelEntry[]
  mode_groups: { id: string; name: string }[]
  can_open_model_folders: boolean
}
interface Snapshot {
  data?: ModelListData
  loading: boolean
  error?: string
}
interface ModelList {
  snapshot: Snapshot
  listeners: Set<() => void>
  revision: number
  completedRevision: number
  completedAt: number
  controller?: AbortController
}

const modelLists = new Map<string, ModelList>()
const emptySnapshot: Snapshot = { loading: false }
const REVALIDATE_AFTER_MS = 15000

export function modelFileStatus(status: unknown): ModelStatusValue {
  return Object.values(ModelStatus).find((known) => known === status) ?? ModelStatus.UNKNOWN
}

function entryFor(backend: string): ModelList {
  const existing = modelLists.get(backend)
  if (existing) return existing
  const entry: ModelList = {
    snapshot: emptySnapshot, listeners: new Set(), revision: 0, completedRevision: -1, completedAt: 0,
  }
  modelLists.set(backend, entry)
  return entry
}

function publish(entry: ModelList, snapshot: Snapshot) {
  entry.snapshot = snapshot
  for (const listener of entry.listeners) listener()
}

function activeBackend() {
  return createApiClient(useBackendStore.getState().backendURL).baseUrl
}

async function load(backend: string, entry: ModelList) {
  if (entry.controller || !entry.listeners.size || entry.completedRevision === entry.revision) return
  const revision = entry.revision
  const controller = new AbortController()
  entry.controller = controller
  const timeout = setTimeout(() => controller.abort(), 15000)
  publish(entry, { data: entry.snapshot.data, loading: true })
  try {
    const result = await createApiClient(backend || '/').json<Partial<ModelListData>>(
      ApiPath.MODELS_LIST, { signal: controller.signal }, '读取模型列表失败',
    )
    if (controller.signal.aborted || entry.revision !== revision) return
    const models = (result.models ?? []).map((model) => ({
      ...model, status: modelFileStatus(model.status), message: model.message ?? '',
    }))
    entry.completedAt = Date.now()
    publish(entry, {
      data: {
        models,
        mode_groups: result.mode_groups ?? [],
        can_open_model_folders: result.can_open_model_folders === true,
      },
      loading: false,
    })
    if (backend === activeBackend()) useDownloadStore.getState().syncFiles(models)
  } catch (error) {
    if (entry.controller === controller && entry.revision === revision) {
      publish(entry, {
        data: entry.snapshot.data, loading: false,
        error: controller.signal.aborted ? undefined : error instanceof Error ? error.message : String(error),
      })
    }
  } finally {
    clearTimeout(timeout)
    if (entry.controller === controller) {
      entry.controller = undefined
      entry.completedRevision = revision
      if (entry.revision !== revision) void load(backend, entry)
      else if (entry.snapshot.loading) publish(entry, { data: entry.snapshot.data, loading: false })
    }
  }
}

export function refreshModelList(backend: string) {
  const entry = entryFor(backend)
  entry.revision++
  return load(backend, entry)
}

export function resetModelListCache() {
  for (const entry of modelLists.values()) entry.controller?.abort()
  modelLists.clear()
}

export function useModelList(backend: string) {
  const entry = entryFor(backend)
  const subscribe = useCallback((listener: () => void) => {
    if (!entry.listeners.size && entry.completedRevision === entry.revision
      && (entry.snapshot.error || !entry.snapshot.data || Date.now() - entry.completedAt >= REVALIDATE_AFTER_MS)) {
      entry.revision++
    }
    entry.listeners.add(listener)
    void load(backend, entry)
    return () => {
      entry.listeners.delete(listener)
      if (entry.listeners.size || !entry.controller) return
      const controller = entry.controller
      entry.controller = undefined
      controller.abort()
      publish(entry, { data: entry.snapshot.data, loading: false })
    }
  }, [backend, entry])
  const getSnapshot = useCallback(() => entry.snapshot, [entry])
  return useSyncExternalStore(subscribe, getSnapshot)
}
