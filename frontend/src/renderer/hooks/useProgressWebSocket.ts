import { useEffect } from 'react'
import { ApiPath } from '../constants'
import { useBackendStore } from '../stores/useBackendStore'
import { useProcessStore } from '../stores/useProcessStore'

/**
 * App 级进度 WebSocket：只连接一次，所有处理页共享 useProcessStore。
 * 仅在任务进行中应用 progress 事件；完成 / 失败由各页 HTTP 回调负责。
 */
export function useProgressWebSocket() {
  const backendURL = useBackendStore((s) => s.backendURL)
  const setWsConnected = useBackendStore((s) => s.setWsConnected)

  useEffect(() => {
    let disposed = false
    let ws: WebSocket | null = null
    let retryTimer: ReturnType<typeof setTimeout> | null = null

    const connect = async () => {
      if (disposed) return
      try {
        const base = window.electronAPI
          ? await window.electronAPI.getBackendURL()
          : backendURL
        if (disposed) return

        const wsURL = base.replace(/^http/, 'ws') + ApiPath.WS_PROGRESS
        ws = new WebSocket(wsURL)

        ws.onopen = () => {
          if (!disposed) setWsConnected(true)
        }

        ws.onmessage = (event) => {
          if (!useProcessStore.getState().isProcessing) return
          try {
            const msg = JSON.parse(event.data)
            if (msg.type === 'progress' && typeof msg.percent === 'number') {
              useProcessStore.getState().updateProgress(msg.percent, msg.message || '')
            }
          } catch (err) {
            console.error('Failed to parse progress message:', err)
          }
        }

        ws.onerror = () => {
          console.error('Progress WebSocket error')
        }

        ws.onclose = () => {
          setWsConnected(false)
          if (disposed) return
          retryTimer = setTimeout(connect, 3000)
        }
      } catch (err) {
        console.error('Failed to connect progress WebSocket:', err)
        if (!disposed) retryTimer = setTimeout(connect, 3000)
      }
    }

    connect()

    return () => {
      disposed = true
      if (retryTimer) clearTimeout(retryTimer)
      ws?.close()
      setWsConnected(false)
    }
  }, [backendURL, setWsConnected])
}
