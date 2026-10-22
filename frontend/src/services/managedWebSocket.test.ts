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

  it('returns a rejected promise if socket construction fails synchronously', async () => {
    const manager = new ManagedWebSocket('ws://test', {
      createSocket: () => { throw new Error('invalid URL') }, maxRetries: 0,
    })
    const pending = manager.startAndWait()
    expect(pending).toBeInstanceOf(Promise)
    await expect(pending).rejects.toThrow('failed to open')
    expect(vi.getTimerCount()).toBe(0)
    manager.dispose()
  })

  it('ignores stale close, message and error events after a reconnect', async () => {
    const onMessage = vi.fn()
    const onError = vi.fn()
    const manager = new ManagedWebSocket('ws://test', {
      createSocket: (url) => new FakeSocket(url) as unknown as WebSocket,
      initialDelayMs: 100, onMessage, onError,
    })
    const opened = manager.startAndWait()
    const old = FakeSocket.instances[0]
    const stale = { close: old.onclose, message: old.onmessage, error: old.onerror }
    old.onopen?.()
    await opened
    old.onclose?.()
    stale.close?.()
    vi.advanceTimersByTime(100)
    const current = FakeSocket.instances[1]
    current.onopen?.()
    stale.close?.()
    stale.message?.({ data: 'stale' } as MessageEvent)
    stale.error?.()
    expect(onMessage).not.toHaveBeenCalled()
    expect(onError).not.toHaveBeenCalled()
    const request = vi.fn(async () => 'still connected')
    await expect(requestAfterWebSocketOpen(manager, request)).resolves.toBe('still connected')
    vi.advanceTimersByTime(1000)
    expect(FakeSocket.instances).toHaveLength(2)
    current.onmessage?.({ data: 'current' } as MessageEvent)
    expect(onMessage).toHaveBeenCalledOnce()
    manager.dispose()
    expect(vi.getTimerCount()).toBe(0)
  })

  it('detaches handlers and ignores already queued events after disposal', () => {
    const onMessage = vi.fn()
    const onError = vi.fn()
    const manager = new ManagedWebSocket('ws://test', {
      createSocket: (url) => new FakeSocket(url) as unknown as WebSocket, onMessage, onError,
    })
    manager.start()
    const socket = FakeSocket.instances[0]
    const message = socket.onmessage
    const error = socket.onerror
    manager.dispose()
    manager.dispose()
    message?.({ data: 'late' } as MessageEvent)
    error?.()
    expect(onMessage).not.toHaveBeenCalled()
    expect(onError).not.toHaveBeenCalled()
    expect(socket.onmessage).toBeNull()
    expect(socket.onclose).toBeNull()
    expect(socket.close).toHaveBeenCalledOnce()
  })

  it('reconnects progress during an in-flight request without resubmitting it', async () => {
    let finish!: (value: string) => void
    const request = vi.fn(() => new Promise<string>((resolve) => { finish = resolve }))
    const manager = new ManagedWebSocket('ws://test', {
      createSocket: (url) => new FakeSocket(url) as unknown as WebSocket, initialDelayMs: 100,
    })
    const pending = requestAfterWebSocketOpen(manager, request)
    FakeSocket.instances[0].onopen?.()
    await Promise.resolve()
    expect(request).toHaveBeenCalledOnce()
    FakeSocket.instances[0].onclose?.()
    vi.advanceTimersByTime(100)
    FakeSocket.instances[1].onopen?.()
    expect(request).toHaveBeenCalledOnce()
    finish('HTTP result')
    await expect(pending).resolves.toBe('HTTP result')
    manager.dispose()
    expect(vi.getTimerCount()).toBe(0)
  })
})
