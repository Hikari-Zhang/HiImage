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
})
