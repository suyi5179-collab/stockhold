#!/usr/bin/env node
// CI gate for the learn page's source links:
//  1. anchors.json matches a fresh scan (not stale);
//  2. every anchor id referenced anywhere (learn stages, JS, docs, videos) exists;
//  3. every anchor's line range lies inside its file, is not empty, and sits right under its marker;
//  4. every anchor is referenced somewhere (no dead anchors).
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { OUT, scan } from './learn_anchors.mjs'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const problems = []
const { anchors, errors } = scan()
problems.push(...errors)
const committed = fs.existsSync(OUT) ? JSON.parse(fs.readFileSync(OUT, 'utf8')) : {}
if (JSON.stringify(committed) !== JSON.stringify(anchors)) problems.push('anchors.json is stale: run node scripts/learn_anchors.mjs')

// @learn:anchor-refs
const refs = new Map()          // id -> where it was referenced
const ref = (id, where) => { if (!refs.has(id)) refs.set(id, where) }
const read = (p) => fs.readFileSync(path.join(root, p), 'utf8')
const files = (dir, re) => fs.existsSync(path.join(root, dir))
  ? fs.readdirSync(path.join(root, dir), { recursive: true }).filter((f) => re.test(f)).map((f) => path.join(dir, f)) : []
// learn stages: "src": ["a", "b"]
for (const f of files('stockhold', /\.py$/)) {
  for (const m of read(f).matchAll(/"src":\s*\[([^\]]*)\]/g)) for (const id of m[1].matchAll(/"([a-z0-9-]+)"/g)) ref(id[1], f)
}
// front end: anchor('id') and data-anchor="id"
for (const f of [...files('stockhold/static', /\.js$/), ...files('stockhold/templates', /\.html$/)]) {
  for (const m of read(f).matchAll(/(?:anchor\('|data-anchor=")([a-z0-9-]+)/g)) ref(m[1], f)
}
// docs: [text](anchor:id) in interview / problems, 锚点 `id` in the tutorial chapters
for (const f of ['docs/interview.md', 'docs/problems.md', 'docs/design.md', ...files('docs/tutorial', /\.md$/)]) {
  if (!fs.existsSync(path.join(root, f))) continue
  const text = read(f)
  for (const m of text.matchAll(/anchor:([a-z0-9-]+)/g)) ref(m[1], f)
  for (const m of text.matchAll(/锚点\s*`([a-z0-9-]+)`/g)) ref(m[1], f)
  for (const m of text.matchAll(/锚点\s*`([a-z0-9-]+)`(?:\s*[、和]\s*`([a-z0-9-]+)`)+/g)) for (const id of m[0].matchAll(/`([a-z0-9-]+)`/g)) ref(id[1], f)
}
// videos: problem walkthroughs cut code by anchor; the recording frames a[data-anchor=id]
const psrc = 'video/problems.src.json'
if (fs.existsSync(path.join(root, psrc))) for (const p of JSON.parse(read(psrc))) for (const k of ['code_from', 'before_from']) if (p[k]) ref(p[k].anchor, psrc)
const narr = 'video/narration.json'
if (fs.existsSync(path.join(root, narr))) for (const s of JSON.parse(read(narr))) for (const m of `${s.highlight || ''} ${s.action}`.matchAll(/(?:data-anchor=\\?"?|\bsource:)([a-z0-9-]+)/g)) ref(m[1], narr)
for (const [id, where] of refs) if (!anchors[id]) problems.push(`${where} references unknown anchor ${id}`)
// @learn:end anchor-refs
for (const [id, a] of Object.entries(anchors)) {
  const lines = read(a.path).split('\n')
  if (a.start > a.end) problems.push(`${id}: empty range`)
  if (a.end > lines.length) problems.push(`${id}: L${a.end} beyond end of ${a.path}`)
  if (!lines[a.start - 2]?.includes('@learn:' + id)) problems.push(`${id}: L${a.start - 1} of ${a.path} is not its marker`)
  if (!refs.has(id)) problems.push(`${id}: defined in ${a.path} but never referenced`)
}
if (problems.length) {
  console.error(problems.map((p) => '✗ ' + p).join('\n'))
  process.exit(1)
}
console.log(`✓ ${Object.keys(anchors).length} anchors, ${refs.size} references, all fresh`)
