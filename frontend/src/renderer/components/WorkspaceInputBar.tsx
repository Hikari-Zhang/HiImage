import { clsx } from 'clsx'
import { useWorkspaceStore, type WorkspaceInputSource } from '../stores/useWorkspaceStore'

type Props = {
  onChange?: (source: WorkspaceInputSource) => void
}

/**
 * 超分 / 合成的处理输入切换：有跨页结果时，默认用结果，可切回原图。
 */
export default function WorkspaceInputBar({ onChange }: Props) {
  const sourceImage = useWorkspaceStore((s) => s.sourceImage)
  const resultImage = useWorkspaceStore((s) => s.resultImage)
  const inputSource = useWorkspaceStore((s) => s.inputSource)
  const setInputSource = useWorkspaceStore((s) => s.setInputSource)

  if (!sourceImage || !resultImage) return null

  const select = (source: WorkspaceInputSource) => {
    setInputSource(source)
    onChange?.(source)
  }

  return (
    <div className="flex items-center gap-1">
      <span className="text-[11px] text-fg-secondary mr-1">处理输入</span>
      <button
        type="button"
        onClick={() => select('original')}
        className={clsx(
          'px-2 py-1 rounded text-xs',
          inputSource === 'original'
            ? 'bg-blue-500 text-white'
            : 'bg-bg-primary text-fg-secondary hover:text-fg-primary',
        )}
      >
        原图
      </button>
      <button
        type="button"
        onClick={() => select('result')}
        className={clsx(
          'px-2 py-1 rounded text-xs',
          inputSource === 'result'
            ? 'bg-blue-500 text-white'
            : 'bg-bg-primary text-fg-secondary hover:text-fg-primary',
        )}
      >
        处理结果
      </button>
    </div>
  )
}
