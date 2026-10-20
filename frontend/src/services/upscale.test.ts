import { describe, expect, it } from 'vitest'
import { resolveOutscale } from './upscale'

describe('resolveOutscale', () => {
  it('uses the model default when no override is provided', () => {
    expect(resolveOutscale(null, 2)).toBe(2)
    expect(resolveOutscale(undefined, 2)).toBe(2)
  })
})
