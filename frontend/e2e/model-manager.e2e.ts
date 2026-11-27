import { expect, test } from '@playwright/test'

test.skip(process.env.HIIMAGE_TEST_MODEL_MANAGER !== '1', '需要启动隔离的模型管理测试服务')

test.afterEach(async ({ request }) => {
  expect((await request.delete('/api/e2e-fixture')).status()).toBe(204)
})

test('saves a custom library, downloads shared weights there and confirms deletion', async ({ page }) => {
  const fixture = await page.request.get('/api/e2e-fixture')
  expect(fixture.ok()).toBeTruthy()
  const { directory } = await fixture.json()
  await page.goto('/#/settings')
  await page.getByRole('button', { name: '模型管理', exact: true }).click()
  const location = page.getByRole('textbox', { name: '模型存储位置' })
  await location.fill(directory)
  const saved = page.waitForResponse(response => response.url().endsWith('/api/models/storage'))
  await page.getByRole('button', { name: '保存位置' }).click()
  expect((await saved).ok()).toBeTruthy()
  await page.reload()
  await page.getByRole('button', { name: '模型管理', exact: true }).click()
  await expect(location).toHaveValue(directory)
  const row = (name: string) => page.getByText(name, { exact: true }).locator('xpath=../../..')
  const primary = row('主测试模型')
  const alias = row('共享测试模型')
  await expect(primary.getByText('未下载', { exact: true })).toBeVisible()
  await expect(alias.getByText('未下载', { exact: true })).toBeVisible()

  const submitted = page.waitForResponse(response =>
    response.request().method() === 'POST' && response.url().endsWith('/api/models/download/e2e-primary'),
  )
  await primary.getByTitle('下载此模型').click()
  expect((await submitted).ok()).toBeTruthy()
  await expect(primary.getByText('已下载', { exact: true })).toBeVisible()
  await expect(alias.getByText('已下载', { exact: true })).toBeVisible()

  const deleteWeight = primary.getByRole('button', { name: '删除 主测试模型 的模型权重' })
  await deleteWeight.click()
  const dialog = page.getByRole('dialog', { name: '删除模型权重' })
  await expect(dialog.getByText('shared-fixture.pth', { exact: true })).toBeVisible()
  await expect(dialog.getByRole('listitem').filter({ hasText: '主测试模型' })).toBeVisible()
  await expect(dialog.getByRole('listitem').filter({ hasText: '共享测试模型' })).toBeVisible()
  await dialog.getByRole('button', { name: '取消', exact: true }).click()
  await expect(dialog).toBeHidden()
  expect(await (await page.request.get('/api/e2e-fixture')).json()).toEqual({ directory, weight_exists: true, default_exists: false })

  await deleteWeight.click()
  const deletion = page.waitForResponse(response =>
    response.request().method() === 'DELETE' && response.url().endsWith('/api/models/e2e-primary/file-resources'),
  )
  await dialog.getByRole('button', { name: '确认删除' }).click()
  expect((await deletion).ok()).toBeTruthy()
  await expect(primary.getByText('未下载', { exact: true })).toBeVisible()
  await expect(alias.getByText('未下载', { exact: true })).toBeVisible()
  expect(await (await page.request.get('/api/e2e-fixture')).json()).toEqual({ directory, weight_exists: false, default_exists: false })
})
