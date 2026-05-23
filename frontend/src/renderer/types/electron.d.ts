// Electron API 类型定义
// 此文件作为全局脚本加载，自动合并到全局 Window 接口

interface BackendConfig {
  mode: 'local' | 'remote'
  remoteHost: string
  remotePort: number
}

interface ElectronAPI {
  platform: string
  openFile: () => Promise<string | null>
  openFiles: () => Promise<string[] | null>
  openDirectory: () => Promise<string | null>
  saveFile: (defaultPath?: string) => Promise<string | null>
  saveImageFile: (filePath: string, base64Data: string) => Promise<{ success: boolean; path?: string; error?: string }>
  readImageFile: (filePath: string) => Promise<string>
  getBackendURL: () => Promise<string>
  getBackendConfig: () => Promise<BackendConfig>
  updateBackendConfig: (config: Partial<BackendConfig>) => Promise<string>
  onBackendReady: (callback: () => void) => void
  onBackendError: (callback: (error: string) => void) => void
}

interface Window {
  electronAPI: ElectronAPI
}
