#!/usr/bin/env node
// Scan the repo for `@learn:<id>` … `@learn:end <id>` comment pairs and write
// stockhold/static/generated/anchors.json: {id: {path, start, end}}.
// start/end are the first and last line *between* the markers, so a GitHub
// link highlights the code, not the comments around it. Line numbers are
// never written by hand.
// Usage: learn_anchors.mjs [--check]   (--check: fail if the file is stale)
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
export const OUT = path.join(root, 'stockhold/static/generated/anchors.json')
const exts = new Set(['.py', '.mjs', '.js', '.sql', '.yml', '.sh', '.html'])
const skip = new Set(['node_modules', '.git', '.venv', 'build', 'cdk.out', 'generated', 'test-results', 'playwright-report',
  '.runtime', '__pycache__', '.pytest_cache', 'videos', 'out'])
const self = new Set(['scripts/learn_anchors.mjs'])

function* walk(dir) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    if (skip.has(e.name)) continue
    const p = path.join(dir, e.name)
    if (e.isDirectory()) yield* walk(p)
    else if (exts.has(path.extname(e.name))) yield p
  }
}

export function scan() {
  const anchors = {}
  const errors = []
  const open = /@learn:([a-z0-9-]+)\s*(?:-->|\*\/)?\s*$/
  const close = /@learn:end ([a-z0-9-]+)\s*(?:-->|\*\/)?\s*$/
  for (const file of walk(root)) {
    const rel = path.relative(root, file).split(path.sep).join('/')
    if (self.has(rel)) continue
    const lines = fs.readFileSync(file, 'utf8').split('\n')
    const stack = {}
    lines.forEach((line, i) => {
      let m
      if ((m = line.match(close))) {
        const id = m[1]
        if (stack[id] === undefined) errors.push(`${rel}:${i + 1}: end without start for ${id}`)
        else {
          if (anchors[id]) errors.push(`${rel}:${i + 1}: duplicate anchor ${id} (also in ${anchors[id].path})`)
          anchors[id] = { path: rel, start: stack[id] + 2, end: i }
          delete stack[id]
        }
      } else if ((m = line.match(open)) && m[1] !== 'end') {
        stack[m[1]] = i
      }
    })
    for (const id of Object.keys(stack)) errors.push(`${rel}: @learn:${id} never closed`)
  }
  const sorted = Object.fromEntries(Object.keys(anchors).sort().map((k) => [k, anchors[k]]))
  return { anchors: sorted, errors }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const { anchors, errors } = scan()
  if (errors.length) {
    console.error(errors.join('\n'))
    process.exit(1)
  }
  const json = JSON.stringify(anchors, null, 2) + '\n'
  if (process.argv.includes('--check')) {
    const cur = fs.existsSync(OUT) ? fs.readFileSync(OUT, 'utf8') : ''
    if (cur !== json) {
      console.error('anchors.json is stale: run node scripts/learn_anchors.mjs')
      process.exit(1)
    }
  } else {
    fs.mkdirSync(path.dirname(OUT), { recursive: true })
    fs.writeFileSync(OUT, json)
  }
  console.log(`${Object.keys(anchors).length} anchors`)
}
