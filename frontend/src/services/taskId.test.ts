import { describe, expect, it } from 'vitest'
import { createTaskId } from './taskId'

describe('createTaskId', () => {
  it('uses an injected UUID generator', () => {
    expect(createTaskId(() => 'fixed-uuid')).toBe('fixed-uuid')
  })

  it('returns distinct non-empty fallback IDs when randomUUID is unavailable', () => {
    const first = createTaskId(null)
    const second = createTaskId(null)

    expect(first).toMatch(/^task-/)
    expect(second).toMatch(/^task-/)
    expect(first).not.toBe(second)
  })
})
