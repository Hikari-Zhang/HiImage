export interface BackendConfig {
  mode: 'local' | 'remote'
  remoteHost: string
  remotePort: number
}

export interface ElectronAPI {
  openFile: () => Promise<string | null>
  saveFile: (defaultPath?: string) => Promise<string | null>
  saveImageFile: (filePath: string, base64Data: string) => Promise<{ success: boolean; path?: string; error?: string }>
  readImageFile: (filePath: string) => Promise<string>
  getBackendURL: () => Promise<string>
  getBackendConfig: () => Promise<BackendConfig>
  updateBackendConfig: (config: Partial<BackendConfig>) => Promise<string>
  onBackendReady: (callback: () => void) => void
  onBackendError: (callback: (error: string) => void) => void
}

declare global {
  interface Window {
    electronAPI: ElectronAPI
  }
}
