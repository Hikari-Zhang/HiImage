import { app, BrowserWindow, shell, ipcMain, dialog } from 'electron'
import { join, extname } from 'path'
import { homedir } from 'os'
import { mkdir, writeFile, readFile } from 'fs/promises'
import { BackendManager } from './backend-manager'

/** 跨平台默认输出目录：~/Documents/HiImage */
function getOutputDir(): string {
  return join(homedir(), 'Documents', 'HiImage')
}

/** 确保输出目录存在（不存在则自动创建） */
async function ensureOutputDir(): Promise<void> {
  try {
    await mkdir(getOutputDir(), { recursive: true })
  } catch {
    // 已存在或无权限时忽略
  }
}

let mainWindow: BrowserWindow | null = null
const backendManager = new BackendManager()

function createWindow(): void {
  mainWindow = new BrowserWindow({
    width: 1200,
    height: 800,
    minWidth: 900,
    minHeight: 600,
    show: false,
    backgroundColor: '#1e1e1e',
    titleBarStyle: 'hiddenInset',
    trafficLightPosition: { x: 12, y: 12 },
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      sandbox: false,
    },
  })

  mainWindow.on('ready-to-show', () => {
    mainWindow?.show()
  })

  mainWindow.webContents.setWindowOpenHandler((details) => {
    shell.openExternal(details.url)
    return { action: 'deny' }
  })

  // HMR in dev, load build in production
  if (!app.isPackaged && process.env['ELECTRON_RENDERER_URL']) {
    mainWindow.loadURL(process.env['ELECTRON_RENDERER_URL'])
  } else {
    mainWindow.loadFile(join(__dirname, '../renderer/index.html'))
  }
}

// IPC Handlers
function setupIPC(): void {
  // File dialog: open
  ipcMain.handle('dialog:openFile', async () => {
    await ensureOutputDir()
    const result = await dialog.showOpenDialog({
      defaultPath: getOutputDir(),
      properties: ['openFile'],
      filters: [{ name: 'Images', extensions: ['png', 'jpg', 'jpeg', 'bmp', 'webp', 'tiff'] }],
    })
    return result.canceled ? null : result.filePaths[0]
  })

  // File dialog: save
  ipcMain.handle('dialog:saveFile', async (_event, defaultFileName?: string) => {
    await ensureOutputDir()
    const result = await dialog.showSaveDialog({
      defaultPath: join(getOutputDir(), defaultFileName || 'result.png'),
      filters: [{ name: 'PNG Image', extensions: ['png'] }, { name: 'JPEG Image', extensions: ['jpg', 'jpeg'] }],
    })
    return result.canceled ? null : result.filePath
  })

  // Backend URL
  ipcMain.handle('backend:getURL', () => {
    return backendManager.getBaseURL()
  })

  // Connection config
  ipcMain.handle('backend:getConfig', () => {
    return backendManager.getConfig()
  })

  ipcMain.handle('backend:updateConfig', async (_event, config: Record<string, unknown>) => {
    await backendManager.updateConfig(config)
    return backendManager.getBaseURL()
  })

  // File write: save image base64 → disk
  ipcMain.handle('file:save', async (_event, filePath: string, base64Data: string) => {
    try {
      const base64 = base64Data.replace(/^data:image\/\w+;base64,/, '')
      const buffer = Buffer.from(base64, 'base64')
      await writeFile(filePath, buffer)
      return { success: true, path: filePath }
    } catch (err: any) {
      return { success: false, error: err.message }
    }
  })

  // File read: disk → base64 data URL (for renderer display)
  ipcMain.handle('file:read', async (_event, filePath: string) => {
    try {
      const buffer = await readFile(filePath)
      const ext = extname(filePath).toLowerCase().replace('.', '')
      const mimeMap: Record<string, string> = {
        jpg: 'image/jpeg', jpeg: 'image/jpeg',
        png: 'image/png', bmp: 'image/bmp',
        webp: 'image/webp', tiff: 'image/tiff', gif: 'image/gif',
      }
      const mime = mimeMap[ext] || 'image/png'
      return `data:${mime};base64,${buffer.toString('base64')}`
    } catch (err: any) {
      throw new Error(`无法读取文件: ${err.message}`)
    }
  })
}

// App lifecycle
app.whenReady().then(async () => {
  setupIPC()

  // Start backend
  try {
    await backendManager.start()
    console.log('[Main] Backend started successfully')
  } catch (err) {
    console.error('[Main] Failed to start backend:', err)
  }

  createWindow()

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow()
    }
  })
})

app.on('window-all-closed', async () => {
  await backendManager.stop()
  if (process.platform !== 'darwin') {
    app.quit()
  }
})

app.on('before-quit', async () => {
  await backendManager.stop()
})
