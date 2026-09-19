const $ = id => document.getElementById(id)
const api = window.AstrBotPluginPage
const titles = {overview:'概览',subscriptions:'订阅管理',nodes:'代理节点',groups:'代理组',routes:'分流规则',platforms:'平台域名模板',control:'内核管理',logs:'连接日志'}
const subtitles = {overview:'运行状态、节点健康和真实流量接入范围',subscriptions:'导入、刷新并维护订阅来源',nodes:'筛选节点、核对支持状态并执行测速',groups:'组织出口节点与故障处理策略',routes:'按优先级管理域名和目标出口',platforms:'生成域名规则；这不代表平台 SDK 已接入代理',control:'管理插件自有内核、制品与运行配置',logs:'查看最近的配置、安装和连接事件'}
let state, original, tab='overview', controlResult=null, kernelStatus={state:'not_configured',ready:false,message:'尚未检查'}, importPreview=null, importing=false, probeTask=null
let installPollTimer=null

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
function kernelStateLabel(item){ return {running:'运行中',installed:'已安装',update_available:'有更新',not_installed:'未安装',invalid:'校验失败',unsupported:'平台不支持',stopped:'已停止',failed:'运行失败',connection_failed:'连接失败',auth_failed:'认证失败'}[item?.state]||'未检查' }
function kernelStateClass(item){ return ['running','installed'].includes(item?.state)?'ok':(['update_available','not_installed'].includes(item?.state)?'pending':(['invalid','unsupported','failed','connection_failed','auth_failed'].includes(item?.state)?'invalid':'')) }
function kernelCard(item,current){
  const artifact=item.artifact||{}, task=item.install||{}, busy=task.state==='running'
  const version=artifact.recommended_version||artifact.version||'--'
  const installed=artifact.installed_version||'未安装'
  const state=busy?{state:'running'}:artifact
  const action=artifact.state==='unsupported'?'':(busy?`<button data-kernel-cancel="${esc(item.id)}">取消</button>`:`<button class="${artifact.state==='update_available'?'primary':''}" data-kernel-install="${esc(item.id)}" data-version="${esc(version)}">${artifact.state==='update_available'?'更新内核':artifact.state==='installed'?'重新安装':'下载安装'}</button>`)
  const versions=(artifact.available_versions||[]).map(value=>`<option value="${esc(value)}" ${value===version?'selected':''}>${esc(value)}</option>`).join('')
  return `<article class="kernel-card ${current?'current':''}" data-kernel-card="${esc(item.id)}"><div class="kernel-card-head"><div><small>${current?'当前运行':'可用适配器'}</small><h3>${esc(item.display_name||item.id)}</h3></div><span class="kernel-badge ${kernelStateClass(state)}">${kernelStateLabel(state)}</span></div><div class="kernel-facts"><div><span>已安装</span><b>${esc(installed)}</b></div><div><span>推荐版本</span>${versions?`<select data-kernel-version="${esc(item.id)}">${versions}</select>`:`<b>${esc(version)}</b>`}</div><div><span>平台资源</span><b>${esc(artifact.resource_key||'--')}</b></div></div><p class="kernel-message">${esc(task.message||artifact.message||'')}</p><div class="kernel-actions">${action}${artifact.download_url?`<a href="${esc(artifact.download_url)}" target="_blank" rel="noopener noreferrer">官方下载地址</a>`:''}</div><details><summary>制品校验信息</summary><code>${esc(artifact.artifact||'资源未配置')}<br>${esc(artifact.expected_sha256||'')}</code></details></article>`
}
function nextRun(item){ if(!item.enabled||!item.interval)return '手动'; return item.next_refresh_at<=Date.now()/1000?'即将刷新':time(item.next_refresh_at) }
function verificationPanel(value){
  const verification=value||{entry:{state:'not_started',message:'尚未验证'},rule:{state:'not_started',message:'尚未验证'},exit:{state:'unconfirmed',message:'尚未验证'}}
  const labels={passed:'入口请求成功',failed:'入口请求失败',matched:'规则已命中',default:'默认 MATCH 规则',confirmed:'出口已确认',unconfirmed:'无法判定',not_started:'尚未验证'}
  const levels=[['入口',verification.entry],['规则',verification.rule],['出口',verification.exit]]
  return `<div class="verification-levels">${levels.map(([label,item])=>`<article class="verification ${esc(item?.state||'not_started')}"><small>${label}</small><b>${esc(labels[item?.state]||'无法判定')}</b><span>${esc(item?.message||'')}</span>${item?.ip?`<code>${esc(item.ip)}</code>`:''}</article>`).join('')}</div>`
}

function render(){
  $('crumb').textContent=titles[tab]; $('page-subtitle').textContent=subtitles[tab]
  document.querySelectorAll('[data-tab]').forEach(button=>{button.classList.toggle('active',button.dataset.tab===tab);button.setAttribute('aria-current',button.dataset.tab===tab?'page':'false')})
  $('content').dataset.view=tab; let html=''
  if(tab==='overview'){
    const values=Object.values(state.health||{})
    const ok=values.filter(item=>item.status==='ok').length, bad=values.filter(item=>['error','timeout'].includes(item.status)).length
    const kernelText={not_installed:'未安装',invalid:'校验失败',unsupported:'平台不支持',stopped:'已停止',failed:'运行失败',connection_failed:'连接失败',version_unsupported:'版本不支持',saved:'已保存',pending_apply:'待应用',applied:'已应用',runtime_inconsistent:'运行配置不一致',restore_failed:'恢复失败',fail_closed:'失败关闭'}[kernelStatus.state]||'未检查'
    const kernelClass=kernelStatus.state==='applied'?'online':(['failed','connection_failed','runtime_inconsistent','restore_failed'].includes(kernelStatus.state)?'error':'neutral')
    html=`<div class="hero"><div><small>当前配置</small><strong>${esc(state.name)}</strong></div><div class="runtime-state"><span class="${kernelClass}">内核：${kernelText}</span><small>${esc(kernelStatus.message||'')}</small></div></div>
      <div class="cards">${[['subscriptions','订阅'],['nodes','节点'],['groups','代理组'],['routes','规则']].map(([key,label])=>`<article><b>${state[key].length}</b><span>${label}</span></article>`).join('')}</div>
      <div class="cards"><article><b>${ok}</b><span>可用节点</span></article><article><b>${bad}</b><span>异常节点</span></article><article><b>${state.subscriptions.filter(item=>item.enabled&&item.interval).length}</b><span>自动订阅</span></article><article><b>${state.events.length}</b><span>最近事件</span></article></div>
      <section class="panel"><div class="section-head"><div><small>真实接入范围 · ${esc(state.traffic_audit?.version||'未审计')}</small><h2>AstrBot 流量清单</h2></div><button id="integration-check" title="重新检查新增的插件和 MCP">重新检查</button></div><div class="traffic-inventory">${(state.traffic_inventory||[]).map(item=>`<article><div><b>${esc(item.name)}</b><small>${esc(item.method)}</small></div><span class="traffic-state ${esc(item.status)}">${esc({managed:'已接管',direct:'明确直连',not_connected:'未接入',unknown:'无法判定'}[item.status]||'无法判定')}</span><p>${esc(item.message)}</p><small>策略接入：${esc({managed:'已纳入统一策略',pending:'等待 AstrBot 重启',declared:'已声明协议，等待配置入口',needs_protocol:'需声明接入协议'}[item.integration?.state]||'待检查')} · ${esc(item.integration?.message||'')}</small><small>验证：${esc(item.verification||'当前无专项验证')}</small><small>旁路风险：${esc(item.bypass_risk||'待审计')}</small>${item.restart?'<small>变更后需要重启相关组件</small>':''}${item.discovered?.length?`<small>已发现：${esc(item.discovered.map(value=>value.name+' ('+(value.declaration?({compatible:'协议兼容',missing:'缺少协议',invalid:'协议无效'}[value.declaration.state]||'协议待检查'):(value.locality_label||value.type||value.transport||'未知'))+')').join(' · '))}</small>`:''}</article>`).join('')}</div></section>
      <section class="panel"><div class="section-head"><div><small>运行时指纹与注册表</small><h2>官方兼容层</h2></div></div><p class="muted">${esc(state.compatibility?.message||'尚未安装')}</p><div class="proxy-status"><b>${esc(state.compatibility?.astrbot||'未知 AstrBot')}</b><span>${esc(state.compatibility?.state||'not_installed')}</span><code>${esc(Object.entries(state.compatibility?.sdk_versions||{}).map(([key,value])=>key+' '+value).join(' · ')||'未读取 SDK 版本')}</code></div></section>
      <section class="panel"><div class="section-head"><div><small>跨进程与跨容器</small><h2>MCP 私有出口</h2></div></div><div class="proxy-status"><b>${state.proxy_entry?.private?.enabled?'私网入口已准备':'私网入口未启用'}</b><span>${state.proxy_entry?.private?.authenticated?'随机认证已启用':'认证未就绪'}</span><code>${esc((state.proxy_entry?.private?.service_host||'容器服务名')+':'+(state.proxy_entry?.private?.port||'--'))}</code></div><p class="muted">该入口只应在受信容器网络内使用，不发布宿主机端口。页面不会显示用户名、密码或完整认证 URL；配置入口不等于 MCP 流量已经接管。</p></section>
      <section class="panel"><div class="bar"><div><small>AstrBot 核心流量</small><h2>全局代理接入</h2></div><div class="actions"><button id="astrbot-proxy-enable" ${state.astrbot_proxy?.effective?'disabled':''}>接入稳定入口</button><button id="astrbot-proxy-restore" ${state.astrbot_proxy?.backup_available?'':'disabled'}>恢复旧配置</button></div></div><div class="proxy-status"><b>${esc(state.astrbot_proxy?.status||'not_connected')}</b><span>${esc(state.astrbot_proxy?.message||'尚未读取 AstrBot 全局代理状态')}</span><code>${esc((state.astrbot_proxy?.no_proxy||[]).join(', ')||'--')}</code></div>${state.astrbot_proxy?.restart_required?'<p class="muted">AstrBot 需要重启后才会使用新的全局代理配置。</p>':''}</section>
      <section class="panel"><div class="section-head"><div><small>规则诊断</small><h2>分流预览</h2></div></div><div class="inline"><input id="host" placeholder="api.telegram.org"><button id="preview">查询</button></div><pre id="result">输入域名查看命中的代理组和节点。</pre></section>`
    html+=`<section class="panel"><h2>实际出站验证</h2><p class="muted">核心验证使用 AstrBot 当前进程的全局代理环境；只有目标返回出口 IP 且内核记录可关联规则与链路时才确认。</p><div class="inline"><input id="verify-url" value="https://api.ipify.org?format=json"><button id="verify-astrbot-egress">验证 AstrBot 核心出口</button><button id="verify-outbound">验证稳定入口</button></div>${verificationPanel(state.application?.verification)}<pre id="verify-result">${esc(state.application?.verification?JSON.stringify(state.application.verification,null,2):'尚未验证。')}</pre></section>`
    html+=`<section class="panel"><h2>首次使用</h2><div class="steps"><span>1 安装自管内核</span><span>2 导入订阅</span><span>3 筛选并选择节点</span><span>4 建立代理组</span><span>5 配置规则组</span><span>6 应用配置</span><span>7 验证实际出口</span></div></section>`
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
    const source=$('node-source')?.value||'全部', protocol=$('node-protocol')?.value||'全部', region=$('node-region')?.value||'全部', status=$('node-status')?.value||'全部'
    const sources=[...new Set(state.nodes.map(node=>node.subscription_id||'手动'))], protocols=[...new Set(state.nodes.map(node=>node.protocol))], regions=[...new Set(state.nodes.map(node=>node.region||'其他'))]
    const shown=state.nodes.map((node,index)=>({node,index})).filter(({node})=>(source==='全部'||(node.subscription_id||'手动')===source)&&(protocol==='全部'||node.protocol===protocol)&&(region==='全部'||(node.region||'其他')===region)&&(status==='全部'||(node.invalid_reference?'失效':health(node.id).status)===status))
    html=`<section class="panel"><div class="bar"><h2>代理节点</h2><div class="actions"><button id="add">新增节点</button><button id="test-all">批量测速</button></div></div>
      <div class="filter"><select id="node-source"><option>全部</option>${sources.map(value=>`<option ${value===source?'selected':''}>${esc(value)}</option>`).join('')}</select><select id="node-protocol"><option>全部</option>${protocols.map(value=>`<option ${value===protocol?'selected':''}>${esc(value)}</option>`).join('')}</select><select id="node-region"><option>全部</option>${regions.map(value=>`<option ${value===region?'selected':''}>${esc(value)}</option>`).join('')}</select><select id="node-status"><option>全部</option>${['ok','error','timeout','unknown','失效'].map(value=>`<option ${value===status?'selected':''}>${esc(value)}</option>`).join('')}</select></div>
      ${probeTask?`<div class="probe-progress"><progress value="${probeTask.completed}" max="${probeTask.total}"></progress><span>${probeTask.completed}/${probeTask.total}</span><button id="cancel-probe" ${probeTask.status!=='running'?'disabled':''}>取消</button></div>`:''}
      ${shown.map(({node,index})=>{const item=health(node.id),support=node.support||{status:'unverified',reason:'尚未验证'};const memberships=state.groups.filter(group=>group.node_ids.includes(node.id)).map(group=>group.name).join('、')||'未加入组';return `<div class="node-card" data-i="${index}">
        <div class="table"><input data-k="name" value="${esc(node.display_name||node.name)}"><span class="chip">${esc(node.protocol||'unknown')} · ${esc(node.engine||'')}</span><span class="chip ${support.status==='supported'?'ok':'pending'}">${esc({supported:'已支持',unverified:'未验证',unsupported:'不支持'}[support.status]||'未验证')}</span><input data-k="endpoint" value="${esc(node.endpoint)}" placeholder="完整连接 URI 或 HTTP/SOCKS 地址"><label><input type="checkbox" data-k="excluded" ${node.excluded?'checked':''}>确认排除测速/组选优</label><button data-del="nodes">删除</button></div>
        <div class="node-meta"><span class="chip ${node.invalid_reference?'invalid':item.status}">${node.invalid_reference?'引用失效':statusLabel(item)}</span><b>${item.latency_ms??'--'}</b><span>ms</span><span>${time(item.checked_at)}</span><span>${esc(node.subscription_id?'订阅：'+node.subscription_id:'手动节点')}</span><span>${esc(memberships)}</span>${node.suspected_notice?'<span class="chip pending">疑似订阅提示</span>':''}<button data-test="${esc(node.id)}">测速</button></div>
        ${support.reason?`<div class="node-error">${esc(support.reason)}</div>`:''}${node.notice_reason?`<div class="muted">${esc(node.notice_reason)}，请人工确认是否排除。</div>`:''}${item.error?`<div class="node-error">${esc(item.error)}</div>`:''}</div>`}).join('')||'<p class="muted">暂无节点。</p>'}</section>`
  } else if(tab==='groups'){
    html=`<section class="panel"><div class="bar"><h2>代理组</h2><button id="add">新增代理组</button></div>
      ${state.groups.map((group,index)=>`<div class="group-card" data-i="${index}"><div class="table"><input data-k="name" value="${esc(group.name)}"><select data-k="mode">${['direct','select','url-test','fallback'].map(mode=>`<option ${group.mode===mode?'selected':''}>${mode}</option>`).join('')}</select><select data-k="selected"><option value="">内核策略选择</option>${state.nodes.map(node=>`<option value="${esc(node.id)}" ${node.id===group.selected?'selected':''}>${esc(node.name)}</option>`).join('')}</select><button data-del="groups" ${group.id==='direct'?'disabled':''}>删除</button></div>${group.id!=='direct'?`<div class="table"><input data-k="test_url" value="${esc(group.test_url)}" placeholder="测速目标"><input type="number" data-k="test_interval" value="${group.test_interval}"><input type="number" data-k="tolerance" value="${group.tolerance}"><select data-k="failure_policy"><option value="fail-closed" ${group.failure_policy==='fail-closed'?'selected':''}>全部不可用时失败关闭</option><option value="keep-last" ${group.failure_policy==='keep-last'?'selected':''}>保留最后选择</option></select></div>`:''}<div class="nodes">${state.nodes.map(node=>`<label><input type="checkbox" data-node="${esc(node.id)}" ${group.node_ids.includes(node.id)?'checked':''}>${esc(node.name)}</label>`).join('')||'<span class="muted">暂无节点</span>'}</div></div>`).join('')}</section>`
  } else if(tab==='routes'){
    html=`<section class="panel"><div class="bar"><h2>规则组</h2><button id="add">新增规则组</button></div>${state.rule_groups.map((rule,index)=>`<div class="group-card" data-i="${index}"><div class="table"><input data-k="name" value="${esc(rule.name)}"><input type="number" data-k="priority" value="${rule.priority}"><select data-k="target">${groupOptions(rule.target)}</select><label><input type="checkbox" data-k="enabled" ${rule.enabled?'checked':''}>启用</label><button data-del="rule_groups">删除</button></div><textarea data-domains rows="3" placeholder="每行：exact api.example.com 或 suffix example.com">${esc(rule.domains.map(domain=>domain.match+' '+domain.host).join('\n'))}</textarea></div>`).join('')}</section>`
  } else if(tab==='platforms'){
    html=`<section class="panel"><h2>平台域名模板</h2><p class="muted">这里只生成内核域名规则，不会自动配置平台 SDK，也不代表平台流量已经接入。</p>${Object.entries(state.templates).map(([id,template])=>{const domains=template.domains||template.hosts.map(host=>({host,match:'exact'}));return `<div class="platform"><b>${esc(template.name)}</b><small>${esc(domains.map(item=>item.match+' '+item.host).join(' · '))}</small><select data-platform="${esc(id)}">${groupOptions((state.platforms[id]||{}).group_id||'direct')}</select><button data-template="${esc(id)}">应用或更新域名模板</button></div>`}).join('')}</section>`
  } else if(tab==='control'){
    const groups=controlResult?.groups||[]
    const artifact=kernelStatus.artifact||state.kernel?.artifact||{}, process=kernelStatus.process||state.kernel?.process||{}
    const install=kernelStatus.install||state.kernel?.install||{}, total=Number(install.total||artifact.size||0), downloaded=Number(install.downloaded||0), percent=total?Math.min(100,Math.round(downloaded*100/total)):0
    const taskRunning=install.state==='running'
    const adapters=state.adapters||[]
    const installed=adapters.filter(item=>['installed','update_available'].includes(item.artifact?.state)).length
    html=`<section class="panel kernel-overview"><div class="bar"><div><small>资源清单 · ${installed}/${adapters.length} 已准备</small><h2>插件自管内核</h2></div><div class="actions"><button id="refresh-kernels">刷新资源状态</button><button id="runtime-apply" class="primary">应用代理配置</button></div></div><p class="muted">每个内核都使用固定版本和 SHA-256 校验。未安装的内核不会参与运行；当前运行入口保持不变。</p><div class="kernel-grid">${adapters.map(item=>kernelCard(item, item.id===kernelStatus.adapter||item.id===state.control?.adapter)).join('')}</div>
      <div class="kernel-platform"><span>当前平台</span><b>${esc(artifact.platform?.os||'--')} / ${esc(artifact.platform?.arch||'--')}</b><small>${esc(artifact.platform?.libc||'无 libc 标识')}</small><span>当前进程</span><b>${esc(process.state||'未知')}</b><small>${esc(kernelStatus.message||'')}</small></div></section>
      <section class="panel kernel-current"><div class="bar"><div><small>当前适配器 · ${esc(kernelStatus.adapter||state.control?.adapter||'--')}</small><h2>运行控制</h2></div><div class="actions"><select id="adapter-select">${adapters.map(item=>`<option value="${esc(item.id)}" ${item.id===kernelStatus.adapter||item.id===state.control?.adapter?'selected':''}>${esc(item.display_name||item.id)}</option>`).join('')}</select><button id="adapter-switch">切换内核</button><button id="kernel-start">启动</button><button id="kernel-stop">停止</button><button id="control-status">刷新运行状态</button></div></div>
      <div class="download-status"><div><b>${esc(install.message||'暂无安装任务')}</b><small>${esc(install.source||'')}${install.attempt?` · 第 ${install.attempt}/${install.attempts} 次`:''}</small></div><progress max="100" value="${percent}"></progress><span>${downloaded?formatBytes(downloaded)+' / ':''}${total?formatBytes(total):'--'}</span></div>
      <div class="inline"><input id="kernel-file" type="file" accept=".gz,.zip,.tar.gz"><button id="kernel-upload">校验并安装离线制品</button></div><p class="muted">控制密钥、监听地址和稳定代理入口由插件内部生成并仅绑定回环地址，不需要手工配置。更新失败会恢复上一份可运行制品。</p></section>
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
    else if(tab==='groups'){ const item=state.groups[Number(row.dataset.i)]; item[input.dataset.k]=input.type==='number'?Number(input.value):input.value }
    else { const item=state.rule_groups[Number(row.dataset.i)]; item[input.dataset.k]=input.type==='checkbox'?input.checked:input.type==='number'?Number(input.value):input.value }
  }))
  document.querySelectorAll('[data-domains]').forEach(input=>input.addEventListener('change',()=>{const item=state.rule_groups[Number(input.closest('[data-i]').dataset.i)];item.domains=input.value.split('\n').map(line=>line.trim().split(/\s+/,2)).filter(parts=>parts.length===2).map(([match,host])=>({match:match==='suffix'?'suffix':'exact',host}))}))
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
    else state.rule_groups.push({id:'rules-'+Date.now(),name:'新规则组',domains:[{host:'example.com',match:'exact'}],target:'direct',priority:100,enabled:true})
    render()
  })
  $('add-subscription')?.addEventListener('click',()=>{state.subscriptions.push({id:'sub-'+Date.now(),name:'新订阅',url:'',group:'默认',enabled:true,interval:60,node_ids:[],updated_at:0,next_refresh_at:0,upload:0,download:0,total:0,expire:0,last_error:'',consecutive_errors:0,errors:[]});render()})
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
  $('refresh-kernels')?.addEventListener('click',async()=>{await load();if(tab==='control')note('内核资源状态已刷新')})
  $('adapter-switch')?.addEventListener('click',async()=>{try{const selected=$('adapter-select').value;if(!confirm(`切换到 ${selected} 将停止当前内核，并要求重新安装和应用配置。`))return;await api.apiPost('adapter-select',{adapter:selected});await load();note(`已切换到 ${selected}`)}catch(error){note(error.message,true)}})
  $('kernel-install')?.addEventListener('click',startKernelInstall)
  $('kernel-install-cancel')?.addEventListener('click',cancelKernelInstall)
  document.querySelectorAll('[data-kernel-install]').forEach(button=>button.addEventListener('click',()=>{const select=button.closest('[data-kernel-card]')?.querySelector('[data-kernel-version]');startKernelInstall(button.dataset.kernelInstall,select?.value||button.dataset.version)}))
  document.querySelectorAll('[data-kernel-cancel]').forEach(button=>button.addEventListener('click',()=>cancelKernelInstall(button.dataset.kernelCancel)))
  $('kernel-start')?.addEventListener('click',()=>kernelAction('kernel-start','正在启动内核...'))
  $('kernel-stop')?.addEventListener('click',()=>kernelAction('kernel-stop','正在停止内核...'))
  $('kernel-upload')?.addEventListener('click',uploadKernel)
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
function formatBytes(value){if(!value)return '0 B';const units=['B','KiB','MiB','GiB'];let size=value,index=0;while(size>=1024&&index<units.length-1){size/=1024;index++}return `${size.toFixed(index?1:0)} ${units[index]}`}
async function startKernelInstall(adapter=state.control?.adapter||kernelStatus.adapter,version=''){try{const payload={adapter};if(version)payload.version=version;const install=await api.apiPost('kernel-install',payload);if(adapter===state.control?.adapter||adapter===kernelStatus.adapter)kernelStatus.install=install;const item=state.adapters?.find(value=>value.id===adapter);if(item)item.install=install;render();note((version?'内核更新':'内核安装')+'任务已创建');pollKernelInstall(adapter)}catch(error){note(error.message,true)}}
async function cancelKernelInstall(adapter=state.control?.adapter||kernelStatus.adapter){try{const result=await api.apiPost('kernel-install-cancel',{adapter});if(adapter===state.control?.adapter||adapter===kernelStatus.adapter)kernelStatus.install=result;const item=state.adapters?.find(value=>value.id===adapter);if(item)item.install=result;render();note('在线安装已取消')}catch(error){note(error.message,true)}}
async function pollKernelInstall(adapter=state.control?.adapter||kernelStatus.adapter){clearTimeout(installPollTimer);try{const install=await api.apiPost('kernel-install-status',{adapter});if(adapter===state.control?.adapter||adapter===kernelStatus.adapter)kernelStatus.install=install;const item=state.adapters?.find(value=>value.id===adapter);if(item)item.install=install;if(install.state==='running'){render();installPollTimer=setTimeout(()=>pollKernelInstall(adapter),700)}else{await load();note(install.message,install.state!=='completed'&&install.state!=='cancelled')}}catch(error){note(error.message,true)}}
async function kernelAction(route,message){try{note(message);await api.apiPost(route,{});await load();note('内核状态已更新')}catch(error){note(error.message,true)}}
async function uploadKernel(){const file=$('kernel-file')?.files?.[0];if(!file)return note('请选择与当前平台匹配的固定版本制品',true);if(file.size>64*1024*1024)return note('制品超过 64 MiB 限制',true);try{note('正在校验离线制品...');const content=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',',2)[1]);reader.onerror=reject;reader.readAsDataURL(file)});await api.apiPost('kernel-upload',{content});await load();note('离线制品已校验并安装')}catch(error){note(error.message,true)}}
async function startProbe(nodeIds){
  try{const started=await api.apiPost('probe-task',{node_ids:nodeIds,timeout:5,concurrency:5});probeTask={id:started.task_id,total:started.total,completed:0,status:'running'};render();note('测速任务已开始');pollProbe()}catch(error){note(error.message,true)}
}
async function pollProbe(){
  if(!probeTask)return
  try{const task=await api.apiPost('probe-task-status',{task_id:probeTask.id});probeTask=task;for(const result of task.results)if(result.health)state.health[result.node_id]=result.health;render();if(task.status==='running')setTimeout(pollProbe,500);else note(`测速完成：${task.summary.ok} 可用，${task.summary.error+task.summary.timeout} 失败，${task.summary.skipped} 未执行，${task.summary.cancelled} 已取消`)}catch(error){note(error.message,true)}
}
async function load(){ try{ state=await api.apiGet('state');kernelStatus=await api.apiGet('kernel-status');original=structuredClone(state);controlResult=null;importPreview=null;render();for(const item of (state.adapters||[]))if(item.install?.state==='running')pollKernelInstall(item.id) }catch(error){note(error.message,true)} }

document.querySelectorAll('[data-tab]').forEach(button=>button.addEventListener('click',()=>{tab=button.dataset.tab;render()}))
$('reload').addEventListener('click',load)
$('rollback').addEventListener('click',async()=>{try{state=await api.apiPost('rollback',{});original=structuredClone(state);controlResult=null;importPreview=null;render();note('已恢复上一版配置')}catch(error){note(error.message,true)}})
$('save').addEventListener('click',()=>{readControl();$('diff-text').textContent=JSON.stringify({before:original,after:state},null,2);$('diff').showModal()})
$('cancel').addEventListener('click',()=>$('diff').close())
$('confirm').addEventListener('click',async()=>{try{await saveChanges();$('diff').close();render();note('配置已保存')}catch(error){note(error.message,true)}})
load()
