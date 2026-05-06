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
 *
 * 所有 async 操作通过内部串行队列执行，避免并发竞态。
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

  /**
   * 串行队列：所有操作都排进这条链，保证一次只有一个操作在执行。
   * 快速连续调用时，后续操作会等前一个完成再执行。
   */
  private queue: Promise<void> = Promise.resolve()
  private abortController: AbortController = new AbortController()

  constructor() {
    // 延迟加载配置（app.getPath 需要 app ready 之后才能调用）
  }

  // ==================== Public API（全部串行化） ====================

  /** 启动后端 */
  start(): Promise<void> {
    return this.enqueue(() => this._start())
  }

  /** 停止后端（local 模式才有效，排队执行） */
  stop(): Promise<void> {
    return this.enqueue(() => this._stop())
  }

  /**
   * 强制退出：跳过队列、取消所有等待中的操作、直接 kill 进程。
   * 专供 app 退出时调用，不能用于普通切换。
   */
  async forceQuit(): Promise<void> {
    // 取消所有正在等待的 waitForReady / waitForExit
    this.abortController.abort()
    // 重置队列，后续排队的任务不再执行
    this.queue = Promise.resolve()
    // 直接 kill
    await this.killLocalProcess()
    // 重建 abortController，防止下次启动时立即被 abort（不太可能，但防御性保留）
    this.abortController = new AbortController()
  }

  /** 更新连接配置（会自动停旧/启新进程） */
  updateConfig(newConfig: Partial<BackendConfig>): Promise<void> {
    return this.enqueue(() => this._updateConfig(newConfig))
  }

  /** 获取后端 Base URL */
  getBaseURL(): string {
    this.ensureConfigLoaded()
    if (this.config.mode === 'remote') {
      return `http://${this.config.remoteHost}:${this.config.remotePort}`
    }
    return `http://127.0.0.1:${this.localPort}`
  }

  /** 获取当前连接配置（快照） */
  getConfig(): BackendConfig {
    this.ensureConfigLoaded()
    return { ...this.config }
  }

  // ==================== 串行队列调度 ====================

  /**
   * 把一个 async 任务追加到串行队列末尾。
   * 无论上一个任务是成功还是失败，下一个任务都会执行。
   */
  private enqueue(task: () => Promise<void>): Promise<void> {
    // 保存当前队列末尾的 Promise，用于向调用方传递 reject
    const result = this.queue.then(() => task())
    // 队列本身吞掉错误，保证下一个任务继续入队
    this.queue = result.catch(() => {})
    return result
  }

  // ==================== 实际实现（私有，不加锁，由队列保证串行） ====================

  private async _start(): Promise<void> {
    this.ensureConfigLoaded()

    // 后端已由外部管理（如 dev.js 已启动），跳过本地启动，直接验证可达性
    if (process.env.HIMAGE_BACKEND_MANAGED === '1') {
      await this.waitForReady(`http://127.0.0.1:${this.localPort}`, 10000)
      this.isReady = true
      console.log('[BackendManager] Using externally managed backend')
      return
    }

    if (this.config.mode === 'local') {
      await this.startLocalBackend()
    } else {
      await this.checkRemoteBackend()
    }
  }

  private async _stop(): Promise<void> {
    await this.killLocalProcess()
  }

  private async _updateConfig(newConfig: Partial<BackendConfig>): Promise<void> {
    const oldMode = this.config.mode

    // local → remote：先停止本地进程（config 还没改，killLocalProcess 不受 mode 影响）
    if (oldMode === 'local' && newConfig.mode === 'remote') {
      await this.killLocalProcess()
    }

    // 更新配置
    this.config = { ...this.config, ...newConfig }
    this.saveConfig()

    // remote → local：启动本地进程
    if (oldMode === 'remote' && this.config.mode === 'local') {
      await this.startLocalBackend()
    }
  }

  // ==================== 底层进程操作 ====================

  /**
   * 杀掉当前本地进程（不检查 mode），供内部复用。
   */
  private async killLocalProcess(): Promise<void> {
    if (!this.process || !this.process.pid) return

    const proc = this.process
    const pid = proc.pid!
    // 立即置 null，防止重入和后续误用
    this.process = null
    this.isReady = false

    console.log(`[BackendManager] Stopping local backend (PID: ${pid})...`)

    /**
     * 等待进程退出的辅助函数。
     * 用 once 而非 on，避免重复监听器堆积。
     * 同时检查进程是否已经退出（exitCode !== null），避免错过已触发的事件。
     * abort 信号触发时立即 resolve(false)。
     */
    const signal = this.abortController.signal
    const waitForExit = (timeoutMs: number): Promise<boolean> => {
      return new Promise((resolve) => {
        // 如果进程已经退出，直接返回
        if (proc.exitCode !== null || proc.killed) {
          resolve(true)
          return
        }
        const cleanup = () => {
          proc.removeListener('exit', onExit)
          signal.removeEventListener('abort', onAbort)
          clearTimeout(timer)
        }
        const timer = setTimeout(() => { cleanup(); resolve(false) }, timeoutMs)
        const onExit = () => { cleanup(); resolve(true) }
        const onAbort = () => { cleanup(); resolve(false) }
        proc.once('exit', onExit)
        signal.addEventListener('abort', onAbort, { once: true })
      })
    }

    // 第一步：SIGTERM 优雅终止，等待 5 秒
    try {
      proc.kill('SIGTERM')
    } catch {
      // 进程可能已退出，忽略
    }

    const exitedGracefully = await waitForExit(5000)
    if (exitedGracefully) {
      console.log('[BackendManager] Backend stopped gracefully')
      return
    }

    // 第二步：SIGKILL 强制终止整个进程组
    console.log('[BackendManager] Process still running, force killing...')
    try {
      if (process.platform === 'win32') {
        spawn('taskkill', ['/F', '/T', '/PID', String(pid)], { shell: true })
      } else {
        // detached:true 保证 Python 是进程组组长，-pid 可以杀掉整个进程树
        try {
          process.kill(-pid, 'SIGKILL')
          console.log(`[BackendManager] Killed process group -${pid}`)
        } catch (e) {
          // 进程组不存在（可能已退出），降级直接 kill 主进程
          console.warn(`[BackendManager] kill(-${pid}) failed, trying direct:`, e)
          try { proc.kill('SIGKILL') } catch { /* 已退出 */ }
        }
      }
    } catch (err) {
      console.error('[BackendManager] Error force-killing backend:', err)
    }

    const exitedForced = await waitForExit(3000)
    if (exitedForced) {
      console.log('[BackendManager] Backend force-killed')
    } else {
      // 最后兜底：直接用系统 kill -9 <pid>（不用进程组）
      console.warn('[BackendManager] Still running after SIGKILL, trying direct kill...')
      try {
        process.kill(pid, 'SIGKILL')
      } catch { /* 已退出 */ }
      await waitForExit(2000)
    }
  }

  private async startLocalBackend(): Promise<void> {
    // 启动前先清理端口，防止上次没 kill 干净的残留进程阻塞新启动
    await this.freePort(this.localPort)

    const { command, args } = this.getStartCommand()
    console.log(`[BackendManager] Starting local: ${command} ${args.join(' ')}`)

    this.process = spawn(command, args, {
      stdio: ['ignore', 'pipe', 'pipe'],
      // detached: true 让 Python 成为新的进程组组长，
      // 这样 kill(-pid, SIGKILL) 可以杀掉整个进程树（uvicorn workers + iopaint subprocesses）
      detached: process.platform !== 'win32',
      env: { ...process.env, PYTHONUNBUFFERED: '1', PYTHONIOENCODING: 'utf-8' },
    })

    this.process.stdout?.on('data', (data: Buffer) => {
      console.log(`[Backend] ${data.toString().trim()}`)
    })
    this.process.stderr?.on('data', (data: Buffer) => {
      console.error(`[Backend] ${data.toString().trim()}`)
    })
    const proc = this.process
    this.process.once('exit', (code) => {
      console.log(`[BackendManager] Backend exited with code ${code}`)
      this.isReady = false
      // 如果没有被 killLocalProcess 抢先置 null，在这里清理
      if (this.process === proc) this.process = null
    })

    // 等待就绪，同时监听进程提前退出（端口冲突等原因）
    await Promise.race([
      this.waitForReady(`http://127.0.0.1:${this.localPort}`, 30000),
      new Promise<never>((_, reject) => {
        proc.once('exit', (code) => {
          reject(new Error(`Backend process exited prematurely with code ${code}`))
        })
      }),
    ])
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

  // ==================== 工具方法 ====================

  /**
   * 清理占用指定端口的所有进程（macOS/Linux: lsof + kill；Windows: netstat + taskkill）
   * 启动新后端前调用，避免端口残留导致 code 1 崩溃。
   */
  private async freePort(port: number): Promise<void> {
    if (process.platform === 'win32') {
      // Windows: 用 netstat 找到占用端口的 PID，再 taskkill
      return new Promise((resolve) => {
        const child = spawn('cmd', ['/c', `netstat -ano | findstr /i ":${port}"`], { shell: true })
        const chunks: Buffer[] = []
        child.stdout?.on('data', (d: Buffer) => chunks.push(d))
        child.on('close', () => {
          const output = Buffer.concat(chunks).toString('utf-8')
          const pids = new Set<string>()
          for (const line of output.split('\n')) {
            const trimmed = line.trim()
            if (!trimmed) continue
            const parts = trimmed.split(/\s+/)
            const pid = parts[parts.length - 1]
            if (pid && /^\d+$/.test(pid)) pids.add(pid)
          }
          for (const pid of pids) {
            try { spawn('taskkill', ['/F', '/PID', pid], { shell: true }) } catch {}
          }
          // 等一小段时间让端口释放
          setTimeout(resolve, 500)
        })
      })
    } else {
      // macOS/Linux: lsof 找到所有占用该端口的 PID 并 kill -9
      return new Promise((resolve) => {
        const cmd = spawn('sh', ['-c', `lsof -ti tcp:${port} | xargs -r kill -9`])
        cmd.on('close', () => resolve())
      })
    }
  }

  private ensureConfigLoaded(): void {
    if (!this.configLoaded) {
      this.configLoaded = true
      this.loadConfig()
    }
  }

  private getStartCommand(): { command: string; args: string[] } {
    if (app.isPackaged) {
      const backendPath = path.join(process.resourcesPath, 'backend', 'hiimage-backend')
      return { command: backendPath, args: ['--port', String(this.localPort)] }
    } else {
      const projectRoot = path.join(__dirname, '..', '..', '..')
      const venvPython = process.platform === 'win32'
        ? path.join(projectRoot, 'venv', 'Scripts', 'python.exe')
        : path.join(projectRoot, 'venv', 'bin', 'python')
      const runScript = path.join(projectRoot, 'backend', 'run.py')
      return { command: venvPython, args: [runScript, '--port', String(this.localPort)] }
    }
  }

  private waitForReady(baseURL: string, timeoutMs: number): Promise<void> {
    const signal = this.abortController.signal
    return new Promise((resolve, reject) => {
      if (signal.aborted) { reject(new Error('aborted')); return }

      const startTime = Date.now()
      const interval = setInterval(() => {
        if (signal.aborted) {
          clearInterval(interval)
          reject(new Error('aborted'))
          return
        }
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
        req.on('error', () => { /* not ready yet */ })
        req.end()
      }, 500)

      signal.addEventListener('abort', () => {
        clearInterval(interval)
        reject(new Error('aborted'))
      }, { once: true })
    })
  }

  private saveConfig(): void {
    try {
      const configDir = app.getPath('userData')
      const configPath = path.join(configDir, CONFIG_FILE)
      fs.writeFileSync(configPath, JSON.stringify(this.config, null, 2), 'utf-8')
    } catch (err) {
      console.error('[BackendManager] Failed to save config:', err)
    }
  }

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
