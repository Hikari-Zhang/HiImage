import { afterEach, describe, expect, it, vi } from 'vitest'
import { createApiClient } from '../apiClient'
import { createCatalogAPI } from './catalog'

afterEach(() => vi.unstubAllGlobals())

describe('catalog endpoints use the common HTTP error contract', () => {
  it.each([
    'getInpaintModels', 'getUpscaleModels', 'getCutoutModels', 'getSynthesisModes',
    'getSynthesisModels', 'getPostprocessMethods', 'getDevices',
  ] as const)('rejects failures instead of returning error data from %s', async (method) => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ detail: '后端暂不可用' }), { status: 503 })))
    await expect(createCatalogAPI(createApiClient())[method]()).rejects.toMatchObject({ message: '后端暂不可用', status: 503 })
  })
})
