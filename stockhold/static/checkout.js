// Checkout simulator: everything here comes from the backend's real stock report.
const $ = (s) => document.querySelector(s)
async function api(path, opts = {}) {
  const r = await fetch(path, { headers: { 'Content-Type': 'application/json' }, ...opts })
  const j = await r.json()
  if (!r.ok) throw new Error(j.error?.message || r.statusText)
  return j
}
const stat = (k, v, hl) => `<div class="stat" data-stat="${k}"><span>${k}</span><b ${hl ? `data-hl="${hl}"` : ''}>${v}</b></div>`

function renderStock(rep) {
  $('#cur-sku').textContent = rep.sku
  $('#stats').innerHTML = stat('容量 capacity', rep.capacity) + stat('可卖 available', rep.available, 'available') +
    stat('占用 held', rep.held, 'held') + stat('已确认 confirmed', rep.confirmed) +
    stat('超卖 oversold', rep.oversold, 'oversold') + stat('恒等式成立', rep.balanced ? '是' : '否', 'balanced')
  $('#shard-bars').innerHTML = rep.shards.map((s) => {
    const pct = s.capacity ? Math.round((100 * s.available) / s.capacity) : 0
    return `<div class="bar" data-shard="${s.shard}"><span>分片 ${s.shard}</span><i style="width:${pct}%"></i><b>${s.available}/${s.capacity}</b><em>v${s.version}</em></div>`
  }).join('')
}

async function load() { renderStock(await api('/checkout/api/stock/' + encodeURIComponent($('#sku').value))) }

$('#sku').addEventListener('change', load)
$('#new-sku').addEventListener('click', async () => {
  const rep = await api('/checkout/api/new-sku', { method: 'POST', body: JSON.stringify({ units: +$('#units').value, shards: +$('#shards').value }) })
  const o = document.createElement('option')
  o.value = rep.sku; o.textContent = `${rep.sku}（${rep.capacity} 件 · ${rep.shards.length} 分片）`
  $('#sku').appendChild(o); $('#sku').value = rep.sku
  renderStock(rep)
})
$('#go').addEventListener('click', async () => {
  $('#busy').textContent = '下单中…'
  $('#go').disabled = true
  try {
    const r = await api('/checkout/api/flash', { method: 'POST', body: JSON.stringify({ sku: $('#sku').value, orders: +$('#orders').value, concurrency: +$('#concurrency').value }) })
    renderStock(r.after)
    $('#result-card').hidden = false
    $('#result').innerHTML = stat('成功占到', r.held, 'r-held') + stat('卖光了', r.sold_out, 'r-sold-out') + stat('冲突放弃', r.contention, 'r-contention') +
      stat('每单冲突次数', r.conflicts_per_hold, 'r-conflicts') + stat('p50 毫秒', r.latency_ms.p50) + stat('p99 毫秒', r.latency_ms.p99, 'r-p99') +
      stat('超卖', r.oversold, 'r-oversold')
    $('#result-note').textContent = `${r.orders} 单、${r.concurrency} 并发、${r.shards} 个分片，用时 ${r.wall_s} 秒。超卖恒为零靠的是条件写；分片多了，冲突和尾延迟会降下来。`
  } catch (e) { $('#busy').textContent = '失败：' + e.message; return } finally { $('#go').disabled = false }
  $('#busy').textContent = ''
})
load()
