import { useState, useEffect, useRef } from 'react'
import { Trash2, ArrowDown, Filter, CircleAlert, Info, AlertTriangle, Bug } from 'lucide-react'
import { clsx } from 'clsx'
import { useFirstVisible } from '../hooks/useFirstVisible'
import { apiClient, ensureApiSuccess } from '../services/apiClient'
import { ManagedWebSocket } from '../services/managedWebSocket'

interface LogEntry {
  timestamp: string
  level: 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR'
  message: string
  source: string
}

const LEVEL_CONFIG = {
  DEBUG: { color: 'text-fg-secondary', bg: '', icon: Bug },
  INFO: { color: 'text-fg-accent', bg: '', icon: Info },
  WARNING: { color: 'text-status-warning', bg: 'bg-yellow-900/10', icon: AlertTriangle },
  ERROR: { color: 'text-status-error', bg: 'bg-red-900/15', icon: CircleAlert },
}

export default function Logs({ isVisible = false }: { isVisible?: boolean }) {
  const [logs, setLogs] = useState<LogEntry[]>([])
  const [filter, setFilter] = useState<string>('ALL')
  const [autoScroll, setAutoScroll] = useState(true)
  const scrollRef = useRef<HTMLDivElement>(null)
  const ready = useFirstVisible(isVisible)

  // Fetch initial logs
  useEffect(() => {
    if (!ready) return
    const controller = new AbortController()
    let active = true
    const fetchLogs = async () => {
      try {
        const data = await apiClient.json<{ logs: LogEntry[] }>('/api/logs?limit=500', { signal: controller.signal }, '读取日志失败')
        if (active) setLogs(data.logs)
      } catch (err) {
        if (active) console.error('Failed to fetch logs:', err)
      }
    }
    fetchLogs()
    return () => { active = false; controller.abort() }
  }, [ready])

  // WebSocket for real-time logs
  useEffect(() => {
    if (!ready) return
    const connection = new ManagedWebSocket(apiClient.websocketUrl('/api/ws/logs'), {
      onMessage: (event) => {
        const msg = JSON.parse(event.data)
        if (msg.type === 'log' && msg.data) {
          setLogs((prev) => [...prev.slice(-499), msg.data])
        }
      },
      onError: () => {
        console.error('Log WebSocket error')
      },
    })
    connection.start()
    return () => connection.dispose()
  }, [ready])

  // Auto scroll to bottom
  useEffect(() => {
    if (autoScroll && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [logs, autoScroll])

  const handleClear = async () => {
    try {
      await ensureApiSuccess(await apiClient.fetch('/api/logs', { method: 'DELETE' }), '清空日志失败')
      setLogs([])
    } catch (err) {
      console.error('Failed to clear logs:', err)
    }
  }

  const filteredLogs = filter === 'ALL'
    ? logs
    : logs.filter((log) => log.level === filter)

  const errorCount = logs.filter((l) => l.level === 'ERROR').length
  const warningCount = logs.filter((l) => l.level === 'WARNING').length

  return (
    <div className="flex-1 flex flex-col min-h-0">
      {/* Top bar with titlebar safe area */}
      <div className="page-header">
        <div className="flex items-center flex-wrap w-full gap-3">
          <h1 className="text-xl font-semibold tracking-tight text-fg-primary">日志</h1>
          <div className="flex items-center gap-2 ml-2 no-drag">
          {errorCount > 0 && (
            <span className="text-xs text-status-error flex items-center gap-1">
              <CircleAlert size={12} />
              {errorCount}
            </span>
          )}
          {warningCount > 0 && (
            <span className="text-xs text-status-warning flex items-center gap-1">
              <AlertTriangle size={12} />
              {warningCount}
            </span>
          )}
        </div>
        <div className="flex-1" />

        {/* Filter */}
        <div className="flex items-center gap-1 no-drag">
          <Filter size={12} className="text-fg-secondary" />
          <select
            aria-label="日志级别"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            className="bg-bg-primary border border-border-subtle text-fg-primary text-xs px-3 py-2 rounded-xl"
          >
            <option value="ALL">全部</option>
            <option value="ERROR">仅 Error</option>
            <option value="WARNING">Warning</option>
            <option value="INFO">Info</option>
            <option value="DEBUG">Debug</option>
          </select>
        </div>

        {/* Auto scroll toggle */}
        <button
          onClick={() => setAutoScroll(!autoScroll)}
          className={clsx(
            'flex items-center gap-1 text-xs px-3 py-2 rounded-xl transition-colors no-drag',
            autoScroll ? 'text-fg-accent bg-bg-active' : 'text-fg-secondary hover:bg-bg-hover'
          )}
          title="自动滚动到底部"
        >
          <ArrowDown size={12} />
          自动滚动
        </button>

        {/* Clear */}
        <button
          onClick={handleClear}
          className="flex items-center gap-1 text-xs text-fg-secondary hover:text-status-error px-3 py-2 rounded-xl hover:bg-bg-hover transition-colors no-drag"
          title="清空日志"
        >
          <Trash2 size={12} />
          清空
        </button>
      </div>
      </div>

      {/* Log content */}
      <div
        ref={scrollRef}
        className="flex-1 min-h-0 overflow-y-auto font-mono text-xs leading-5 p-0"
      >
        {filteredLogs.length === 0 ? (
          <div className="flex items-center justify-center h-full text-fg-secondary">
            <p>暂无日志</p>
          </div>
        ) : (
          <table className="w-full border-collapse">
            <tbody>
              {filteredLogs.map((log, i) => {
                const config = LEVEL_CONFIG[log.level] || LEVEL_CONFIG.INFO
                const Icon = config.icon
                return (
                  <tr
                    key={i}
                    className={clsx(
                      'border-b border-border-subtle/30 hover:bg-bg-hover/50 transition-colors',
                      config.bg
                    )}
                  >
                    {/* Timestamp */}
                    <td className="py-1 px-2 text-fg-secondary whitespace-nowrap w-[160px] align-top">
                      {formatTimestamp(log.timestamp)}
                    </td>
                    {/* Level badge */}
                    <td className="py-1 px-1 w-[80px] align-top">
                      <span className={clsx('inline-flex items-center gap-1', config.color)}>
                        <Icon size={11} />
                        {log.level}
                      </span>
                    </td>
                    {/* Source */}
                    <td className="py-1 px-1 text-fg-secondary w-[80px] align-top truncate">
                      {log.source || '-'}
                    </td>
                    {/* Message */}
                    <td className="py-1 px-2 text-fg-primary break-all">
                      {log.message}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>

      {/* Status bar */}
      <div className="h-6 bg-bg-secondary border-t border-border-subtle flex items-center px-3 text-[11px] text-fg-secondary">
        <span>共 {filteredLogs.length} 条日志</span>
        {filter !== 'ALL' && <span className="ml-2">(过滤: {filter})</span>}
      </div>
    </div>
  )
}

function formatTimestamp(iso: string): string {
  try {
    const d = new Date(iso)
    const hh = String(d.getHours()).padStart(2, '0')
    const mm = String(d.getMinutes()).padStart(2, '0')
    const ss = String(d.getSeconds()).padStart(2, '0')
    const ms = String(d.getMilliseconds()).padStart(3, '0')
    return `${hh}:${mm}:${ss}.${ms}`
  } catch {
    return iso
  }
}
