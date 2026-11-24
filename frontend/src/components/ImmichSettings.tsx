import { useBackendStore } from '../stores/useBackendStore'
import { useCallback, useEffect, useState } from 'react'
import Button from './ui/Button'
import { immichApi, jsonRequest, rememberImmichAccess, useImmich, type ImmichConnection } from '../services/immich'

type Operation = { name: string; state: string; kind: string; url: string | null }
const operationStatus: Record<string, string> = {
  created: '已上传', rejected: '上传被拒绝，可重试', duplicate: '图库已存在相同内容', replaced: '替换完成',
  uploading: '上传状态待确认', trashing: '移入回收站状态待确认',
  migration_pending: '新图已上传，迁移待完成', album_pending: '加入相册待完成',
}

export default function ImmichSettings() {
  const backend = useBackendStore(state => state.backendURL)
  const access = useImmich(state => state.accessBackend === backend ? state.access : '')
  const [token, setToken] = useState(access)
  const [url, setUrl] = useState('')
  const [key, setKey] = useState('')
  const [mode, setMode] = useState<ImmichConnection['mode']>('upload')
  const [configured, setConfigured] = useState(false)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [operations, setOperations] = useState<Operation[]>()

  const applyConnection = useCallback((connection: ImmichConnection) => {
    setUrl(connection.url)
    setMode(connection.mode)
    setConfigured(connection.configured)
    setMessage(connection.configured ? '已载入配置' : '访问已解锁，请配置 Immich')
  }, [])

  useEffect(() => {
    const { access, accessBackend } = useImmich.getState()
    if (!access || accessBackend !== backend) return
    const controller = new AbortController()
    setBusy(true)
    void immichApi().json<ImmichConnection>('/connection', { signal: controller.signal })
      .then(connection => {
        if (!controller.signal.aborted) applyConnection(connection)
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) setMessage(error instanceof Error ? error.message : String(error))
      })
      .finally(() => {
        if (!controller.signal.aborted) setBusy(false)
      })
    return () => controller.abort()
  }, [backend, applyConnection])

  async function run(action: 'load' | 'test' | 'save' | 'operations') {
    setBusy(true)
    setMessage('')
    const api = immichApi(token)
    try {
      if (action === 'operations') {
        setOperations(await api.json<Operation[]>('/operations'))
        rememberImmichAccess(token, api.backend)
        return
      }
      if (action === 'load') {
        const connection = await api.json<ImmichConnection>('/connection')
        rememberImmichAccess(token, api.backend)
        applyConnection(connection)
        return
      }
      const path = action === 'test' ? '/connection/test' : '/connection'
      const result = await api.json<{ version: string; account: string }>(
        path, jsonRequest(action === 'test' ? 'POST' : 'PUT', { url, key, mode }),
      )
      rememberImmichAccess(token, api.backend)
      if (action === 'save') {
        setKey('')
        setConfigured(true)
      }
      setMessage(`${action === 'save' ? '已保存' : '读取测试通过'} · Immich ${result.version} · ${result.account}。写入权限在操作时验证。`)
    } catch (error) {
      setMessage(String(error instanceof Error ? error.message : error))
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="overflow-auto p-5 space-y-4 max-w-xl">
      <h2 className="font-medium">Immich 服务</h2>
      <p className="text-sm text-fg-secondary">访问令牌验证通过后，会在当前标签页的会话中保留，刷新后自动载入连接。</p>
      <label className="block">HiImage Immich 访问令牌
        <input className="block w-full bg-bg-tertiary p-2" type="password" autoComplete="off" value={token} onChange={event => setToken(event.target.value)} />
      </label>
      <Button size="sm" disabled={!token || busy} onClick={() => void run('load')}>解锁并载入配置</Button>
      <label className="block">服务地址
        <input className="block w-full bg-bg-tertiary p-2" type="url" value={url} onChange={event => setUrl(event.target.value)} placeholder="https://photos.example.com" />
      </label>
      <label className="block">Immich API Key
        <input className="block w-full bg-bg-tertiary p-2" type="password" autoComplete="off" value={key}
          onChange={event => setKey(event.target.value)} placeholder={configured ? '已配置，同一地址留空沿用' : ''} />
      </label>
      <label className="block">连接模式
        <select className="block bg-bg-tertiary p-2" value={mode} onChange={event => setMode(event.target.value as ImmichConnection['mode'])}>
          <option value="read">只读</option>
          <option value="upload">读取与上传</option>
          <option value="replace">读取、上传与替换来源</option>
        </select>
      </label>
      <div className="flex gap-2">
        <Button size="sm" variant="secondary" disabled={busy || !token || !url} onClick={() => void run('test')}>测试连接</Button>
        <Button size="sm" disabled={busy || !token || !url} onClick={() => void run('save')}>保存连接</Button>
      </div>
      <p role="status">{message}</p>
      <Button size="sm" variant="secondary" disabled={busy || !token} onClick={() => void run('operations')}>查看最近写回记录</Button>
      {operations && <ul className="text-sm space-y-1">
        {!operations.length && <li>暂无写回记录</li>}
        {operations.map((entry, index) => <li key={index}>
          {entry.name} · {operationStatus[entry.state] ?? entry.state}
          {entry.url && <> · <a href={entry.url} target="_blank" rel="noreferrer" className="underline">在 Immich 中查看</a></>}
        </li>)}
      </ul>}
    </section>
  )
}
