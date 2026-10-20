import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ManagedWebSocket, requestAfterWebSocketOpen } from './managedWebSocket'

class FakeSocket {
  static instances: FakeSocket[] = []
  onmessage: ((event: MessageEvent) => void) | null = null
  onerror: (() => void) | null = null
  onclose: (() => void) | null = null
  onopen: (() => void) | null = null
  close = vi.fn()

  constructor(public readonly url: string) {
    FakeSocket.instances.push(this)
  }
}

describe('ManagedWebSocket lifecycle', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    FakeSocket.instances = []
  })

  afterEach(() => vi.useRealTimers())

  it('does not reconnect after StrictMode-style cleanup', () => {
    const manager = new ManagedWebSocket('ws://test', {
      createSocket: (url) => new FakeSocket(url) as unknown as WebSocket,
    })
    manager.start()
    const staleOnClose = FakeSocket.instances[0].onclose

    manager.dispose()
    staleOnClose?.()
    vi.runAllTimers()

    expect(FakeSocket.instances).toHaveLength(1)
    expect(FakeSocket.instances[0].close).toHaveBeenCalledOnce()
  })

  it('uses finite exponential reconnect attempts', () => {
    const manager = new ManagedWebSocket('ws://test', {
      createSocket: (url) => new FakeSocket(url) as unknown as WebSocket,
      maxRetries: 2,
      initialDelayMs: 100,
    })
    manager.start()
    FakeSocket.instances[0].onclose?.()
    vi.advanceTimersByTime(100)
    FakeSocket.instances[1].onclose?.()
    vi.advanceTimersByTime(200)
    FakeSocket.instances[2].onclose?.()
    vi.runAllTimers()

    expect(FakeSocket.instances).toHaveLength(3)
  })

  it('resets the retry budget after a successful open', () => {
    const manager = new ManagedWebSocket('ws://test', {
      createSocket: (url) => new FakeSocket(url) as unknown as WebSocket,
      maxRetries: 1,
      initialDelayMs: 100,
    })
    manager.start()
    FakeSocket.instances[0].onclose?.()
    vi.advanceTimersByTime(100)
    FakeSocket.instances[1].onopen?.()
    FakeSocket.instances[1].onclose?.()
    vi.advanceTimersByTime(100)

    expect(FakeSocket.instances).toHaveLength(3)
  })

  it('waits for open before invoking the request', async () => {
    const request = vi.fn(async () => 'result')
    const manager = new ManagedWebSocket('ws://test', {
      createSocket: (url) => new FakeSocket(url) as unknown as WebSocket,
    })

    const result = requestAfterWebSocketOpen(manager, request, 1000)
    expect(request).not.toHaveBeenCalled()

    FakeSocket.instances[0].onopen?.()
    await expect(result).resolves.toBe('result')
    expect(request).toHaveBeenCalledOnce()
  })

  it('rejects a pending open when disposed without leaving a timer', async () => {
    const manager = new ManagedWebSocket('ws://test', {
      createSocket: (url) => new FakeSocket(url) as unknown as WebSocket,
    })
    const pending = manager.startAndWait(1000)
    const rejected = expect(pending).rejects.toThrow('disposed')

    manager.dispose()

    await rejected
    expect(vi.getTimerCount()).toBe(0)
  })

  it('rejects and does not invoke the request when opening finally fails', async () => {
    const request = vi.fn(async () => 'result')
    const manager = new ManagedWebSocket('ws://test', {
      createSocket: (url) => new FakeSocket(url) as unknown as WebSocket,
      maxRetries: 0,
    })
    const pending = requestAfterWebSocketOpen(manager, request, 1000)
    const rejected = expect(pending).rejects.toThrow('failed to open')

    FakeSocket.instances[0].onclose?.()

    await rejected
    expect(request).not.toHaveBeenCalled()
  })

  it('times out a pending open and leaves no hanging promise or timer', async () => {
    const manager = new ManagedWebSocket('ws://test', {
      createSocket: (url) => new FakeSocket(url) as unknown as WebSocket,
    })
    const pending = manager.startAndWait(100)
    const rejected = expect(pending).rejects.toThrow('timed out')

    await vi.advanceTimersByTimeAsync(100)

    await rejected
    expect(FakeSocket.instances[0].close).toHaveBeenCalledOnce()
    expect(vi.getTimerCount()).toBe(0)
  })
})
