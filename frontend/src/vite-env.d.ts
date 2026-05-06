/// <reference types="vite/client" />

interface ElectronAPI {
  platform: string
  openFile: () => Promise<string | null>
  saveFile: (defaultPath?: string) => Promise<string | null>
  saveImageFile: (filePath: string, base64Data: string) => Promise<{ success: boolean; path?: string; error?: string }>
  readImageFile: (filePath: string) => Promise<string>
  getBackendURL: () => Promise<string>
  getBackendConfig: () => Promise<Record<string, unknown>>
  updateBackendConfig: (config: Record<string, unknown>) => Promise<string>
  windowMinimize: () => void
  windowMaximize: () => void
  windowClose: () => void
  onBackendReady: (callback: () => void) => void
  onBackendError: (callback: (error: string) => void) => void
}

interface Window {
  electronAPI: ElectronAPI
}
