import { useState, useEffect } from 'react'
import { Save, RotateCcw, Monitor, Globe, CheckCircle2, XCircle, Loader2 } from 'lucide-react'
import { clsx } from 'clsx'
import PageHeader from '../components/layout/PageHeader'
import { showToast } from '../components/ui'
import { useSettingsStore } from '../stores/useSettingsStore'
import { useBackendStore } from '../stores/useBackendStore'
import { useBackendAPI } from '../hooks/useBackendAPI'
import ModelManager from './ModelManager'

type ConnectionMode = 'local' | 'remote'
type SettingsTab = 'general' | 'models'

// 默认值（用于「恢复默认」）
const DEFAULTS = {
  device: 'cpu',
  serverPort: 51821,
  keepaliveSeconds: 300,
  startupTimeout: 1800,
  hfEndpoint: 'https://huggingface.com',
  hfToken: '',
  githubMirror: '',
  defaultDilation: 10,
  disableNsfw: true,
  lowMem: true,
  cpuOffload: false,
  cpuTextencoder: false,
  downloadMaxConcurrent: 3,
}

export default function Settings() {
  const backendURL = useBackendStore((s) => s.backendURL)
  const setBackendURL = useBackendStore((s) => s.setBackendURL)
  const store = useSettingsStore()
  const { getDevices } = useBackendAPI()

  const [activeTab, setActiveTab] = useState<SettingsTab>('general')

  // 本地 draft state — 用户编辑中但尚未保存的值
  const [connectionMode, setConnectionMode] = useState<ConnectionMode>('local')
  const [remoteHost, setRemoteHost] = useState('127.0.0.1')
  const [remotePort, setRemotePort] = useState('8787')
  const [connectionStatus, setConnectionStatus] = useState<'idle' | 'connected' | 'disconnected' | 'testing'>('idle')
  const [isSwitching, setIsSwitching] = useState(false)

  const [device, setDevice] = useState(store.device)
  const [port, setPort] = useState(String(store.serverPort))
  const [keepalive, setKeepalive] = useState(String(store.keepaliveSeconds))
  const [startupTimeout, setStartupTimeout] = useState(String(store.startupTimeout))
  const [hfEndpoint, setHfEndpoint] = useState(store.hfEndpoint)
  const [hfToken, setHfToken] = useState(store.hfToken)
  const [githubMirror, setGithubMirror] = useState(store.githubMirror)
  const [dilation, setDilation] = useState(String(store.defaultDilation))
  const [disableNsfw, setDisableNsfw] = useState(store.disableNsfw)
  const [lowMem, setLowMem] = useState(store.lowMem)
  const [cpuOffload, setCpuOffload] = useState(store.cpuOffload)
  const [cpuTextencoder, setCpuTextencoder] = useState(store.cpuTextencoder)
  const [downloadMaxConcurrent, setDownloadMaxConcurrent] = useState(String(store.downloadMaxConcurrent))
  const [isSaving, setIsSaving] = useState(false)

  // 设备可用性（从后端检测）
  const [deviceAvailability, setDeviceAvailability] = useState<
    Record<string, { available: boolean; desc: string; reason?: string }>
  >({})
  const [devicesLoading, setDevicesLoading] = useState(true)

  // 初始化：从 Electron 主进程读取连接配置，从后端读取应用设置
  useEffect(() => {
    // 读取连接模式
    if (window.electronAPI?.getBackendConfig) {
      window.electronAPI.getBackendConfig().then((cfg: any) => {
        setConnectionMode(cfg.mode ?? 'local')
        setRemoteHost(cfg.remoteHost ?? '127.0.0.1')
        setRemotePort(String(cfg.remotePort ?? 8787))
      })
    }

    // 读取应用设置
    store.loadSettings(backendURL).then(() => {
      const s = useSettingsStore.getState()
      setDevice(s.device)
      setPort(String(s.serverPort))
      setKeepalive(String(s.keepaliveSeconds))
      setStartupTimeout(String(s.startupTimeout))
      setHfEndpoint(s.hfEndpoint)
      setHfToken(s.hfToken)
      setGithubMirror(s.githubMirror)
      setDilation(String(s.defaultDilation))
      setDisableNsfw(s.disableNsfw)
      setLowMem(s.lowMem)
      setCpuOffload(s.cpuOffload)
      setCpuTextencoder(s.cpuTextencoder)
      setDownloadMaxConcurrent(String(s.downloadMaxConcurrent))
    })

    // 检测计算设备可用性
    setDevicesLoading(true)
    getDevices()
      .then((data) => {
        if (data?.devices) {
          const map: Record<string, { available: boolean; desc: string; reason?: string }> = {}
          for (const d of data.devices) {
            map[d.id] = { available: d.available, desc: d.desc, reason: d.reason }
          }
          setDeviceAvailability(map)
        }
      })
      .catch(() => {
        // 后端不可达时不阻断页面，所有设备视为可用
      })
      .finally(() => setDevicesLoading(false))
  }, [backendURL])

  const handleTestConnection = async () => {
    setConnectionStatus('testing')
    const url = connectionMode === 'local'
      ? `http://127.0.0.1:8787/api/health`
      : `http://${remoteHost}:${remotePort}/api/health`
    try {
      const res = await fetch(url, { signal: AbortSignal.timeout(5000) })
      setConnectionStatus(res.ok ? 'connected' : 'disconnected')
    } catch {
      setConnectionStatus('disconnected')
    }
  }

  // 切换连接模式（立即生效，通知主进程）
  const handleSwitchMode = async (newMode: ConnectionMode) => {
    if (newMode === connectionMode) return
    setIsSwitching(true)
    setConnectionStatus('idle')
    try {
      const newConfig: Record<string, unknown> = { mode: newMode }
      if (newMode === 'remote') {
        newConfig.remoteHost = remoteHost
        newConfig.remotePort = Number(remotePort) || 8787
      }
      // 通知主进程切换模式（会停止/启动本地后端）
      const newURL = await window.electronAPI?.updateBackendConfig(newConfig)
      if (newURL) {
        setBackendURL(newURL)
      }
      setConnectionMode(newMode)
      showToast('success', newMode === 'local' ? '已切换到本地模式，正在启动后端...' : '已切换到远程模式')
    } catch (err: any) {
      showToast('error', `切换失败: ${err.message}`)
    } finally {
      setIsSwitching(false)
    }
  }

  // 保存远程连接配置（仅 remote 模式下需要更新 host/port）
  const handleSaveConnection = async () => {
    if (connectionMode !== 'remote') return
    setIsSwitching(true)
    try {
      const newURL = await window.electronAPI?.updateBackendConfig({
        mode: 'remote',
        remoteHost: remoteHost,
        remotePort: Number(remotePort) || 8787,
      })
      if (newURL) setBackendURL(newURL)
      showToast('success', '连接配置已保存')
    } catch (err: any) {
      showToast('error', `保存失败: ${err.message}`)
    } finally {
      setIsSwitching(false)
    }
  }

  const handleSave = async () => {
    setIsSaving(true)
    try {
      // 先把 draft 写入 store
      store.setSettings({
        device,
        serverPort: Number(port) || DEFAULTS.serverPort,
        keepaliveSeconds: Number(keepalive) || DEFAULTS.keepaliveSeconds,
        startupTimeout: Number(startupTimeout) || DEFAULTS.startupTimeout,
        hfEndpoint,
        hfToken,
        githubMirror,
        defaultDilation: Number(dilation) || DEFAULTS.defaultDilation,
        disableNsfw,
        lowMem,
        cpuOffload,
        cpuTextencoder,
        downloadMaxConcurrent: Math.max(1, Math.min(10, Number(downloadMaxConcurrent) || 3)),
      })
      // 再持久化到后端
      await useSettingsStore.getState().saveSettings(backendURL)
      showToast('success', '设置已保存')
    } catch (err: any) {
      showToast('error', `保存失败: ${err.message}`)
    } finally {
      setIsSaving(false)
    }
  }

  const handleReset = () => {
    setDevice(DEFAULTS.device)
    setPort(String(DEFAULTS.serverPort))
    setKeepalive(String(DEFAULTS.keepaliveSeconds))
    setStartupTimeout(String(DEFAULTS.startupTimeout))
    setHfEndpoint(DEFAULTS.hfEndpoint)
    setHfToken(DEFAULTS.hfToken)
    setGithubMirror(DEFAULTS.githubMirror)
    setDilation(String(DEFAULTS.defaultDilation))
    setDisableNsfw(DEFAULTS.disableNsfw)
    setLowMem(DEFAULTS.lowMem)
    setCpuOffload(DEFAULTS.cpuOffload)
    setCpuTextencoder(DEFAULTS.cpuTextencoder)
    setDownloadMaxConcurrent(String(DEFAULTS.downloadMaxConcurrent))
    showToast('success', '已恢复默认值，点击保存生效')
  }

  return (
    <div className="flex-1 flex flex-col min-h-0">
      <PageHeader title="设置" />

      {/* 页签栏 */}
      <div className="flex border-b border-border-subtle flex-shrink-0 px-4">
        {([
          { key: 'general', label: '通用设置' },
          { key: 'models',  label: '模型管理' },
        ] as { key: SettingsTab; label: string }[]).map((tab) => (
          <button
            key={tab.key}
            onClick={() => setActiveTab(tab.key)}
            className={clsx(
              'px-4 py-2.5 text-xs font-medium border-b-2 -mb-px transition-colors',
              activeTab === tab.key
                ? 'border-border-focus text-fg-accent'
                : 'border-transparent text-fg-secondary hover:text-fg-primary'
            )}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* 模型管理页签：始终挂载，通过 CSS 控制显隐，避免切换路由后状态丢失 */}
      <div className={activeTab === 'models' ? 'flex flex-1 min-h-0 overflow-hidden' : 'hidden'}>
        <ModelManager />
      </div>

      {/* 通用设置页签 */}
      {activeTab === 'general' && (
      <div className="flex-1 min-h-0 overflow-y-auto p-5 space-y-4 max-w-[640px]">
        {/* Connection Mode */}
        <section className="bg-bg-tertiary rounded-lg p-4">
          <h3 className="text-sm font-medium mb-3">连接方式</h3>
          <div className="grid grid-cols-2 gap-3 mb-3">
            <button
              onClick={() => handleSwitchMode('local')}
              disabled={isSwitching}
              className={clsx(
                'p-3 rounded-md border text-center transition-all',
                connectionMode === 'local'
                  ? 'bg-bg-active border-border-focus'
                  : 'bg-bg-primary border-border-subtle hover:border-fg-secondary',
                isSwitching && 'opacity-60 cursor-not-allowed'
              )}
            >
              {isSwitching && connectionMode !== 'local' ? (
                <Loader2 size={18} className="mx-auto mb-1 animate-spin text-fg-accent" />
              ) : (
                <Monitor size={18} className={clsx('mx-auto mb-1', connectionMode === 'local' ? 'text-fg-accent' : 'text-fg-secondary')} />
              )}
              <div className={clsx('text-xs font-medium', connectionMode === 'local' ? 'text-fg-accent' : 'text-fg-primary')}>
                本地启动
              </div>
              <div className="text-[11px] text-fg-secondary mt-0.5">自动管理后端进程</div>
            </button>
            <button
              onClick={() => handleSwitchMode('remote')}
              disabled={isSwitching}
              className={clsx(
                'p-3 rounded-md border text-center transition-all',
                connectionMode === 'remote'
                  ? 'bg-bg-active border-border-focus'
                  : 'bg-bg-primary border-border-subtle hover:border-fg-secondary',
                isSwitching && 'opacity-60 cursor-not-allowed'
              )}
            >
              {isSwitching && connectionMode !== 'remote' ? (
                <Loader2 size={18} className="mx-auto mb-1 animate-spin text-fg-accent" />
              ) : (
                <Globe size={18} className={clsx('mx-auto mb-1', connectionMode === 'remote' ? 'text-fg-accent' : 'text-fg-secondary')} />
              )}
              <div className={clsx('text-xs font-medium', connectionMode === 'remote' ? 'text-fg-accent' : 'text-fg-primary')}>
                远程连接
              </div>
              <div className="text-[11px] text-fg-secondary mt-0.5">连接远程 / Docker 后端</div>
            </button>
          </div>

          {/* Remote connection settings */}
          {connectionMode === 'remote' && (
            <div className="space-y-3 pt-2 border-t border-border-subtle">
              <div className="grid grid-cols-3 gap-2 mt-3">
                <div className="col-span-2">
                  <label className="text-xs text-fg-secondary mb-1 block">主机地址</label>
                  <input
                    type="text"
                    value={remoteHost}
                    onChange={(e) => setRemoteHost(e.target.value)}
                    placeholder="192.168.1.100 或 my-server.com"
                    className="w-full bg-bg-primary border border-border-subtle text-fg-primary text-xs px-2 py-1.5 rounded focus:border-border-focus focus:outline-none"
                  />
                </div>
                <div>
                  <label className="text-xs text-fg-secondary mb-1 block">端口</label>
                  <input
                    type="text"
                    value={remotePort}
                    onChange={(e) => setRemotePort(e.target.value)}
                    className="w-full bg-bg-primary border border-border-subtle text-fg-primary text-xs px-2 py-1.5 rounded focus:border-border-focus focus:outline-none"
                  />
                </div>
              </div>

              {/* Connection test + save */}
              <div className="flex items-center gap-2">
                <button
                  onClick={handleTestConnection}
                  disabled={connectionStatus === 'testing'}
                  className="text-xs px-3 py-1.5 bg-bg-primary border border-border-subtle rounded hover:bg-bg-hover transition-colors disabled:opacity-50"
                >
                  {connectionStatus === 'testing' ? '测试中...' : '测试连接'}
                </button>
                <button
                  onClick={handleSaveConnection}
                  disabled={isSwitching}
                  className="text-xs px-3 py-1.5 bg-border-focus text-white rounded hover:bg-blue-600 transition-colors disabled:opacity-50"
                >
                  应用地址
                </button>
                {connectionStatus === 'connected' && (
                  <span className="text-xs text-status-success flex items-center gap-1">
                    <CheckCircle2 size={12} /> 连接成功
                  </span>
                )}
                {connectionStatus === 'disconnected' && (
                  <span className="text-xs text-status-error flex items-center gap-1">
                    <XCircle size={12} /> 未连接
                  </span>
                )}
              </div>

              {/* Docker hint */}
              <div className="bg-bg-primary rounded p-2.5 border border-border-subtle">
                <p className="text-[11px] text-fg-secondary leading-relaxed">
                  <strong className="text-fg-primary">Docker 部署：</strong>
                  在服务器上运行 <code className="bg-bg-hover px-1 rounded text-fg-accent">docker compose up -d</code> 启动后端，
                  然后在此处填入服务器 IP 和端口 (默认 8787)。
                </p>
              </div>
            </div>
          )}

          {/* Local mode info */}
          {connectionMode === 'local' && (
            <div className="bg-bg-primary rounded p-2.5 border border-border-subtle mt-2">
              <p className="text-[11px] text-fg-secondary leading-relaxed">
                <strong className="text-fg-primary">本地模式：</strong>
                应用启动时自动启动 Python 后端进程 (127.0.0.1:8787)，关闭应用时自动停止。
                适合个人本地使用。
              </p>
            </div>
          )}
        </section>

        {/* Compute Device */}
        <section className="bg-bg-tertiary rounded-lg p-4">
          <h3 className="text-sm font-medium mb-3">计算设备</h3>
          <div className="grid grid-cols-3 gap-3">
            {devicesLoading
              ? Array.from({ length: 3 }).map((_, i) => (
                  <div key={i} className="p-3 rounded-md border border-border-subtle bg-bg-primary animate-pulse h-[60px]" />
                ))
              : [
                  { id: 'mps', label: 'MPS', fallbackDesc: 'Apple Silicon' },
                  { id: 'cpu', label: 'CPU', fallbackDesc: '通用（较慢）' },
                  { id: 'cuda', label: 'CUDA', fallbackDesc: 'NVIDIA GPU' },
                ].map((d) => {
                  const info = deviceAvailability[d.id]
                  const isAvailable = info === undefined ? true : info.available
                  const desc = info?.desc || d.fallbackDesc
                  const reason = info?.reason
                  const isSelected = device === d.id
                  return (
                    <button
                      key={d.id}
                      onClick={() => isAvailable && setDevice(d.id)}
                      disabled={!isAvailable}
                      title={!isAvailable && reason ? reason : undefined}
                      className={clsx(
                        'p-3 rounded-md border text-center transition-all',
                        isSelected && isAvailable
                          ? 'bg-bg-active border-border-focus'
                          : isAvailable
                            ? 'bg-bg-primary border-border-subtle hover:border-fg-secondary'
                            : 'bg-bg-primary border-border-subtle opacity-40 cursor-not-allowed'
                      )}
                    >
                      <div className={clsx(
                        'text-xs font-medium',
                        isSelected && isAvailable ? 'text-fg-accent' : isAvailable ? 'text-fg-primary' : 'text-fg-secondary'
                      )}>
                        {d.label}
                      </div>
                      <div className="text-[11px] text-fg-secondary mt-0.5 leading-tight">
                        {isAvailable ? desc : '不可用'}
                      </div>
                    </button>
                  )
                })
            }
          </div>
          {/* 不可用设备的诊断说明 */}
          {!devicesLoading && Object.entries(deviceAvailability)
            .filter(([, info]) => !info.available && info.reason)
            .map(([id, info]) => (
              <div key={id} className="mt-2 bg-bg-primary border border-border-subtle rounded p-2.5">
                <p className="text-[11px] text-fg-secondary leading-relaxed">
                  <strong className="text-fg-primary">{id.toUpperCase()} 不可用：</strong>
                  {info.reason}
                </p>
              </div>
            ))
          }
        </section>

        {/* Server Config */}
        <section className="bg-bg-tertiary rounded-lg p-4">
          <h3 className="text-sm font-medium mb-3">服务器配置</h3>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-xs text-fg-secondary mb-1 block">IOPaint 端口</label>
              <input
                type="text"
                value={port}
                onChange={(e) => setPort(e.target.value)}
                className="w-full bg-bg-primary border border-border-subtle text-fg-primary text-xs px-2 py-1.5 rounded focus:border-border-focus focus:outline-none"
              />
            </div>
            <div>
              <label className="text-xs text-fg-secondary mb-1 block">保活时间 (秒)</label>
              <input
                type="text"
                value={keepalive}
                onChange={(e) => setKeepalive(e.target.value)}
                className="w-full bg-bg-primary border border-border-subtle text-fg-primary text-xs px-2 py-1.5 rounded focus:border-border-focus focus:outline-none"
              />
            </div>
            <div>
              <label className="text-xs text-fg-secondary mb-1 block">启动超时 (秒)</label>
              <input
                type="text"
                value={startupTimeout}
                onChange={(e) => setStartupTimeout(e.target.value)}
                className="w-full bg-bg-primary border border-border-subtle text-fg-primary text-xs px-2 py-1.5 rounded focus:border-border-focus focus:outline-none"
              />
            </div>
            <div>
              <label className="text-xs text-fg-secondary mb-1 block">
                最大同时下载数
                <span className="ml-1 text-[10px] text-fg-tertiary">（1 - 10）</span>
              </label>
              <input
                type="number"
                min={1}
                max={10}
                value={downloadMaxConcurrent}
                onChange={(e) => setDownloadMaxConcurrent(e.target.value)}
                className="w-full bg-bg-primary border border-border-subtle text-fg-primary text-xs px-2 py-1.5 rounded focus:border-border-focus focus:outline-none"
              />
            </div>
          </div>
        </section>

        {/* Inpaint Settings */}
        <section className="bg-bg-tertiary rounded-lg p-4">
          <h3 className="text-sm font-medium mb-3">修复参数</h3>
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs text-fg-secondary mb-1 block">默认遮罩扩张 (px)</label>
                <input
                  type="text"
                  value={dilation}
                  onChange={(e) => setDilation(e.target.value)}
                  className="w-full bg-bg-primary border border-border-subtle text-fg-primary text-xs px-2 py-1.5 rounded focus:border-border-focus focus:outline-none"
                />
              </div>
            </div>
            {/* NSFW toggle */}
            <div className="flex items-center justify-between py-1">
              <div>
                <div className="text-xs text-fg-primary">禁用 NSFW 安全检查</div>
                <div className="text-[11px] text-fg-secondary mt-0.5">
                  使用 SD 类扩散模型时需要开启，否则可能误拦截正常图片
                </div>
              </div>
              <button
                onClick={() => setDisableNsfw(!disableNsfw)}
                className={clsx(
                  'relative w-10 h-5 rounded-full transition-colors flex-shrink-0 ml-3',
                  disableNsfw ? 'bg-border-focus' : 'bg-bg-hover'
                )}
              >
                <div
                  className={clsx(
                    'absolute top-0.5 w-4 h-4 rounded-full bg-white transition-transform',
                    disableNsfw ? 'translate-x-[22px]' : 'translate-x-[2px]'
                  )}
                />
              </button>
            </div>
          </div>
        </section>

        {/* Memory Optimization */}
        <section className="bg-bg-tertiary rounded-lg p-4">
          <h3 className="text-sm font-medium mb-1">内存优化</h3>
          <p className="text-[11px] text-fg-secondary mb-3">仅对扩散模型（AnyText、SD 系列）生效，修改后重启后端进程才能应用</p>
          <div className="space-y-1">

            {/* Low-mem toggle */}
            <div className="flex items-center justify-between py-2 border-b border-border-subtle">
              <div>
                <div className="text-xs text-fg-primary">低内存模式 <span className="text-[10px] text-fg-accent ml-1">推荐</span></div>
                <div className="text-[11px] text-fg-secondary mt-0.5">
                  启用 Attention Slicing + VAE Tiling，显著降低显存峰值，速度略降
                </div>
              </div>
              <button
                onClick={() => setLowMem(!lowMem)}
                className={clsx(
                  'relative w-10 h-5 rounded-full transition-colors flex-shrink-0 ml-3',
                  lowMem ? 'bg-border-focus' : 'bg-bg-hover'
                )}
              >
                <div className={clsx(
                  'absolute top-0.5 w-4 h-4 rounded-full bg-white transition-transform',
                  lowMem ? 'translate-x-[22px]' : 'translate-x-[2px]'
                )} />
              </button>
            </div>

            {/* CPU Offload toggle */}
            <div className="flex items-center justify-between py-2 border-b border-border-subtle">
              <div>
                <div className="text-xs text-fg-primary">CPU 显存卸载</div>
                <div className="text-[11px] text-fg-secondary mt-0.5">
                  将模型权重动态卸载到内存，显存降至最低，速度明显下降
                </div>
              </div>
              <button
                onClick={() => setCpuOffload(!cpuOffload)}
                className={clsx(
                  'relative w-10 h-5 rounded-full transition-colors flex-shrink-0 ml-3',
                  cpuOffload ? 'bg-border-focus' : 'bg-bg-hover'
                )}
              >
                <div className={clsx(
                  'absolute top-0.5 w-4 h-4 rounded-full bg-white transition-transform',
                  cpuOffload ? 'translate-x-[22px]' : 'translate-x-[2px]'
                )} />
              </button>
            </div>

            {/* CPU Text Encoder toggle */}
            <div className="flex items-center justify-between py-2">
              <div>
                <div className="text-xs text-fg-primary">Text Encoder 在 CPU 运行</div>
                <div className="text-[11px] text-fg-secondary mt-0.5">
                  文字编码器在 CPU 执行，释放约 1 GB 显存，对速度影响较小
                </div>
              </div>
              <button
                onClick={() => setCpuTextencoder(!cpuTextencoder)}
                className={clsx(
                  'relative w-10 h-5 rounded-full transition-colors flex-shrink-0 ml-3',
                  cpuTextencoder ? 'bg-border-focus' : 'bg-bg-hover'
                )}
              >
                <div className={clsx(
                  'absolute top-0.5 w-4 h-4 rounded-full bg-white transition-transform',
                  cpuTextencoder ? 'translate-x-[22px]' : 'translate-x-[2px]'
                )} />
              </button>
            </div>

          </div>
        </section>

        {/* Network */}
        <section className="bg-bg-tertiary rounded-lg p-4">
          <h3 className="text-sm font-medium mb-3">网络 / HuggingFace</h3>
          <div className="space-y-3">
            <div>
              <label className="text-xs text-fg-secondary mb-1 block">HuggingFace Endpoint</label>
              <input
                type="text"
                value={hfEndpoint}
                onChange={(e) => setHfEndpoint(e.target.value)}
                className="w-full bg-bg-primary border border-border-subtle text-fg-primary text-xs px-2 py-1.5 rounded focus:border-border-focus focus:outline-none"
              />
            </div>
            <div>
              <label className="text-xs text-fg-secondary mb-1 block">HuggingFace Token</label>
              <input
                type="password"
                value={hfToken}
                onChange={(e) => setHfToken(e.target.value)}
                placeholder="hf_xxxx..."
                className="w-full bg-bg-primary border border-border-subtle text-fg-primary text-xs px-2 py-1.5 rounded focus:border-border-focus focus:outline-none"
              />
            </div>
            <div>
              <label className="text-xs text-fg-secondary mb-1 block">
                GitHub 镜像加速
                <span className="ml-1 text-[10px] text-fg-tertiary">（用于 rembg 抠图模型下载）</span>
              </label>
              <input
                type="text"
                value={githubMirror}
                onChange={(e) => setGithubMirror(e.target.value)}
                placeholder="例：https://mirror.ghproxy.com"
                className="w-full bg-bg-primary border border-border-subtle text-fg-primary text-xs px-2 py-1.5 rounded focus:border-border-focus focus:outline-none"
              />
            </div>
            <div className="bg-bg-primary rounded p-2.5 border border-border-subtle">
              <p className="text-[11px] text-fg-secondary leading-relaxed">
                <strong className="text-fg-primary">HF 镜像：</strong>
                无法访问 HuggingFace 时，将 Endpoint 改为
                <code className="bg-bg-hover px-1 rounded text-fg-accent mx-1">https://hf-mirror.com</code>。
                <br />
                <strong className="text-fg-primary">GitHub 镜像：</strong>
                抠图模型（BiRefNet / IS-Net 等）从 GitHub 下载，如速度慢可填入
                <code className="bg-bg-hover px-1 rounded text-fg-accent mx-1">https://mirror.ghproxy.com</code>
                加速。下载功能在「模型管理」页签中。
              </p>
            </div>
          </div>
        </section>

        {/* Actions */}
        <div className="flex gap-3 pt-2 pb-4">
          <button
            onClick={handleSave}
            disabled={isSaving}
            className="flex-1 py-2.5 bg-border-focus text-white text-sm font-medium rounded-md hover:bg-blue-600 transition-colors flex items-center justify-center gap-2 disabled:opacity-60"
          >
            <Save size={14} />
            {isSaving ? '保存中...' : '保存设置'}
          </button>
          <button
            onClick={handleReset}
            className="py-2.5 px-4 border border-border-subtle text-fg-secondary text-sm rounded-md hover:bg-bg-hover transition-colors flex items-center gap-2"
          >
            <RotateCcw size={14} />
            恢复默认
          </button>
        </div>
      </div>
      )}
    </div>
  )
}
