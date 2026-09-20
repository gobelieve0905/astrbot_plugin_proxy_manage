/* 代理管理中心 - 统一前端逻辑与交互系统 (0.3.16) */
const $ = id => document.getElementById(id)
const api = window.AstrBotPluginPage
const titles = {overview:'概览',subscriptions:'订阅管理',nodes:'代理节点',groups:'代理组',routes:'分流规则',platforms:'平台域名模板',control:'内核管理',logs:'连接日志'}
const subtitles = {
  overview:'运行状态、节点健康、出站三层验证与真实流量接入范围',
  subscriptions:'导入、刷新并维护多来源订阅源及差分状态',
  nodes:'筛选节点、核对支持状态并执行批量或单个测速',
  groups:'组织出口节点、配置测速优选与失败关闭容灾策略',
  routes:'按优先级管理域名精确与后缀匹配分流规则',
  platforms:'一键生成主流平台域名分流模板与目标组绑定',
  control:'管理插件自有内核资源、多版本切换、离线安装与运行监督',
  logs:'查看最近的配置审计、内核生命周期与出站连接事件'
}

let state, original, tab='overview', controlResult=null, kernelStatus={state:'not_configured',ready:false,message:'尚未检查'}, importPreview=null, importing=false, probeTask=null
const resourcePollTimers=new Map()

function initLayout(){
  $('app').innerHTML=`
    <div class="layout">
      <nav class="sidebar">
        <button data-tab="overview">概览</button>
        <button data-tab="subscriptions">订阅管理</button>
        <button data-tab="nodes">代理节点</button>
        <button data-tab="groups">代理组</button>
        <button data-tab="routes">分流规则</button>
        <button data-tab="platforms">平台域名模板</button>
        <button data-tab="control">内核管理</button>
        <button data-tab="logs">连接日志</button>
      </nav>
      <main class="main-content">
        <header class="top-bar">
          <div class="breadcrumb">
            <span id="crumb">概览</span>
            <small id="page-subtitle" class="muted"></small>
          </div>
          <div class="toolbar">
            <button id="reload" class="btn btn-secondary" title="刷新数据">重新加载</button>
            <button id="rollback" class="btn btn-secondary" title="恢复上一版配置">回退</button>
            <button id="save" class="btn btn-primary">保存配置</button>
          </div>
        </header>
        <div id="content" data-view="overview"></div>
        <div id="notice" class="notice" hidden></div>
      </main>
    </div>
    <dialog id="diff">
      <h3>配置变更确认</h3>
      <pre id="diff-text"></pre>
      <div class="dialog-actions">
        <button id="dialog-cancel-btn" class="btn btn-secondary">取消</button>
        <button id="confirm" class="btn btn-primary">确认保存</button>
      </div>
    </dialog>
  `
}

function bindEvents(){
  document.querySelectorAll('[data-tab]').forEach(button=>button.addEventListener('click',()=>{tab=button.dataset.tab;render();if(tab==='groups')refreshGroupStatus()}))
  $('reload').addEventListener('click',load)
  $('rollback').addEventListener('click',async()=>{try{state=await api.apiPost('rollback',{});original=structuredClone(state);controlResult=null;importPreview=null;render();note('已恢复上一版配置')}catch(error){note(error.message,true)}})
  $('save').addEventListener('click',()=>{readControl();$('diff-text').textContent=JSON.stringify({before:original,after:state},null,2);$('diff').showModal()})
  $('cancel')?.addEventListener('click',()=>$('diff').close())
  $('dialog-cancel-btn')?.addEventListener('click',()=>$('diff').close())
  $('confirm').addEventListener('click',async()=>{try{await saveChanges();$('diff').close();render();note('配置已保存')}catch(error){note(error.message,true)}})
}

async function load(){
  try{
    state = await api.apiGet("state")
    kernelStatus = await api.apiGet("kernel-status")
    original = structuredClone(state)
    controlResult = null
    importPreview = null
    render()
    for(const item of (state.adapters||[])) {
      if(item.install?.state === "running") {
        pollKernelInstall(item.id)
      }
    }
  } catch(error) {
    note(error.message, true)
  }
}

function init(){
  initLayout()
  bindEvents()
  load()
}

init()
const openKernelResources=new Set()
let noticeTimer=null

const esc = value => String(value ?? '').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))

function note(text,error=false){
  const notice=$('notice')
  if(!notice)return
  clearTimeout(noticeTimer)
  notice.textContent=text
  notice.hidden=!text
  notice.className=error?'error':''
  if(text)noticeTimer=setTimeout(()=>{notice.hidden=true;notice.textContent=''},error?8000:3200)
}

function groupOptions(selected){
  return (state.groups||[]).map(group=>`<option value="${esc(group.id)}" ${group.id===selected?'selected':''}>${esc(group.name)}</option>`).join('')
}

function time(value){
  return value?new Date(value*1000).toLocaleString():'从未'
}

function bytes(value){
  value=Number(value||0)
  if(!value)return '0 B'
  const units=['B','KB','MB','GB','TB']
  const index=Math.min(Math.floor(Math.log(value)/Math.log(1024)),4)
  return (value/1024**index).toFixed(index?1:0)+' '+units[index]
}

function formatBytes(value){
  if(!value)return '0 B'
  const units=['B','KiB','MiB','GiB']
  let size=value,index=0
  while(size>=1024&&index<units.length-1){size/=1024;index++}
  return `${size.toFixed(index?1:0)} ${units[index]}`
}

function traffic(item){
  if(!item.total&&!item.upload&&!item.download) return '无流量信息'
  const used=(item.upload||0)+(item.download||0)
  return item.total?`${bytes(used)} / ${bytes(item.total)}`:`已用 ${bytes(used)}`
}

function expiry(value){
  if(!value)return '无到期信息'
  const days=Math.ceil((value*1000-Date.now())/86400000)
  return `${new Date(value*1000).toLocaleDateString()}（${days>=0?days+' 天':'已到期'}）`
}

function health(id){
  return state.health?.[id]||{status:'unknown',latency_ms:null,error:'',checked_at:0}
}

function statusLabel(item){
  return {ok:'可用',error:'异常',timeout:'超时',pending:'待接入内核',unknown:'未检测',invalid:'引用失效'}[item.status]||'未检测'
}

function kernelStateLabel(item){
  return {
    running:'运行中',
    running_limited:'运行中（有限控制）',
    installed:'已安装',
    update_available:'有更新',
    not_installed:'未安装',
    invalid:'校验失败',
    unsupported:'平台不支持',
    stopped:'已停止',
    failed:'运行失败',
    connection_failed:'连接失败',
    auth_failed:'认证失败',
    disabled:'未启用'
  }[item?.state]||'未检查'
}

function kernelStateClass(item){
  return ['running','running_limited','installed'].includes(item?.state)?'ok':(['update_available','not_installed','disabled'].includes(item?.state)?'pending':(['invalid','unsupported','failed','connection_failed','auth_failed'].includes(item?.state)?'invalid':''))
}

function kernelCard(item,current){
  const artifact=item.artifact||{}, task=item.install||{}, busy=task.state==='running'
  const version=artifact.selected_version||artifact.recommended_version||artifact.version||'--'
  const resource=artifact.version_resources?.[version]||artifact
  const installed=artifact.installed_version||'未安装'
  const stateVal=busy?{state:'running'}:artifact
  const installLabel=!artifact.ready?'安装所选版本':version===installed?'重新安装':'覆盖安装 '+version
  const action=artifact.state==='unsupported'?'':(busy?(task.operation==='install'?`<button class="btn btn-ghost" data-kernel-cancel="${esc(item.id)}">取消</button>`:''):`<button class="btn ${version!==installed?'btn-primary':''}" data-kernel-install="${esc(item.id)}" data-version="${esc(version)}">${esc(installLabel)}</button>`)
  const versions=(artifact.available_versions||[]).map(value=>`<option value="${esc(value)}" ${value===version?'selected':''}>${esc(value)}</option>`).join('')
  const check=artifact.update_check||{}
  const enable=artifact.ready?(item.enabled?`<button class="btn btn-secondary" data-kernel-enable="${esc(item.id)}" data-enabled="false">停用</button>`:`<button class="btn btn-primary" data-kernel-enable="${esc(item.id)}" data-enabled="true">启用</button>`):''
  const select=artifact.ready&&item.enabled&&!current?`<button class="btn btn-secondary" data-kernel-select="${esc(item.id)}">设为当前内核</button>`:''
  const percent=Number(task.progress??(task.total?Math.round(Number(task.downloaded||0)*100/Number(task.total)):0))
  const taskProgress=task.state&&task.state!=='idle'?`<div class="kernel-progress"><div><b>${esc(task.message||'资源任务处理中')}</b><span>${esc(task.phase||'')}</span></div><progress max="100" value="${Math.min(100,percent)}"></progress><small>${task.total?formatBytes(task.downloaded||0)+' / '+formatBytes(task.total):percent+'%'}</small></div>`:''
  const uninstall=artifact.ready&&!busy?`<button class="btn btn-danger" data-kernel-uninstall="${esc(item.id)}">卸载</button>`:''
  const expanded=openKernelResources.has(item.id)||busy
  return `<details class="kernel-resource ${current?'current':''}" data-kernel-card="${esc(item.id)}" ${expanded?'open':''}>
    <summary>
      <div>
        <b>${esc(item.display_name||item.id)}</b>
        <small>${current?'当前运行选择 · ':''}${item.enabled?'已启用':'未启用'} · 已安装 ${esc(installed)}</small>
      </div>
      <span class="kernel-resource-meta">${esc(resource.resource_key||artifact.resource_key||'--')}</span>
      <span class="kernel-badge ${kernelStateClass(stateVal)}">${kernelStateLabel(stateVal)}</span>
    </summary>
    <div class="kernel-resource-body">
      <div class="kernel-facts">
        <div><span>已安装版本</span><b>${esc(installed)}</b></div>
        <div><span>目标版本</span>${versions?`<select data-kernel-version="${esc(item.id)}">${versions}</select>`:`<b>${esc(version)}</b>`}</div>
        <div><span>平台匹配架构</span><b>${esc(resource.resource_key||artifact.resource_key||'--')}</b></div>
      </div>
      <p class="kernel-message">${esc(task.message||artifact.message||'')}${check.checked_at?`<br>更新检查：${esc(check.message||'')}（${time(check.checked_at)}）`:''}</p>
      ${taskProgress}
      <div class="kernel-actions">
        ${action}
        ${uninstall}
        ${enable}
        ${select}
        <button class="btn btn-secondary" data-kernel-check="${esc(item.id)}">检查更新</button>
      </div>
      ${resource.download_url?`<div class="kernel-download-url"><span>官方下载地址 (固定版本)</span><code>${esc(resource.download_url)}</code></div>`:''}
      <details class="kernel-verification"><summary>制品强摘要与校验信息</summary><code>${esc(resource.artifact||'资源未配置')}<br>${esc(resource.expected_sha256||'')}</code></details>
      <div class="kernel-offline">
        <b>离线安装制品 (强摘要校验)</b>
        <div class="inline">
          <input data-kernel-file="${esc(item.id)}" type="file" accept=".gz,.zip,.tar.gz">
          <button class="btn btn-secondary" data-kernel-upload="${esc(item.id)}" data-version="${esc(version)}">校验并安装离线制品</button>
        </div>
      </div>
    </div>
  </details>`
}

function nextRun(item){
  if(!item.enabled||!item.interval)return '手动'
  return item.next_refresh_at<=Date.now()/1000?'即将刷新':time(item.next_refresh_at)
}

function verificationPanel(value){
  const verification=value||{entry:{state:'not_started',message:'尚未验证'},rule:{state:'not_started',message:'尚未验证'},exit:{state:'unconfirmed',message:'尚未验证'}}
  const labels={passed:'入口握手成功',failed:'入口握手失败',matched:'规则已命中',default:'默认 MATCH 规则',confirmed:'出口已确认',unconfirmed:'无法判定',not_started:'尚未验证'}
  const levels=[['1. 入口握手层',verification.entry],['2. 分流规则层',verification.rule],['3. 真实出站出口层',verification.exit]]
  return `<div class="verification-levels">${levels.map(([label,item])=>`<article class="verification ${esc(item?.state||'not_started')}"><small>${label}</small><b>${esc(labels[item?.state]||'无法判定')}</b><span>${esc(item?.message||'')}</span>${item?.ip?`<code>${esc(item.ip)}</code>`:''}</article>`).join('')}</div>`
}

function render(){
  $('crumb').textContent=titles[tab]; $('page-subtitle').textContent=subtitles[tab]
  document.querySelectorAll('[data-tab]').forEach(button=>{
    const isActive=button.dataset.tab===tab
    button.classList.toggle('active',isActive)
    button.setAttribute('aria-current',isActive?'page':'false')
  })
  $('content').dataset.view=tab
  let html=''

  if(tab==='overview'){
    const values=Object.values(state.health||{})
    const ok=values.filter(item=>item.status==='ok').length
    const bad=values.filter(item=>['error','timeout'].includes(item.status)).length
    const kernelText={not_installed:'未安装',invalid:'校验失败',stopped:'已停止',failed:'异常',connection_failed:'失联',auth_failed:'认证失败',disabled:'未启用',running:'正常'}[kernelStatus.process?.state]||kernelStatus.state||'未配置'
    const isOnline=kernelStatus.ready&&kernelStatus.process?.ready
    const verification=state.application?.verification||{}
    const inventory=state.traffic_inventory||{}

    html=`<section class="hero">
      <div>
        <small class="muted">出站流量控制面 · 当前运行内核</small>
        <strong>${esc(kernelStatus.display_name||kernelStatus.adapter||'未选择内核')}</strong>
        <p class="muted">稳定入口 127.0.0.1:${esc(state.proxy_entry?.port||6182)} · ${esc(kernelStatus.message||'尚未启动')}</p>
      </div>
      <div class="runtime-state">
        <span class="${isOnline?'online':(kernelStatus.process?.state==='stopped'?'neutral':'error')}">● ${esc(isOnline?'运行中 / 正常':kernelText)}</span>
        <small class="muted">最后核对：${time(state.application?.applied_at)}</small>
      </div>
    </section>

    <div class="cards">
      <article><b>${ok} / ${state.nodes?.length||0}</b><span>可用节点 (${bad} 异常)</span></article>
      <article><b>${state.groups?.length||0}</b><span>代理组 (${state.groups?.filter(g=>g.mode==='url-test').length||0} 优选)</span></article>
      <article><b>${state.rule_groups?.length||0}</b><span>分流规则组</span></article>
      <article><b>${verification.exit?.state==='confirmed'?'已确认':'待验证'}</b><span>实际出站状态</span></article>
    </div>

    <section class="panel">
      <div class="section-head">
        <div>
          <small>出站真实性核对</small>
          <h2>三层出站验证</h2>
        </div>
      </div>
      <p class="muted">验证请求必须经过 AstrBot 进程的统一代理环境；仅当目标返回出站 IP 且内核链路命中规则时才确认。</p>
      <div class="inline">
        <input id="verify-url" value="https://api.ipify.org?format=json" placeholder="验证 URL">
        <button id="verify-astrbot-egress" class="btn btn-primary">验证 AstrBot 核心出口</button>
        <button class="btn btn-secondary" id="verify-outbound">验证稳定入口</button>
      </div>
      ${verificationPanel(state.application?.verification)}
      <pre id="verify-result">${esc(state.application?.verification?JSON.stringify(state.application.verification,null,2):'尚未进行出站验证。')}</pre>
    </section>

    <section class="panel">
      <div class="bar">
        <div>
          <small>出站流量生命周期</small>
          <h2>统一流量接入清单</h2>
        </div>
        <button class="btn btn-secondary" id="integration-check">检查统一接入协议</button>
      </div>
      <p class="muted">检测 AstrBot 平台 SDK、Provider、插件与 MCP 的出站接管状态；未接入或未经验证项不会宣称已接管。</p>
      <div class="traffic-inventory">
        ${Object.entries(inventory).map(([key,value])=>`<article>
          <div>
            <b>${esc(value.title||key)}</b>
            <span class="muted">${esc(value.summary||'')}</span>
          </div>
          <span class="traffic-state ${esc(value.state||'unknown')}">${esc(value.state_label||value.state||'未知')}</span>
          <p>${esc(value.detail||'')} · 验证方式：${esc(value.verification||'无')}</p>
          ${value.items?.length?`<small class="muted">${esc(value.items.map(item=>item.name+' ('+item.status+')').join(' · '))}</small>`:''}
        </article>`).join('')}
      </div>
    </section>

    <section class="panel">
      <div class="bar">
        <div>
          <small>AstrBot 核心出站</small>
          <h2>全局代理接入与白名单</h2>
        </div>
        <div class="actions">
          <button class="btn btn-secondary" id="astrbot-proxy-enable" ${state.astrbot_proxy?.effective?'disabled':''}>接入稳定入口</button>
          <button class="btn btn-secondary" id="astrbot-proxy-restore" ${state.astrbot_proxy?.backup_available?'':'disabled'}>恢复旧配置</button>
        </div>
      </div>
      <div class="proxy-status">
        <b>状态：${esc(state.astrbot_proxy?.status||'not_connected')}</b>
        <span>${esc(state.astrbot_proxy?.message||'尚未读取 AstrBot 全局代理状态')}</span>
        <code>内部直连 (no_proxy): ${esc((state.astrbot_proxy?.no_proxy||[]).join(', ')||'--')}</code>
      </div>
      ${state.astrbot_proxy?.restart_required?'<p class="muted">AstrBot 需要重启后才会使用新的全局代理配置。</p>':''}
    </section>

    <section class="panel">
      <div class="section-head">
        <div>
          <small>分流测试诊断</small>
          <h2>分流规则查询器</h2>
        </div>
      </div>
      <div class="inline">
        <input id="host" placeholder="例如：api.openai.com 或 api.telegram.org">
        <button class="btn btn-secondary" id="preview">查询命中断言</button>
      </div>
      <pre id="result">输入域名查看命中的代理组和出口节点。</pre>
    </section>

    <section class="panel">
      <div class="section-head">
        <div>
          <small>快速入门</small>
          <h2>首次使用推荐流程</h2>
        </div>
      </div>
      <div class="steps">
        <span>1 安装自管内核</span>
        <span>2 导入订阅</span>
        <span>3 筛选并选择节点</span>
        <span>4 建立代理组</span>
        <span>5 配置规则组</span>
        <span>6 应用配置</span>
        <span>7 验证实际出口</span>
      </div>
    </section>`
  } else if(tab==='subscriptions'){
    const groups=[...new Set(state.subscriptions.map(item=>item.group))]
    const current=$('#group-filter')?.value||'全部'
    const shown=current==='全部'?state.subscriptions:state.subscriptions.filter(item=>item.group===current)
    html=`<section class="panel">
      <div class="bar">
        <div>
          <small>批量解析</small>
          <h2>导入订阅源</h2>
        </div>
        <button class="btn btn-secondary" id="preview-import">预览导入</button>
      </div>
      <textarea id="sub-links" rows="3" placeholder="每行一个 HTTP/HTTPS 订阅链接，支持 Clash YAML、Base64 订阅或单节点链接"></textarea>
      <div class="import-form">
        <input id="import-group" value="主力" placeholder="订阅分组" list="sub-groups">
        <input id="import-interval" type="number" min="0" max="1440" value="60" placeholder="刷新间隔(分)">
        <small class="muted">0 为手动刷新</small>
        <button id="confirm-import" class="btn btn-primary" ${importPreview?'':'disabled'}>确认导入</button>
      </div>
      <pre id="import-result">${esc(importPreview?JSON.stringify(importPreview.items.map(item=>({url:item.url,summary:item.summary})),null,2):'导入前会先预览协议类型、地区分布与节点数量。')}</pre>
    </section>

    <section class="panel">
      <div class="bar">
        <div>
          <small>管理列表</small>
          <h2>已配置订阅 (${state.subscriptions?.length||0})</h2>
        </div>
        <button class="btn btn-secondary" id="add-subscription">新增订阅</button>
      </div>
      <div class="filter">
        <select id="group-filter">
          <option>全部</option>
          ${groups.map(group=>`<option ${group===current?'selected':''}>${esc(group)}</option>`).join('')}
        </select>
      </div>
      ${shown.map(item=>subHtml(item)).join('')||'<p class="empty-state">暂无订阅，请在上方输入链接导入。</p>'}
    </section>`
  } else if(tab==='nodes'){
    const source=$('node-source')?.value||'全部', protocol=$('node-protocol')?.value||'全部', region=$('node-region')?.value||'全部', status=$('node-status')?.value||'全部'
    const sources=[...new Set(state.nodes.map(node=>node.subscription_id||'手动'))], protocols=[...new Set(state.nodes.map(node=>node.protocol))], regions=[...new Set(state.nodes.map(node=>node.region||'其他'))]
    const shown=state.nodes.map((node,index)=>({node,index})).filter(({node})=>(source==='全部'||(node.subscription_id||'手动')===source)&&(protocol==='全部'||node.protocol===protocol)&&(region==='全部'||(node.region||'其他')===region)&&(status==='全部'||(node.invalid_reference?'失效':health(node.id).status)===status))

    html=`<section class="panel">
      <div class="bar">
        <div>
          <small>节点池管理</small>
          <h2>代理节点 (${shown.length} / ${state.nodes?.length||0})</h2>
        </div>
        <div class="actions">
          <button class="btn btn-secondary" id="add">新增节点</button>
          <button id="test-all" class="btn btn-primary">批量测速</button>
        </div>
      </div>
      <div class="filter">
        <select id="node-source"><option>全部来源</option>${sources.map(value=>`<option value="${esc(value)}" ${value===source?'selected':''}>来源：${esc(value)}</option>`).join('')}</select>
        <select id="node-protocol"><option>全部协议</option>${protocols.map(value=>`<option value="${esc(value)}" ${value===protocol?'selected':''}>协议：${esc(value)}</option>`).join('')}</select>
        <select id="node-region"><option>全部地区</option>${regions.map(value=>`<option value="${esc(value)}" ${value===region?'selected':''}>地区：${esc(value)}</option>`).join('')}</select>
        <select id="node-status"><option>全部状态</option>${['ok','error','timeout','unknown','失效'].map(value=>`<option value="${esc(value)}" ${value===status?'selected':''}>状态：${esc(value)}</option>`).join('')}</select>
      </div>
      ${probeTask?`<div class="probe-progress"><progress value="${probeTask.completed}" max="${probeTask.total}"></progress><span>正在测速: ${probeTask.completed}/${probeTask.total}</span><button class="btn btn-ghost" id="cancel-probe" ${probeTask.status!=='running'?'disabled':''}>取消测速</button></div>`:''}
      ${shown.map(({node,index})=>{
        const item=health(node.id)
        const support=node.support||{status:'unverified',reason:'尚未验证'}
        const memberships=state.groups.filter(group=>group.node_ids.includes(node.id)).map(group=>group.name).join('、')||'未加入组'
        return `<div class="node-card" data-i="${index}">
          <div class="table">
            <input data-k="name" value="${esc(node.display_name||node.name)}" placeholder="节点名称">
            <span class="chip">${esc(node.protocol||'unknown')} · ${esc(node.engine||'')}</span>
            <span class="chip ${support.status==='supported'?'ok':'pending'}">${esc({supported:'已支持',unverified:'未验证',unsupported:'不支持'}[support.status]||'未验证')}</span>
            <input data-k="endpoint" value="${esc(node.endpoint)}" placeholder="完整连接 URI 或 Host:Port 地址">
            <label><input type="checkbox" data-k="excluded" ${node.excluded?'checked':''}>排除测速/优选</label>
            <button data-del="nodes" class="btn btn-danger">删除</button>
          </div>
          <div class="node-meta">
            <span class="chip ${node.invalid_reference?'invalid':item.status}">${node.invalid_reference?'引用失效':statusLabel(item)}</span>
            <b>${item.latency_ms??'--'}</b> <span>ms</span>
            <span>检测：${time(item.checked_at)}</span>
            <span>${esc(node.subscription_id?'订阅：'+node.subscription_id:'手动节点')}</span>
            <span>代理组：${esc(memberships)}</span>
            ${node.suspected_notice?'<span class="chip pending">疑似订阅公告</span>':''}
            <button class="btn btn-secondary" data-test="${esc(node.id)}">测速</button>
          </div>
          ${support.reason?`<div class="node-error">${esc(support.reason)}</div>`:''}
          ${node.notice_reason?`<div class="muted">${esc(node.notice_reason)}，请人工确认是否排除。</div>`:''}
          ${item.error?`<div class="node-error">${esc(item.error)}</div>`:''}
        </div>`
      }).join('')||'<p class="empty-state">暂无匹配节点。</p>'}
    </section>`
  } else if(tab==='groups'){
    const runtimeGroups=controlResult?.groups||[]
    html=`<section class="panel">
      <div class="bar">
        <div>
          <small>拓扑结构</small>
          <h2>代理组配置 (${state.groups?.length||0})</h2>
        </div>
        <button class="btn btn-secondary" id="add">新增代理组</button>
      </div>
      ${state.groups.map((group,index)=>`<div class="group-card" data-i="${index}">
        <div class="table">
          <input data-k="name" value="${esc(group.name)}" placeholder="代理组名称">
          <select data-k="mode">
            ${['direct','select','url-test','fallback'].map(mode=>`<option ${group.mode===mode?'selected':''}>${mode}</option>`).join('')}
          </select>
          <select data-k="selected">
            <option value="">策略自动选择</option>
            ${state.nodes.map(node=>`<option value="${esc(node.id)}" ${node.id===group.selected?'selected':''}>${esc(node.name)}</option>`).join('')}
          </select>
          <button data-del="groups" class="btn btn-danger" ${group.id==='direct'?'disabled':''}>删除</button>
        </div>
        ${group.id!=='direct'?`<div class="table">
          <input data-k="test_url" value="${esc(group.test_url)}" placeholder="测速目标 URL">
          <input type="number" data-k="test_interval" value="${group.test_interval}" placeholder="测速间隔(s)">
          <input type="number" data-k="tolerance" value="${group.tolerance}" placeholder="容差(ms)">
          <select data-k="failure_policy">
            <option value="fail-closed" ${group.failure_policy==='fail-closed'?'selected':''}>全部不可用时失败关闭</option>
            <option value="keep-last" ${group.failure_policy==='keep-last'?'selected':''}>保留最后选择</option>
          </select>
        </div>`:''}
        <div class="nodes">
          ${state.nodes.map(node=>`<label><input type="checkbox" data-node="${esc(node.id)}" ${group.node_ids.includes(node.id)?'checked':''}>${esc(node.name)}</label>`).join('')||'<span class="muted">暂无节点</span>'}
        </div>
      </div>`).join('')}
    </section>

    <section class="panel">
      <div class="bar">
        <div>
          <small>内核实时运行态</small>
          <h2>运行中代理组切换</h2>
        </div>
        <button class="btn btn-secondary" id="group-runtime-refresh">刷新运行状态</button>
      </div>
      ${runtimeGroups.length?runtimeGroups.map(group=>`<div class="control-group">
        <b>${esc(group.display_name)}</b>
        <small class="muted">${esc(group.type)} · 当前命中：${esc(group.selected_display_name||'无')}</small>
        <select data-select="${esc(group.id)}">
          ${group.members.map(node=>`<option value="${esc(node.id)}" ${node.id===group.selected_node_id?'selected':''} ${node.available?'':'disabled'}>${esc(node.display_name)}${node.available?'':'（不可用）'}</option>`).join('')}
        </select>
      </div>`).join(''):'<p class="empty-state">尚未读取运行状态，或当前内核未运行/无可选代理组。</p>'}
    </section>`
  } else if(tab==='routes'){
    html=`<section class="panel">
      <div class="bar">
        <div>
          <small>路由规则</small>
          <h2>分流规则组 (${state.rule_groups?.length||0})</h2>
        </div>
        <button class="btn btn-secondary" id="add">新增规则组</button>
      </div>
      ${state.rule_groups.map((rule,index)=>`<div class="group-card" data-i="${index}">
        <div class="table">
          <input data-k="name" value="${esc(rule.name)}" placeholder="规则组名称">
          <input type="number" data-k="priority" value="${rule.priority}" placeholder="优先级 (越小越高)">
          <select data-k="target">${groupOptions(rule.target)}</select>
          <label><input type="checkbox" data-k="enabled" ${rule.enabled?'checked':''}>启用</label>
          <button data-del="rule_groups" class="btn btn-danger">删除</button>
        </div>
        <textarea data-domains rows="3" placeholder="每行一条规则：exact api.example.com 或 suffix example.com">${esc(rule.domains.map(domain=>domain.match+' '+domain.host).join('\n'))}</textarea>
      </div>`).join('')||'<p class="empty-state">暂无分流规则。</p>'}
    </section>`
  } else if(tab==='platforms'){
    html=`<section class="panel">
      <div class="section-head">
        <div>
          <small>预置模板</small>
          <h2>平台域名分流模板</h2>
        </div>
      </div>
      <p class="muted">快速生成主流平台与 Provider 的域名分流规则；这属于内核路由配置，不代表平台 SDK 的网络代理已自动就绪。</p>
      ${Object.entries(state.templates||{}).map(([id,template])=>{
        const domains=template.domains||template.hosts.map(host=>({host,match:'exact'}))
        return `<div class="platform">
          <b>${esc(template.name)}</b>
          <small class="muted">${esc(domains.map(item=>item.match+' '+item.host).join(' · '))}</small>
          <select data-platform="${esc(id)}">${groupOptions((state.platforms[id]||{}).group_id||'direct')}</select>
          <button class="btn btn-secondary" data-template="${esc(id)}">应用或更新模板</button>
        </div>`
      }).join('')}
    </section>`
  } else if(tab==='control'){
    const artifact=kernelStatus.artifact||state.kernel?.artifact||{}
    const process=kernelStatus.process||state.kernel?.process||{}
    const adapters=state.adapters||[]
    const installed=adapters.filter(item=>item.artifact?.ready).length
    const enabled=adapters.filter(item=>item.enabled&&item.artifact?.ready).length
    const currentId=kernelStatus.adapter||state.control?.adapter||''
    const runnable=adapters.filter(item=>item.enabled&&item.artifact?.ready)

    html=`<section class="panel kernel-current">
      <div class="section-head">
        <div>
          <small>第一栏 · 当前选定内核 ${esc(currentId||'--')}</small>
          <h2>运行控制</h2>
        </div>
        <span class="kernel-badge ${process.ready?'ok':process.state==='failed'?'invalid':'pending'}">${esc(process.ready?'运行中':process.state||'已停止')}</span>
      </div>
      <div class="runtime-control">
        <label>
          <span>切换运行内核</span>
          <select id="adapter-select" ${runnable.length?'':'disabled'}>
            ${runnable.map(item=>`<option value="${esc(item.id)}" ${item.id===currentId?'selected':''}>${esc(item.display_name||item.id)}</option>`).join('')||'<option>无可用内核 (请在下方安装并启用)</option>'}
          </select>
        </label>
        <div class="runtime-actions">
          <button class="btn btn-secondary" id="adapter-switch" ${runnable.length?'':'disabled'}>切换内核</button>
          <button class="btn btn-secondary" id="kernel-start" ${runnable.length?'':'disabled'}>启动</button>
          <button class="btn btn-ghost" id="kernel-stop">停止</button>
          <button class="btn btn-secondary" id="control-status">刷新状态</button>
          <button id="runtime-apply" class="btn btn-primary" ${runnable.length?'':'disabled'}>应用代理配置</button>
        </div>
      </div>
      <div class="runtime-facts">
        <div><span>运行环境</span><b>${esc(artifact.platform?.os||'--')} / ${esc(artifact.platform?.arch||'--')}</b></div>
        <div><span>进程状态</span><b>${esc(process.state||'未知')}</b></div>
        <div><span>内核回执</span><b>${esc(kernelStatus.message||'尚未读取')}</b></div>
      </div>
      <p class="muted runtime-note">提示：监听地址、控制密钥和稳定代理入口由插件拥有并管理，无需手动填写。</p>
    </section>

    <section class="panel kernel-overview">
      <div class="bar">
        <div>
          <small>第二栏 · ${installed}/${adapters.length} 已下载 · ${enabled}/${adapters.length} 已启用</small>
          <h2>内核资源管理</h2>
        </div>
        <div class="actions">
          <button class="btn btn-secondary" id="refresh-kernels">刷新资源状态</button>
          <button class="btn btn-secondary" id="check-all-kernels">检查全部更新</button>
        </div>
      </div>
      <p class="muted">展开各内核卡片可进行版本选择、下载、更新、强摘要核验、卸载或离线制品安装。启用仅代表允许作为运行候选，不会自动启动。</p>
      <div class="kernel-resources">
        ${adapters.map(item=>kernelCard(item,item.id===currentId&&process.state==='running')).join('')}
      </div>
    </section>`
  } else {
    html=`<section class="panel">
      <div class="section-head">
        <div>
          <small>审计追踪</small>
          <h2>连接与配置审计日志 (${state.events?.length||0})</h2>
        </div>
      </div>
      ${(state.events||[]).slice().reverse().map(event=>`<div class="log">
        <span>${esc(event.action)} · ${esc(event.result||'')}</span>
        <small>${new Date(event.at*1000).toLocaleString()}</small>
      </div>`).join('')||'<p class="empty-state">暂无审计事件。</p>'}
    </section>`
  }

  $('content').innerHTML=html
  bind()
}

function subHtml(item){
  return `<div class="sub-card" data-i="${state.subscriptions.indexOf(item)}" data-id="${esc(item.id)}">
    <div class="table">
      <input data-k="name" value="${esc(item.name)}" placeholder="订阅名称">
      <input data-k="group" value="${esc(item.group)}" list="sub-groups" placeholder="所属分组">
      <input data-k="url" value="${esc(item.url)}" placeholder="订阅链接 URL">
      <label><input type="checkbox" data-k="enabled" ${item.enabled?'checked':''}>启用</label>
      <button data-del="subscriptions" class="btn btn-danger">删除</button>
    </div>
    <div class="sub-meta">
      <span>${item.node_ids.length} 节点</span>
      <span>流量：${traffic(item)}</span>
      <span>到期：${expiry(item.expire)}</span>
      <span>更新：${time(item.updated_at)}</span>
      <span>下次：${nextRun(item)}</span>
      <input class="interval" type="number" min="0" max="1440" data-k="interval" value="${item.interval}" title="自动刷新间隔(分)">
      <button class="btn btn-secondary" data-refresh="${esc(item.id)}">立即刷新</button>
    </div>
    ${item.last_error?`<div class="node-error">${esc(item.last_error)}（连续失败 ${item.consecutive_errors} 次）</div>`:''}
    ${item.last_diff?.at?`<details><summary>最近同步差异：新增 ${item.last_diff.added?.length||0}、变更 ${item.last_diff.changed?.length||0}、删除 ${item.last_diff.deleted?.length||0}、未变 ${item.last_diff.unchanged?.length||0}</summary><pre>${esc(JSON.stringify(item.last_diff,null,2))}</pre></details>`:''}
    ${item.errors?.length?`<details><summary>错误历史记录 (${item.errors.length})</summary>${item.errors.slice().reverse().map(error=>`<div class="node-error">${new Date(error.at*1000).toLocaleString()} · ${esc(error.message)}</div>`).join('')}</details>`:''}
  </div>`
}

function bind(){
  document.querySelectorAll('[data-k]').forEach(input=>input.addEventListener('change',()=>{
    const row=input.closest('[data-i]'); if(!row)return
    if(tab==='subscriptions'){
      const item=state.subscriptions[Number(row.dataset.i)]
      item[input.dataset.k]=input.type==='checkbox'?input.checked:input.type==='number'?Number(input.value):input.value
    } else if(tab==='nodes'){
      const item=state.nodes[Number(row.dataset.i)]
      item[input.dataset.k]=input.type==='checkbox'?input.checked:input.value
      if(input.dataset.k==='name'){item.display_name=input.value;item.user_alias=input.value}
    } else if(tab==='groups'){
      const item=state.groups[Number(row.dataset.i)]
      item[input.dataset.k]=input.type==='number'?Number(input.value):input.value
    } else {
      const item=state.rule_groups[Number(row.dataset.i)]
      item[input.dataset.k]=input.type==='checkbox'?input.checked:input.type==='number'?Number(input.value):input.value
    }
  }))

  document.querySelectorAll('[data-domains]').forEach(input=>input.addEventListener('change',()=>{
    const item=state.rule_groups[Number(input.closest('[data-i]').dataset.i)]
    item.domains=input.value.split('\n').map(line=>line.trim().split(/\s+/,2)).filter(parts=>parts.length===2).map(([match,host])=>({match:match==='suffix'?'suffix':'exact',host}))
  }))

  document.querySelectorAll('[data-node]').forEach(input=>input.addEventListener('change',()=>{
    const group=state.groups[input.closest('[data-i]').dataset.i]; const id=input.dataset.node
    group.node_ids=input.checked?[...new Set([...group.node_ids,id])]:group.node_ids.filter(value=>value!==id)
    if(group.selected&&!group.node_ids.includes(group.selected))group.selected=''
  }))

  document.querySelectorAll('[data-del]').forEach(button=>button.addEventListener('click',()=>{
    const row=button.closest('[data-i]'),list=state[button.dataset.del],item=list[Number(row.dataset.i)]
    if(item.id==='direct')return
    list.splice(Number(row.dataset.i),1)
    render()
  }))

  $('add')?.addEventListener('click',()=>{
    if(tab==='nodes')state.nodes.push({id:'node-'+Date.now(),name:'新节点',display_name:'新节点',protocol:'http',engine:'direct-http',kind:'http',endpoint:'',connection:{},subscription_id:'',enabled:true,excluded:false,exclusion_reason:''})
    else if(tab==='groups')state.groups.push({id:'group-'+Date.now(),name:'新代理组',mode:'select',node_ids:[],selected:'',enabled:true})
    else state.rule_groups.push({id:'rules-'+Date.now(),name:'新规则组',domains:[{host:'example.com',match:'exact'}],target:'direct',priority:100,enabled:true})
    render()
  })

  $('add-subscription')?.addEventListener('click',()=>{
    state.subscriptions.push({id:'sub-'+Date.now(),name:'新订阅',url:'',group:'默认',enabled:true,interval:60,node_ids:[],updated_at:0,next_refresh_at:0,upload:0,download:0,total:0,expire:0,last_error:'',consecutive_errors:0,errors:[]})
    render()
  })

  $('group-filter')?.addEventListener('change',render)
  $('preview-import')?.addEventListener('click',previewImport)
  $('confirm-import')?.addEventListener('click',confirmImport)
  document.querySelectorAll('[data-refresh]').forEach(button=>button.addEventListener('click',()=>refreshSubscription(button.dataset.refresh)))
  document.querySelectorAll('[data-test]').forEach(button=>button.addEventListener('click',()=>startProbe([button.dataset.test])))
  $('test-all')?.addEventListener('click',()=>startProbe(state.nodes.map(node=>node.id)))
  $('cancel-probe')?.addEventListener('click',async()=>{await api.apiPost('probe-task-cancel',{task_id:probeTask.id});note('正在取消测速任务')})
  ;['node-source','node-protocol','node-region','node-status'].forEach(id=>$(id)?.addEventListener('change',render))
  document.querySelectorAll('[data-platform]').forEach(select=>select.addEventListener('change',()=>{state.platforms[select.dataset.platform]={name:select.dataset.platform,group_id:select.value,enabled:true}}))
  document.querySelectorAll('[data-template]').forEach(button=>button.addEventListener('click',()=>{
    const template=state.templates[button.dataset.template],id=`${button.dataset.template}-${Date.now()}`
    state.groups.push({id,name:template.name,mode:'select',node_ids:[],selected:'',enabled:true})
    const domains=template.domains||template.hosts.map(host=>({host,match:'exact'})), existing=state.rule_groups.find(item=>item.id===button.dataset.template)
    const rule={id:button.dataset.template,name:template.name,domains:structuredClone(domains),target:id,priority:100,enabled:true}
    if(existing)Object.assign(existing,rule);else state.rule_groups.push(rule)
    render();note('模板已添加，请选择节点')
  }))
  $('preview')?.addEventListener('click',async()=>{$('result').textContent=JSON.stringify(await api.apiPost('preview',{host:$('host').value}),null,2)})
  $('verify-outbound')?.addEventListener('click',async()=>{try{const result=await api.apiPost('verify-outbound',{url:$('verify-url').value});state.application={...(state.application||{}),verification:result};render();note(result.verified?'出口已确认':'验证未能确认实际出口，请查看三个层级的证据',!result.verified)}catch(error){note(error.message,true)}})
  $('verify-astrbot-egress')?.addEventListener('click',async()=>{try{const result=await api.apiPost('verify-astrbot-egress',{url:$('verify-url').value});state.application={...(state.application||{}),verification:result};await load();note(result.verified?'AstrBot 核心出口已确认':'AstrBot 核心出口未能确认',!result.verified)}catch(error){note(error.message,true)}})
  $('integration-check')?.addEventListener('click',async()=>{try{const result=await api.apiPost('integration-check',{});state=result.snapshot;original=structuredClone(state);render();note('统一接入协议检查已完成')}catch(error){note(error.message,true)}})
  $('astrbot-proxy-enable')?.addEventListener('click',async()=>{try{const result=await api.apiPost('astrbot-proxy-enable',{});await load();note(result.message)}catch(error){note(error.message,true)}})
  $('astrbot-proxy-restore')?.addEventListener('click',async()=>{try{const result=await api.apiPost('astrbot-proxy-restore',{});await load();note(result.message)}catch(error){note(error.message,true)}})
  $('control-status')?.addEventListener('click',checkControl)
  $('group-runtime-refresh')?.addEventListener('click',refreshGroupStatus)
  $('refresh-kernels')?.addEventListener('click',async()=>{await load();if(tab==='control')note('内核资源状态已刷新')})
  $('check-all-kernels')?.addEventListener('click',checkAllKernelUpdates)
  document.querySelectorAll('[data-kernel-check]').forEach(button=>button.addEventListener('click',()=>checkKernelUpdate(button.dataset.kernelCheck)))
  document.querySelectorAll('[data-kernel-enable]').forEach(button=>button.addEventListener('click',()=>toggleKernel(button.dataset.kernelEnable,button.dataset.enabled==='true')))
  document.querySelectorAll('[data-kernel-select]').forEach(button=>button.addEventListener('click',()=>selectKernel(button.dataset.kernelSelect)))
  $('adapter-switch')?.addEventListener('click',async()=>{try{const selected=$('adapter-select').value;if(!confirm(`切换到 ${selected} 将停止当前内核，并要求重新安装和应用配置。`))return;await api.apiPost('adapter-select',{adapter:selected});await load();note(`已切换到 ${selected}`)}catch(error){note(error.message,true)}})
  document.querySelectorAll('.kernel-resource').forEach(details=>details.addEventListener('toggle',()=>{const adapter=details.dataset.kernelCard;if(details.open)openKernelResources.add(adapter);else openKernelResources.delete(adapter)}))
  document.querySelectorAll('[data-kernel-version]').forEach(select=>select.addEventListener('change',()=>{const item=state.adapters?.find(value=>value.id===select.dataset.kernelVersion);if(item){item.artifact.selected_version=select.value;render()}}))
  document.querySelectorAll('[data-kernel-install]').forEach(button=>button.addEventListener('click',()=>{const select=button.closest('[data-kernel-card]')?.querySelector('[data-kernel-version]');startKernelInstall(button.dataset.kernelInstall,select?.value||button.dataset.version)}))
  document.querySelectorAll('[data-kernel-cancel]').forEach(button=>button.addEventListener('click',()=>cancelKernelInstall(button.dataset.kernelCancel)))
  document.querySelectorAll('[data-kernel-uninstall]').forEach(button=>button.addEventListener('click',()=>uninstallKernel(button.dataset.kernelUninstall)))
  $('kernel-start')?.addEventListener('click',()=>kernelAction('kernel-start','正在启动内核...'))
  $('kernel-stop')?.addEventListener('click',()=>kernelAction('kernel-stop','正在停止内核...'))
  document.querySelectorAll('[data-kernel-upload]').forEach(button=>button.addEventListener('click',()=>uploadKernel(button.dataset.kernelUpload,button.dataset.version)))
  $('runtime-apply')?.addEventListener('click',async()=>{try{await saveChanges();await api.apiPost('runtime-apply',{});controlResult=await api.apiGet('control-status');render();note('代理配置已应用并完成运行状态核对')}catch(error){note(error.message,true)}})
  document.querySelectorAll('[data-select]').forEach(select=>select.addEventListener('change',async()=>{try{await api.apiPost('control-select',{group_id:select.dataset.select,node_id:select.value});await checkControl();note('代理组已切换')}catch(error){note(error.message,true)}}))
}

function readControl(){}
async function saveChanges(){ readControl(); state=await api.apiPost('save',state); original=structuredClone(state) }
async function previewImport(){
  const urls=$('sub-links').value.split(/\s+/).filter(Boolean); if(!urls.length)return note('请先输入订阅链接',true)
  const button=$('preview-import'); button.disabled=true; note('正在预览订阅...')
  try{ importPreview=await api.apiPost('subscription-preview',{urls,group:$('import-group').value||'默认',interval:Number($('import-interval').value)});render();note('预览完成，请确认后导入') }catch(error){note(error.message,true)}finally{button.disabled=false}
}
async function confirmImport(){
  if(!importPreview||importing)return; importing=true
  try{ state=await api.apiPost('subscription-import',{preview_id:importPreview.preview_id});original=structuredClone(state);importPreview=null;render();note('订阅导入成功') }catch(error){note(error.message,true)}finally{importing=false}
}
async function refreshSubscription(id){
  try{ note('正在刷新订阅...'); const result=await api.apiPost('subscription-refresh',{id});state=result.snapshot;original=structuredClone(state);render();const d=result.result.diff;note(`订阅刷新成功：新增 ${d.added.length}、变更 ${d.changed.length}、删除 ${d.deleted.length}、未变 ${d.unchanged.length}`) }catch(error){note(error.message,true)}
}
async function checkControl(){
  try{ kernelStatus=await api.apiGet('kernel-status');controlResult=kernelStatus.ready?await api.apiGet('control-status'):null;render();note(kernelStatus.message,!kernelStatus.ready) }catch(error){note(error.message,true)}
}
async function refreshGroupStatus(){try{controlResult=await api.apiGet('control-status');render();note('代理组运行状态已刷新')}catch(error){controlResult=null;render();note(error.message,true)}}
async function startKernelInstall(adapter=state.control?.adapter||kernelStatus.adapter,version=''){try{const payload={adapter};if(version)payload.version=version;const install=await api.apiPost('kernel-install',payload);if(adapter===state.control?.adapter||adapter===kernelStatus.adapter)kernelStatus.install=install;const item=state.adapters?.find(value=>value.id===adapter);if(item)item.install=install;render();note((version?'内核更新':'内核安装')+'任务已创建');pollKernelInstall(adapter)}catch(error){note(error.message,true)}}
async function cancelKernelInstall(adapter=state.control?.adapter||kernelStatus.adapter){try{const result=await api.apiPost('kernel-install-cancel',{adapter});if(adapter===state.control?.adapter||adapter===kernelStatus.adapter)kernelStatus.install=result;const item=state.adapters?.find(value=>value.id===adapter);if(item)item.install=result;render();note('在线安装已取消')}catch(error){note(error.message,true)}}
async function uninstallKernel(adapter){if(!confirm(`确定卸载 ${adapter} 内核资源？已启用但未运行的内核会同时停用。`))return;try{const task=await api.apiPost('kernel-uninstall',{adapter});const item=state.adapters?.find(value=>value.id===adapter);if(item)item.install=task;render();pollKernelInstall(adapter)}catch(error){note(error.message,true)}}
async function checkKernelUpdate(adapter){try{note('正在检查 '+adapter+' 更新...');await api.apiPost('kernel-update-check',{adapter});await load();note(adapter+' 更新检查完成')}catch(error){note(error.message,true)}}
async function checkAllKernelUpdates(){try{note('正在检查全部内核更新...');await api.apiPost('kernel-update-check-all',{});await load();note('全部内核更新检查完成')}catch(error){note(error.message,true)}}
async function toggleKernel(adapter,enabled){try{await api.apiPost('core-enable',{adapter,enabled});await load();note(enabled?'内核已启用':'内核已停用')}catch(error){note(error.message,true)}}
async function selectKernel(adapter){try{await api.apiPost('adapter-select',{adapter});await load();note('已切换到 '+adapter)}catch(error){note(error.message,true)}}
async function pollKernelInstall(adapter=state.control?.adapter||kernelStatus.adapter){clearTimeout(resourcePollTimers.get(adapter));try{const install=await api.apiPost('kernel-install-status',{adapter});if(adapter===state.control?.adapter||adapter===kernelStatus.adapter)kernelStatus.install=install;const item=state.adapters?.find(value=>value.id===adapter);if(item)item.install=install;if(install.state==='running'){render();resourcePollTimers.set(adapter,setTimeout(()=>pollKernelInstall(adapter),700))}else{resourcePollTimers.delete(adapter);await load();note(install.message,install.state!=='completed'&&install.state!=='cancelled')}}catch(error){resourcePollTimers.delete(adapter);note(error.message,true)}}
async function kernelAction(route,message){try{note(message);await api.apiPost(route,{});await load();note('内核状态已更新')}catch(error){note(error.message,true)}}
async function uploadKernel(adapter,version){const file=document.querySelector(`[data-kernel-file="${CSS.escape(adapter)}"]`)?.files?.[0];if(!file)return note('请选择与当前平台匹配的固定版本制品',true);if(file.size>64*1024*1024)return note('制品超过 64 MiB 限制',true);const item=state.adapters?.find(value=>value.id===adapter);if(item)item.install={state:'running',operation:'install',phase:'uploading',progress:10,message:'正在上传离线制品'};openKernelResources.add(adapter);render();try{note('正在校验离线制品...');const content=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',',2)[1]);reader.onerror=reject;reader.readAsDataURL(file)});await api.apiPost('kernel-upload',{adapter,version,content});await load();note('离线制品已校验并安装；请在资源栏中启用')}catch(error){await load();note(error.message,true)}}
async function startProbe(nodeIds){
  try{const started=await api.apiPost('probe-task',{node_ids:nodeIds,timeout:5,concurrency:5});probeTask={id:started.task_id,total:started.total,completed:0,status:'running'};render();note('测速任务已开始');pollProbe()}catch(error){note(error.message,true)}
}
