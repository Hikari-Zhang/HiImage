import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import ModelSelect from './ModelSelect'
import { useDownloadStore } from '../../stores/useDownloadStore'

const startDownload = vi.hoisted(() => vi.fn())
vi.mock('../../hooks/useDownloadManager', () => ({ useDownloadManager: () => ({ startDownload }) }))
afterEach(() => { cleanup(); startDownload.mockClear(); useDownloadStore.setState({ tasks: {} }) })

it('offers reconnection for an unconfirmed selected model without saying download failed', () => {
  useDownloadStore.getState().setTask('a', { status: 'error', statusSyncFailed: true })
  render(<ModelSelect value="a" onChange={() => {}} options={[{ value: 'a', label: '模型 A' }]} />)
  expect(screen.getByText('下载状态未确认，请重新连接')).toBeInTheDocument()
  expect(screen.queryByText(/下载失败/)).not.toBeInTheDocument()
  fireEvent.click(screen.getByTitle('重新连接下载状态'))
  expect(startDownload).toHaveBeenCalledWith('a')
})

it('continues to show actual backend errors as download failure', () => {
  useDownloadStore.getState().setTask('a', { status: 'error', message: '服务器错误' })
  render(<ModelSelect value="a" onChange={() => {}} options={[{ value: 'a', label: '模型 A' }]} />)
  expect(screen.getByText('下载失败: 服务器错误')).toBeInTheDocument()
  expect(screen.queryByTitle('重新连接下载状态')).not.toBeInTheDocument()
})
