// Shared by the learn tabs: commit-pinned source links and the video slots.
const S = window.STOCKHOLD
// @learn:source-url
function anchor(id) {
  const a = S.anchors[id]
  if (!a) return null
  return `${S.repo}/blob/${S.sha}/${a.path}#L${a.start}-L${a.end}`
}
// @learn:end source-url
function fillSourceLinks(root = document) {
  root.querySelectorAll('a.src-link[data-anchor]').forEach((a) => {
    const url = anchor(a.dataset.anchor)
    if (url) { a.href = url; a.target = '_blank'; a.rel = 'noopener' }
  })
}
// Play /videos/<name>.mp4 in `slot` if it exists; say so plainly if it has not been generated yet.
async function playVideo(slot, name) {
  const src = `/videos/${name}.mp4`
  let ok = false
  try { ok = (await fetch(src, { method: 'HEAD' })).ok } catch { ok = false }
  slot.hidden = false
  slot.innerHTML = ok
    ? `<video data-testid="video" src="${src}" controls autoplay playsinline style="width:100%;max-height:420px"></video>`
    : `<p class="muted" data-testid="video-missing">视频待生成：<code>docs/videos/${name}.mp4</code></p>`
}
document.addEventListener('click', (ev) => {
  const b = ev.target.closest('[data-problem-video]')
  if (b) { ev.preventDefault(); playVideo(document.querySelector(`[data-video-slot="${b.dataset.problemVideo}"]`), 'problems/' + b.dataset.problemVideo) }
})
fillSourceLinks()
