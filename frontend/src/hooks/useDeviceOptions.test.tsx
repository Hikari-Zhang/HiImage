import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, renderHook, waitFor } from '@testing-library/react'
import { useDeviceOptions } from './useDeviceOptions'
import { useSettingsStore } from '../stores/useSettingsStore'
import { getEditorSettingsStore, useEditorSettings } from '../stores/useEditorSettings'

const getDevices = vi.hoisted(() => vi.fn())
vi.mock('./useBackendAPI', () => ({ useBackendAPI: () => ({ getDevices }) }))

describe('device correction respects editor ownership', () => {
  beforeEach(() => {
    useSettingsStore.setState({ device: 'mps' })
    getEditorSettingsStore('upscale').getState().setDevice('cuda')
    getEditorSettingsStore('watermark').getState().setDevice('cpu')
    getDevices.mockResolvedValue({ devices: [
      { id: 'cuda', available: false, desc: 'unavailable' },
      { id: 'mps', available: false, desc: 'unavailable' },
      { id: 'cpu', available: true, desc: 'CPU' },
    ] })
  })
  afterEach(cleanup)

  it('corrects only the selected editor when its GPU is unavailable', async () => {
    renderHook(() => {
      const { device, setDevice } = useEditorSettings('upscale')
      return useDeviceOptions({ device, setDevice })
    })
    await waitFor(() => expect(getEditorSettingsStore('upscale').getState().device).toBe('cpu'))
    expect(getEditorSettingsStore('watermark').getState().device).toBe('cpu')
    expect(useSettingsStore.getState().device).toBe('mps')
  })

  it('retains global-device correction for the settings page', async () => {
    renderHook(() => useDeviceOptions())
    await waitFor(() => expect(useSettingsStore.getState().device).toBe('cpu'))
    expect(getEditorSettingsStore('upscale').getState().device).toBe('cuda')
  })

  it('aborts device discovery when the page leaves', () => {
    getDevices.mockReturnValueOnce(new Promise(() => {}))
    const hook = renderHook(() => useDeviceOptions())
    const signal = getDevices.mock.calls[getDevices.mock.calls.length - 1][0] as AbortSignal
    expect(signal.aborted).toBe(false)
    hook.unmount()
    expect(signal.aborted).toBe(true)
  })
})
