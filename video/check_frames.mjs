#!/usr/bin/env node
// After a recording: check every red frame in video/timeline.json against
// the element it was meant to frame. A frame must contain its target,
// must not leave more than a fixed margin around it (a frame around a
// whole panel when the narration talks about one number is the failure
// we care about), must be on screen, and its target must have a real
// size. Prints one line per segment.
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const t = JSON.parse(fs.readFileSync(path.join(here, 'timeline.json'), 'utf8'))
let bad = 0
for (const s of t.segments) {
  if (!s.rects) { console.log(`${s.id}  (no frame)`); continue }
  const [tx, ty, tw, th] = s.rects.target
  const [fx, fy, fw, fh] = s.rects.frame
  const contains = fx <= tx + 1 && fy <= ty + 1 && fx + fw >= tx + tw - 1 && fy + fh >= ty + th - 1
  // box: 5/4 px padding; circle: an ellipse needs more room around a short number
  const maxPad = s.shape === 'circle' ? Math.max(40, 0.6 * Math.max(tw, th)) : 12
  const tight = fw - tw <= 2 * maxPad && fh - th <= 2 * maxPad
  // fully visible, unless the target is taller than the viewport itself
  const onScreen = th > t.size.height ? ty < t.size.height && ty + th > 0 : ty >= 0 && ty + th <= t.size.height
  const ok = tw > 0 && th > 0 && contains && tight && onScreen
  const slack = `+${fw - tw}x${fh - th}px`
  if (!ok) bad++
  console.log(`${ok ? '✓' : '✗'} ${s.id} ${s.shape.padEnd(6)} target ${tw}x${th} @${tx},${ty}  frame ${fw}x${fh}  pad ${slack}  ${s.highlight}`)
}
console.log(bad ? `\n${bad} frame(s) off` : '\nall frames on target')
process.exit(bad ? 1 : 0)
