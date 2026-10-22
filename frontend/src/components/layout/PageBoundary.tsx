import { Component, lazy, Suspense, useMemo, useState, type ComponentType, type ReactNode } from 'react'

type PageLoader = () => Promise<{ default: ComponentType<{ isVisible?: boolean }> }>

class PageErrorBoundary extends Component<
  { children: ReactNode; onRetry: () => void },
  { failed: boolean }
> {
  state = { failed: false }
  static getDerivedStateFromError() { return { failed: true } }
  componentDidCatch(error: Error) { console.error('Page failed to load:', error) }
  render() {
    if (this.state.failed) {
      return <div role="alert" className="p-5 text-sm text-fg-secondary">
        <p>页面暂时无法显示，请重试。</p>
        <button className="mt-3 px-3 py-2 bg-bg-primary border border-border-subtle rounded" onClick={this.props.onRetry}>重试加载</button>
      </div>
    }
    return this.props.children
  }
}

export default function LazyPage({ load }: { load: PageLoader }) {
  const [attempt, setAttempt] = useState(0)
  const Page = useMemo(() => lazy(load), [load, attempt])
  return (
    <PageErrorBoundary key={attempt} onRetry={() => setAttempt((value) => value + 1)}>
      <Suspense fallback={<div role="status" className="p-5 text-sm text-fg-secondary">正在加载页面…</div>}>
        <Page isVisible />
      </Suspense>
    </PageErrorBoundary>
  )
}
