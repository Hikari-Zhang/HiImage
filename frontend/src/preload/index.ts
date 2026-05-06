import { contextBridge, ipcRenderer } from 'electron'

/**
 * Expose Electron APIs to renderer via contextBridge
 */
contextBridge.exposeInMainWorld('electronAPI', {
  // File dialogs
  openFile: (): Promise<string | null> => ipcRenderer.invoke('dialog:openFile'),
  saveFile: (defaultPath?: string): Promise<string | null> =>
    ipcRenderer.invoke('dialog:saveFile', defaultPath),
  saveImageFile: (filePath: string, base64Data: string): Promise<{ success: boolean; path?: string; error?: string }> =>
    ipcRenderer.invoke('file:save', filePath, base64Data),
  readImageFile: (filePath: string): Promise<string> =>
    ipcRenderer.invoke('file:read', filePath),

  // Backend
  getBackendURL: (): Promise<string> => ipcRenderer.invoke('backend:getURL'),
  getBackendConfig: (): Promise<Record<string, unknown>> => ipcRenderer.invoke('backend:getConfig'),
  updateBackendConfig: (config: Record<string, unknown>): Promise<string> =>
    ipcRenderer.invoke('backend:updateConfig', config),

  // App events
  onBackendReady: (callback: () => void) => {
    ipcRenderer.on('backend:ready', callback)
  },
  onBackendError: (callback: (error: string) => void) => {
    ipcRenderer.on('backend:error', (_event, error) => callback(error))
  },
})
