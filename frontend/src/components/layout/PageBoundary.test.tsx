import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import LazyPage from './PageBoundary'

afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('lazy page lifecycle', () => {
  it('shows a loading state until the page chunk arrives', async () => {
    let resolve!: (value: { default: () => React.JSX.Element }) => void
    const pending = new Promise<{ default: () => React.JSX.Element }>((done) => { resolve = done })
    render(<LazyPage load={() => pending} />)
    expect(screen.getByRole('status')).toHaveTextContent('正在加载页面')
    await act(async () => { resolve({ default: () => <p>页面内容</p> }) })
    expect(screen.getByText('页面内容')).toBeInTheDocument()
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('retries a failed chunk without reloading the application', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined)
    const load = vi.fn()
      .mockRejectedValueOnce(new Error('chunk unavailable'))
      .mockResolvedValueOnce({ default: () => <p>加载成功</p> })
    render(<LazyPage load={load} />)
    expect(await screen.findByRole('alert')).toHaveTextContent('页面暂时无法显示')
    fireEvent.click(screen.getByRole('button', { name: '重试加载' }))
    expect(await screen.findByText('加载成功')).toBeInTheDocument()
    expect(load).toHaveBeenCalledTimes(2)
  })
})
