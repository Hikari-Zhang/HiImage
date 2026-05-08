import { useEffect, useState } from 'react'
import { useBackendAPI } from './useBackendAPI'
import { useSettingsStore } from '../stores/useSettingsStore'

export interface DeviceOption {
  value: string
  label: string
  disabled: boolean
  description?: string
}

// 可用设备的优先选择顺序（越靠前越优先）
const DEVICE_PRIORITY = ['cuda', 'mps', 'cpu']

const DEFAULT_OPTIONS: DeviceOption[] = [
  { value: 'cuda', label: 'CUDA (NVIDIA)', disabled: false },
  { value: 'mps', label: 'MPS (Apple Silicon)', disabled: false },
  { value: 'cpu', label: 'CPU（通用）', disabled: false },
]

/**
 * 获取计算设备选项列表，并根据后端检测结果标记不可用设备。
 * 同时自动将当前选中设备纠正为最优可用设备（若当前选中设备不可用）。
 */
export function useDeviceOptions(): { options: DeviceOption[]; loading: boolean } {
  const { getDevices } = useBackendAPI()
  const { device, setDevice } = useSettingsStore()
  const [options, setOptions] = useState<DeviceOption[]>(DEFAULT_OPTIONS)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    setLoading(true)

    getDevices()
      .then((data) => {
        if (cancelled) return
        if (!data?.devices) {
          // 后端不可达：保持默认（全部可用），停止加载
          setLoading(false)
          return
        }

        const availMap: Record<string, { available: boolean; desc: string; reason?: string }> = {}
        for (const d of data.devices) {
          availMap[d.id] = { available: d.available, desc: d.desc, reason: d.reason }
        }

        const newOptions: DeviceOption[] = [
          {
            value: 'cuda',
            label: 'CUDA (NVIDIA)',
            disabled: availMap['cuda'] ? !availMap['cuda'].available : false,
            description: availMap['cuda']?.reason,
          },
          {
            value: 'mps',
            label: 'MPS (Apple Silicon)',
            disabled: availMap['mps'] ? !availMap['mps'].available : false,
            description: availMap['mps']?.reason,
          },
          {
            value: 'cpu',
            label: 'CPU（通用）',
            disabled: false, // CPU 永远可用
          },
        ]

        setOptions(newOptions)

        // 若当前选中设备不可用，自动切换到最优可用设备
        const currentInfo = availMap[device]
        const currentUnavailable = currentInfo && !currentInfo.available
        if (currentUnavailable) {
          const best = DEVICE_PRIORITY.find((id) => {
            const info = availMap[id]
            return !info || info.available
          })
          if (best && best !== device) {
            setDevice(best)
          }
        }
      })
      .catch(() => {
        // 后端不可达，保持默认选项
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => {
      cancelled = true
    }
  // 仅在组件挂载时执行一次；device/setDevice 变更由组件自身处理
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return { options, loading }
}
