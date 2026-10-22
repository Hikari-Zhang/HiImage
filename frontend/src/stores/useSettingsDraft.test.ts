import { beforeEach, describe, expect, it } from 'vitest'
import { getSettingsDraftStore } from './useSettingsDraft'
import { useSettingsStore } from './useSettingsStore'

const draft = getSettingsDraftStore('draft-test')
beforeEach(() => {
  useSettingsStore.setState({ serverPort: 51821, hfEndpoint: 'https://huggingface.com' })
  draft.getState().reset()
})

describe('settings drafts', () => {
  it('retains unsaved edits when saved settings are fetched again', () => {
    draft.getState().setPort('52000')
    useSettingsStore.setState({ serverPort: 53000 })
    draft.getState().syncFromSettings()
    expect(draft.getState().port).toBe('52000')
    expect(draft.getState().dirty).toBe(true)
  })

  it('does not clear edits made while an older save was pending', () => {
    draft.getState().setHfToken('first-token')
    const savedRevision = draft.getState().revision
    draft.getState().setHfToken('new-token')
    draft.getState().markSaved(savedRevision)
    expect(draft.getState().hfToken).toBe('new-token')
    expect(draft.getState().dirty).toBe(true)
    draft.getState().markSaved(draft.getState().revision)
    expect(draft.getState().hfToken).toBe('')
    expect(draft.getState().dirty).toBe(false)
  })

  it('isolates drafts for different backends', () => {
    const other = getSettingsDraftStore('another-backend')
    other.getState().reset()
    draft.getState().setPort('52000')
    expect(other.getState().port).toBe('51821')
    expect(getSettingsDraftStore('draft-test').getState().port).toBe('52000')
  })
})
