import { useEffect } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import AppRoutes from './AppRoutes'

const lifecycle = vi.hoisted(() => ({ loaded: [] as string[], mounted: [] as string[], unmounted: [] as string[] }))
function TestPage({ name }: { name: string }) {
  useEffect(() => {
    lifecycle.mounted.push(name)
    return () => { lifecycle.unmounted.push(name) }
  }, [name])
  return <p>{name} 页面</p>
}
vi.mock('./pages/SuperResolution', () => {
  lifecycle.loaded.push('upscale')
  return { default: () => <TestPage name="upscale" /> }
})
vi.mock('./pages/Settings', () => {
  lifecycle.loaded.push('settings')
  return { default: () => <TestPage name="settings" /> }
})
vi.mock('./pages/WatermarkRemoval', () => {
  lifecycle.loaded.push('watermark')
  return { default: () => <TestPage name="watermark" /> }
})
vi.mock('./pages/CutoutPage', () => { throw new Error('unvisited cutout was loaded') })
vi.mock('./pages/SmartSynthesis', () => { throw new Error('unvisited synthesis was loaded') })
vi.mock('./pages/Logs', () => { throw new Error('unvisited logs was loaded') })

afterEach(cleanup)
describe('route page ownership', () => {
  it('loads only visited pages, unmounts the old page, and remounts on return', async () => {
    render(<MemoryRouter initialEntries={['/upscale']}><AppRoutes /></MemoryRouter>)
    expect(await screen.findByText('upscale 页面')).toBeInTheDocument()
    expect(lifecycle.loaded).toEqual(['upscale'])
    fireEvent.click(screen.getByRole('button', { name: '设置' }))
    expect(await screen.findByText('settings 页面')).toBeInTheDocument()
    expect(screen.queryByText('upscale 页面')).not.toBeInTheDocument()
    expect(lifecycle.unmounted).toContain('upscale')
    expect(lifecycle.loaded).toEqual(['upscale', 'settings'])
    fireEvent.click(screen.getByRole('button', { name: '超分辨率' }))
    expect(await screen.findByText('upscale 页面')).toBeInTheDocument()
    expect(lifecycle.mounted.filter((name) => name === 'upscale')).toHaveLength(2)
    expect(lifecycle.unmounted).toContain('settings')
  })

  it.each(['/', '/unknown'])('redirects %s to the default editor', async (path) => {
    render(<MemoryRouter initialEntries={[path]}><AppRoutes /></MemoryRouter>)
    expect(await screen.findByText('watermark 页面')).toBeInTheDocument()
  })
})
