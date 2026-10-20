import { beforeEach, describe, expect, it } from 'vitest'
import { applyTaskProgressMessage } from './taskProgress'
import { useProcessStore } from '../stores/useProcessStore'

describe('applyTaskProgressMessage', () => {
  beforeEach(() => useProcessStore.getState().reset())

  it('treats websocket complete as progress and waits for HTTP to finish', () => {
    const store = useProcessStore.getState()
    store.startProcess('model', 'task-a')

    applyTaskProgressMessage(
      'task-a',
      JSON.stringify({ type: 'complete', message: '后端阶段完成' }),
      store.updateProgress,
    )

    expect(useProcessStore.getState()).toMatchObject({
      currentTaskId: 'task-a',
      isProcessing: true,
      progress: 100,
      statusMessage: '后端阶段完成',
    })
  })
})
