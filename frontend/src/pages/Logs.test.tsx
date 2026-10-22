import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import Logs from './Logs'

const connections = vi.hoisted(() => ({ start: vi.fn(), dispose: vi.fn() }))
vi.mock('../services/managedWebSocket', () => ({
  ManagedWebSocket: class {
    start = connections.start
    dispose = connections.dispose
  },
}))

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks() })

describe('log deletion respects HTTP status', () => {
  it('closes its log connection and cancels initial loading when leaving', () => {
    connections.start.mockClear()
    connections.dispose.mockClear()
    let signal!: AbortSignal
    vi.stubGlobal('fetch', vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
      signal = init!.signal as AbortSignal
      return new Promise<Response>((_resolve, reject) => {
        signal.addEventListener('abort', () => reject(new DOMException('cancelled', 'AbortError')), { once: true })
      })
    }))
    const page = render(<Logs isVisible />)
    expect(connections.start).toHaveBeenCalledOnce()
    expect(signal.aborted).toBe(false)
    page.unmount()
    expect(connections.dispose).toHaveBeenCalledOnce()
    expect(signal.aborted).toBe(true)
  })

  it.each([false, true])('updates the displayed logs only on successful deletion (%s)', async (success) => {
    const error = vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.stubGlobal('fetch', vi.fn(async (_path: string, init?: RequestInit) => {
      if (init?.method === 'DELETE') {
        return success ? new Response(null, { status: 204 }) : new Response('日志服务不可用', { status: 503 })
      }
      return new Response(JSON.stringify({ logs: [{ timestamp: '12:00', level: 'INFO', source: 'test', message: 'existing log' }] }))
    }))
    render(<Logs isVisible />)
    expect(await screen.findByText('existing log')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '清空' }))
    if (success) {
      await waitFor(() => expect(screen.queryByText('existing log')).not.toBeInTheDocument())
    } else {
      await waitFor(() => expect(error).toHaveBeenCalledWith('Failed to clear logs:', expect.objectContaining({ status: 503 })))
      expect(screen.getByText('existing log')).toBeInTheDocument()
    }
  })
})
