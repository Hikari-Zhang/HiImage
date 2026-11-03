import { useState, useEffect } from 'react'
import LazyPage from '../components/layout/PageBoundary'
import { getSettingsDraftStore, useSettingsDraft } from '../stores/useSettingsDraft'
import { Save, RotateCcw, CheckCircle2, XCircle, Loader2 } from 'lucide-react'
import { clsx } from 'clsx'
import PageHeader from '../components/layout/PageHeader'
import { showToast } from '../components/ui'
import { useSettingsStore } from '../stores/useSettingsStore'
import { useBackendStore } from '../stores/useBackendStore'
import { useBackendAPI } from '../hooks/useBackendAPI'
import { useFirstVisible } from '../hooks/useFirstVisible'
const loadModelManager = () => import('./ModelManager')

type SettingsTab = 'general' | 'models'

// 默认值（用于「恢复默认」）
const DEFAULTS = {
  device: 'cpu',
  hfEndpoint: 'https://huggingface.com',
  hfToken: '',
  githubMirror: '',
  defaultDilation: 10,
  disableNsfw: true,
  downloadMaxConcurrent: 3,
  upscaleTile: 0,  // 0 = 自动计算
}

export default function Settings({ isVisible = false }: { isVisible?: boolean }) {
  const backendURL = useBackendStore((s) => s.backendURL)
  const isConnected = useBackendStore((s) => s.isConnected)
  const checkHealth = useBackendStore((s) => s.checkHealth)
  const store = useSettingsStore()
  const { getDevices } = useBackendAPI()
  const ready = useFirstVisible(isVisible)

  const {
    activeTab, setActiveTab, device, setDevice,
    hfEndpoint, setHfEndpoint, hfToken, setHfToken,
    githubMirror, setGithubMirror, dilation, setDilation, disableNsfw, setDisableNsfw,
    downloadMaxConcurrent, setDownloadMaxConcurrent, upscaleTile, setUpscaleTile,
    isSaving, setIsSaving, settingsLoadError, setSettingsLoadError,
  } = useSettingsDraft(backendURL)

  // 设备可用性（从后端检测）
  const [deviceAvailability, setDeviceAvailability] = useState<
    Record<string, { available: boolean; desc: string; reason?: string }>
  >({})
  const [devicesLoading, setDevicesLoading] = useState(true)

  // 初始化：从当前浏览器服务读取应用设置
  useEffect(() => {
    if (!ready) return
    let active = true
    const controller = new AbortController()
    // 读取应用设置
    setSettingsLoadError('')
    if (!getSettingsDraftStore(backendURL).getState().isSaving) store.loadSettings(backendURL, controller.signal).then(() => {
      if (active) getSettingsDraftStore(backendURL).getState().syncFromSettings()
    }).catch((err) => {
      if (!active) return
      const message = `读取设置失败: ${err instanceof Error ? err.message : String(err)}`
      setSettingsLoadError(message)
      showToast('error', message)
    })

    // 检测计算设备可用性
    setDevicesLoading(true)
    checkHealth()
    getDevices(controller.signal)
      .then((data) => {
        if (active && data?.devices) {
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
      .finally(() => { if (active) setDevicesLoading(false) })
    return () => { active = false; controller.abort() }
  }, [backendURL, ready, checkHealth])

  const handleSave = async () => {
    if (getSettingsDraftStore(backendURL).getState().isSaving) return
    const revision = getSettingsDraftStore(backendURL).getState().revision
    setIsSaving(true)
    try {
      // 先把 draft 写入 store
      store.setSettings({
        device,
        hfEndpoint,
        hfToken,
        githubMirror,
        defaultDilation: Number(dilation) || DEFAULTS.defaultDilation,
        disableNsfw,
        downloadMaxConcurrent: Math.max(1, Math.min(10, Number(downloadMaxConcurrent) || 3)),
        upscaleTile: Number(upscaleTile) || 0,
      })
      // 再持久化到后端
      await useSettingsStore.getState().saveSettings(backendURL)
      getSettingsDraftStore(backendURL).getState().markSaved(revision)
      showToast('success', '设置已保存')
    } catch (err: any) {
      showToast('error', `保存失败: ${err.message}`)
    } finally {
      setIsSaving(false)
    }
  }

  const handleClearHfToken = async () => {
    if (!window.confirm('确定要清除已配置的 HuggingFace Token 吗？')) return
    try {
      await store.clearHfToken(backendURL)
      setHfToken('')
      showToast('success', 'HuggingFace Token 已清除')
    } catch (err: any) {
      showToast('error', `清除失败: ${err.message}`)
    }
  }

  const handleReset = () => {
    setDevice(DEFAULTS.device)
    setHfEndpoint(DEFAULTS.hfEndpoint)
    setHfToken(DEFAULTS.hfToken)
    setGithubMirror(DEFAULTS.githubMirror)
    setDilation(String(DEFAULTS.defaultDilation))
    setDisableNsfw(DEFAULTS.disableNsfw)
    setDownloadMaxConcurrent(String(DEFAULTS.downloadMaxConcurrent))
    setUpscaleTile(String(DEFAULTS.upscaleTile))
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

      {activeTab === 'models' && (
        <div className="flex flex-1 min-h-0 overflow-hidden">
          <LazyPage load={loadModelManager} />
        </div>
      )}

      {/* 通用设置页签 */}
      {activeTab === 'general' && (
      <div className="flex-1 min-h-0 overflow-y-auto p-5 space-y-4 max-w-[640px]">
        {settingsLoadError && (
          <div role="alert" className="rounded-md border border-status-error/40 bg-status-error/10 p-3 text-xs text-status-error">
            {settingsLoadError}
          </div>
        )}
        <section className="bg-bg-tertiary rounded-lg p-4">
          <div className="flex items-center justify-between">
            <div>
              <h3 className="text-sm font-medium">当前服务</h3>
              <p className="text-[11px] text-fg-secondary mt-1">{backendURL || '当前页面同源服务'}</p>
            </div>
            {isConnected
              ? <span className="text-xs text-status-success flex items-center gap-1"><CheckCircle2 size={13} /> 服务正常</span>
              : <span className="text-xs text-status-error flex items-center gap-1"><XCircle size={13} /> 服务不可用</span>}
          </div>
          <button onClick={() => checkHealth()} className="mt-3 text-xs px-3 py-1.5 bg-bg-primary border border-border-subtle rounded">
            刷新健康状态
          </button>
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

        {/* Upscale Settings */}
        <section className="bg-bg-tertiary rounded-lg p-4">
          <h3 className="text-sm font-medium mb-3">超分辨率设置</h3>
          <div className="space-y-3">
            <div>
              <label className="text-xs text-fg-secondary mb-1 block">
                默认 Tile 大小
                <span className="ml-1 text-[10px] text-fg-tertiary">（0 = 自动计算）</span>
              </label>
              <input
                type="number"
                min={0}
                max={2048}
                step={64}
                value={upscaleTile}
                onChange={(e) => setUpscaleTile(e.target.value)}
                className="w-full bg-bg-primary border border-border-subtle text-fg-primary text-xs px-2 py-1.5 rounded focus:border-border-focus focus:outline-none"
              />
              <p className="text-[11px] text-fg-tertiary mt-1">
                分块大小：0 = 自动计算；建议值：256 / 512 / 1024。大图处理时降低此值可节省显存。
              </p>
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
              <div className="flex items-center justify-between mb-1">
                <label htmlFor="hf-token" className="text-xs text-fg-secondary">HuggingFace Token</label>
                {store.hfTokenConfigured && (
                  <span className="text-[11px] text-status-success">Token 已配置</span>
                )}
              </div>
              <input
                id="hf-token"
                type="password"
                value={hfToken}
                onChange={(e) => setHfToken(e.target.value)}
                placeholder={store.hfTokenConfigured ? '输入新 Token 以替换' : 'hf_xxxx...'}
                className="w-full bg-bg-primary border border-border-subtle text-fg-primary text-xs px-2 py-1.5 rounded focus:border-border-focus focus:outline-none"
              />
              {store.hfTokenConfigured && (
                <button
                  type="button"
                  onClick={handleClearHfToken}
                  className="mt-2 text-xs text-status-error hover:underline"
                >
                  清除已配置 Token
                </button>
              )}
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
