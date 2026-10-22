import type { ApiClient } from './apiClient'
import { ensureApiSuccess } from './apiClient'

export async function saveSettingsRequest(client: Pick<ApiClient, 'fetch'>, payload: unknown): Promise<void> {
  const response = await client.fetch('/api/settings', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  await ensureApiSuccess(response, '保存设置失败')
}

export async function clearHfTokenRequest(client: Pick<ApiClient, 'fetch'>): Promise<void> {
  await saveSettingsRequest(client, { clear_hf_token: true })
}
