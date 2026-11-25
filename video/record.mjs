#!/usr/bin/env node
// Record the teaching walkthrough, paced by the narration. For every segment of
// video/narration.json: run its action, draw a red frame around its element,
// then hold for the segment's spoken duration (video/durations.json, default
// 4 s). Writes one webm plus video/timeline.json with the real start of each
// segment and its part (an action that waits on the backend can run longer
// than its narration, so planned and real starts differ).
//
//   node video/record.mjs        (needs ./scripts/demo.sh on :8840, and no tests running)
import { chromium } from '../e2e/node_modules/@playwright/test/index.mjs'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const base = process.env.STOCKHOLD_URL || 'http://127.0.0.1:8840'
const segments = JSON.parse(fs.readFileSync(path.join(here, 'narration.json'), 'utf8'))
const durFile = path.join(here, 'durations.json')
const durations = fs.existsSync(durFile) ? JSON.parse(fs.readFileSync(durFile, 'utf8')) : {}
const outDir = path.join(here, 'out')
fs.mkdirSync(outDir, { recursive: true })

const size = { width: 1600, height: 900 }
const browser = await chromium.launch()
const context = await browser.newContext({ viewport: size, recordVideo: { dir: outDir, size }, deviceScaleFactor: 1 })
const page = await context.newPage()
const runs = {}            // scenario -> run id, so later parts can go back to the same order
const holds = {}           // scenario -> hold id
const t0 = Date.now()

// @learn:record-frame
// The highlight is a red frame drawn over the page, not a class on the element:
// it hugs exactly the thing being discussed (a number, a log line, a button),
// follows it if the page scrolls or re-renders it, and pulses when it appears.
// shape "circle" draws an ellipse for small targets. Returns the target's and
// the frame's rectangles so check_frames.mjs can prove the frame is on target.
async function frame(sel, shape = 'box') {
  if (!sel) {
    await page.evaluate(() => document.getElementById('sh-frame')?.remove())
    return null
  }
  const loc = page.locator(sel).first()
  await loc.waitFor({ state: 'visible', timeout: 20_000 })
  await loc.evaluate((el) => el.scrollIntoView({ block: 'center', inline: 'nearest' }))
  await page.waitForTimeout(150)
  await page.evaluate(({ sel, shape }) => {
    document.getElementById('sh-frame')?.remove()
    if (!document.getElementById('sh-frame-style')) {
      const st = document.createElement('style')
      st.id = 'sh-frame-style'
      st.textContent = `@keyframes sh-pulse { 0% { transform: scale(1.35); opacity: 0 } 55% { transform: scale(.96); opacity: 1 } 75% { transform: scale(1.04) } 100% { transform: scale(1) } }
        #sh-frame { position: fixed; pointer-events: none; z-index: 9998; border: 3px solid #e5383b; box-shadow: 0 0 0 6px rgba(229,56,59,.25); animation: sh-pulse .55s ease-out; }`
      document.head.appendChild(st)
    }
    const find = () => [...document.querySelectorAll(sel)].find((e) => e.getBoundingClientRect().width > 0)
    let el = find()
    if (!el) throw new Error('frame target not found: ' + sel)
    const f = document.createElement('div')
    f.id = 'sh-frame'
    document.body.appendChild(f)
    const place = () => {
      if (!el.isConnected) el = find() || el     // the page re-rendered it: frame the new copy
      const r = el.getBoundingClientRect()
      const px = shape === 'circle' ? Math.max(14, r.width * 0.22) : 5
      const py = shape === 'circle' ? Math.max(10, r.height * 0.45) : 4
      Object.assign(f.style, { left: r.left - px + 'px', top: r.top - py + 'px', width: r.width + 2 * px + 'px', height: r.height + 2 * py + 'px', borderRadius: shape === 'circle' ? '50%' : '8px' })
    }
    place()
    const follow = () => { if (f.isConnected) { place(); requestAnimationFrame(follow) } }
    requestAnimationFrame(follow)
  }, { sel, shape })
  await page.waitForTimeout(650)      // measure after the pulse (a transform) has settled
  return page.evaluate((sel) => {
    const el = [...document.querySelectorAll(sel)].find((e) => e.getBoundingClientRect().width > 0)
    const f = document.getElementById('sh-frame')
    if (!el || !f) return null
    const r = el.getBoundingClientRect(), fr = f.getBoundingClientRect()
    return { target: [r.left, r.top, r.width, r.height].map(Math.round), frame: [fr.left, fr.top, fr.width, fr.height].map(Math.round) }
  }, sel)
}
// @learn:end record-frame

async function caption(text) {
  // Show a link's URL on screen instead of leaving the page for GitHub or Jaeger.
  await page.evaluate((text) => {
    let c = document.getElementById('sh-caption')
    if (!c) {
      c = document.createElement('div')
      c.id = 'sh-caption'
      c.style.cssText = 'position:fixed;left:50%;bottom:24px;transform:translateX(-50%);background:#111827;color:#fde68a;font:14px ui-monospace,monospace;padding:8px 14px;border-radius:8px;z-index:9999;max-width:90%;word-break:break-all'
      document.body.appendChild(c)
    }
    c.textContent = text
    c.style.display = text ? 'block' : 'none'
  }, text)
}

async function clickFramed(sel) {
  // Frame what is about to be clicked, let the viewer see it, then click.
  await frame(sel, 'box')
  await page.waitForTimeout(900)
  await page.locator(sel).first().click()
  await page.waitForTimeout(400)
}

async function act(action) {
  const [name, ...rest] = action.split(':')
  const arg = rest.join(':')
  switch (name) {
    case 'noop':
      return
    case 'goto':
      await page.goto(base + arg)
      return
    case 'submit': {                 // submit:<scenario>
      await clickFramed(`[data-scenario="${arg}"]`)
      await page.waitForURL(/run=/)
      runs[arg] = new URL(page.url()).searchParams.get('run')
      holds[arg] = (await page.locator('[data-testid=hold-id]').textContent()).trim()
      return
    }
    case 'wait': {                   // wait:<stage> until the backend says it is done
      await page.locator(`.timeline li[data-stage="${arg}"][data-status=done]`).waitFor({ timeout: 120_000 })
      await page.waitForTimeout(300)
      return
    }
    case 'stage': {                  // stage:<id> wait for it, then open its detail
      await page.locator(`.timeline li[data-stage="${arg}"][data-status=done]`).waitFor({ timeout: 120_000 })
      await clickFramed(`.timeline li[data-stage="${arg}"]`)
      await page.locator(`#stage-detail[data-stage="${arg}"]`).waitFor()
      return
    }
    case 'expand':                   // expand:<details selector>
      await clickFramed(`${arg} > summary`)
      return
    case 'click':
      await clickFramed(arg)
      return
    case 'source': {                 // source:<anchor id> show the commit-pinned link it would open
      const a = page.locator(`a.src-link[data-anchor="${arg}"]`).first()
      await a.scrollIntoViewIfNeeded()
      await a.hover()
      await caption(await a.getAttribute('href'))
      return
    }
    case 'run':                      // run:<scenario> back to an earlier order
      await page.goto(`${base}/learn?run=${runs[arg]}`)
      await page.locator('.timeline li[data-stage=order][data-status=done]').waitFor()
      return
    case 'login':
      await page.goto(base + '/login?next=/ops')
      await page.locator('input[name=username]').fill('oncall')
      await page.locator('input[name=password]').fill('oncall-demo')
      await page.locator('form button[type=submit]').click()
      await page.waitForURL('**/ops')
      return
    case 'ops_hold': {               // open the replay tool searched for the bad message's hold
      await page.goto(`${base}/ops?q=${holds.dlq}`)
      await page.locator('#results tr[data-mid]').first().waitFor({ timeout: 60_000 })
      return
    }
    case 'trace': {
      const a = page.locator('[data-testid=trace-link]')
      await a.hover()
      await caption(await a.getAttribute('href'))
      return
    }
    case 'checkout_new': {           // checkout_new:<shards>:<units>
      const [shards, units] = arg.split(':')
      await page.locator('#shards').fill(shards)
      await page.locator('#units').fill(units)
      await clickFramed('[data-testid=new-sku]')
      await page.locator('#cur-sku').filter({ hasText: `sale-${shards}x-` }).waitFor()
      return
    }
    case 'checkout_go': {            // checkout_go:<orders>
      await page.locator('#orders').fill(arg)
      await clickFramed('[data-testid=flash-go]')
      await page.locator('#result-card:not([hidden])').waitFor({ timeout: 120_000 })
      await page.waitForFunction(() => !document.querySelector('[data-testid=flash-go]').disabled)
      return
    }
    default:
      throw new Error(`unknown action ${action}`)
  }
}

// @learn:record-pacing
const timeline = []
for (const s of segments) {
  const startMs = Date.now() - t0
  await caption('')
  await act(s.action)
  const rects = await frame(s.highlight, s.shape || 'box')
  const want = (durations[s.id] ?? 4) * 1000
  const spent = Date.now() - t0 - startMs
  if (spent < want) await page.waitForTimeout(want - spent)
  timeline.push({ id: s.id, part: s.part, startMs, durMs: Date.now() - t0 - startMs, plannedMs: want, highlight: s.highlight, shape: s.shape || 'box', rects })
  console.log(`${s.id} ${s.part} @${(startMs / 1000).toFixed(1)}s ${s.action}`)
}
// @learn:end record-pacing
await page.waitForTimeout(800)
const video = page.video()
await context.close()
await browser.close()
const webm = path.join(outDir, 'learn.webm')
fs.renameSync(await video.path(), webm)
fs.writeFileSync(path.join(here, 'timeline.json'), JSON.stringify({ video: path.relative(path.dirname(here), webm), size, segments: timeline }, null, 2) + '\n')
console.log(`wrote ${webm} and video/timeline.json (${timeline.length} segments, ${((Date.now() - t0) / 1000).toFixed(0)} s)`)
