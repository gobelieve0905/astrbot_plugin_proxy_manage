const $ = id => document.getElementById(id)
const api = window.AstrBotPluginPage
const titles = {overview:'概览',subscriptions:'订阅管理',nodes:'代理节点',groups:'代理组',routes:'分流规则',platforms:'平台策略',control:'控制接口',logs:'连接日志'}
let state, original, tab='overview', controlResult=null, kernelStatus={state:'not_configured',ready:false,message:'尚未检查'}, importPreview=null, importing=false

const esc = value => String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))
function note(text,error=false){ $('notice').textContent=text; $('notice').hidden=!text; $('notice').className=error?'error':'' }
function groupOptions(selected){ return state.groups.map(group=>`<option value="${esc(group.id)}" ${group.id===selected?'selected':''}>${esc(group.name)}</option>`).join('') }
function time(value){ return value?new Date(value*1000).toLocaleString():'从未' }
function bytes(value){ value=Number(value||0); if(!value)return '0 B'; const units=['B','KB','MB','GB','TB']; const index=Math.min(Math.floor(Math.log(value)/Math.log(1024)),4); return (value/1024**index).toFixed(index?1:0)+' '+units[index] }
function traffic(item){
  if(!item.total&&!item.upload&&!item.download) return '无流量信息'
  const used=(item.upload||0)+(item.download||0)
  return item.total?`${bytes(used)} / ${bytes(item.total)}`:`已用 ${bytes(used)}`
}
function expiry(value){ if(!value)return '无到期信息'; const days=Math.ceil((value*1000-Date.now())/86400000); return `${new Date(value*1000).toLocaleDateString()}（${days>=0?days+' 天':'已到期'}）` }
function health(id){ return state.health?.[id]||{status:'unknown',latency_ms:null,error:'',checked_at:0} }
function statusLabel(item){ return {ok:'可用',error:'异常',timeout:'超时',pending:'待接入内核',unknown:'未检测',invalid:'引用失效'}[item.status]||'未检测' }
function nextRun(item){ if(!item.enabled||!item.interval)return '手动'; return item.next_refresh_at<=Date.now()/1000?'即将刷新':time(item.next_refresh_at) }

function render(){
  $('crumb').textContent=titles[tab]; let html=''
  if(tab==='overview'){
    const values=Object.values(state.health||{})
    const ok=values.filter(item=>item.status==='ok').length, bad=values.filter(item=>['error','timeout'].includes(item.status)).length
    const kernelText={not_configured:'未配置',connection_failed:'连接失败',auth_failed:'认证失败',version_unsupported:'版本不支持',config_not_applied:'配置未应用',runtime_inconsistent:'运行配置不一致',connected:'已连接'}[kernelStatus.state]||'未检查'
    const kernelClass=kernelStatus.state==='connected'?'online':'error'
    html=`<div class="hero"><b>当前配置</b><strong>${esc(state.name)}</strong><span class="${kernelClass}">● 内核：${kernelText}</span><small>${esc(kernelStatus.message||'')}</small></div>
      <div class="cards">${[['subscriptions','订阅'],['nodes','节点'],['groups','代理组'],['routes','规则']].map(([key,label])=>`<article><b>${state[key].length}</b><span>${label}</span></article>`).join('')}</div>
      <div class="cards"><article><b>${ok}</b><span>可用节点</span></article><article><b>${bad}</b><span>异常节点</span></article><article><b>${state.subscriptions.filter(item=>item.enabled&&item.interval).length}</b><span>自动订阅</span></article><article><b>${state.events.length}</b><span>最近事件</span></article></div>
      <section class="panel"><h2>分流预览</h2><div class="inline"><input id="host" placeholder="api.telegram.org"><button id="preview">查询</button></div><pre id="result">输入域名查看命中的代理组和节点。</pre></section>`
  } else if(tab==='subscriptions'){
    const groups=[...new Set(state.subscriptions.map(item=>item.group))]; const current=$('#group-filter')?.value||'全部'
    const shown=current==='全部'?state.subscriptions:state.subscriptions.filter(item=>item.group===current)
    html=`<section class="panel"><div class="bar"><h2>导入订阅</h2><button id="preview-import">预览导入</button></div>
      <textarea id="sub-links" rows="4" placeholder="每行一个 HTTP/HTTPS 订阅链接"></textarea>
      <div class="import-form"><input id="import-group" value="主力" placeholder="订阅分组"><input id="import-interval" type="number" min="0" max="1440" value="60"><small>间隔分钟，0 表示手动</small><button id="confirm-import" ${importPreview?'':'disabled'}>确认导入</button></div>
      <pre id="import-result">${esc(importPreview?JSON.stringify(importPreview.items.map(item=>({url:item.url,summary:item.summary})),null,2):'导入前会先预览协议、地区、命名规则和订阅流量。')}</pre></section>
      <section class="panel"><div class="bar"><h2>订阅列表</h2><button id="add-subscription">新增订阅</button></div>
      <div class="filter"><select id="group-filter"><option>全部</option>${groups.map(group=>`<option ${group===current?'selected':''}>${esc(group)}</option>`).join('')}</select></div>
      ${shown.map(item=>subHtml(item)).join('')||'<p class="muted">暂无订阅。</p>'}</section>`
  } else if(tab==='nodes'){
    html=`<section class="panel"><div class="bar"><h2>代理节点</h2><div class="actions"><button id="add">新增节点</button><button id="test-all">批量测速</button></div></div>
      ${state.nodes.map((node,index)=>{const item=health(node.id),support=node.support||{status:'unverified',reason:'尚未验证'};return `<div class="node-card" data-i="${index}">
        <div class="table"><input data-k="name" value="${esc(node.display_name||node.name)}"><span class="chip">${esc(node.protocol||'unknown')} · ${esc(node.engine||'')}</span><span class="chip ${support.status==='supported'?'ok':'pending'}">${esc({supported:'已支持',unverified:'未验证',unsupported:'不支持'}[support.status]||'未验证')}</span><input data-k="endpoint" value="${esc(node.endpoint)}" placeholder="完整连接 URI 或 HTTP/SOCKS 地址"><label><input type="checkbox" data-k="excluded" ${node.excluded?'checked':''}>确认排除测速/组选优</label><button data-del="nodes">删除</button></div>
        <div class="node-meta"><span class="chip ${node.invalid_reference?'invalid':item.status}">${node.invalid_reference?'引用失效':statusLabel(item)}</span><b>${item.latency_ms??'--'}</b><span>ms</span><span>${time(item.checked_at)}</span><span>${esc(node.subscription_id?'订阅：'+node.subscription_id:'手动节点')}</span>${node.suspected_notice?'<span class="chip pending">疑似订阅提示</span>':''}<button data-test="${esc(node.id)}" ${node.invalid_reference||support.status!=='supported'||node.excluded?'disabled':''}>测速</button></div>
        ${support.reason?`<div class="node-error">${esc(support.reason)}</div>`:''}${node.notice_reason?`<div class="muted">${esc(node.notice_reason)}，请人工确认是否排除。</div>`:''}${item.error?`<div class="node-error">${esc(item.error)}</div>`:''}</div>`}).join('')||'<p class="muted">暂无节点。</p>'}</section>`
  } else if(tab==='groups'){
    html=`<section class="panel"><div class="bar"><h2>代理组</h2><button id="add">新增代理组</button></div>
      ${state.groups.map((group,index)=>`<div class="group-card" data-i="${index}"><div class="table"><input data-k="name" value="${esc(group.name)}"><select data-k="mode">${['direct','select','url-test','fallback'].map(mode=>`<option ${group.mode===mode?'selected':''}>${mode}</option>`).join('')}</select><select data-k="selected"><option value="">手动首节点</option>${state.nodes.map(node=>`<option value="${esc(node.id)}" ${node.id===group.selected?'selected':''}>${esc(node.name)}</option>`).join('')}</select><button data-del="groups" ${group.id==='direct'?'disabled':''}>删除</button></div><div class="nodes">${state.nodes.map(node=>`<label><input type="checkbox" data-node="${esc(node.id)}" ${group.node_ids.includes(node.id)?'checked':''}>${esc(node.name)}</label>`).join('')||'<span class="muted">暂无节点</span>'}</div></div>`).join('')}</section>`
  } else if(tab==='routes'){
    html=`<section class="panel"><div class="bar"><h2>分流规则</h2><button id="add">新增规则</button></div>${state.routes.map((route,index)=>`<div class="table" data-i="${index}"><input data-k="host" value="${esc(route.host)}"><select data-k="match"><option ${route.match==='exact'?'selected':''}>exact</option><option ${route.match==='suffix'?'selected':''}>suffix</option></select><input type="number" data-k="priority" value="${route.priority}"><select data-k="target">${groupOptions(route.target)}</select><button data-del="routes">删除</button></div>`).join('')}</section>`
  } else if(tab==='platforms'){
    html=`<section class="panel"><h2>平台策略</h2>${Object.entries(state.templates).map(([id,template])=>`<div class="platform"><b>${esc(template.name)}</b><small>${esc(template.hosts.join(' · '))}</small><select data-platform="${esc(id)}">${groupOptions((state.platforms[id]||{}).group_id||'direct')}</select><button data-template="${esc(id)}">应用模板</button></div>`).join('')}</section>`
  } else if(tab==='control'){
    const groups=controlResult?.groups||[]
    html=`<section class="panel"><div class="bar"><h2>Mihomo / Clash 外部控制</h2><div class="actions"><button id="control-status">保存并检查</button><button id="runtime-apply" class="primary">应用代理配置</button></div></div><div class="control-form"><label><input type="checkbox" data-ck="enabled" ${state.control.enabled?'checked':''}> 启用</label><input id="control-url" value="${esc(state.control.url)}" placeholder="控制接口地址，如 http://host:9090"><input id="control-secret" type="password" value="${esc(state.control.secret)}" placeholder="控制接口密钥"><input id="control-timeout" type="number" min="3" max="30" value="${state.control.timeout}"><select id="control-deployment"><option value="existing" ${state.control.deployment==='existing'?'selected':''}>现有内核</option><option value="dedicated" ${state.control.deployment==='dedicated'?'selected':''}>独立内核</option></select><select id="control-scope"><option value="providers-groups-rules" ${state.control.scope==='providers-groups-rules'?'selected':''}>仅管理订阅/代理组/规则</option><option value="full" ${state.control.scope==='full'?'selected':''}>完整配置（暂不支持）</option></select><input id="proxy-http-url" value="${esc(state.proxy_entry?.http_url||'')}" placeholder="实际 HTTP 代理入口，如 http://host:7890"><input id="proxy-socks-url" value="${esc(state.proxy_entry?.socks_url||'')}" placeholder="实际 SOCKS 代理入口（可选）"></div><p class="muted">控制接口用于管理和核对内核；代理入口用于 AstrBot 实际出站，两者地址可以不同。</p></section>
      ${groups.length?`<section class="panel"><h2>代理组状态</h2>${groups.map(group=>`<div class="control-group"><b>${esc(group.display_name)}</b><small>${esc(group.type)} · ${esc(group.selected_display_name||'无')}</small><select data-select="${esc(group.id)}">${group.members.map(node=>`<option value="${esc(node.id)}" ${node.id===group.selected_node_id?'selected':''} ${node.available?'':'disabled'}>${esc(node.display_name)}${node.available?'':'（不可用）'}</option>`).join('')}</select></div>`).join('')}</section>`:'<section class="panel"><p class="muted">尚未检查，或控制接口没有可切换代理组。</p></section>'}`
  } else {
    html=`<section class="panel"><h2>连接日志</h2>${state.events.slice().reverse().map(event=>`<div class="log">${esc(event.action)} · ${esc(event.result||'')}<small>${new Date(event.at*1000).toLocaleString()}</small></div>`).join('')||'<p class="muted">暂无事件。</p>'}</section>`
  }
  $('content').innerHTML=html; bind()
}

function subHtml(item){
  return `<div class="sub-card" data-i="${state.subscriptions.indexOf(item)}" data-id="${esc(item.id)}">
    <div class="table"><input data-k="name" value="${esc(item.name)}"><input data-k="group" value="${esc(item.group)}" list="sub-groups"><input data-k="url" value="${esc(item.url)}"><label><input type="checkbox" data-k="enabled" ${item.enabled?'checked':''}>启用</label><button data-del="subscriptions">删除</button></div>
    <div class="sub-meta"><span>${item.node_ids.length} 节点</span><span>${traffic(item)}</span><span>${expiry(item.expire)}</span><span>更新：${time(item.updated_at)}</span><span>下次：${nextRun(item)}</span><input class="interval" type="number" min="0" max="1440" data-k="interval" value="${item.interval}"><button data-refresh="${esc(item.id)}">刷新</button></div>
    ${item.last_error?`<div class="node-error">${esc(item.last_error)}（连续失败 ${item.consecutive_errors} 次）</div>`:''}
    ${item.last_diff?.at?`<details><summary>最近差异：新增 ${item.last_diff.added?.length||0}、变更 ${item.last_diff.changed?.length||0}、删除 ${item.last_diff.deleted?.length||0}、未变 ${item.last_diff.unchanged?.length||0}</summary><pre>${esc(JSON.stringify(item.last_diff,null,2))}</pre></details>`:''}
    ${item.errors?.length?`<details><summary>错误历史 ${item.errors.length}</summary>${item.errors.slice().reverse().map(error=>`<div class="node-error">${new Date(error.at*1000).toLocaleString()} · ${esc(error.message)}</div>`).join('')}</details>`:''}</div>`
}

function bind(){
  document.querySelectorAll('[data-k]').forEach(input=>input.addEventListener('change',()=>{
    const row=input.closest('[data-i]'); if(!row)return
    if(tab==='subscriptions'){ const item=state.subscriptions[Number(row.dataset.i)]; item[input.dataset.k]=input.type==='checkbox'?input.checked:input.type==='number'?Number(input.value):input.value }
    else if(tab==='nodes'){ const item=state.nodes[Number(row.dataset.i)]; item[input.dataset.k]=input.type==='checkbox'?input.checked:input.value; if(input.dataset.k==='name'){item.display_name=input.value;item.user_alias=input.value} }
    else if(tab==='groups'){ const item=state.groups[Number(row.dataset.i)]; item[input.dataset.k]=input.value }
    else { const item=state.routes[Number(row.dataset.i)]; item[input.dataset.k]=input.type==='number'?Number(input.value):input.value }
  }))
  document.querySelectorAll('[data-node]').forEach(input=>input.addEventListener('change',()=>{
    const group=state.groups[input.closest('[data-i]').dataset.i]; const id=input.dataset.node
    group.node_ids=input.checked?[...new Set([...group.node_ids,id])]:group.node_ids.filter(value=>value!==id)
    if(group.selected&&!group.node_ids.includes(group.selected))group.selected=''
  }))
  document.querySelectorAll('[data-del]').forEach(button=>button.addEventListener('click',()=>{
    const row=button.closest('[data-i]'),list=state[button.dataset.del],item=list[Number(row.dataset.i)]
    if(item.id==='direct')return; list.splice(Number(row.dataset.i),1); render()
  }))
  $('add')?.addEventListener('click',()=>{
    if(tab==='nodes')state.nodes.push({id:'node-'+Date.now(),name:'新节点',display_name:'新节点',protocol:'http',engine:'direct-http',kind:'http',endpoint:'',connection:{},subscription_id:'',enabled:true,excluded:false,exclusion_reason:''})
    else if(tab==='groups')state.groups.push({id:'group-'+Date.now(),name:'新代理组',mode:'select',node_ids:[],selected:'',enabled:true})
    else state.routes.push({id:'rule-'+Date.now(),host:'example.com',match:'exact',target:'direct',priority:100,enabled:true})
    render()
  })
  $('add-subscription')?.addEventListener('click',()=>{state.subscriptions.push({id:'sub-'+Date.now(),name:'新订阅',url:'',group:'默认',enabled:true,interval:60,node_ids:[],updated_at:0,next_refresh_at:0,upload:0,download:0,total:0,expire:0,last_error:'',consecutive_errors:0,errors:[]});render()})
  $('group-filter')?.addEventListener('change',render)
  $('preview-import')?.addEventListener('click',previewImport)
  $('confirm-import')?.addEventListener('click',confirmImport)
  document.querySelectorAll('[data-refresh]').forEach(button=>button.addEventListener('click',()=>refreshSubscription(button.dataset.refresh)))
  document.querySelectorAll('[data-test]').forEach(button=>button.addEventListener('click',async()=>{button.disabled=true;try{const result=await api.apiPost('node-probe',{node_id:button.dataset.test});state.health[result.node_id]=result.health;render();note('节点测速完成')}catch(error){note(error.message,true)}finally{button.disabled=false}}))
  $('test-all')?.addEventListener('click',async()=>{const button=$('test-all');button.disabled=true;note('正在批量测速...');try{const result=await api.apiPost('nodes-probe',{node_ids:state.nodes.map(node=>node.id)});state.health=result.health;render();note(`测速完成：${result.succeeded} 个可用，${result.failed} 个失败，${result.skipped||0} 个未执行`)}catch(error){note(error.message,true)}finally{button.disabled=false}})
  document.querySelectorAll('[data-platform]').forEach(select=>select.addEventListener('change',()=>{state.platforms[select.dataset.platform]={name:select.dataset.platform,group_id:select.value,enabled:true}}))
  document.querySelectorAll('[data-template]').forEach(button=>button.addEventListener('click',()=>{
    const template=state.templates[button.dataset.template],id=`${button.dataset.template}-${Date.now()}`
    state.groups.push({id,name:template.name,mode:'select',node_ids:[],selected:'',enabled:true})
    template.hosts.forEach(host=>state.routes.push({id:`rule-${Date.now()}-${host}`,host,match:'exact',target:id,priority:100,enabled:true}))
    render();note('模板已添加，请选择节点')
  }))
  $('preview')?.addEventListener('click',async()=>{$('result').textContent=JSON.stringify(await api.apiPost('preview',{host:$('host').value}),null,2)})
  $('control-status')?.addEventListener('click',checkControl)
  $('runtime-apply')?.addEventListener('click',async()=>{try{await saveChanges();await api.apiPost('runtime-apply',{});controlResult=await api.apiGet('control-status');render();note('代理配置已应用并完成运行状态核对')}catch(error){note(error.message,true)}})
  document.querySelectorAll('[data-select]').forEach(select=>select.addEventListener('change',async()=>{try{await api.apiPost('control-select',{group_id:select.dataset.select,node_id:select.value});await checkControl();note('代理组已切换')}catch(error){note(error.message,true)}}))
}

function readControl(){ if(tab!=='control')return; state.control={enabled:document.querySelector('[data-ck]')?.checked??state.control.enabled,url:$('control-url').value,secret:$('control-secret').value,timeout:Number($('control-timeout').value||8),deployment:$('control-deployment').value,scope:$('control-scope').value}; state.proxy_entry={...(state.proxy_entry||{}),http_url:$('proxy-http-url').value,socks_url:$('proxy-socks-url').value,source:'configured'} }
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
  try{ await saveChanges();controlResult=await api.apiGet('control-status');render();note('控制接口连接成功') }catch(error){note(error.message,true)}
}
async function load(){ try{ state=await api.apiGet('state');kernelStatus=await api.apiGet('kernel-status');original=structuredClone(state);controlResult=null;importPreview=null;render() }catch(error){note(error.message,true)} }

document.querySelectorAll('[data-tab]').forEach(button=>button.addEventListener('click',()=>{tab=button.dataset.tab;render()}))
$('reload').addEventListener('click',load)
$('rollback').addEventListener('click',async()=>{try{state=await api.apiPost('rollback',{});original=structuredClone(state);controlResult=null;importPreview=null;render();note('已恢复上一版配置')}catch(error){note(error.message,true)}})
$('save').addEventListener('click',()=>{readControl();$('diff-text').textContent=JSON.stringify({before:original,after:state},null,2);$('diff').showModal()})
$('cancel').addEventListener('click',()=>$('diff').close())
$('confirm').addEventListener('click',async()=>{try{await saveChanges();$('diff').close();render();note('配置已保存')}catch(error){note(error.message,true)}})
load()
