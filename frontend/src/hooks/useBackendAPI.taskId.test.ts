import { act, renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useBackendAPI } from './useBackendAPI'
import { useBackendStore } from '../stores/useBackendStore'

describe('useBackendAPI task_id propagation', () => {
  afterEach(() => { vi.unstubAllGlobals(); useBackendStore.getState().setBackendURL('') })

  it('keeps function identities stable and switches every domain to a new backend', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{}')))
    const hook = renderHook(() => useBackendAPI())
    const first = hook.result.current
    hook.rerender()
    expect(hook.result.current).toBe(first)
    act(() => useBackendStore.getState().setBackendURL('https://backend.test'))
    expect(hook.result.current).not.toBe(first)
    await hook.result.current.getDevices()
    await hook.result.current.cutout({ image: 'i', model: 'm', device: 'cpu', task_id: 'task' })
    expect(fetch).toHaveBeenNthCalledWith(1, 'https://backend.test/api/devices', undefined)
    expect(fetch).toHaveBeenNthCalledWith(2, 'https://backend.test/api/cutout', expect.objectContaining({ method: 'POST' }))
  })

  it('includes task_id in every inference request body', async () => {
    const bodies: Array<Record<string, unknown>> = []
    vi.stubGlobal('fetch', vi.fn(async (_url: string, init?: RequestInit) => {
      bodies.push(JSON.parse(String(init?.body)))
      return new Response(JSON.stringify({ regions: [], image: '', width: 1, height: 1 }))
    }))
    const api = renderHook(() => useBackendAPI()).result.current as Record<string, (...args: any[]) => Promise<unknown>>

    await api.detectWatermark('image', 0.5, 'task-detect')
    await api.inpaint({ image: 'i', rois: [], model: 'm', device: 'cpu', dilation: 1, disable_nsfw: false, task_id: 'task-inpaint' })
    await api.inpaintWithMask({ image: 'i', mask: 'm', model: 'm', device: 'cpu', dilation: 1, disable_nsfw: false, task_id: 'task-mask' })
    await api.runPipeline({ image: 'i', inpaint_model: 'm', device: 'cpu', dilation: 1, disable_nsfw: false, postprocess_method: 'none', postprocess_enabled: false, upscale_enabled: false, upscale_model: 'u', task_id: 'task-pipeline' })
    await api.upscale({ image: 'i', model: 'm', device: 'cpu', task_id: 'task-upscale' })
    await api.runSynthesis({ source_image: 'i', mode: 'mode', model_id: 'm', device: 'cpu', task_id: 'task-synthesis' })
    await api.cutout({ image: 'i', model: 'm', device: 'cpu', task_id: 'task-cutout' })
    await api.cutoutRefine({ mask: 'm', original_image: 'i', feather: 0, dilate: 0, task_id: 'task-refine' })
    await api.cutoutComposite({ image: 'i', background_color: '#fff', task_id: 'task-composite' })

    expect(bodies.map((body) => body.task_id)).toEqual([
      'task-detect',
      'task-inpaint',
      'task-mask',
      'task-pipeline',
      'task-upscale',
      'task-synthesis',
      'task-cutout',
      'task-refine',
      'task-composite',
    ])
  })
})
