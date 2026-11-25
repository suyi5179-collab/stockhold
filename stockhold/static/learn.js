// Learn mode: poll the backend's real state for one run and light up the stages it has reached.
const $ = (s, el = document) => el.querySelector(s)
const esc = (v) => String(v ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]))
const J = (v) => esc(JSON.stringify(v, null, 2))
const t = (ms) => (ms ? new Date(ms).toLocaleTimeString('zh-CN', { hour12: false }) : '')
const STEPS = Object.fromEntries([...window.LEARN_STEPS.main, ...window.LEARN_STEPS.branch].map((s) => [s.id, s]))
let runId = null, last = null, selected = null, userPicked = false, timer = null
window.__learnLog = []        // the order in which stages turned done, for the e2e test

const obj = (b) => (typeof b === 'string' ? JSON.parse(b) : b)
const box = (id, label, body) => `<details data-json="${id}"><summary>${label}</summary><pre>${body}</pre></details>`

// One renderer per stage: show the real values, each with a data-hl hook for the recording.
const R = {
  order: (d) => `<p>请求 <code>${esc(d.request.method)} ${esc(d.request.path)}</code>，<code>Idempotency-Key: <span data-hl="idem-key">${esc(d.request.headers['Idempotency-Key'])}</span></code></p>
    <pre data-hl="request">${J(d.request.body)}</pre><p>响应 <b data-hl="response-status">${d.response.status}</b>，hold <code>${esc(d.response.hold_id)}</code></p>`,
  reserve: (d) => `<table class="grid kv">
      <tr><td>分片</td><td><code>SHARD#0${d.reserve.shard}</code></td></tr>
      <tr><td>available</td><td><code data-hl="available">${d.reserve.available_before} → ${d.reserve.available_after}</code></td></tr>
      <tr><td>version</td><td><code data-hl="version">${d.reserve.version_before} → ${d.reserve.version_after}</code></td></tr>
      <tr><td>条件</td><td><code data-hl="condition">${esc(d.condition)}</code></td></tr>
      <tr><td>尝试 / 冲突</td><td><code data-hl="attempts">${d.reserve.attempts} / ${d.reserve.conflicts}</code></td></tr></table>
    ${box('hold-row', 'hold 这一行', J(d.hold_row))}${box('shard-row', '库存分片这一行（现在）', J(d.shard_now))}`,
  enqueue: (d) => `<p>队列 <code>${esc(d.queue)}</code>，延迟 <b data-hl="delay">${d.delay_seconds}</b> 秒，消息 <code data-hl="expiry-mid">${esc(d.message_id)}</code></p>${box('expiry-body', '消息体', J(d.body))}`,
  trigger: (d) => d.kind === 'timeout'
    ? `<p>没人付款。到期时间 <code>${t(d.expires_at_ms)}</code>，还剩 <b data-hl="countdown">${d.seconds_left}</b> 秒</p>`
    : `<p>付款失败事件进了 <code>stockhold-payment-events</code>，消息 <code data-hl="payment-mid">${esc(d.message_id)}</code></p>${box('payment-body', '消息体', J(obj(d.body)))}`,
  lambda: (d) => `<p>状态 <code data-hl="transition">${esc(d.transition.from)} → ${esc(d.transition.to)}</code>，由 <b data-hl="by">${esc(d.transition.by)}</b> 在 ${t(d.transition.at_ms)} 完成</p>
    <p>分片现在 available <b data-hl="shard-after">${d.shard_after.available}</b>，version ${d.shard_after.version}</p>
    <h3>Lambda 在 CloudWatch Logs 里打的行</h3>${(d.logs || []).map((l) => `<pre data-hl="lambda-log">${esc(l)}</pre>`).join('') || '<p class="muted">（日志还没到）</p>'}`,
  s3: (d) => d.objects.map((o, i) => `<p>s3://${esc(d.bucket)}/<code data-hl="${i ? 's3-key-' + i : 's3-key'}">${esc(o.key)}</code></p><pre data-hl="${i ? 's3-line-' + i : 's3-line'}">${esc(JSON.stringify(o.event))}</pre>`).join(''),
  duckdb: (d) => {
    const tbl = (res, id) => `<table class="grid" data-table="${id}"><tr>${res.columns.map((c) => `<th>${esc(c)}</th>`).join('')}</tr>` +
      res.rows.map((r) => `<tr data-sku="${esc(r.sku)}">${res.columns.map((c) => `<td data-col="${esc(c)}">${esc(r[c])}</td>`).join('')}</tr>`).join('') + '</table>'
    return `<p>DuckDB 读 S3 事件日志的本地镜像，跑 <code>sql/rates_by_product.sql</code>（Athena 上同一条 SQL）。</p>${tbl(d.rates, 'rates')}
      <h3>hold 时长建议（sql/hold_timeout_recommendation.sql）</h3>${d.recommendation.rows.length ? tbl(d.recommendation, 'recommend') : '<p class="muted">还没有确认付款的订单，算不出建议。</p>'}
      ${box('sql', '这条 SQL', esc(d.rates.sql))}`
  },
  bad: (d) => `<p>故障开关：这个 hold 接下来 <b data-hl="fault">${d.fault ? d.fault.remaining : 0}</b> 次处理都失败（下游订单服务挂了）</p>${box('bad-body', '消息体', J(obj(d.message.body)))}`,
  retry: (d) => `<table class="grid"><tr><th>第几次</th><th>错误码</th><th>时间</th></tr>${d.attempts.map((a) => `<tr data-attempt="${a.receive_count}"><td>${a.receive_count}</td><td><code>${esc(a.code)}</code></td><td>${t(a.at_ms)}</td></tr>`).join('')}</table>`,
  dlq: (d) => `<p>进了死信，原因 <code data-hl="dlq-code">${esc(d.code)}</code>，收到 ${d.receive_count} 次，状态 <b>${esc(d.status)}</b></p>
    <p><a class="primary-link" data-testid="open-ops" href="${esc(last?.ops_link)}">在重放工具里打开 →</a></p>`,
  dryrun: (d) => `<p>${esc(d.by)} 做了 dry-run：<code data-hl="plan">${esc(d.plan.allowed ? d.plan.effect.transition : d.plan.reason)}</code></p>`,
  replay: (d) => `<p>${esc(d.by)} 重放了，新消息 <code data-hl="replay-mid">${esc(d.new_message_id)}</code></p>`,
  released: (d) => `<p>状态 <code data-hl="released">${esc(d.transition.from)} → ${esc(d.transition.to)}</code>，分片 available <b>${d.shard_now.available}</b></p>`,
}

function srcLinks(id) {
  return (STEPS[id].src || []).map((a) => `<a class="src-link" data-anchor="${a}" href="${anchor(a)}" target="_blank" rel="noopener">看源码：${a}</a>`).join(' ')
}

let shownKey = null
function showDetail(id, force = false) {
  selected = id
  document.querySelectorAll('.timeline li').forEach((li) => li.classList.toggle('sel', li.dataset.stage === id))
  const st = [...(last?.stages || []), ...(last?.branch || [])].find((s) => s.id === id)
  // Re-render only when this stage's data changed: polling must not replace what someone is reading.
  const key = id + JSON.stringify(st || null)
  if (!force && key === shownKey) return
  shownKey = key
  const el = $('#stage-detail')
  el.dataset.stage = id
  el.dataset.status = st?.status || 'pending'
  const body = st && st.data ? R[id](st.data) : `<p class="muted">${st?.status === 'active' ? '正在等后端走到这一步…' : '还没到这一步。'}</p>`
  el.innerHTML = `<h2>${esc(STEPS[id].title)} <span class="pill ${st?.status}">${st?.status || 'pending'}</span></h2>${body}<p class="srcs">${srcLinks(id)}</p>`
}

function apply(data) {
  last = data
  const all = [...data.stages, ...(data.branch || [])]
  for (const s of all) {
    const li = document.querySelector(`.timeline li[data-stage="${s.id}"]`)
    if (li.dataset.status !== 'done' && s.status === 'done') window.__learnLog.push(s.id)
    li.dataset.status = s.status
  }
  $('#learn-branch').hidden = $('#branch-h').hidden = !data.branch
  const tr = data.run.trace_id ? ` · trace <a href="${S.jaeger}/trace/${data.run.trace_id}" target="_blank" data-testid="trace-link">${data.run.trace_id.slice(0, 12)}…</a>` : ''
  $('#run-info').innerHTML = `${esc(data.run.scenario)} · run <code>${esc(data.run.run_id)}</code> · hold <code data-testid="hold-id">${esc(data.hold.hold_id)}</code> · 状态 <b data-testid="hold-status">${esc(data.hold.status)}</b>${tr}`
  if (!userPicked) {
    const cur = [...all].reverse().find((s) => s.status === 'done') || all[0]
    showDetail(cur.id)
  } else if (selected) showDetail(selected)
  const finished = data.branch ? data.branch.every((s) => s.status === 'done') && data.stages.every((s) => s.status === 'done') : data.stages.every((s) => s.status === 'done')
  if (finished) { clearInterval(timer); timer = null }
}

async function poll() {
  if (!runId) return
  const r = await fetch(`/learn/api/runs/${runId}`)
  if (r.ok) apply(await r.json())
}

document.addEventListener('click', async (ev) => {
  const sc = ev.target.closest('[data-scenario]')
  if (sc) {
    sc.disabled = true
    const r = await fetch('/learn/api/start', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ scenario: sc.dataset.scenario }) })
    sc.disabled = false
    const j = await r.json()
    runId = j.run_id; userPicked = false; window.__learnLog = []
    document.querySelectorAll('.timeline li').forEach((li) => { li.dataset.status = 'pending' })
    history.replaceState(null, '', `/learn?run=${runId}`)
    clearInterval(timer); timer = setInterval(poll, 1000); poll()
    return
  }
  const vb = ev.target.closest('.video-btn[data-part]')
  if (vb) { ev.stopPropagation(); return playVideo($('#video-box'), vb.dataset.part) }
  const li = ev.target.closest('.timeline li')
  if (li) { userPicked = true; showDetail(li.dataset.stage, true) }
})

const qsRun = new URLSearchParams(location.search).get('run')
if (qsRun) { runId = qsRun; timer = setInterval(poll, 1000); poll() }
