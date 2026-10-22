import { describe, expect, it, vi } from 'vitest'
import {
  createTaskProgressSession,
  type ProgressConnection,
  type ProgressConnectionFactory,
} from './taskProgress'

class FakeConnection implements ProgressConnection {
  readonly dispose = vi.fn()
  private resolveOpen!: () => void
  private rejectOpen!: (error: Error) => void
  private readonly opening = new Promise<void>((resolve, reject) => {
    this.resolveOpen = resolve
    this.rejectOpen = reject
  })

  startAndWait = vi.fn(() => this.opening)
  open(): void { this.resolveOpen() }
  fail(): void { this.rejectOpen(new Error('offline')) }
}

function factoryHarness() {
  const connections: FakeConnection[] = []
  const urls: string[] = []
  const factory: ProgressConnectionFactory = (url) => {
    urls.push(url)
    const connection = new FakeConnection()
    connections.push(connection)
    return connection
  }
  return { connections, urls, factory }
}

describe('task progress session integration matrix', () => {
  it('does not submit a replaced task after its WebSocket finally opens', async () => {
    const harness = factoryHarness()
    const post = vi.fn(async () => 'unexpected')
    let current = true
    const session = createTaskProgressSession('replaced', {
      createConnection: harness.factory,
      websocketUrl: () => 'ws://test/progress',
      isCurrent: () => current,
    })
    const pending = session.run(post)
    const rejected = expect(pending).rejects.toThrow('任务已被新任务替代')
    current = false
    harness.connections[0].open()
    await rejected
    expect(post).not.toHaveBeenCalled()
    expect(harness.connections[0].dispose).toHaveBeenCalledOnce()
  })
  it.each(['watermark', 'cutout', 'upscale-single', 'synthesis'])(
    'waits for open, sends matching task id, and finally closes for %s',
    async (kind) => {
      const harness = factoryHarness()
      const taskId = `${kind}-task`
      const post = vi.fn(async (requestTaskId: string) => requestTaskId)
      const session = createTaskProgressSession(taskId, {
        createConnection: harness.factory,
        websocketUrl: (id) => `ws://test/progress?task_id=${id}`,
      })

      const pending = session.run(post)
      expect(post).not.toHaveBeenCalled()
      expect(harness.urls[0]).toContain(`task_id=${taskId}`)

      harness.connections[0].open()
      await expect(pending).resolves.toBe(taskId)
      expect(post).toHaveBeenCalledWith(taskId)
      expect(harness.connections[0].dispose).toHaveBeenCalledOnce()
    },
  )

  it('does not POST when opening fails and still disposes', async () => {
    const harness = factoryHarness()
    const post = vi.fn(async () => 'unexpected')
    const session = createTaskProgressSession('failed-task', {
      createConnection: harness.factory,
      websocketUrl: () => 'ws://test/progress',
    })
    const pending = session.run(post)
    const rejected = expect(pending).rejects.toThrow('进度连接失败')

    harness.connections[0].fail()

    await rejected
    expect(post).not.toHaveBeenCalled()
    expect(harness.connections[0].dispose).toHaveBeenCalledOnce()
  })

  it('isolates every batch item with its own task id and connection', async () => {
    const harness = factoryHarness()
    const first = createTaskProgressSession('batch-item-1', {
      createConnection: harness.factory,
      websocketUrl: (id) => `ws://test/progress?task_id=${id}`,
    })
    const second = createTaskProgressSession('batch-item-2', {
      createConnection: harness.factory,
      websocketUrl: (id) => `ws://test/progress?task_id=${id}`,
    })
    const seen: string[] = []
    const firstRun = first.run(async (id) => { seen.push(id) })
    const secondRun = second.run(async (id) => { seen.push(id) })

    harness.connections.forEach((connection) => connection.open())
    await Promise.all([firstRun, secondRun])

    expect(seen).toEqual(['batch-item-1', 'batch-item-2'])
    expect(new Set(harness.urls).size).toBe(2)
    expect(harness.connections.every((connection) => connection.dispose.mock.calls.length === 1)).toBe(true)
  })

  it('rejects duplicate execution without repeating POST or disposing the active connection', async () => {
    const harness = factoryHarness()
    const session = createTaskProgressSession('once', { createConnection: harness.factory })
    const post = vi.fn(async () => 'result')
    const first = session.run(post)
    await expect(session.run(post)).rejects.toThrow('只能执行一次')
    expect(harness.connections[0].dispose).not.toHaveBeenCalled()
    harness.connections[0].open()
    await expect(first).resolves.toBe('result')
    await expect(session.run(post)).rejects.toThrow('只能执行一次')
    expect(post).toHaveBeenCalledOnce()
    expect(harness.connections).toHaveLength(1)
  })

  it('does not allocate a connection for a task replaced before execution', async () => {
    const factory = vi.fn()
    const post = vi.fn()
    const session = createTaskProgressSession('stale', { createConnection: factory, isCurrent: () => false })
    expect(factory).not.toHaveBeenCalled()
    await expect(session.run(post)).rejects.toThrow('任务已被新任务替代')
    expect(factory).not.toHaveBeenCalled()
    expect(post).not.toHaveBeenCalled()
  })

  it('handles a connection factory failure as a retryable new-session failure', async () => {
    const post = vi.fn(async () => 'recovered')
    const failed = createTaskProgressSession('failed', {
      createConnection: () => { throw new Error('constructor failed') },
    })
    await expect(failed.run(post)).rejects.toThrow('进度连接失败: constructor failed')
    expect(post).not.toHaveBeenCalled()
    const harness = factoryHarness()
    const retry = createTaskProgressSession('retry', { createConnection: harness.factory })
    const pending = retry.run(post)
    harness.connections[0].open()
    await expect(pending).resolves.toBe('recovered')
    expect(post).toHaveBeenCalledWith('retry')
  })

  it('disposes on HTTP failure and never automatically retries inference', async () => {
    const harness = factoryHarness()
    const session = createTaskProgressSession('http-failed', { createConnection: harness.factory })
    const failure = new Error('network interrupted')
    const post = vi.fn(async () => { throw failure })
    const pending = session.run(post)
    const rejected = expect(pending).rejects.toBe(failure)
    harness.connections[0].open()
    await rejected
    expect(post).toHaveBeenCalledOnce()
    expect(harness.connections[0].dispose).toHaveBeenCalledOnce()
  })

  it('ignores progress after replacement and after the HTTP request finishes', async () => {
    let onMessage!: (event: MessageEvent) => void
    let current = true
    let finish!: (value: string) => void
    const connection = { startAndWait: async () => {}, dispose: vi.fn() }
    const updateProgress = vi.fn()
    const session = createTaskProgressSession('lifecycle', {
      isCurrent: () => current, updateProgress,
      createConnection: (_url, handlers) => { onMessage = handlers.onMessage; return connection },
    })
    const post = vi.fn(() => new Promise<string>((resolve) => { finish = resolve }))
    const pending = session.run(post)
    await Promise.resolve()
    const event = { data: '{"type":"progress","percent":30}' } as MessageEvent
    onMessage(event)
    expect(updateProgress).toHaveBeenCalledOnce()
    current = false
    onMessage(event)
    expect(updateProgress).toHaveBeenCalledOnce()
    finish('done')
    await pending
    current = true
    onMessage(event)
    expect(updateProgress).toHaveBeenCalledOnce()
    expect(connection.dispose).toHaveBeenCalledOnce()
  })
})
