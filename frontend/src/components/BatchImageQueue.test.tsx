import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import BatchImageQueue from './BatchImageQueue'

describe('BatchImageQueue', () => {
  beforeEach(() => {
    Object.defineProperty(HTMLElement.prototype, 'clientHeight', {
      configurable: true,
      value: 500,
    })
    vi.stubGlobal('IntersectionObserver', class {
      observe() {}
      unobserve() {}
      disconnect() {}
    })
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('offers an individual download callback for every successful result', async () => {
    const onDownload = vi.fn()
    render(
      <BatchImageQueue
        items={[{
          id: '1',
          fileName: 'photo.jpg',
          status: 'success',
          resultDataUrl: 'data:image/png;base64,YQ==',
        }]}
        currentIndex={null}
        onRemove={() => undefined}
        onClear={() => undefined}
        onAdd={() => undefined}
        onDownload={onDownload}
      />,
    )

    await userEvent.click(await screen.findByRole('button', { name: '下载 photo.jpg' }))
    expect(onDownload).toHaveBeenCalledWith('1')
  })
})
