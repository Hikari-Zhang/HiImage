import { useRef } from 'react'

/**
 * 页面惰性初始化 hook —— 首次变为可见时返回 true，之后永远保持 true。
 *
 * 用于非默认页面，避免应用启动时触发不必要的网络请求：
 *
 * ```tsx
 * export default function MyPage({ isVisible = false }: { isVisible?: boolean }) {
 *   const ready = useFirstVisible(isVisible)
 *
 *   useEffect(() => {
 *     if (!ready) return
 *     fetchData() // 首次可见时才触发
 *   }, [ready])
 * }
 * ```
 *
 * @param isVisible 当前页面是否处于活动状态（由 MainLayout 传入）
 * @returns 页面是否已经被显示过（true = 可以执行初始化）
 */
export function useFirstVisible(isVisible: boolean): boolean {
  const hasBeenVisible = useRef(false)
  if (isVisible && !hasBeenVisible.current) {
    hasBeenVisible.current = true
  }
  return hasBeenVisible.current
}
