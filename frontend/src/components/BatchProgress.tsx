import { Progress } from '../components/ui'

interface BatchProgressProps {
  current: number
  total: number
  currentImage?: string
  statusMessage?: string
}

export default function BatchProgress({
  current,
  total,
  currentImage,
  statusMessage,
}: BatchProgressProps) {
  const progress = total > 0 ? Math.round((current / total) * 100) : 0

  return (
    <div className="space-y-1.5">
      <Progress
        value={progress}
        label={
          statusMessage ||
          (total > 0 ? `整体进度: ${current}/${total} (${progress}%)` : '等待开始...')
        }
      />

      {currentImage && current < total && (
        <p className="text-[10px] text-fg-tertiary truncate">
          当前: {currentImage}
        </p>
      )}

      {total > 0 && (
        <div className="flex justify-between text-[10px] text-fg-tertiary">
          <span>已处理: {current} 张</span>
          <span>剩余: {total - current} 张</span>
        </div>
      )}
    </div>
  )
}
