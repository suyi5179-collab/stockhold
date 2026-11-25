import { expect, test, type Page } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const anchors = JSON.parse(fs.readFileSync(path.join(ROOT, 'stockhold/static/generated/anchors.json'), 'utf8'))
const MAIN = ['order', 'reserve', 'enqueue', 'trigger', 'lambda', 's3', 'duckdb']
const BRANCH = ['bad', 'retry', 'dlq', 'dryrun', 'replay', 'released']

async function login(page: Page, user: string, pass: string, next = '/ops') {
  await page.goto(`/login?next=${next}`)
  await page.locator('input[name=username]').fill(user)
  await page.locator('input[name=password]').fill(pass)
  await page.locator('form button[type=submit]').click()
  await page.waitForURL(`**${next}`)
}

// A source link must be commit-pinned (never "main") and point at lines that exist right under the marker.
async function expectSourceLink(page: Page, scope: string, id: string) {
  const repo = await page.evaluate(() => (window as any).STOCKHOLD.repo)
  expect(repo).toMatch(/^https:\/\/github\.com\/[\w.-]+\/stockhold$/)
  const href = await page.locator(`${scope} a.src-link[data-anchor="${id}"]`).first().getAttribute('href')
  const a = anchors[id]
  const esc = (s: string) => s.replace(/[.*+?^${}()|[\]\\/]/g, '\\$&')
  expect(href).toMatch(new RegExp(`^${esc(repo)}/blob/[0-9a-f]{40}/${esc(a.path)}#L${a.start}-L${a.end}$`))
  const lines = fs.readFileSync(path.join(ROOT, a.path), 'utf8').split('\n')
  expect(lines[a.start - 2]).toContain('@learn:' + id)
  expect(lines[a.end]).toContain('@learn:end ' + id)
  expect(a.end).toBeLessThanOrEqual(lines.length)
}

test('learn: a real order lights the stages in order, with real data and pinned source links', async ({ page }) => {
  await page.goto('/learn')
  await page.getByTestId('learn-submit').click()
  for (const id of MAIN) await expect(page.locator(`[data-testid=step-${id}]`)).toHaveAttribute('data-status', 'done')
  expect(await page.evaluate(() => (window as any).__learnLog)).toEqual(MAIN)        // in order, not all at once
  await page.getByTestId('step-reserve').click()
  await expect(page.locator('#stage-detail [data-hl=available]')).toHaveText(/^\d+ → \d+$/)
  await expect(page.locator('#stage-detail [data-hl=condition]')).toHaveText('available >= :n AND version = :v')
  await expectSourceLink(page, '#stage-detail', 'reserve-transact')
  await page.getByTestId('step-lambda').click()
  await expect(page.locator('#stage-detail [data-hl=transition]')).toHaveText('HELD → RELEASED')
  await expect(page.locator('#stage-detail [data-hl=lambda-log]').first()).toContainText('"fn": "payments"')
  await page.getByTestId('step-s3').click()
  await expect(page.locator('#stage-detail [data-hl=s3-key]')).toHaveText(/^events\/dt=\d{4}-\d\d-\d\d\/hour=\d\d\//)
  await page.getByTestId('step-duckdb').click()
  await expect(page.locator('#stage-detail table[data-table=rates] tr[data-sku=learn-tee]')).toBeVisible()
  await page.locator('.video-btn[data-part=p07-eventlog]').first().click()
  await expect(page.getByTestId('video').or(page.getByTestId('video-missing'))).toBeVisible()
})

test('bad message: retried, dead-lettered, found in the replay tool, dry-run, replayed once', async ({ page }) => {
  await login(page, 'oncall', 'oncall-demo')
  await page.goto('/learn')
  await page.getByTestId('learn-submit-dlq').click()
  for (const id of ['bad', 'retry', 'dlq']) await expect(page.locator(`[data-testid=step-${id}]`)).toHaveAttribute('data-status', 'done')
  await page.getByTestId('step-retry').click()
  await expect(page.locator('#stage-detail tr[data-attempt]')).toHaveCount(3)
  const learnUrl = page.url()
  await page.getByTestId('step-dlq').click()
  await page.getByTestId('open-ops').click()
  await page.locator('#results tr[data-mid]').first().click()
  await expect(page.locator('#d-attempts tbody tr')).toHaveCount(3)
  await expect(page.locator('#d-compare tr[data-field=status] [data-hl=now]')).toHaveText('"HELD"')
  await page.getByTestId('dry-run').click()
  await expect(page.getByTestId('plan')).toHaveAttribute('data-allowed', 'true')
  await expect(page.locator('#plan [data-hl=transition]')).toHaveText('HELD -> RELEASED')
  await page.getByTestId('replay').click()
  await expect(page.getByTestId('replay-result')).toHaveAttribute('data-outcome', 'replayed')
  await page.getByTestId('replay').click()
  await expect(page.getByTestId('replay-result')).toHaveAttribute('data-outcome', 'ALREADY_REPLAYED')
  await expect(page.locator('#audit tr[data-action=replay]')).toHaveCount(1)
  await page.goto(learnUrl)
  for (const id of BRANCH) await expect(page.locator(`[data-testid=step-${id}]`)).toHaveAttribute('data-status', 'done')
  await expect(page.getByTestId('hold-status')).toHaveText('RELEASED')
})

test('viewer can search and dry-run but not replay', async ({ page }) => {
  await login(page, 'viewer', 'viewer-demo')
  await expect(page.getByTestId('replay-needs-role')).toBeAttached()
  await expect(page.getByTestId('replay')).toBeDisabled()
  await expect(page.getByTestId('alerts').locator('[data-alert=DLQOldestMessageAge]')).toBeVisible()
  expect((await page.request.post('/ops/api/messages/anything/replay')).status()).toBe(403)
})

test('checkout simulator: a flash sale on shards never oversells', async ({ page }) => {
  await login(page, 'oncall', 'oncall-demo', '/checkout')
  await page.locator('#shards').fill('4')
  await page.locator('#units').fill('40')
  await page.getByTestId('new-sku').click()
  await expect(page.locator('#shard-bars .bar')).toHaveCount(4)
  await page.getByTestId('orders').fill('60')
  await page.getByTestId('flash-go').click()
  await expect(page.locator('[data-hl=r-oversold]')).toHaveText('0')
  const held = Number(await page.locator('[data-hl=r-held]').textContent())
  expect(held).toBeGreaterThan(0)
  expect(held).toBeLessThanOrEqual(40)
  await expect(page.locator('[data-hl=balanced]')).toHaveText('是')
})

test('interview tab: open a question and see its answer', async ({ page }) => {
  await page.goto('/learn/interview')
  const q = page.locator('details.qa[data-q="1"]').first()
  await q.locator('summary').click()
  await expect(q.locator('.answer')).toContainText('一句话答案')
  expect(await page.locator('details.qa').count()).toBeGreaterThanOrEqual(25)
  await expectSourceLink(page, 'details.qa', 'state-machine')
})

test('problems tab: expand one, see how it was found and fixed, with a video slot', async ({ page }) => {
  await page.goto('/learn/problems')
  const p = page.locator('details[data-problem=q07-oversell]')
  await p.locator('summary').click()
  await expect(p.locator('.answer')).toContainText('怎么定位')
  await expect(p.locator('.answer pre').first()).toContainText('assert 20 == 0')
  await expectSourceLink(page, 'details[data-problem=q07-oversell]', 'reserve-transact')
  await p.locator('[data-problem-video]').click()
  await expect(p.getByTestId('video').or(p.getByTestId('video-missing'))).toBeVisible()
})
