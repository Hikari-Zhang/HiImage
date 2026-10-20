import type { ApiClient } from './apiClient'

export async function saveSettingsRequest(client: Pick<ApiClient, 'fetch'>, payload: unknown): Promise<void> {
  const response = await client.fetch('/api/settings', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (response.ok) return

  let message = `保存设置失败 (${response.status})`
  try {
    const data = await response.json() as { detail?: string; message?: string }
    message = data.detail || data.message || message
  } catch {
    const text = await response.text().catch(() => '')
    if (text) message = text
  }
  throw new Error(message)
}

export async function clearHfTokenRequest(client: Pick<ApiClient, 'fetch'>): Promise<void> {
  await saveSettingsRequest(client, { clear_hf_token: true })
}
