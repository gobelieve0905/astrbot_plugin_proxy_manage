const $ = id => document.getElementById(id)
const bridge = window.AstrBotPluginPage
const titles = { overview:'概览', subscriptions:'订阅管理', nodes:'代理节点', groups:'代理组', routes:'分流规则', platforms:'平台策略', control:'控制接口', logs:'连接日志' }
let state = null
let original = null
let tab = 'overview'
let controlResult = null

const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))
function notice(text, error = false) { $('notice').textContent = text; $('notice').hidden = !text; $('notice').className = error ? 'error' : '' }
function groupOptions(selected) { return state.groups.map(group => `<option value="${esc(group.id)}" ${group.id === selected ? 'selected' : ''}>${esc(group.name)}</option>`).join('') }
function formatTime(value) { return value ? new Date(value * 1000).toLocaleString() : '未刷新' }

function render() {
  $('crumb').textContent = titles[tab]
  let html = ''
  if (tab === 'overview') {
    html = `
      <div class="hero"><b>当前配置</b><strong>${esc(state.name)}</strong><span class="online">● 运行中</span></div>
      <div class="cards">
        ${[['subscriptions','订阅'],['nodes','节点'],['groups','代理组'],['routes','规则']].map(([key,label]) => `<article><b>${state[key].length}</b><span>${label}</span></article>`).join('')}
      </div>
      <section class="panel"><h2>分流预览</h2>
        <div class="inline"><input id="host" placeholder="api.telegram.org"><button id="preview">查询</button></div>
        <pre id="result">输入域名查看命中的代理组和节点。</pre>
      </section>`
  } else if (tab === 'subscriptions') {
    html = `
      <section class="panel"><div class="bar"><h2>订阅导入</h2><button id="import-links">导入并刷新</button></div>
        <textarea id="sub-links" rows="4" placeholder="每行一个 HTTP/HTTPS 订阅链接"></textarea>
      </section>
      <section class="panel"><div class="bar"><h2>订阅列表</h2><button id="add-subscription">新增订阅</button></div>
        ${state.subscriptions.map((item,index) => `
          <div class="subscription" data-i="${index}">
            <input data-k="name" value="${esc(item.name)}" placeholder="订阅名称">
            <input data-k="url" value="${esc(item.url)}" placeholder="https://example.com/subscription">
            <label><input type="checkbox" data-k="enabled" ${item.enabled ? 'checked' : ''}> 启用</label>
            <button data-sub-refresh="${esc(item.id)}">刷新</button>
            <button data-del="subscriptions">删除</button>
            <small>${item.node_ids.length} 个节点 · ${formatTime(item.updated_at)}</small>
          </div>`).join('') || '<p class="muted">暂无订阅。</p>'}
      </section>`
  } else if (tab === 'nodes') {
    html = `<section class="panel"><div class="bar"><h2>代理节点</h2><button id="add">新增节点</button></div>
      ${state.nodes.map((node,index) => `
        <div class="table" data-i="${index}">
          <input data-k="name" value="${esc(node.name)}" placeholder="节点名称">
          <select data-k="kind">${['http','https','socks5','socks5h','mihomo'].map(kind => `<option ${node.kind === kind ? 'selected' : ''}>${kind}</option>`).join('')}</select>
          <input data-k="endpoint" value="${esc(node.endpoint)}" placeholder="http://host:port 或 vmess://...">
          <button data-del="nodes">删除</button>
        </div>`).join('') || '<p class="muted">暂无节点。</p>'}
    </section>`
  } else if (tab === 'groups') {
    html = `<section class="panel"><div class="bar"><h2>代理组</h2><button id="add">新增代理组</button></div>
      ${state.groups.map((group,index) => `
        <div class="group-card" data-i="${index}">
          <div class="table">
            <input data-k="name" value="${esc(group.name)}">
            <select data-k="mode">${['direct','select','url-test','fallback'].map(mode => `<option ${group.mode === mode ? 'selected' : ''}>${mode}</option>`).join('')}</select>
            <select data-k="selected"><option value="">自动选择</option>${state.nodes.map(node => `<option value="${esc(node.id)}" ${node.id === group.selected ? 'selected' : ''}>${esc(node.name)}</option>`).join('')}</select>
            <button data-del="groups" ${group.id === 'direct' ? 'disabled' : ''}>删除</button>
          </div>
          <div class="nodes">${state.nodes.map(node => `<label><input type="checkbox" data-node="${esc(node.id)}" ${group.node_ids.includes(node.id) ? 'checked' : ''}>${esc(node.name)}</label>`).join('') || '<span class="muted">暂无节点</span>'}</div>
        </div>`).join('')}
    </section>`
  } else if (tab === 'routes') {
    html = `<section class="panel"><div class="bar"><h2>分流规则</h2><button id="add">新增规则</button></div>
      ${state.routes.map((route,index) => `
        <div class="table" data-i="${index}">
          <input data-k="host" value="${esc(route.host)}" placeholder="example.com">
          <select data-k="match"><option ${route.match === 'exact' ? 'selected' : ''}>exact</option><option ${route.match === 'suffix' ? 'selected' : ''}>suffix</option></select>
          <input type="number" data-k="priority" value="${route.priority}">
          <select data-k="target">${groupOptions(route.target)}</select>
          <button data-del="routes">删除</button>
        </div>`).join('')}
    </section>`
  } else if (tab === 'platforms') {
    html = `<section class="panel"><h2>平台策略</h2>
      ${Object.entries(state.templates).map(([id,template]) => `
        <div class="platform">
          <b>${esc(template.name)}</b><small>${esc(template.hosts.join(' · '))}</small>
          <select data-platform="${esc(id)}">${groupOptions((state.platforms[id] || {}).group_id || 'direct')}</select>
          <button data-template="${esc(id)}">应用模板</button>
        </div>`).join('')}
    </section>`
  } else if (tab === 'control') {
    const groups = Object.entries(controlResult?.proxies || {}).filter(([,group]) => ['Selector','URLTest','Fallback','LoadBalance'].includes(group.type))
    html = `<section class="panel"><div class="bar"><h2>Mihomo / Clash 外部控制</h2><button id="control-status">保存并检查</button></div>
      <div class="control-form">
        <label><input type="checkbox" data-ck="enabled" ${state.control.enabled ? 'checked' : ''}> 启用控制接口</label>
        <input id="control-url" value="${esc(state.control.url)}" placeholder="http://127.0.0.1:9090">
        <input id="control-secret" type="password" value="${esc(state.control.secret)}" placeholder="外部控制密钥">
        <input id="control-timeout" type="number" min="3" max="30" value="${state.control.timeout}">
      </div>
      </section>
      ${groups.length ? `<section class="panel"><h2>代理组状态</h2>${groups.map(([name,group]) => `
        <div class="control-group">
          <b>${esc(name)}</b><small>${esc(group.type)} · 当前：${esc(group.now || '无')}</small>
          <select data-select="${esc(name)}">${(group.all || []).map(node => `<option value="${esc(node)}" ${node === group.now ? 'selected' : ''}>${esc(node)}</option>`).join('')}</select>
        </div>`).join('')}</section>` : '<p class="muted">尚未检查，或控制接口没有可切换的代理组。</p>'}
`
  } else {
    html = `<section class="panel"><h2>连接日志</h2>${state.events.slice().reverse().map(event => `<div class="log">${esc(event.action)} · ${esc(event.result || '')}<small>${new Date(event.at * 1000).toLocaleString()}</small></div>`).join('') || '<p class="muted">暂无事件。</p>'}</section>`
  }
  $('content').innerHTML = html
  bind()
}

function bind() {
  document.querySelectorAll('[data-k]').forEach(input => input.addEventListener('change', () => {
    const row = input.closest('[data-i]')
    const list = tab === 'nodes' ? state.nodes : tab === 'groups' ? state.groups : tab === 'routes' ? state.routes : state.subscriptions
    if (!row) return
    list[row.dataset.i][input.dataset.k] = input.type === 'checkbox' ? input.checked : input.type === 'number' ? Number(input.value) : input.value
  }))
  document.querySelectorAll('[data-node]').forEach(input => input.addEventListener('change', () => {
    const card = input.closest('[data-i]'); const group = state.groups[card.dataset.i]; const id = input.dataset.node
    group.node_ids = input.checked ? [...new Set([...group.node_ids, id])] : group.node_ids.filter(value => value !== id)
    if (group.selected && !group.node_ids.includes(group.selected)) group.selected = ''
  }))
  document.querySelectorAll('[data-del]').forEach(button => button.addEventListener('click', () => {
    const row = button.closest('[data-i]'); const list = state[button.dataset.del]
    if (list[row.dataset.i]?.id === 'direct') return
    list.splice(row.dataset.i, 1); render()
  }))
  $('add')?.addEventListener('click', () => {
    if (tab === 'nodes') state.nodes.push({ id:`node-${Date.now()}`, name:'新节点', kind:'http', endpoint:'', enabled:true })
    else if (tab === 'groups') state.groups.push({ id:`group-${Date.now()}`, name:'新代理组', mode:'select', node_ids:[], selected:'', enabled:true })
    else state.routes.push({ id:`rule-${Date.now()}`, host:'example.com', match:'exact', target:'direct', priority:100, enabled:true })
    render()
  })
  $('add-subscription')?.addEventListener('click', () => {
    state.subscriptions.push({ id:`sub-${Date.now()}`, name:`订阅 ${state.subscriptions.length + 1}`, url:'', enabled:true, node_ids:[], updated_at:0 })
    render()
  })
  $('import-links')?.addEventListener('click', async () => {
    const links = $('sub-links').value.split(/\s+/).filter(Boolean)
    if (!links.length) return notice('请先输入订阅链接', true)
    links.forEach((url,index) => state.subscriptions.push({ id:`sub-${Date.now()}-${index}`, name:`订阅 ${state.subscriptions.length + 1}`, url, enabled:true, node_ids:[], updated_at:0 }))
    await refreshAll()
  })
  document.querySelectorAll('[data-sub-refresh]').forEach(button => button.addEventListener('click', () => refreshSubscription(button.dataset.subRefresh)))
  document.querySelectorAll('[data-platform]').forEach(select => select.addEventListener('change', () => {
    state.platforms[select.dataset.platform] = { name:select.dataset.platform, group_id:select.value, enabled:true }
  }))
  document.querySelectorAll('[data-template]').forEach(button => button.addEventListener('click', () => {
    const template = state.templates[button.dataset.template]; const id = `${button.dataset.template}-${Date.now()}`
    state.groups.push({ id, name:template.name, mode:'select', node_ids:[], selected:'', enabled:true })
    template.hosts.forEach(host => state.routes.push({ id:`rule-${Date.now()}-${host}`, host, match:'exact', target:id, priority:100, enabled:true }))
    render(); notice('模板已添加，请在代理组中选择节点')
  }))
  $('preview')?.addEventListener('click', async () => { $('result').textContent = JSON.stringify(await bridge.apiPost('preview', { host:$('host').value }), null, 2) })
  $('control-status')?.addEventListener('click', checkControl)
  document.querySelectorAll('[data-select]').forEach(select => select.addEventListener('change', async () => {
    const group = select.dataset.select
    try { await bridge.apiPost('control-select', { name:group, node:select.value }); await checkControl(); notice('代理组已切换') } catch (error) { notice(error.message, true) }
  }))
}

function readControl() {
  if (tab !== 'control') return
  state.control = {
    enabled:document.querySelector('[data-ck]')?.checked ?? state.control.enabled,
    url:$('control-url').value,
    secret:$('control-secret').value,
    timeout:Number($('control-timeout').value || state.control.timeout)
  }
}

async function saveChanges() {
  readControl()
  state = await bridge.apiPost('save', state)
  original = structuredClone(state)
}

async function refreshSubscription(id) {
  try { await saveChanges(); state = await bridge.apiPost('subscription-refresh', { id }); original = structuredClone(state); render(); notice('订阅已刷新') }
  catch (error) { notice(error.message, true) }
}

async function refreshAll() {
  try {
    await saveChanges()
    for (const item of state.subscriptions) { if (item.enabled) state = await bridge.apiPost('subscription-refresh', { id:item.id }) }
    original = structuredClone(state); render(); notice('订阅已导入并刷新')
  } catch (error) { notice(error.message, true) }
}

async function checkControl() {
  try { await saveChanges(); controlResult = await bridge.apiGet('control-status'); render(); notice('控制接口连接成功') }
  catch (error) { notice(error.message, true) }
}

async function load() {
  try { state = await bridge.apiGet('state'); original = structuredClone(state); controlResult = null; render() }
  catch (error) { notice(error.message, true) }
}

document.querySelectorAll('[data-tab]').forEach(button => button.addEventListener('click', () => { tab = button.dataset.tab; render() }))
$('reload').addEventListener('click', load)
$('rollback').addEventListener('click', async () => { try { state = await bridge.apiPost('rollback', {}); original = structuredClone(state); controlResult = null; render(); notice('已恢复上一版配置') } catch (error) { notice(error.message, true) } })
$('save').addEventListener('click', () => { readControl(); $('diff-text').textContent = JSON.stringify({ before:original, after:state }, null, 2); $('diff').showModal() })
$('cancel').addEventListener('click', () => $('diff').close())
$('confirm').addEventListener('click', async () => { try { await saveChanges(); $('diff').close(); render(); notice('配置已保存') } catch (error) { notice(error.message, true) } })
load()
