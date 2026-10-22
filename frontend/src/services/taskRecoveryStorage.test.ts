import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import { TASK_RECOVERY_KEY, readPendingTasks, rememberPendingTask, forgetPendingTask } from './taskRecoveryStorage'
import { useProcessStore } from '../stores/useProcessStore'
import { useBackendStore } from '../stores/useBackendStore'

beforeEach(() => {
  sessionStorage.clear()
  useProcessStore.setState({ tasks: {}, sessions: {} })
  useBackendStore.setState({ backendURL: '' })
})
afterEach(() => { vi.restoreAllMocks(); sessionStorage.clear() })

describe('task recovery metadata', () => {
  it('persists only bounded task metadata, never input or result fields', () => {
    for (let index = 0; index < 25; index++) {
      const item = { taskId: `task-${index}`, sessionId: 'upscale' as const, backendURL: '', createdAt: index,
        image: 'private-image', prompt: 'private-prompt', token: 'private-token' }
      rememberPendingTask(item)
    }
    const records = readPendingTasks()
    expect(records).toHaveLength(20)
    expect(records[0].taskId).toBe('task-5')
    expect(sessionStorage.getItem(TASK_RECOVERY_KEY)).not.toContain('private')
    expect(Object.keys(records[0]).sort()).toEqual(['backendURL', 'createdAt', 'sessionId', 'taskId'])
  })

  it('isolates the same task ID on different backends', () => {
    rememberPendingTask({ taskId: 'one', sessionId: 'upscale', backendURL: '', createdAt: 1 })
    rememberPendingTask({ taskId: 'one', sessionId: 'cutout', backendURL: 'https://other', createdAt: 2 })
    forgetPendingTask('one', '')
    expect(readPendingTasks()).toEqual([{ taskId: 'one', sessionId: 'cutout', backendURL: 'https://other', createdAt: 2 }])
  })

  it.each(['invalid-json', '{}', '[null, {"taskId":5}]', 'x'.repeat(32769)])('ignores corrupt storage (%#)', (raw) => {
    sessionStorage.setItem(TASK_RECOVERY_KEY, raw)
    expect(readPendingTasks()).toEqual([])
  })

  it('keeps failed and completed task IDs for recovery until explicitly dismissed', () => {
    const process = useProcessStore.getState()
    useBackendStore.setState({ backendURL: 'https://backend.example/' })
    process.startProcess('upscale', 'model', 'failed')
    process.setError('failed', 'network interrupted')
    expect(readPendingTasks()).toMatchObject([{ taskId: 'failed', backendURL: 'https://backend.example' }])
    process.startProcess('upscale', 'model', 'success')
    process.finishProcess('success')
    expect(readPendingTasks().map((item) => item.taskId)).toEqual(['failed', 'success'])
    forgetPendingTask('success', 'https://backend.example')
    expect(readPendingTasks().map((item) => item.taskId)).toEqual(['failed'])
  })

  it('does not let a stale task completion clear another task metadata', () => {
    const process = useProcessStore.getState()
    process.startProcess('watermark', 'model', 'old')
    process.startProcess('watermark', 'model', 'new')
    process.finishProcess('old')
    expect(readPendingTasks().map((item) => item.taskId)).toEqual(['old', 'new'])
  })

  it('keeps processing functional when storage is unavailable', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('blocked') })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('quota') })
    const process = useProcessStore.getState()
    expect(() => process.startProcess('upscale', 'model', 'one')).not.toThrow()
    expect(useProcessStore.getState().sessions.upscale?.isProcessing).toBe(true)
    expect(() => process.finishProcess('one')).not.toThrow()
    expect(useProcessStore.getState().sessions.upscale?.isProcessing).toBe(false)
  })
})
