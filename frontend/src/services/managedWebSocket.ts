type ManagedWebSocketOptions = {
  createSocket?: (url: string) => WebSocket
  maxRetries?: number
  initialDelayMs?: number
  onMessage?: (event: MessageEvent) => void
  onError?: () => void
}

export class ManagedWebSocket {
  private socket: WebSocket | null = null
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private openTimer: ReturnType<typeof setTimeout> | null = null
  private openPromise: Promise<void> | null = null
  private resolveOpen: (() => void) | null = null
  private rejectOpen: ((error: Error) => void) | null = null
  private retryCount = 0
  private opened = false
  private disposed = false

  constructor(
    private readonly url: string,
    private readonly options: ManagedWebSocketOptions = {},
  ) {}

  start(): void {
    if (this.disposed || this.socket) return
    this.connect()
  }

  startAndWait(timeoutMs = 5000): Promise<void> {
    if (this.disposed) return Promise.reject(new Error('WebSocket disposed before open'))
    if (this.opened) return Promise.resolve()
    if (this.openPromise) return this.openPromise

    this.openPromise = new Promise<void>((resolve, reject) => {
      this.resolveOpen = resolve
      this.rejectOpen = reject
    })
    this.openTimer = setTimeout(() => {
      this.settleOpenFailure(new Error('WebSocket open timed out'))
      this.dispose()
    }, timeoutMs)
    // connect() can fail synchronously and clear this.openPromise.
    const pending = this.openPromise
    this.start()
    return pending
  }

  dispose(): void {
    this.disposed = true
    this.opened = false
    if (this.reconnectTimer !== null) {
      clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
    this.settleOpenFailure(new Error('WebSocket disposed before open'))
    const socket = this.socket
    this.socket = null
    if (socket) this.detach(socket)
    socket?.close()
  }

  private detach(socket: WebSocket): void {
    socket.onopen = null
    socket.onclose = null
    socket.onmessage = null
    socket.onerror = null
  }

  private settleOpenSuccess(): void {
    this.opened = true
    this.retryCount = 0
    if (this.openTimer !== null) clearTimeout(this.openTimer)
    this.openTimer = null
    const resolve = this.resolveOpen
    this.openPromise = null
    this.resolveOpen = null
    this.rejectOpen = null
    resolve?.()
  }

  private settleOpenFailure(error: Error): void {
    if (this.openTimer !== null) clearTimeout(this.openTimer)
    this.openTimer = null
    const reject = this.rejectOpen
    this.openPromise = null
    this.resolveOpen = null
    this.rejectOpen = null
    reject?.(error)
  }

  private connect(): void {
    if (this.disposed) return
    const createSocket = this.options.createSocket ?? ((url: string) => new WebSocket(url))
    let socket: WebSocket
    try {
      socket = createSocket(this.url)
    } catch {
      this.handleConnectionFailure()
      return
    }
    this.socket = socket
    socket.onmessage = (event) => {
      if (!this.disposed && this.socket === socket) this.options.onMessage?.(event)
    }
    socket.onerror = () => {
      if (!this.disposed && this.socket === socket) this.options.onError?.()
    }
    socket.onopen = () => {
      if (this.disposed || this.socket !== socket) return
      this.settleOpenSuccess()
    }
    socket.onclose = () => {
      if (this.disposed || this.socket !== socket) return
      this.detach(socket)
      this.socket = null
      this.opened = false
      this.handleConnectionFailure()
    }
  }

  private handleConnectionFailure(): void {
    if (this.disposed || this.reconnectTimer !== null) return
    const maxRetries = this.options.maxRetries ?? 5
    if (this.retryCount >= maxRetries) {
      this.settleOpenFailure(new Error('WebSocket failed to open'))
      return
    }
    const delay = (this.options.initialDelayMs ?? 500) * (2 ** this.retryCount)
    this.retryCount += 1
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null
      this.connect()
    }, delay)
  }
}

export async function requestAfterWebSocketOpen<T>(
  connection: ManagedWebSocket,
  request: () => Promise<T>,
  timeoutMs = 5000,
): Promise<T> {
  await connection.startAndWait(timeoutMs)
  return request()
}
