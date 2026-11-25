// DLQ replay tool. Plain JavaScript: fetch the JSON endpoints, render tables.
const $ = (s, el = document) => el.querySelector(s)
const esc = (v) => String(v ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]))
const fmt = (ms) => (ms ? new Date(ms).toLocaleTimeString('zh-CN', { hour12: false }) : '')
let current = null
const picked = new Set()

async function api(path, opts = {}) {
  const r = await fetch(path, { headers: { 'Content-Type': 'application/json' }, ...opts })
  const j = await r.json()
  if (!r.ok) throw Object.assign(new Error(j.error?.message || r.statusText), { body: j, status: r.status })
  return j
}

async function loadAlerts() {
  try {
    const a = await api('/ops/api/alerts')
    $('#alerts').innerHTML = a.alerts.map((x) =>
      `<span class="alert ${x.state}" data-alert="${x.name}" data-state="${x.state}"><b>${x.name}</b> ${x.state} · ${x.value}${x.name.includes('Age') ? ' s' : ''} (阈值 ${x.threshold})</span>`).join('') +
      `<span class="muted">未处理 <b data-testid="open-count">${a.open}</b> 条 · 最老 ${a.oldest_open_age_s}s · 15 分钟内进死信 ${a.redriven_15m} 条 · 主队列积压 ${a.payments_queue_visible}</span>`
  } catch (e) { $('#alerts').textContent = '告警加载失败：' + e.message }
}

async function search(ev) {
  ev?.preventDefault()
  await api('/ops/api/sync', { method: 'POST' }).catch(() => {})
  const qs = new URLSearchParams(new FormData($('#search'))).toString()
  const { items } = await api('/ops/api/messages?' + qs)
  $('#results tbody').innerHTML = items.map((m) => `
    <tr data-mid="${esc(m.message_id)}" data-code="${esc(m.code)}" data-status="${esc(m.status)}" class="clickable">
      <td><input type="checkbox" data-pick="${esc(m.message_id)}"></td>
      <td>${fmt(m.archived_at_ms)}</td><td><code data-hl="code">${esc(m.code)}</code></td>
      <td><code>${esc(m.hold_id)}</code></td><td>${esc(m.sku)}</td><td>${esc(m.event_type)}</td>
      <td>${esc(m.receive_count)}</td><td><span class="pill ${esc(m.status)}">${esc(m.status)}</span></td></tr>`).join('') ||
    '<tr><td colspan="8" class="muted" data-testid="no-results">没有匹配的消息</td></tr>'
  loadAlerts()
}

async function open(mid) {
  const d = await api('/ops/api/messages/' + encodeURIComponent(mid))
  current = mid
  $('#detail').hidden = false
  $('#d-mid').textContent = mid
  $('#d-body').textContent = d.body ? JSON.stringify(d.body, null, 2) : d.message.raw
  $('#d-attempts tbody').innerHTML = (d.message.attempts || []).map((a) =>
    `<tr data-attempt="${a.receive_count}"><td>${a.receive_count}</td><td><code>${esc(a.code)}</code></td><td>${esc(a.message)}</td></tr>`).join('')
  $('#d-compare tbody').innerHTML = d.compare.map((c) =>
    `<tr data-field="${esc(c.path)}" class="${c.same ? '' : 'mismatch'}"><td>${esc(c.path)}</td><td><code>${esc(JSON.stringify(c.a))}</code></td><td><code data-hl="now">${esc(JSON.stringify(c.b))}</code></td></tr>`).join('')
  $('#d-hold').textContent = d.hold ? JSON.stringify(d.hold, null, 2) : '(hold 不存在)'
  $('#plan').hidden = true
  $('#replay-result').hidden = true
  loadAudit(mid)
  $('#detail').scrollIntoView({ block: 'start' })
}

function showPlan(p) {
  const el = $('#plan')
  el.hidden = false
  el.dataset.allowed = p.allowed
  el.innerHTML = p.allowed
    ? `<b>可以重放</b>：<code data-hl="transition">${esc(p.effect.transition)}</code>` +
      (p.effect.available_before !== undefined ? `，分片 ${p.effect.shard} 的 available <code data-hl="available">${p.effect.available_before} → ${p.effect.available_after}</code>` : '') +
      (p.warning ? `<br><span class="warn">${esc(p.warning)}</span>` : '')
    : `<b>不能重放</b>：<span data-hl="reason">${esc(p.reason)}</span>`
}

async function loadAudit(mid = '') {
  const { items } = await api('/ops/api/audit' + (mid ? '?message_id=' + encodeURIComponent(mid) : ''))
  $('#audit tbody').innerHTML = items.slice(0, 30).map((a) =>
    `<tr data-action="${esc(a.action)}"><td>${fmt(a.at_ms)}</td><td>${esc(a.actor)}</td><td>${esc(a.action)}</td><td><code>${esc(a.message_id.slice(0, 8))}</code></td>` +
    `<td>${esc(a.result?.new_message_id ? '新消息 ' + a.result.new_message_id.slice(0, 8) : a.plan?.reason || a.plan?.effect?.transition || '')}</td></tr>`).join('')
}

document.addEventListener('click', async (ev) => {
  const t = ev.target
  if (t.matches('[data-pick]')) {
    t.checked ? picked.add(t.dataset.pick) : picked.delete(t.dataset.pick)
    $('#compare-btn').disabled = picked.size !== 2
    return
  }
  const row = t.closest('#results tr[data-mid]')
  if (row) return open(row.dataset.mid)
  if (t.id === 'dry-run') {
    showPlan(await api(`/ops/api/messages/${current}/dry-run`, { method: 'POST' }))
    return loadAudit(current)
  }
  if (t.id === 'replay') {
    const out = $('#replay-result')
    out.hidden = false
    try {
      const r = await api(`/ops/api/messages/${current}/replay`, { method: 'POST' })
      out.dataset.outcome = 'replayed'
      out.innerHTML = `<b>已重放</b>：新消息 <code data-hl="new-mid">${esc(r.new_message_id)}</code>，重放号 ${esc(r.replay_id)}`
    } catch (e) {
      out.dataset.outcome = e.body?.error?.code || 'error'
      out.innerHTML = `<b>${esc(e.body?.error?.code || '失败')}</b>：${esc(e.message)}`
    }
    loadAudit(current)
    return search()
  }
  if (t.id === 'compare-btn') {
    const [a, b] = [...picked]
    const { diff } = await api(`/ops/api/compare?a=${a}&b=${b}`)
    $('#diff-card').hidden = false
    $('#diff tbody').innerHTML = diff.map((d) => `<tr class="${d.same ? '' : 'mismatch'}"><td>${esc(d.path)}</td><td><code>${esc(JSON.stringify(d.a))}</code></td><td><code>${esc(JSON.stringify(d.b))}</code></td></tr>`).join('')
    return
  }
  if (t.dataset.drill) {
    const r = await api('/ops/api/drill', { method: 'POST', body: JSON.stringify({ kind: t.dataset.drill }) })
    $('#drill-out').textContent = `已发出 ${r.message_id.slice(0, 8)}${r.hold_id ? '（hold ' + r.hold_id + '）' : ''}，大约十五秒后进死信`
  }
})

$('#search').addEventListener('submit', search)
const q = new URLSearchParams(location.search).get('q')
if (q) $('#search [name=q]').value = q
search()
loadAudit()
setInterval(loadAlerts, 5000)
