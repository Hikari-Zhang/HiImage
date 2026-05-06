import { ChildProcess, spawn } from 'child_process'
import { app } from 'electron'
import path from 'path'
import http from 'http'
import fs from 'fs'

export type ConnectionMode = 'local' | 'remote'

interface BackendConfig {
  mode: ConnectionMode
  remoteHost: string
  remotePort: number
}

const CONFIG_FILE = 'connection.json'

/**
 * 管理 Python FastAPI 后端进程的生命周期
 * 支持两种模式：
 * - local: 自动启动本地 Python 后端进程
 * - remote: 连接远程后端（Docker / 其他服务器）
 */
export class BackendManager {
  private process: ChildProcess | null = null
  private localPort: number = 8787
  private isReady: boolean = false
  private config: BackendConfig = {
    mode: 'local',
    remoteHost: '127.0.0.1',
    remotePort: 8787,
  }

  private configLoaded = false

  constructor() {
    // 延迟加载配置（app.getPath 需要 app ready 之后才能调用）
  }

  /**
   * 启动后端（local 模式下启动进程，remote 模式下只检查连通性）
   */
  async start(): Promise<void> {
    this.ensureConfigLoaded()
    if (this.config.mode === 'local') {
      await this.startLocalBackend()
    } else {
      await this.checkRemoteBackend()
    }
  }

  /**
   * 停止后端进程（仅 local 模式有效）
   */
  async stop(): Promise<void> {
    if (this.config.mode !== 'local' || !this.process) return

    console.log('[BackendManager] Stopping local backend...')
    this.process.kill('SIGTERM')

    await new Promise<void>((resolve) => {
      const timeout = setTimeout(() => {
        if (this.process) {
          this.process.kill('SIGKILL')
        }
        resolve()
      }, 5000)

      this.process!.on('exit', () => {
        clearTimeout(timeout)
        resolve()
      })
    })

    this.process = null
    this.isReady = false
  }

  /**
   * 获取后端 Base URL（根据当前模式返回）
   */
  getBaseURL(): string {
    this.ensureConfigLoaded()
    if (this.config.mode === 'remote') {
      return `http://${this.config.remoteHost}:${this.config.remotePort}`
    }
    return `http://127.0.0.1:${this.localPort}`
  }

  /**
   * 获取当前连接配置
   */
  getConfig(): BackendConfig {
    this.ensureConfigLoaded()
    return { ...this.config }
  }

  /**
   * 更新连接配置并持久化
   */
  async updateConfig(newConfig: Partial<BackendConfig>): Promise<void> {
    const oldMode = this.config.mode
    this.config = { ...this.config, ...newConfig }
    this.saveConfig()

    // 如果模式从 local 切换到 remote，停止本地进程
    if (oldMode === 'local' && this.config.mode === 'remote') {
      await this.stop()
    }
    // 如果模式从 remote 切换到 local，启动本地进程
    if (oldMode === 'remote' && this.config.mode === 'local') {
      await this.startLocalBackend()
    }
  }

  // ==================== Private ====================

  private ensureConfigLoaded(): void {
    if (!this.configLoaded) {
      this.configLoaded = true
      this.loadConfig()
    }
  }

  private async startLocalBackend(): Promise<void> {
    const { command, args } = this.getStartCommand()
    console.log(`[BackendManager] Starting local: ${command} ${args.join(' ')}`)

    this.process = spawn(command, args, {
      stdio: ['ignore', 'pipe', 'pipe'],
      env: {
        ...process.env,
        PYTHONUNBUFFERED: '1',
      },
    })

    this.process.stdout?.on('data', (data: Buffer) => {
      console.log(`[Backend] ${data.toString().trim()}`)
    })

    this.process.stderr?.on('data', (data: Buffer) => {
      console.error(`[Backend] ${data.toString().trim()}`)
    })

    this.process.on('exit', (code) => {
      console.log(`[BackendManager] Backend exited with code ${code}`)
      this.isReady = false
    })

    await this.waitForReady(`http://127.0.0.1:${this.localPort}`, 30000)
    this.isReady = true
  }

  private async checkRemoteBackend(): Promise<void> {
    const url = `http://${this.config.remoteHost}:${this.config.remotePort}`
    console.log(`[BackendManager] Checking remote backend: ${url}`)

    try {
      await this.waitForReady(url, 10000)
      this.isReady = true
      console.log('[BackendManager] Remote backend is reachable')
    } catch {
      console.warn('[BackendManager] Remote backend not reachable, will retry on demand')
      this.isReady = false
    }
  }

  private getStartCommand(): { command: string; args: string[] } {
    if (app.isPackaged) {
      const backendPath = path.join(process.resourcesPath, 'backend', 'clearwatermark-backend')
      return { command: backendPath, args: ['--port', String(this.localPort)] }
    } else {
      const projectRoot = path.join(__dirname, '..', '..', '..')
      const venvPython = path.join(projectRoot, 'venv', 'bin', 'python')
      const runScript = path.join(projectRoot, 'backend', 'run.py')
      return { command: venvPython, args: [runScript, '--port', String(this.localPort)] }
    }
  }

  private waitForReady(baseURL: string, timeoutMs: number): Promise<void> {
    return new Promise((resolve, reject) => {
      const startTime = Date.now()
      const interval = setInterval(() => {
        if (Date.now() - startTime > timeoutMs) {
          clearInterval(interval)
          reject(new Error(`Backend not reachable at ${baseURL}`))
          return
        }

        const req = http.get(`${baseURL}/api/health`, (res) => {
          if (res.statusCode === 200) {
            clearInterval(interval)
            resolve()
          }
        })

        req.on('error', () => {
          // Not ready yet
        })

        req.end()
      }, 500)
    })
  }

  /**
   * 持久化配置到 userData 目录
   */
  private saveConfig(): void {
    try {
      const configDir = app.getPath('userData')
      const configPath = path.join(configDir, CONFIG_FILE)
      fs.writeFileSync(configPath, JSON.stringify(this.config, null, 2), 'utf-8')
    } catch (err) {
      console.error('[BackendManager] Failed to save config:', err)
    }
  }

  /**
   * 从 userData 目录加载配置
   */
  private loadConfig(): void {
    try {
      const configDir = app.getPath('userData')
      const configPath = path.join(configDir, CONFIG_FILE)
      if (fs.existsSync(configPath)) {
        const raw = fs.readFileSync(configPath, 'utf-8')
        const loaded = JSON.parse(raw) as Partial<BackendConfig>
        this.config = { ...this.config, ...loaded }
      }
    } catch (err) {
      console.error('[BackendManager] Failed to load config:', err)
    }
  }
}
