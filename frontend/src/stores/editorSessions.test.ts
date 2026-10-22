import { beforeEach, describe, expect, it } from 'vitest'
import { useImageStore, useSynthesisImageStore, useUpscaleImageStore } from './useImageStore'
import { useCutoutStore } from './useCutoutStore'
import { isCurrentTask, useProcessStore } from './useProcessStore'
import { getEditorSettingsStore } from './useEditorSettings'
import { useSettingsStore } from './useSettingsStore'
import { useUpscaleBatchStore } from './useUpscaleBatchStore'
import { transferImageToEditor, type EditorDestination } from '../services/editorTransfer'
import { applyTaskProgressMessage } from '../services/taskProgress'

const images = {
  watermark: useImageStore,
  upscale: useUpscaleImageStore,
  synthesis: useSynthesisImageStore,
}

describe('editor session ownership', () => {
  beforeEach(() => {
    Object.values(images).forEach((store) => store.getState().reset())
    useCutoutStore.getState().reset()
    useProcessStore.setState({ sessions: {}, tasks: {} })
  })

  it.each(Object.keys(images) as (keyof typeof images)[])('%s keeps its own image, result and mask', (id) => {
    const otherId = id === 'watermark' ? 'synthesis' : 'watermark'
    const current = images[id].getState()
    current.setSourceImage('original')
    current.setResultImage('result', 40, 20)
    current.setMaskDataURL('mask')
    current.addROI({ x1: 0, y1: 0, x2: 4, y2: 4 })
    images[otherId].getState().setSourceImage('other image')
    expect(images[id].getState()).toMatchObject({
      sourceImage: 'original', resultImage: 'result', resultWidth: 40, resultHeight: 20, maskDataURL: 'mask',
    })
    expect(images[id].getState().rois).toHaveLength(1)
    expect(images[otherId].getState().resultImage).toBeNull()
  })

  it.each(Object.keys(images) as (keyof typeof images)[])('%s clears derived data when its source changes', (id) => {
    const store = images[id].getState()
    store.setSourceImage('old')
    store.setResultImage('old result', 100, 200)
    store.setImageDimensions(50, 100)
    store.setMaskDataURL('old mask')
    store.addROI({ x1: 0, y1: 0, x2: 2, y2: 2 })
    const revision = images[id].getState().sourceRevision
    store.setSourceImage('new')
    store.setImageDimensions(999, 999, revision)
    expect(images[id].getState()).toMatchObject({
      sourceImage: 'new', resultImage: null, showResult: false, resultWidth: 0, resultHeight: 0,
      imageWidth: 0, imageHeight: 0, rois: [], maskStrokes: [], maskDataURL: null,
    })
    store.setImageDimensions(12, 8, images[id].getState().sourceRevision)
    expect(images[id].getState().imageWidth).toBe(12)
  })

  it('updates concurrent sessions independently through WebSocket messages', () => {
    const tasks = useProcessStore.getState()
    tasks.startProcess('watermark', 'lama', 'watermark-task')
    tasks.startProcess('upscale', 'esrgan', 'upscale-task')
    applyTaskProgressMessage('watermark-task', JSON.stringify({ type: 'progress', percent: 30 }), tasks.updateProgress)
    applyTaskProgressMessage('upscale-task', JSON.stringify({ type: 'progress', percent: 70 }), tasks.updateProgress)
    expect(useProcessStore.getState().sessions.watermark?.progress).toBe(30)
    expect(useProcessStore.getState().sessions.upscale?.progress).toBe(70)
    tasks.finishProcess('watermark-task')
    expect(isCurrentTask('upscale-task')).toBe(true)
    expect(useProcessStore.getState().tasks['watermark-task'].status).toBe('succeeded')
  })

  it('changing a source invalidates only its own running task and late response', async () => {
    const tasks = useProcessStore.getState()
    tasks.startProcess('watermark', 'lama', 'old')
    tasks.startProcess('upscale', 'esrgan', 'other')
    let resolve!: (value: string) => void
    const response = new Promise<string>((done) => { resolve = done })
    const pending = response.then((image) => {
      if (isCurrentTask('old')) useImageStore.getState().setResultImage(image)
    })
    useImageStore.getState().setSourceImage('replacement')
    resolve('stale result')
    await pending
    tasks.updateProgress('old', 99)
    tasks.finishProcess('old')
    expect(useImageStore.getState().resultImage).toBeNull()
    expect(useProcessStore.getState().sessions.watermark?.progress).toBe(0)
    expect(useProcessStore.getState().tasks.old.status).toBe('superseded')
    expect(isCurrentTask('other')).toBe(true)
  })

  it('a failed task does not reset another session or accept late progress', () => {
    const tasks = useProcessStore.getState()
    tasks.startProcess('watermark', 'lama', 'one')
    tasks.startProcess('cutout', 'rembg', 'two')
    tasks.setError('one', 'failed')
    tasks.updateProgress('one', 90)
    expect(useProcessStore.getState().sessions.watermark?.progress).toBe(-1)
    expect(isCurrentTask('two')).toBe(true)
    expect(useProcessStore.getState().tasks.one.status).toBe('failed')
  })

  it('single-image replacement does not invalidate the upscale batch task', () => {
    useProcessStore.getState().startProcess('upscale-batch', 'esrgan', 'batch-item')
    useUpscaleImageStore.getState().setSourceImage('new single image')
    expect(isCurrentTask('batch-item')).toBe(true)
  })

  it('bounds terminal history while keeping another editor running', () => {
    const tasks = useProcessStore.getState()
    tasks.startProcess('watermark', 'lama', 'keep-running')
    for (let index = 0; index < 150; index++) {
      tasks.startProcess('upscale', 'esrgan', `batch-${index}`)
      tasks.finishProcess(`batch-${index}`)
    }
    expect(isCurrentTask('keep-running')).toBe(true)
    expect(Object.keys(useProcessStore.getState().tasks).length).toBeLessThanOrEqual(102)
  })

  it('cutout clears stale composite results and rejects an old image size callback', () => {
    const store = useCutoutStore.getState()
    store.setSourceImage('old')
    const revision = useCutoutStore.getState().sourceRevision
    store.setResult('rgba', 'alpha')
    store.setCompositeResult('composite')
    store.setSourceImage('new')
    store.setImageDimensions(100, 200, revision)
    expect(useCutoutStore.getState()).toMatchObject({
      sourceImage: 'new', resultImage: null, resultMask: null, compositeResult: null, imageWidth: 0, imageHeight: 0,
    })
  })

  it('refining a cutout invalidates the previous composite', () => {
    const store = useCutoutStore.getState()
    store.setCompositeResult('old composite')
    store.setResult('new rgba', 'new alpha')
    expect(useCutoutStore.getState().compositeResult).toBeNull()
  })

  it.each(['watermark', 'upscale', 'synthesis', 'cutout'] as EditorDestination[])(
    'explicit transfer replaces only destination %s and normalizes base64', (destination) => {
      Object.values(images).forEach((store) => store.getState().setSourceImage('unchanged'))
      useCutoutStore.getState().setSourceImage('unchanged')
      transferImageToEditor('png-result', destination)
      const stores = { ...images, cutout: useCutoutStore }
      for (const [id, store] of Object.entries(stores)) {
        expect(store.getState().sourceImage).toBe(id === destination ? 'data:image/png;base64,png-result' : 'unchanged')
      }
    },
  )

  it('preserves a transferred data URL without adding a second prefix', () => {
    transferImageToEditor('data:image/webp;base64,result', 'upscale')
    expect(useUpscaleImageStore.getState().sourceImage).toBe('data:image/webp;base64,result')
  })

  it('opens a transferred upscale image in single mode without stopping a batch', () => {
    useUpscaleBatchStore.getState().setMode('batch')
    useProcessStore.getState().startProcess('upscale-batch', 'esrgan', 'running-batch')
    transferImageToEditor('result', 'upscale')
    expect(useUpscaleBatchStore.getState().mode).toBe('single')
    expect(isCurrentTask('running-batch')).toBe(true)
  })

  it('keeps editor parameter changes separate from other editors and global settings', () => {
    const watermark = getEditorSettingsStore('watermark')
    const upscale = getEditorSettingsStore('upscale')
    const original = useSettingsStore.getState().upscaleModel
    const otherOriginal = upscale.getState().upscaleModel
    watermark.getState().setUpscaleModel('session-only')
    expect(upscale.getState().upscaleModel).toBe(otherOriginal)
    expect(useSettingsStore.getState().upscaleModel).toBe(original)
    expect(getEditorSettingsStore('watermark').getState().upscaleModel).toBe('session-only')
  })

  it('initializes an editor from hydrated defaults and keeps later user choices', () => {
    useSettingsStore.setState({ device: 'cpu' })
    const settings = getEditorSettingsStore('cutout')
    expect(settings.getState().device).toBe('cpu')
    settings.getState().setDevice('cuda')
    useSettingsStore.setState({ device: 'mps' })
    expect(getEditorSettingsStore('cutout').getState().device).toBe('cuda')
    useSettingsStore.setState({ device: 'cpu' })
  })
})
