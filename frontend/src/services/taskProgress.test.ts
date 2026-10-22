import { beforeEach, describe, expect, it, vi } from 'vitest'
import { applyTaskProgressMessage } from './taskProgress'
import { useProcessStore } from '../stores/useProcessStore'

describe('applyTaskProgressMessage', () => {
  beforeEach(() => useProcessStore.setState({ tasks: {}, sessions: {} }))

  it('treats websocket complete as progress and waits for HTTP to finish', () => {
    const store = useProcessStore.getState()
    store.startProcess('watermark', 'model', 'task-a')

    applyTaskProgressMessage(
      'task-a',
      JSON.stringify({ type: 'complete', message: '后端阶段完成' }),
      store.updateProgress,
    )

    expect(useProcessStore.getState().sessions.watermark).toMatchObject({
      currentTaskId: 'task-a',
      isProcessing: true,
      progress: 100,
      statusMessage: '后端阶段完成',
    })
  })

  it.each([
    null, [], 'unexpected', { type: 'progress', percent: -10 },
    { type: 'progress', percent: 101 }, { type: 'progress', percent: '20' },
    { type: 'progress', percent: 20, task_id: 'another-task' },
  ])('ignores invalid or mismatched progress payload %j', (message) => {
    const update = vi.fn()
    applyTaskProgressMessage('task-a', JSON.stringify(message), update)
    expect(update).not.toHaveBeenCalled()
  })

  it('never forwards an object as a status message to React', () => {
    const update = vi.fn()
    applyTaskProgressMessage('task-a', '{"type":"progress","percent":30,"message":{"detail":"invalid"}}', update)
    expect(update).toHaveBeenCalledWith('task-a', 30, '')
  })
})
