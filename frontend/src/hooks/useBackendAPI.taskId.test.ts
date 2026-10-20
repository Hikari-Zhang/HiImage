import { renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useBackendAPI } from './useBackendAPI'

describe('useBackendAPI task_id propagation', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('includes task_id in every inference request body', async () => {
    const bodies: Array<Record<string, unknown>> = []
    vi.stubGlobal('fetch', vi.fn(async (_url: string, init?: RequestInit) => {
      bodies.push(JSON.parse(String(init?.body)))
      return { ok: true, json: async () => ({ regions: [], image: '', width: 1, height: 1 }) }
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
