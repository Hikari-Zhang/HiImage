import { beforeEach, describe, expect, it } from 'vitest'
import { isCurrentTask, useProcessStore } from './useProcessStore'

describe('useProcessStore task isolation', () => {
  beforeEach(() => useProcessStore.getState().reset())

  it('ignores progress and terminal updates from an old task', () => {
    const store = useProcessStore.getState()
    store.startProcess('model-a', 'task-a')
    store.startProcess('model-b', 'task-b')

    store.updateProgress('task-a', 80, 'old progress')
    store.finishProcess('task-a', 'old complete')
    store.setError('task-a', 'old error')

    expect(useProcessStore.getState()).toMatchObject({
      currentTaskId: 'task-b',
      currentModel: 'model-b',
      isProcessing: true,
      progress: 0,
      statusMessage: '正在初始化...',
    })
  })

  it('applies updates only to the current task and clears its id on finish', () => {
    const store = useProcessStore.getState()
    store.startProcess('model-a', 'task-a')
    store.updateProgress('task-a', 40, 'running')
    expect(useProcessStore.getState().progress).toBe(40)

    store.finishProcess('task-a', 'done')
    expect(useProcessStore.getState()).toMatchObject({
      currentTaskId: null,
      isProcessing: false,
      progress: 100,
      statusMessage: 'done',
    })
  })

  it('prevents an old HTTP response from writing results or finishing a new task', async () => {
    const store = useProcessStore.getState()
    store.startProcess('model-a', 'task-a')
    let resolveResponse!: (value: string) => void
    const response = new Promise<string>((resolve) => {
      resolveResponse = resolve
    })
    let result: string | null = null
    const oldRequest = (async () => {
      const value = await response
      if (!isCurrentTask('task-a')) return
      result = value
      useProcessStore.getState().finishProcess('task-a', 'old complete')
    })()

    store.startProcess('model-b', 'task-b')
    resolveResponse('old result')
    await oldRequest

    expect(result).toBeNull()
    expect(useProcessStore.getState()).toMatchObject({
      currentTaskId: 'task-b',
      isProcessing: true,
      progress: 0,
    })
  })

  it('keeps processing after a websocket complete-stage progress update', () => {
    const store = useProcessStore.getState()
    store.startProcess('model-a', 'task-a')

    store.updateProgress('task-a', 100, '后端阶段完成')

    expect(useProcessStore.getState()).toMatchObject({
      currentTaskId: 'task-a',
      isProcessing: true,
      progress: 100,
      statusMessage: '后端阶段完成',
    })
  })
})
