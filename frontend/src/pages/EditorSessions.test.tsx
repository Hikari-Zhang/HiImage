import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import WatermarkRemoval from './WatermarkRemoval'
import SuperResolution from './SuperResolution'
import CutoutPage from './CutoutPage'
import { useCutoutStore } from '../stores/useCutoutStore'
import { useImageStore, useUpscaleImageStore } from '../stores/useImageStore'
import { useProcessStore } from '../stores/useProcessStore'
import { getEditorSettingsStore } from '../stores/useEditorSettings'
import { useUpscaleBatchStore } from '../stores/useUpscaleBatchStore'

const api = vi.hoisted(() => ({ upscale: vi.fn(), inpaintWithMask: vi.fn() }))
const dimensions = vi.hoisted(() => vi.fn(async () => ({ width: 8, height: 8 })))
vi.mock('../services/imageDimensions', async (importOriginal) => ({
  ...await importOriginal<typeof import('../services/imageDimensions')>(),
  readImageDimensions: dimensions,
}))

vi.mock('../hooks/useBackendAPI', () => ({ useBackendAPI: () => api }))
vi.mock('../hooks/useDeviceOptions', () => ({
  useDeviceOptions: () => ({ options: [{ value: 'cpu', label: 'CPU', disabled: false }], loading: false }),
}))
vi.mock('../hooks/useDownloadManager', () => ({ useDownloadManager: () => ({ startDownload: vi.fn() }) }))
vi.mock('../hooks/useMaskExport', () => ({
  useMaskExport: () => ({ exportCombinedMask: async () => 'mask', exportMask: async () => 'mask' }),
}))
vi.mock('../services/managedWebSocket', () => ({
  ManagedWebSocket: class {
    startAndWait = async () => undefined
    dispose = () => undefined
  },
}))
vi.mock('../services/fileService', async (importOriginal) => ({
  ...await importOriginal<typeof import('../services/fileService')>(),
  fileToDataUrl: async (file: File) => `data:image/png;base64,${file.name}`,
}))
vi.mock('../components/MaskCanvas', () => ({ default: () => <div>遮罩画布</div> }))
vi.mock('react-konva', () => ({ Stage: () => null, Layer: () => null, Image: () => null, Line: () => null }))
vi.mock('../components/ImageCanvas', () => ({
  default: ({ imageSrc }: { imageSrc: string }) => <div data-testid="source-preview">{imageSrc}</div>,
}))
vi.mock('../components/ImageCompare', () => ({
  default: ({ beforeSrc, afterSrc }: { beforeSrc: string; afterSrc: string }) =>
    <div data-testid="comparison">{beforeSrc}|{afterSrc}</div>,
}))

function Location() {
  return <div data-testid="location">{useLocation().pathname}</div>
}

function RoutedEditors() {
  const navigate = useNavigate()
  return <>
    <button onClick={() => navigate('/upscale')}>打开超分</button>
    <button onClick={() => navigate('/watermark')}>打开去水印</button>
    <Routes>
      <Route path="/upscale" element={<SuperResolution />} />
      <Route path="/watermark" element={<WatermarkRemoval isVisible />} />
    </Routes>
  </>
}

function editors() {
  return render(
    <MemoryRouter initialEntries={['/upscale']}>
      <section data-testid="watermark"><WatermarkRemoval /></section>
      <section data-testid="upscale"><SuperResolution /></section>
      <Location />
    </MemoryRouter>,
  )
}

function deferredResult() {
  let resolve!: (value: { image: string; width: number; height: number }) => void
  const promise = new Promise<{ image: string; width: number; height: number }>((done) => { resolve = done })
  return { promise, resolve }
}

describe('real editor interaction with deferred inference responses', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.stubGlobal('IntersectionObserver', class {
      observe() {}
      unobserve() {}
      disconnect() {}
    })
    useImageStore.getState().reset()
    useUpscaleImageStore.getState().reset()
    useProcessStore.setState({ tasks: {}, sessions: {} })
    useUpscaleBatchStore.setState({ mode: 'single', batchItems: [], batchCurrentIndex: null, isBatchProcessing: false })
    useImageStore.getState().setSourceImage('watermark-original')
    useImageStore.getState().setImageDimensions(8, 8)
    useImageStore.getState().addROI({ x1: 0, y1: 0, x2: 4, y2: 4 })
    useUpscaleImageStore.getState().setSourceImage('upscale-original')
    useUpscaleImageStore.getState().setImageDimensions(8, 8)
    getEditorSettingsStore('watermark').setState({ postprocessEnabled: false, upscaleEnabled: false })
  })

  afterEach(() => { cleanup(); vi.unstubAllGlobals() })

  it('finishes an upscale after another editor uploads a different source', async () => {
    const result = deferredResult()
    api.upscale.mockReturnValueOnce(result.promise)
    const view = editors()
    fireEvent.click(within(screen.getByTestId('upscale')).getByRole('button', { name: '开始超分' }))
    await waitFor(() => expect(api.upscale).toHaveBeenCalledOnce())
    const taskId = api.upscale.mock.calls[0][0].task_id as string
    const input = view.getByTestId('watermark').querySelector('input[type="file"]')!
    fireEvent.change(input, { target: { files: [new File(['png'], 'new-watermark.png', { type: 'image/png' })] } })
    await waitFor(() => expect(useImageStore.getState().sourceImage).toContain('new-watermark.png'))
    expect(useProcessStore.getState().tasks[taskId].status).toBe('running')
    await act(async () => { result.resolve({ image: 'upscaled', width: 32, height: 32 }) })
    expect(useUpscaleImageStore.getState().resultImage).toBe('data:image/png;base64,upscaled')
    expect(useImageStore.getState().resultImage).toBeNull()
    expect(useProcessStore.getState().tasks[taskId].status).toBe('succeeded')
  })

  it('does not attach a late upscale result to a newly uploaded source', async () => {
    const result = deferredResult()
    api.upscale.mockReturnValueOnce(result.promise)
    const view = editors()
    fireEvent.click(within(screen.getByTestId('upscale')).getByRole('button', { name: '开始超分' }))
    await waitFor(() => expect(api.upscale).toHaveBeenCalledOnce())
    const input = view.getByTestId('upscale').querySelector('input[type="file"]')!
    fireEvent.change(input, { target: { files: [new File(['png'], 'replacement.png', { type: 'image/png' })] } })
    await waitFor(() => expect(useUpscaleImageStore.getState().sourceImage).toContain('replacement.png'))
    await act(async () => { result.resolve({ image: 'stale', width: 32, height: 32 }) })
    expect(useUpscaleImageStore.getState().resultImage).toBeNull()
    expect(within(screen.getByTestId('upscale')).queryByTestId('comparison')).not.toBeInTheDocument()
  })

  it('runs both editors and keeps their results attached to the correct source', async () => {
    const upscale = deferredResult()
    const inpaint = deferredResult()
    api.upscale.mockReturnValueOnce(upscale.promise)
    api.inpaintWithMask.mockReturnValueOnce(inpaint.promise)
    editors()
    fireEvent.click(within(screen.getByTestId('upscale')).getByRole('button', { name: '开始超分' }))
    fireEvent.click(within(screen.getByTestId('watermark')).getByRole('button', { name: '去除水印' }))
    await waitFor(() => {
      expect(api.upscale).toHaveBeenCalledOnce()
      expect(api.inpaintWithMask).toHaveBeenCalledOnce()
    })
    await act(async () => { inpaint.resolve({ image: 'cleaned', width: 8, height: 8 }) })
    const taskId = api.upscale.mock.calls[0][0].task_id as string
    expect(useProcessStore.getState().tasks[taskId].status).toBe('running')
    await act(async () => { upscale.resolve({ image: 'enlarged', width: 32, height: 32 }) })
    expect(within(screen.getByTestId('watermark')).getByTestId('comparison')).toHaveTextContent('watermark-original|data:image/png;base64,cleaned')
    expect(within(screen.getByTestId('upscale')).getByTestId('comparison')).toHaveTextContent('upscale-original|data:image/png;base64,enlarged')
  })

  it('preserves source and result when an editor unmounts and remounts', () => {
    useUpscaleImageStore.getState().setResultImage('saved-upscale', 32, 32)
    const first = editors()
    first.unmount()
    editors()
    expect(within(screen.getByTestId('upscale')).getByTestId('comparison')).toHaveTextContent('upscale-original|saved-upscale')
  })

  it.each(['single', 'batch'] as const)('keeps the selected files when the browser clears its live FileList (%s)', async (mode) => {
    useUpscaleBatchStore.setState({ mode })
    const view = render(<MemoryRouter><SuperResolution /></MemoryRouter>)
    const input = view.container.querySelector('input[type="file"]') as HTMLInputElement
    const selected = [new File(['png'], 'selected.png', { type: 'image/png' })]
    // Real browsers mutate an existing FileList when input.value is cleared.
    Object.defineProperty(input, 'files', { configurable: true, get: () => selected })
    Object.defineProperty(input, 'value', { configurable: true, get: () => '', set: () => { selected.length = 0 } })
    fireEvent.change(input)
    if (mode === 'single') {
      await waitFor(() => expect(useUpscaleImageStore.getState().sourceImage).toContain('selected.png'))
    } else {
      expect(useUpscaleBatchStore.getState().batchItems.map((item) => item.fileName)).toEqual(['selected.png'])
    }
    expect(selected).toHaveLength(0)
  })

  it('unlocks a failed editor and retries with a new task without changing its source', async () => {
    api.upscale.mockRejectedValueOnce(new Error('网络连接中断'))
      .mockResolvedValueOnce({ image: 'retry-result', width: 32, height: 32 })
    render(<MemoryRouter initialEntries={['/upscale']}><RoutedEditors /></MemoryRouter>)
    fireEvent.click(screen.getByRole('button', { name: '开始超分' }))
    await waitFor(() => expect(useProcessStore.getState().sessions.upscale?.isProcessing).toBe(false))
    const failedId = api.upscale.mock.calls[0][0].task_id as string
    expect(useProcessStore.getState().tasks[failedId].status).toBe('failed')
    expect(useUpscaleImageStore.getState().sourceImage).toBe('upscale-original')
    expect(screen.getByRole('button', { name: '开始超分' })).toBeEnabled()
    fireEvent.click(screen.getByRole('button', { name: '开始超分' }))
    await waitFor(() => expect(screen.getByTestId('comparison')).toHaveTextContent('retry-result'))
    const retryId = api.upscale.mock.calls[1][0].task_id as string
    expect(retryId).not.toBe(failedId)
    expect(useProcessStore.getState().tasks[retryId].status).toBe('succeeded')
    expect(useProcessStore.getState().tasks[failedId].status).toBe('failed')
    expect(api.upscale).toHaveBeenCalledTimes(2)
  })

  it('finishes inference while its route is unmounted and restores the result on return', async () => {
    const result = deferredResult()
    api.upscale.mockReturnValueOnce(result.promise)
    render(<MemoryRouter initialEntries={['/upscale']}><RoutedEditors /></MemoryRouter>)
    fireEvent.click(screen.getByRole('button', { name: '开始超分' }))
    await waitFor(() => expect(api.upscale).toHaveBeenCalledOnce())
    const taskId = api.upscale.mock.calls[0][0].task_id as string
    fireEvent.click(screen.getByRole('button', { name: '打开去水印' }))
    expect(screen.queryByRole('button', { name: '开始超分' })).not.toBeInTheDocument()
    await act(async () => { result.resolve({ image: 'finished-away', width: 32, height: 32 }) })
    expect(useProcessStore.getState().tasks[taskId].status).toBe('succeeded')
    expect(useImageStore.getState().resultImage).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: '打开超分' }))
    expect(screen.getByTestId('comparison')).toHaveTextContent('upscale-original|data:image/png;base64,finished-away')
  })

  it('locks batch submission during preflight and finishes the queue while away', async () => {
    let finishPreflight!: (size: { width: number; height: number }) => void
    dimensions.mockReturnValueOnce(new Promise((resolve) => { finishPreflight = resolve }))
    const firstResult = deferredResult()
    api.upscale.mockReturnValueOnce(firstResult.promise)
      .mockResolvedValueOnce({ image: 'second', width: 32, height: 32 })
    useUpscaleBatchStore.setState({ mode: 'batch', batchItems: [
      { id: 'first', fileName: 'first.png', dataUrl: 'data:image/png;base64,YQ==', status: 'pending' },
      { id: 'second', fileName: 'second.png', dataUrl: 'data:image/png;base64,Yg==', status: 'pending' },
    ] })
    render(<MemoryRouter initialEntries={['/upscale']}><RoutedEditors /></MemoryRouter>)
    fireEvent.click(screen.getByRole('button', { name: '开始批量处理 (2 张)' }))
    fireEvent.click(screen.getByRole('button', { name: '打开去水印' }))
    fireEvent.click(screen.getByRole('button', { name: '打开超分' }))
    expect(screen.getByRole('button', { name: '处理中...' })).toBeDisabled()
    expect(api.upscale).not.toHaveBeenCalled()
    await act(async () => { finishPreflight({ width: 8, height: 8 }) })
    await waitFor(() => expect(api.upscale).toHaveBeenCalledOnce())
    fireEvent.click(screen.getByRole('button', { name: '打开去水印' }))
    await act(async () => { firstResult.resolve({ image: 'first', width: 32, height: 32 }) })
    await waitFor(() => expect(useUpscaleBatchStore.getState().isBatchProcessing).toBe(false))
    expect(api.upscale).toHaveBeenCalledTimes(2)
    expect(useUpscaleBatchStore.getState().batchItems.map((item) => item.status)).toEqual(['success', 'success'])
    fireEvent.click(screen.getByRole('button', { name: '打开超分' }))
    expect(screen.getByRole('button', { name: '开始批量处理 (2 张)' })).toBeEnabled()
  })

  it('transfers a result only after the user selects a destination', () => {
    useImageStore.getState().setResultImage('data:image/png;base64,cleaned')
    editors()
    expect(useUpscaleImageStore.getState().sourceImage).toBe('upscale-original')
    const select = within(screen.getByTestId('watermark')).getByRole('combobox', { name: '将结果发送到其他工具' })
    fireEvent.change(select, { target: { value: 'upscale' } })
    expect(useUpscaleImageStore.getState().sourceImage).toBe('data:image/png;base64,cleaned')
    expect(useImageStore.getState().resultImage).toBe('data:image/png;base64,cleaned')
    expect(screen.getByTestId('location')).toHaveTextContent('/upscale')
  })

  it('does not let a hidden page handle delete shortcuts', () => {
    render(<MemoryRouter><WatermarkRemoval isVisible={false} /></MemoryRouter>)
    useImageStore.getState().selectROI(useImageStore.getState().rois[0].id)
    fireEvent.keyDown(window, { code: 'Delete' })
    expect(useImageStore.getState().rois).toHaveLength(1)
  })

  it('keeps batch mode and queue when the page remounts', () => {
    useUpscaleBatchStore.setState({
      mode: 'batch',
      batchItems: [{ id: 'one', fileName: 'original.png', status: 'pending' }],
    })
    const first = editors()
    first.unmount()
    editors()
    expect(useUpscaleBatchStore.getState().mode).toBe('batch')
    expect(useUpscaleBatchStore.getState().batchItems[0].fileName).toBe('original.png')
    expect(within(screen.getByTestId('upscale')).getByText('original.png')).toBeInTheDocument()
  })

  it('loads source dimensions for a result explicitly transferred to cutout', () => {
    const pending: Array<{ onload: (() => void) | null }> = []
    vi.stubGlobal('Image', class {
      onload: (() => void) | null = null
      naturalWidth = 32
      naturalHeight = 16
      src = ''
      constructor() { pending.push(this) }
    })
    vi.stubGlobal('ResizeObserver', class { observe() {}; disconnect() {} })
    useCutoutStore.getState().setSourceImage('data:image/png;base64,transferred')
    render(<MemoryRouter><CutoutPage /></MemoryRouter>)
    act(() => pending[0].onload!())
    expect(useCutoutStore.getState()).toMatchObject({ imageWidth: 32, imageHeight: 16 })
  })
})
