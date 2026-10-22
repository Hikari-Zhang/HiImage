import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render } from '@testing-library/react'
import ImageCanvas from './ImageCanvas'

describe('image load lifecycle', () => {
  const pending: Array<{ onload: (() => void) | null; naturalWidth: number; naturalHeight: number }> = []

  beforeEach(() => {
    pending.length = 0
    vi.stubGlobal('Image', class {
      onload: (() => void) | null = null
      naturalWidth = 16
      naturalHeight = 8
      src = ''
      constructor() { pending.push(this) }
    })
    vi.stubGlobal('ResizeObserver', class {
      observe() {}
      disconnect() {}
    })
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null)
  })
  afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

  const props = {
    rois: [], selectedROIs: [], tool: 'draw' as const,
    onROIDrawn: vi.fn(), onROISelect: vi.fn(),
  }

  it('ignores a stale image load after the source changes', () => {
    const oldLoad = vi.fn()
    const newLoad = vi.fn()
    const view = render(<ImageCanvas {...props} imageSrc="old" onImageLoad={oldLoad} />)
    const staleCallback = pending[0].onload!
    view.rerender(<ImageCanvas {...props} imageSrc="new" onImageLoad={newLoad} />)
    act(() => { staleCallback(); pending[1].onload!() })
    expect(oldLoad).not.toHaveBeenCalled()
    expect(newLoad).toHaveBeenCalledWith(16, 8)
  })

  it('does not update image dimensions after unmounting', () => {
    const onLoad = vi.fn()
    const view = render(<ImageCanvas {...props} imageSrc="image" onImageLoad={onLoad} />)
    const callback = pending[0].onload!
    view.unmount()
    act(() => callback())
    expect(onLoad).not.toHaveBeenCalled()
  })
})
