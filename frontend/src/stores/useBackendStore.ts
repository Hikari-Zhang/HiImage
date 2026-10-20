import { create } from 'zustand'
import { createApiClient } from '../services/apiClient'

interface BackendState {
  isConnected: boolean
  backendURL: string
  wsConnected: boolean

  setConnected: (connected: boolean) => void
  setBackendURL: (url: string) => void
  setWsConnected: (connected: boolean) => void
  checkHealth: () => Promise<boolean>
}

export const useBackendStore = create<BackendState>((set, get) => ({
  isConnected: false,
  backendURL: '',
  wsConnected: false,

  setConnected: (connected) => set({ isConnected: connected }),
  setBackendURL: (url) => set({ backendURL: url }),
  setWsConnected: (connected) => set({ wsConnected: connected }),

  checkHealth: async () => {
    const { backendURL } = get()
    try {
      const res = await createApiClient(backendURL).fetch('/api/health')
      const ok = res.ok
      set({ isConnected: ok })
      return ok
    } catch {
      set({ isConnected: false })
      return false
    }
  },
}))
