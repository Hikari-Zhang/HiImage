import { useEffect, useRef, useState } from 'react'
import { createApiClient, ApiError } from '../services/apiClient'
import { createTaskAPI, type TaskSnapshot } from '../services/api/tasks'
import { forgetPendingTask, readPendingTasks, type PendingTask } from '../services/taskRecoveryStorage'
import { useProcessStore } from '../stores/useProcessStore'
import type { EditorSessionId } from '../types/editor'
import { downloadRecoveredResult } from '../services/recoveredResult'

const labels: Record<EditorSessionId, string> = {
  watermark: '去水印', upscale: '超分辨率', 'upscale-batch': '批量超分', synthesis: '智能合成', cutout: '抠图',
}
type RecoveryRow = PendingTask & { text: string; resultAvailable?: boolean; downloading?: boolean }

function statusText(snapshot: TaskSnapshot, taskId: string): string {
  if (snapshot.task_id !== taskId) throw new Error('Task ID mismatch')
  switch (snapshot.status) {
    case 'queued': return '后台正在排队，请等待任务结束，避免重复提交。'
    case 'running': {
      const progress = typeof snapshot.progress === 'number' && Number.isFinite(snapshot.progress)
        && snapshot.progress >= 0 && snapshot.progress <= 100 ? `（${Math.round(snapshot.progress)}%）` : ''
      return `后台仍在处理${progress}，请等待任务结束，避免重复提交。`
    }
    case 'succeeded': return snapshot.result_available === true
      ? '后台处理已完成，可以下载暂存结果。'
      : '后台处理已完成，但刷新前的图片结果无法取回；需要重新选择图片处理。'
    case 'failed': return '后台任务失败，可重新选择图片后重试。'
    case 'interrupted': return '请求已中断，结果无法取回；需要重新选择图片处理。'
    default: throw new Error('Unknown task status')
  }
}

export default function TaskRecoveryNotice({ backendURL }: { backendURL: string }) {
  const backend = createApiClient(backendURL).baseUrl
  const [rows, setRows] = useState<RecoveryRow[]>([])
  const [checking, setChecking] = useState(false)
  const [revision, setRevision] = useState(0)
  const downloads = useRef(new Map<string, AbortController>())

  useEffect(() => () => {
    downloads.current.forEach((controller) => controller.abort())
    downloads.current.clear()
  }, [backend])

  const download = async (row: RecoveryRow) => {
    if (downloads.current.has(row.taskId)) return
    const controller = new AbortController()
    downloads.current.set(row.taskId, controller)
    let timedOut = false
    const timeout = setTimeout(() => { timedOut = true; controller.abort() }, 60000)
    const update = (changes: Partial<RecoveryRow>) => setRows((current) => current.map((item) =>
      item.taskId === row.taskId && item.backendURL === row.backendURL ? { ...item, ...changes } : item))
    update({ downloading: true })
    try {
      const result = await createTaskAPI(createApiClient(row.backendURL)).getTaskResult(row.taskId, controller.signal)
      if (controller.signal.aborted) return
      await downloadRecoveredResult(result, controller.signal)
      if (!controller.signal.aborted) update({ text: '已发起结果下载，请检查浏览器下载记录。' })
    } catch (error) {
      if (timedOut) update({ text: '下载超时，请重试。' })
      if (!controller.signal.aborted) update({ text: error instanceof ApiError && [404, 410].includes(error.status)
        ? '暂存结果已过期或被清理，无法取回，请重新选择图片处理。'
        : '下载结果失败，请重试或刷新任务状态。', resultAvailable: !(error instanceof ApiError && [404, 410].includes(error.status)) })
    } finally {
      clearTimeout(timeout)
      if (downloads.current.get(row.taskId) === controller) {
        downloads.current.delete(row.taskId)
        update({ downloading: false })
      }
    }
  }

  useEffect(() => {
    let active = true
    const controller = new AbortController()
    const tasks = useProcessStore.getState().tasks
    const pending = readPendingTasks().filter((item) => item.backendURL === backend
      && !(tasks[item.taskId]?.status === 'running' && tasks[item.taskId]?.backendURL === backend))
    setRows(pending.map((item) => ({ ...item, text: '正在查询任务状态…' })))
    setChecking(pending.length > 0)
    if (!pending.length) return () => { active = false; controller.abort() }
    const timeout = setTimeout(() => controller.abort(), 10000)
    const api = createTaskAPI(createApiClient(backend))
    void Promise.all(pending.map(async (item) => {
      let text: string
      let resultAvailable = false
      try {
        const snapshot = await api.getTask(item.taskId, controller.signal)
        text = statusText(snapshot, item.taskId)
        resultAvailable = snapshot.status === 'succeeded' && snapshot.result_available === true
      } catch (error) {
        if (!active) return
        text = error instanceof ApiError && error.status === 404
          ? '查询不到任务记录，可能已过期或服务已重启，无法确认任务是否完成。'
          : controller.signal.aborted ? '查询超时，请重试。' : '暂时无法查询任务状态，请检查连接后重试。'
      }
      if (active && !controller.signal.aborted) {
        setRows((current) => current.map((row) => row.taskId === item.taskId ? { ...row, text, resultAvailable } : row))
      } else if (active) {
        setRows((current) => current.map((row) => row.taskId === item.taskId ? { ...row, text: '查询超时，请重试。' } : row))
      }
    })).finally(() => {
      clearTimeout(timeout)
      if (active) setChecking(false)
    })
    return () => { active = false; clearTimeout(timeout); controller.abort() }
  }, [backend, revision])

  const visibleRows = rows.filter((row) => row.backendURL === backend)
  if (!visibleRows.length) return null
  return (
    <section aria-label="刷新前任务状态" className="shrink-0 max-h-[30vh] overflow-auto border-b border-border-subtle bg-bg-secondary px-4 py-3 text-xs text-fg-secondary">
      <div className="flex items-center gap-3 mb-2">
        <strong>刷新前任务状态</strong>
        <button disabled={checking || rows.some((row) => row.downloading)} onClick={() => setRevision((value) => value + 1)} className="underline disabled:opacity-50">{checking ? '正在查询…' : '刷新任务状态'}</button>
      </div>
      <p>结果最多暂存 10 分钟，容量不足可能提前清理。原图、编辑内容和后续批量队列不恢复。移除提示不会取消后台任务。</p>
      <ul className="space-y-2 mt-2" aria-live="polite">
        {visibleRows.map((row) => (
          <li key={row.taskId} className="flex items-start gap-3">
            <span className="flex-1">{labels[row.sessionId]} · <span title={row.taskId}>{row.taskId.slice(0, 12)}</span>：{row.text}</span>
            {row.resultAvailable && <button disabled={row.downloading} className="underline shrink-0 disabled:opacity-50"
              aria-label={`下载任务结果 ${row.taskId}`} onClick={() => void download(row)}>{row.downloading ? '正在取回…' : '下载结果'}</button>}
            <button aria-label={`移除任务提示 ${row.taskId}`} className="underline shrink-0" onClick={() => {
              downloads.current.get(row.taskId)?.abort()
              downloads.current.delete(row.taskId)
              forgetPendingTask(row.taskId, row.backendURL)
              setRows((current) => current.filter((item) => item.taskId !== row.taskId))
            }}>移除提示</button>
          </li>
        ))}
      </ul>
    </section>
  )
}
