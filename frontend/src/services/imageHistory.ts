import { showToast } from '../components/ui/Toast'
import { base64ToBytes } from './fileService'

export const imageHistoryLabels = {
  watermark: '去水印',
  repair: '瑕疵修复',
  cutout: '抠图',
  refine: '抠图精修',
  composite: '背景合成',
  upscale: '单图超分',
  'upscale-batch': '批量超分',
  'prompt-edit': '提示词编辑',
}

type ImageOperation = keyof typeof imageHistoryLabels

export interface ImageHistoryEntry {
  id: string
  operation: ImageOperation
  name: string
  createdAt: number
}

const imagesStore = 'images'
const entriesStore = 'entries'
const listeners = new Set<() => void>()

function openHistory(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open('hiimage-history', 2)
    request.onupgradeneeded = ({ oldVersion }) => {
      const db = request.result
      const entries = db.createObjectStore(entriesStore, { keyPath: 'id' })
      if (oldVersion === 0) {
        db.createObjectStore(imagesStore, { keyPath: 'id' })
      } else {
        const scan = request.transaction!.objectStore(imagesStore).openCursor()
        scan.onsuccess = () => {
          const cursor = scan.result
          if (!cursor) return
          const { id, operation, name, createdAt } = cursor.value as ImageHistoryEntry
          entries.put({ id, operation, name, createdAt })
          cursor.continue()
        }
      }
    }
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error)
  })
}

async function historyRequest<T>(
  mode: IDBTransactionMode,
  run: (entries: IDBObjectStore, images: IDBObjectStore) => IDBRequest<T>,
): Promise<T> {
  const db = await openHistory()
  try {
    return await new Promise((resolve, reject) => {
      const transaction = db.transaction([entriesStore, imagesStore], mode)
      const request = run(transaction.objectStore(entriesStore), transaction.objectStore(imagesStore))
      transaction.oncomplete = () => resolve(request.result)
      transaction.onabort = () => reject(transaction.error ?? new Error('历史记录操作已中止'))
    })
  } finally {
    db.close()
  }
}

function historyChanged() {
  for (const listener of listeners) listener()
}

export function subscribeImageHistory(listener: () => void) {
  listeners.add(listener)
  return () => { listeners.delete(listener) }
}

export function recordImageResult(id: string, operation: ImageOperation, image: string, fileName?: string) {
  const name = `${fileName?.replace(/\.[^.]+$/, '') || imageHistoryLabels[operation]}.png`
  const createdAt = Date.now()
  return Promise.resolve().then(() => {
    const bytes = base64ToBytes(image)
    return historyRequest('readwrite', (entries, images) => {
      // Raw bytes also work in WebKit when IndexedDB cannot store Blobs.
      images.put({ id, image: bytes.buffer })
      return entries.put({ id, operation, name, createdAt } satisfies ImageHistoryEntry)
    })
  }).then(() => {
    historyChanged()
    return true
  }).catch(error => {
    showToast('warning', error?.name === 'QuotaExceededError' ? '浏览器存储空间不足，图片未保存到历史记录' : '保存历史记录失败')
    return false
  })
}

export async function listImageHistory(): Promise<ImageHistoryEntry[]> {
  const entries: ImageHistoryEntry[] = await historyRequest('readonly', entries => entries.getAll())
  return entries.sort((a, b) => b.createdAt - a.createdAt || b.id.localeCompare(a.id))
}

export async function loadHistoryImage(entry: ImageHistoryEntry): Promise<File> {
  const record: { image: ArrayBuffer | Blob } | undefined = await historyRequest('readonly', (_, images) => images.get(entry.id))
  if (!record) throw new Error('这张历史图片已删除')
  return new File([record.image], entry.name, { type: 'image/png' })
}

export async function removeImageHistory(id: string) {
  await historyRequest('readwrite', (entries, images) => {
    images.delete(id)
    return entries.delete(id)
  })
  historyChanged()
}

export async function clearImageHistory() {
  await historyRequest('readwrite', (entries, images) => {
    images.clear()
    return entries.clear()
  })
  historyChanged()
}
