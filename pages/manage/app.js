function initialize() {
const $ = id => document.getElementById(id)
const api = window.AstrBotPluginPage
if (!api || typeof api.ready !== 'function') {
  const notice=document.getElementById('notice')
  if(notice){notice.hidden=false;notice.textContent='AstrBot 页面桥接未就绪，请重新打开插件页面';if(typeof notice.showPopover==='function')notice.showPopover();else notice.setAttribute('data-visible','true')}
  return
}
const titles = {overview:'概览',subscriptions:'订阅管理',nodes:'代理节点',groups:'代理组',routes:'分流规则',platforms:'平台域名模板',control:'内核管理',logs:'连接日志'}
const subtitles = {overview:'运行状态、节点健康和真实流量接入范围',subscriptions:'导入、刷新并维护订阅来源',nodes:'筛选节点、核对支持状态并执行测速',groups:'组织出口节点与故障处理策略',routes:'按优先级管理域名和目标出口',platforms:'生成域名规则；这不代表平台 SDK 已接入代理',control:'管理插件自有内核、制品与运行配置',logs:'查看最近的配置、安装和连接事件'}
let state, original, tab='overview', controlResult=null, kernelStatus={state:'not_configured',ready:false,message:'尚未检查'}, importPreview=null, importMode='single', importDraft={url:'',urls:'',name:'',interval:60}, importing=false, probeTask=null, probeLabel='测速', selectedProbeNodeIds=new Set(), groupProbeRunning=new Set(), groupProbeErrors={}, importDialogReturnFocus=null, groupDialogReturnFocus=null, groupDraft=null, groupNodeQuery='', editingGroupId=null, nodeDialogReturnFocus=null, nodeDialogNodeId='', ruleDialogReturnFocus=null, ruleDraft=null, editingRuleId=null, confirmDialogReturnFocus=null, confirmAction=null, openGroupIds=new Set()
const resourcePollTimers=new Map()
const openKernelResources=new Set()
let noticeTimer=null

const esc = value => String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))
function note(text,error=false){
  const notice=$('notice'); if(!notice)return
  clearTimeout(noticeTimer); notice.textContent=text||''; notice.className=error?'error':''
  if(!text){notice.hidePopover?.();notice.hidden=true;return}
  notice.hidden=false
  if(typeof notice.showPopover==='function')notice.showPopover(); else notice.setAttribute('data-visible','true')
  noticeTimer=setTimeout(()=>{notice.hidePopover?.();notice.hidden=true;notice.textContent=''},error?8000:3200)
}
function groupOptions(selected){ return state.groups.map(group=>`<option value="${esc(group.id)}" ${group.id===selected?'selected':''}>${esc(group.name)}</option>`).join('') }
const groupModes=[
  {value:'select',label:'手动选择',help:'在运行状态中手动切换成员节点。'},
  {value:'url-test',label:'按延迟自动选择',help:'按测速目标和周期选择延迟最低的可用节点。'},
  {value:'fallback',label:'故障自动切换',help:'按成员顺序使用健康节点，故障后切换到下一个。'},
]
const groupModeLabels=Object.fromEntries(groupModes.map(item=>[item.value,item.label]))
function groupStrategy(mode){
  if(mode==='url-test')return {title:'按测速选择延迟最低节点',detail:'根据最近有效测速结果选择延迟最低的可用节点；尚无测速结果时按成员顺序兜底。'}
  if(mode==='fallback')return {title:'按成员顺序故障切换',detail:'按成员顺序使用首个健康节点；当前节点故障后切换到下一个，尚无健康记录时使用首个成员。'}
  return {title:'由用户手动选择',detail:'运行时可在代理组状态中切换成员；未指定初始节点时按成员顺序使用首个可用节点。'}
}
function editableGroups(){ return state.groups.map((group,index)=>({group,index})).filter(item=>item.group.id!=='direct') }
function groupMembers(group){ return state.nodes.filter(node=>group.node_ids?.includes(node.id)) }
function nodeName(node){ return node?.display_name||node?.name||'未命名节点' }
function nodeSubscription(node){
  if(!node?.subscription_id)return '手动节点'
  const subscription=state?.subscriptions?.find(item=>item.id===node.subscription_id)
  return subscription?.name||'未命名订阅'
}
function nodeLabel(node){ return `${nodeSubscription(node)} · ${nodeName(node)}` }
function nodeDetails(node){ return `${nodeSubscription(node)} · ${node?.protocol||'unknown'} · ${node?.region||'其他'}` }
function orderedRuleGroups(){
  return (state?.rule_groups||[]).map((rule,index)=>({rule,index})).sort((left,right)=>Number(left.rule.priority)-Number(right.rule.priority)||left.index-right.index).map(item=>item.rule)
}
function setRuleTarget(ruleId,target){
  const rule=state.rule_groups.find(item=>item.id===ruleId)
  if(!rule)return false
  rule.target=target
  return true
}
function moveRuleGroup(sourceId,targetId,after=false){
  const ordered=orderedRuleGroups(),sourceIndex=ordered.findIndex(rule=>rule.id===sourceId)
  if(sourceIndex<0||sourceId===targetId)return false
  const [moving]=ordered.splice(sourceIndex,1),targetIndex=ordered.findIndex(rule=>rule.id===targetId)
  if(targetIndex<0)return false
  ordered.splice(targetIndex+(after?1:0),0,moving)
  if(ordered.length>10000){note('规则组数量超过优先级范围，无法重新排序',true);return false}
  ordered.forEach((rule,index)=>{rule.priority=index+1})
  state.rule_groups=ordered
  render()
  note(`规则顺序已更新：“${moving.name}”优先级为 ${moving.priority}`)
  requestAnimationFrame(()=>document.querySelector(`[data-rule-drag-handle="${CSS.escape(sourceId)}"]`)?.focus())
  return true
}
function groupNodeMatches(node,query){
  const terms=String(query||'').trim().toLocaleLowerCase().split(/\s+/).filter(Boolean)
  if(!terms.length)return true
  const text=[nodeSubscription(node),nodeName(node),node?.display_name,node?.name,node?.protocol,node?.region].filter(Boolean).join(' ').toLocaleLowerCase()
  return terms.every(term=>text.includes(term))
}
function runtimeNodeLabel(node){
  const source=state?.nodes?.find(item=>item.id===node?.id)
  return source?nodeLabel(source):(node?.display_name||'未命名节点')
}
function runtimeGroupSummary(group){
  if(!controlResult)return {label:'未读取',className:'pending',current:'尚未读取运行状态'}
  if(!group)return {label:'未读取',className:'pending',current:'当前内核未返回此代理组'}
  if(!group.runtime_available)return {label:'待应用',className:'pending',current:'尚未应用到当前内核'}
  if(!group.selected_node_id&&!group.selected_display_name)return {label:'待核对',className:'pending',current:'内核尚未返回当前节点'}
  return {label:'运行中',className:'ok',current:group.selected_node_id?runtimeNodeLabel({id:group.selected_node_id}):group.selected_display_name}
}
function runtimeGroupProbeEvidence(probe){
  const results=Array.isArray(probe?.member_results)?probe.member_results:[]
  if(!results.length)return ''
  const ordered=results.slice().sort((a,b)=>(a.latency_ms??Infinity)-(b.latency_ms??Infinity))
  const selection=probe?.selection||{}
  return `<div class="runtime-group-selection"><span>选优核对</span><b>${esc(selection.message||'已取得成员测速结果')}</b>${selection.minimum_latency_ms!==undefined?`<small>最低 ${esc(selection.minimum_latency_ms)} ms · 当前 ${esc(selection.selected_latency_ms)} ms · 容差 ${esc(selection.tolerance_ms||0)} ms</small>`:''}</div><details class="runtime-group-evidence"><summary>成员测速明细（${results.length}）</summary><div class="runtime-group-members">${ordered.map(item=>`<div class="runtime-group-member ${item.node_id===probe.node_id?'current':''}"><span>${esc(item.node_name||'未命名节点')}${item.node_id===probe.node_id?'<small>当前节点</small>':''}</span><b>${item.latency_ms===null||item.latency_ms===undefined?'--':`${esc(item.latency_ms)} ms`}</b></div>`).join('')}</div></details>`
}
function groupDraftFrom(group){
  const source=group||{}
  return {
    id:source.id||`group-${Date.now()}`,
    name:source.name||'新代理组',
    mode:groupModes.some(item=>item.value===source.mode)?source.mode:'select',
    node_ids:[...(source.node_ids||[])],
    selected:source.selected||'',
    enabled:source.enabled!==false,
    test_url:source.test_url||'https://www.gstatic.com/generate_204',
    test_interval:Number(source.test_interval||300),
    tolerance:Number(source.tolerance??50),
    failure_policy:source.failure_policy==='keep-last'?'keep-last':'fail-closed',
  }
}
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
function nodeSpeed(node){ const item=health(node.id); return item.status==='ok'&&item.latency_ms!==null&&item.latency_ms!==undefined&&Number.isFinite(Number(item.latency_ms))?`${item.latency_ms} ms`:'-- ms' }
function kernelStateLabel(item){ return {running:'运行中',running_limited:'运行中（有限控制）',installed:'已安装',update_available:'有更新',not_installed:'未安装',invalid:'校验失败',unsupported:'平台不支持',stopped:'已停止',failed:'运行失败',connection_failed:'连接失败',auth_failed:'认证失败',disabled:'未启用'}[item?.state]||'未检查' }
function kernelStateClass(item){ return ['running','running_limited','installed'].includes(item?.state)?'ok':(['update_available','not_installed','disabled'].includes(item?.state)?'pending':(['invalid','unsupported','failed','connection_failed','auth_failed'].includes(item?.state)?'invalid':'')) }
function renderTaskBanner(){
  const banner=$('task-banner'), running=probeTask?.status==='running'; if(!banner)return
  if(!running){banner.hidePopover?.();banner.hidden=true;banner.textContent='';return}
  const total=Math.max(1,Number(probeTask.total||0)), completed=Math.min(total,Number(probeTask.completed||0));
  banner.innerHTML=`<div class="task-banner-inner"><progress aria-label="${esc(probeLabel)}进度" value="${completed}" max="${total}"></progress><span>${esc(probeLabel)}：${completed}/${total}</span><button id="cancel-probe" type="button" ${probeTask.status!=='running'?'disabled':''}>取消</button></div>`; banner.hidden=false; if(typeof banner.showPopover==='function')banner.showPopover();else banner.setAttribute('data-visible','true')
}
function requiresKernelProbe(nodeIds){
  const direct=new Set(['http','https','socks','socks5','socks5h']);
  return nodeIds.some(id=>{const node=state.nodes.find(item=>item.id===id);return node&&!direct.has(String(node.protocol||'').toLowerCase())})
}
function kernelCard(item,current){
  const artifact=item.artifact||{}, task=item.install||{}, busy=task.state==='running'
  const version=artifact.selected_version||artifact.recommended_version||artifact.version||'--'
  const resource=artifact.version_resources?.[version]||artifact
  const installed=artifact.installed_version||'未安装'
  const state=busy?{state:'running'}:artifact
  const installLabel=!artifact.ready?'安装所选版本':version===installed?'重新安装':'覆盖安装 '+version
  const action=artifact.state==='unsupported'?'':(busy?(task.operation==='install'?`<button data-kernel-cancel="${esc(item.id)}">取消</button>`:''):`<button class="${version!==installed?'primary':''}" data-kernel-install="${esc(item.id)}" data-version="${esc(version)}">${esc(installLabel)}</button>`)
  const versions=(artifact.available_versions||[]).map(value=>`<option value="${esc(value)}" ${value===version?'selected':''}>${esc(value)}</option>`).join('')
  const check=artifact.update_check||{}
  const enable=artifact.ready?(item.enabled?`<button data-kernel-enable="${esc(item.id)}" data-enabled="false">停用</button>`:`<button class="primary" data-kernel-enable="${esc(item.id)}" data-enabled="true">启用</button>`):''
  const select=artifact.ready&&item.enabled&&!current?`<button data-kernel-select="${esc(item.id)}">设为当前内核</button>`:''
  const percent=Number(task.progress??(task.total?Math.round(Number(task.downloaded||0)*100/Number(task.total)):0))
  const taskProgress=task.state&&task.state!=='idle'?`<div class="kernel-progress"><div><b>${esc(task.message||'资源任务处理中')}</b><span>${esc(task.phase||'')}</span></div><progress max="100" value="${Math.min(100,percent)}"></progress><small>${task.total?formatBytes(task.downloaded||0)+' / '+formatBytes(task.total):percent+'%'}</small></div>`:''
  const uninstall=artifact.ready&&!busy?`<button class="danger" data-kernel-uninstall="${esc(item.id)}">卸载</button>`:''
  const expanded=openKernelResources.has(item.id)||busy
  return `<details class="kernel-resource ${current?'current':''}" data-kernel-card="${esc(item.id)}" ${expanded?'open':''}><summary><div><b>${esc(item.display_name||item.id)}</b><small>${current?'当前选择 · ':''}${item.enabled?'已启用':'未启用'} · 已安装 ${esc(installed)}</small></div><span class="kernel-resource-meta">${esc(resource.resource_key||artifact.resource_key||'--')}</span><span class="kernel-badge ${kernelStateClass(state)}">${kernelStateLabel(state)}</span></summary><div class="kernel-resource-body"><div class="kernel-facts"><div><span>已安装</span><b>${esc(installed)}</b></div><div><span>目标版本</span>${versions?`<select data-kernel-version="${esc(item.id)}">${versions}</select>`:`<b>${esc(version)}</b>`}</div><div><span>平台资源</span><b>${esc(resource.resource_key||artifact.resource_key||'--')}</b></div></div><p class="kernel-message">${esc(task.message||artifact.message||'')}${check.checked_at?`<br>更新检查：${esc(check.message||'')}（${time(check.checked_at)}）`:''}</p>${taskProgress}<div class="kernel-actions">${action}${uninstall}${enable}${select}<button data-kernel-check="${esc(item.id)}">检查更新</button></div>${resource.download_url?`<div class="kernel-download-url"><span>官方下载地址</span><code>${esc(resource.download_url)}</code></div>`:''}<details class="kernel-verification"><summary>制品校验信息</summary><code>${esc(resource.artifact||'资源未配置')}<br>${esc(resource.expected_sha256||'')}</code></details><div class="kernel-offline"><b>离线安装此内核</b><div class="inline"><input data-kernel-file="${esc(item.id)}" type="file" accept=".gz,.zip,.tar.gz"><button data-kernel-upload="${esc(item.id)}" data-version="${esc(version)}">校验并安装离线制品</button></div></div></div></details>`
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
      <div class="cards metric-row metric-row-primary">${[['subscriptions','订阅'],['nodes','节点'],['groups','代理组'],['routes','规则']].map(([key,label])=>`<article><b>${key==='groups'?state.groups.filter(group=>group.id!=='direct').length:state[key].length}</b><span>${label}</span></article>`).join('')}</div>
      <div class="cards metric-row metric-row-secondary"><article><b>${ok}</b><span>可用节点</span></article><article><b>${bad}</b><span>异常节点</span></article><article><b>${state.subscriptions.filter(item=>item.enabled&&item.interval).length}</b><span>自动订阅</span></article><article><b>${state.events.length}</b><span>最近事件</span></article></div>
      <section class="panel"><div class="section-head"><div><small>真实接入范围 · ${esc(state.traffic_audit?.version||'未审计')}</small><h2>AstrBot 流量清单</h2></div><button id="integration-check" title="重新检查新增的插件和 MCP">重新检查</button></div><div class="traffic-inventory">${(state.traffic_inventory||[]).map(item=>`<article><div><b>${esc(item.name)}</b><small>${esc(item.method)}</small></div><span class="traffic-state ${esc(item.status)}">${esc({managed:'已接管',direct:'明确直连',not_connected:'未接入',unknown:'无法判定'}[item.status]||'无法判定')}</span><p>${esc(item.message)}</p><small>策略接入：${esc({managed:'已纳入统一策略',pending:'等待 AstrBot 重启',declared:'已声明协议，等待配置入口',needs_protocol:'需声明接入协议'}[item.integration?.state]||'待检查')} · ${esc(item.integration?.message||'')}</small><small>验证：${esc(item.verification||'当前无专项验证')}</small><small>旁路风险：${esc(item.bypass_risk||'待审计')}</small>${item.restart?'<small>变更后需要重启相关组件</small>':''}${item.discovered?.length?`<small>已发现：${esc(item.discovered.map(value=>value.name+' ('+(value.declaration?({compatible:'协议兼容',missing:'缺少协议',invalid:'协议无效'}[value.declaration.state]||'协议待检查'):(value.locality_label||value.type||value.transport||'未知'))+')').join(' · '))}</small>`:''}</article>`).join('')}</div></section>
      <section class="panel"><div class="section-head"><div><small>运行时指纹与注册表</small><h2>官方兼容层</h2></div></div><p class="muted">${esc(state.compatibility?.message||'尚未安装')}</p><div class="proxy-status"><b>${esc(state.compatibility?.astrbot||'未知 AstrBot')}</b><span>${esc(state.compatibility?.state||'not_installed')}</span><code>${esc(Object.entries(state.compatibility?.sdk_versions||{}).map(([key,value])=>key+' '+value).join(' · ')||'未读取 SDK 版本')}</code></div></section>
      <section class="panel"><div class="section-head"><div><small>跨进程与跨容器</small><h2>MCP 私有出口</h2></div></div><div class="proxy-status"><b>${state.proxy_entry?.private?.enabled?'私网入口已准备':'私网入口未启用'}</b><span>${state.proxy_entry?.private?.authenticated?'随机认证已启用':'认证未就绪'}</span><code>${esc((state.proxy_entry?.private?.service_host||'容器服务名')+':'+(state.proxy_entry?.private?.port||'--'))}</code></div><p class="muted">该入口只应在受信容器网络内使用，不发布宿主机端口。页面不会显示用户名、密码或完整认证 URL；配置入口不等于 MCP 流量已经接管。</p></section>
      <section class="panel"><div class="bar"><div><small>AstrBot 核心流量</small><h2>全局代理接入</h2></div><div class="actions"><button id="astrbot-proxy-enable" ${state.astrbot_proxy?.effective?'disabled':''}>接入稳定入口</button><button id="astrbot-proxy-restore" ${state.astrbot_proxy?.backup_available?'':'disabled'}>恢复旧配置</button></div></div><div class="proxy-status"><b>${esc(state.astrbot_proxy?.status||'not_connected')}</b><span>${esc(state.astrbot_proxy?.message||'尚未读取 AstrBot 全局代理状态')}</span><code>${esc((state.astrbot_proxy?.no_proxy||[]).join(', ')||'--')}</code></div>${state.astrbot_proxy?.restart_required?'<p class="muted">AstrBot 需要重启后才会使用新的全局代理配置。</p>':''}</section>
      <section class="panel"><div class="section-head"><div><small>规则诊断</small><h2>分流预览</h2></div></div><div class="inline"><input id="host" placeholder="api.telegram.org"><button id="preview">查询</button></div><pre id="result">输入域名查看命中的代理组和节点。</pre></section>`
    html+=`<section class="panel"><h2>实际出站验证</h2><p class="muted">核心验证使用 AstrBot 当前进程的全局代理环境；只有目标返回出口 IP 且内核记录可关联规则与链路时才确认。</p><div class="inline"><input id="verify-url" value="https://api.ipify.org?format=json"><button id="verify-astrbot-egress">验证 AstrBot 核心出口</button><button id="verify-outbound">验证稳定入口</button></div>${verificationPanel(state.application?.verification)}<pre id="verify-result">${esc(state.application?.verification?JSON.stringify(state.application.verification,null,2):'尚未验证。')}</pre></section>`
    html+=`<section class="panel"><h2>首次使用</h2><div class="steps"><span>1 安装自管内核</span><span>2 导入订阅</span><span>3 筛选并选择节点</span><span>4 建立代理组</span><span>5 配置规则组</span><span>6 应用配置</span><span>7 验证实际出口</span></div></section>`
  } else if(tab==='subscriptions'){
    const shown=state.subscriptions
    html=`<section class="panel import-entry-panel"><div class="bar"><div><small>来源接入</small><h2>导入订阅</h2><p class="muted">先预览节点、协议和地区，再确认写入配置。</p></div><div class="actions"><button id="open-single-import" class="primary">导入订阅</button><button id="open-batch-import">批量导入</button></div></div></section>
      <section class="panel"><div class="bar"><h2>订阅列表</h2><button id="add-subscription">新增订阅</button></div>
      ${shown.map(item=>subHtml(item)).join('')||'<p class="muted">暂无订阅。</p>'}</section>`
  } else if(tab==='nodes'){
    const source=$('node-source')?.value||'全部', protocol=$('node-protocol')?.value||'全部', region=$('node-region')?.value||'全部', status=$('node-status')?.value||'全部'
    const subscriptionNames=Object.fromEntries(state.subscriptions.map(item=>[item.id,item.name||item.id])), sourceEntries=[...new Map(state.nodes.map(node=>[node.subscription_id||'manual',{id:node.subscription_id||'manual',label:node.subscription_id?(subscriptionNames[node.subscription_id]||'未命名订阅'):'手动节点'}])).values()], protocols=[...new Set(state.nodes.map(node=>node.protocol))], regions=[...new Set(state.nodes.map(node=>node.region||'其他'))]
    const shown=state.nodes.map((node,index)=>({node,index})).filter(({node})=>(source==='全部'||(node.subscription_id||'manual')===source)&&(protocol==='全部'||node.protocol===protocol)&&(region==='全部'||(node.region||'其他')===region)&&(status==='全部'||(node.invalid_reference?'失效':health(node.id).status)===status)), visibleIds=shown.map(({node})=>node.id), visibleSelectedCount=visibleIds.filter(id=>selectedProbeNodeIds.has(id)).length, selectedCount=state.nodes.filter(node=>selectedProbeNodeIds.has(node.id)).length
    html=`<section class="panel"><div class="bar"><div><h2>代理节点</h2><p class="muted panel-lede">列表保留名称、状态和延迟；连接参数及诊断信息可在节点详情中查看。</p></div><div class="actions"><button id="add">新增节点</button><button id="test-selected" ${selectedCount?'':'disabled'}>测速所选 (${selectedCount})</button><button id="test-all">测速全部</button></div></div>
      <div class="filter"><select id="node-source"><option value="全部">全部</option>${sourceEntries.map(entry=>`<option value="${esc(entry.id)}" ${entry.id===source?'selected':''}>${esc(entry.label)}</option>`).join('')}</select><select id="node-protocol"><option>全部</option>${protocols.map(value=>`<option ${value===protocol?'selected':''}>${esc(value)}</option>`).join('')}</select><select id="node-region"><option>全部</option>${regions.map(value=>`<option ${value===region?'selected':''}>${esc(value)}</option>`).join('')}</select><select id="node-status"><option>全部</option>${['ok','error','timeout','unknown','失效'].map(value=>`<option ${value===status?'selected':''}>${esc(value)}</option>`).join('')}</select></div>
      <div class="node-select-toolbar"><span id="probe-selection-summary" aria-live="polite">已选 ${selectedCount} 个 · 当前筛选 ${shown.length} 个（已选 ${visibleSelectedCount} 个）</span><div class="node-select-actions"><button id="select-visible-nodes" type="button" ${shown.length?'':'disabled'}>全选</button><button id="clear-visible-nodes" type="button" ${visibleSelectedCount?'':'disabled'}>全不选</button></div></div>
      ${shown.map(({node,index})=>{const item=health(node.id),support=node.support||{status:'unverified',reason:'尚未验证'};const runtimeStatus=node.invalid_reference?'invalid':item.status;return `<article class="node-card compact-node-card" data-i="${index}">
        <div class="compact-node-main"><label class="node-select-checkbox"><input type="checkbox" data-probe-select="${esc(node.id)}" aria-label="选择 ${esc(nodeLabel(node))} 测速" ${selectedProbeNodeIds.has(node.id)?'checked':''}></label><div class="compact-node-identity"><b class="compact-node-name">${esc(node.display_name||node.name)}</b><div class="compact-node-subline"><span class="chip">${esc(node.protocol||'unknown')}</span><span class="chip ${support.status==='supported'?'ok':'pending'}">${esc({supported:'已支持',unverified:'未验证',unsupported:'不支持'}[support.status]||'未验证')}</span><span class="chip region-chip">${esc(node.region||'其他')}</span><span class="chip ${runtimeStatus}">${node.invalid_reference?'引用失效':statusLabel(item)}</span></div></div><div class="compact-node-health"><b>${item.latency_ms??'--'} <small>ms</small></b><small>${time(item.checked_at)}</small></div><div class="compact-node-actions"><button type="button" data-test="${esc(node.id)}">测速</button><button type="button" data-node-details="${esc(node.id)}">详情</button><button type="button" data-del="nodes" class="danger">删除</button></div></div>
      </article>`}).join('')||'<p class="muted">暂无节点。</p>'}</section>`
  } else if(tab==='groups'){
    const runtimeGroups=new Map((controlResult?.groups||[]).map(group=>[group.id,group]))
    const groups=editableGroups()
    html=`<section class="panel groups-panel"><div class="bar"><div><h2>代理组</h2><p class="muted panel-lede">每个代理组的配置和内核状态收在同一项中，展开后管理节点与运行操作。</p></div><div class="actions"><button id="group-runtime-refresh">刷新状态</button><button id="add" class="primary">新增代理组</button></div></div>
      <p class="muted group-config-note">保存页面配置后，还需在“内核管理”点击“应用代理配置”才会写入当前内核。直连是内核内部默认目标，不作为可编辑代理组展示。</p>
      ${groups.map(({group,index})=>{const members=groupMembers(group),selected=group.mode==='select'&&state.nodes.find(node=>node.id===group.selected),runtime=runtimeGroups.get(group.id),summary=runtimeGroupSummary(runtime),opened=openGroupIds.has(group.id),probe=runtime?.last_probe,probing=groupProbeRunning.has(group.id),error=groupProbeErrors[group.id],canProbe=Boolean(controlResult?.group_probe_supported&&runtime?.runtime_available);return `<details class="group-accordion" data-group-accordion="${esc(group.id)}" data-group-id="${esc(group.id)}" ${opened?'open':''}><summary><span class="group-summary-copy"><b>${esc(group.name)}</b><small>${esc(groupModeLabels[group.mode]||group.mode)} · ${members.length} 个成员 · 当前：${esc(summary.current)}</small></span><span class="chip ${summary.className}">${summary.label}</span><span class="group-summary-toggle" aria-hidden="true"></span></summary><div class="group-accordion-body"><div class="group-accordion-toolbar"><div class="group-card-meta"><span>成员：${members.length}</span>${selected?`<span>初始：${esc(nodeLabel(selected))}</span>`:''}${group.mode!=='select'?`<span>策略：${esc(groupStrategy(group.mode).title)}</span><span>测速：每 ${esc(group.test_interval||300)} 秒</span>`:''}</div><div class="group-card-actions"><button type="button" data-edit-group="${esc(group.id)}">编辑配置</button><button type="button" data-del="groups" class="danger">删除</button></div></div><div class="group-card-members">${members.map(node=>`<span class="chip" title="${esc(nodeDetails(node))}">${esc(nodeLabel(node))}</span>`).join('')||'<span class="muted">尚未选择节点</span>'}</div><section class="group-runtime-inline" aria-label="${esc(group.name)}运行状态"><div class="runtime-group-head"><div><b>运行状态</b><small>${esc(runtime?.type||groupModeLabels[group.mode]||'运行组')}</small></div><button type="button" data-group-probe="${esc(group.id)}" ${canProbe&&!probing?'':'disabled'}>${probing?'测速中…':'核对选优'}</button></div><div class="runtime-group-facts"><div><span>当前连接节点</span><b>${esc(runtime?.selected_node_id?runtimeNodeLabel({id:runtime.selected_node_id}):runtime?.selected_display_name||'无')}</b></div><div><span>最近选优核对</span><b>${probe?`${esc(probe.latency_ms)} ms`:'-- ms'}</b><small>${probe?`${esc(probe.node_name||'测速节点')} · ${time(probe.checked_at)}${probe.stale?' · 配置已变更':''}`:'尚未核对'}</small></div></div>${group.mode==='url-test'?'<small class="runtime-group-note">内核按周期自动选优；“核对选优”只展示成员结果，不会手动固定节点。</small>':''}${probe?runtimeGroupProbeEvidence(probe):''}${error?`<p class="runtime-group-error" role="alert">${esc(error)}</p>`:''}${runtime?.runtime_available?`<label class="runtime-group-select"><span>切换当前节点</span><select data-select="${esc(group.id)}" aria-label="切换${esc(group.name)}的当前节点">${runtime.members.map(node=>`<option value="${esc(node.id)}" ${node.id===runtime.selected_node_id?'selected':''} ${node.available?'':'disabled'}>${esc(runtimeNodeLabel(node))}${node.available?'':'（不可用）'}</option>`).join('')}</select></label>`:''}${!canProbe?`<small class="runtime-group-note">${controlResult?.adapter==='xray'?'当前内核不支持代理组控制面测速。':controlResult?.group_probe_message||(!runtime?.runtime_available?'代理组尚未应用到当前内核。':'当前内核不支持代理组测速。')}</small>`:''}</section></div></details>`}).join('')||'<div class="group-empty"><b>还没有代理组</b><span>新增代理组后，可在同一项中查看配置和运行状态。</span></div>'}</section>`
  } else if(tab==='routes'){
    const orderedRules=orderedRuleGroups()
    html=`<section class="panel routes-panel"><div class="bar"><div><h2>规则组</h2><p class="muted panel-lede">拖动规则组调整优先顺序；也可使用上移、下移按钮。目标代理组可直接在卡片中修改。</p></div><button id="add" class="primary" type="button">新增规则组</button></div><div class="rule-list">${orderedRules.map((rule,index)=>`<article class="rule-card" data-rule-id="${esc(rule.id)}"><div class="rule-card-head"><button type="button" class="rule-drag-handle" draggable="true" data-rule-drag-handle="${esc(rule.id)}" aria-label="拖动调整 ${esc(rule.name)} 的优先级" title="拖动调整优先级">⠿ 拖动</button><div class="rule-card-title"><b>${esc(rule.name)}</b><small>优先顺序 ${index+1} · 优先级 ${esc(rule.priority)} · ${rule.domains.length} 条域名</small></div><span class="chip ${rule.enabled?'ok':'pending'}">${rule.enabled?'已启用':'已停用'}</span><div class="rule-card-actions"><button type="button" data-rule-move="up" data-rule-id="${esc(rule.id)}" aria-label="上移 ${esc(rule.name)}" ${index===0?'disabled':''}>上移</button><button type="button" data-rule-move="down" data-rule-id="${esc(rule.id)}" aria-label="下移 ${esc(rule.name)}" ${index===orderedRules.length-1?'disabled':''}>下移</button><button type="button" data-edit-rule="${esc(rule.id)}">编辑</button><button type="button" data-del="rule_groups" class="danger">删除</button></div></div><div class="rule-card-controls"><label class="rule-target-control" for="rule-target-${esc(rule.id)}"><span>目标代理组</span><select id="rule-target-${esc(rule.id)}" data-rule-target="${esc(rule.id)}">${groupOptions(rule.target)}</select></label></div><div class="rule-card-domains">${rule.domains.map(domain=>`<span class="chip">${esc(domain.match)} ${esc(domain.host)}</span>`).join('')||'<span class="muted">暂无域名</span>'}</div></article>`).join('')||'<div class="group-empty"><b>还没有规则组</b><span>新增规则组后，可在弹窗中配置规则。</span></div>'}</div></section>`
  } else if(tab==='platforms'){
    html=`<section class="panel"><h2>平台域名模板</h2><p class="muted">这里只生成内核域名规则，不会自动配置平台 SDK，也不代表平台流量已经接入。</p>${Object.entries(state.templates).map(([id,template])=>{const domains=template.domains||template.hosts.map(host=>({host,match:'exact'}));return `<div class="platform"><b>${esc(template.name)}</b><small>${esc(domains.map(item=>item.match+' '+item.host).join(' · '))}</small><select data-platform="${esc(id)}">${groupOptions((state.platforms[id]||{}).group_id||'direct')}</select><button data-template="${esc(id)}">应用或更新域名模板</button></div>`}).join('')}</section>`
  } else if(tab==='control'){
    const artifact=kernelStatus.artifact||state.kernel?.artifact||{}, process=kernelStatus.process||state.kernel?.process||{}
    const adapters=state.adapters||[]
    const installed=adapters.filter(item=>item.artifact?.ready).length, enabled=adapters.filter(item=>item.enabled&&item.artifact?.ready).length
    const currentId=kernelStatus.adapter||state.control?.adapter||''
    const runnable=adapters.filter(item=>item.enabled&&item.artifact?.ready)
    html=`<section class="panel kernel-current"><div class="section-head"><div><small>第一栏 · 当前选择 ${esc(currentId||'--')}</small><h2>运行控制</h2></div><span class="kernel-badge ${process.ready?'ok':process.state==='failed'?'invalid':'pending'}">${esc(process.ready?'运行中':process.state||'已停止')}</span></div><div class="runtime-control"><label><span>运行内核</span><select id="adapter-select" ${runnable.length?'':'disabled'}>${runnable.map(item=>`<option value="${esc(item.id)}" ${item.id===currentId?'selected':''}>${esc(item.display_name||item.id)}</option>`).join('')||'<option>没有可运行内核</option>'}</select></label><div class="runtime-actions"><button id="adapter-switch" ${runnable.length?'':'disabled'}>切换内核</button><button id="kernel-start" ${runnable.length?'':'disabled'}>启动</button><button id="kernel-stop">停止</button><button id="control-status">刷新状态</button><button id="runtime-apply" class="primary" ${runnable.length?'':'disabled'}>应用代理配置</button></div></div><div class="runtime-facts"><div><span>系统</span><b>${esc(artifact.platform?.os||'--')} / ${esc(artifact.platform?.arch||'--')}</b></div><div><span>运行状态</span><b>${esc(process.state||'未知')}</b></div><div><span>状态说明</span><b>${esc(kernelStatus.message||'尚未读取')}</b></div></div><p class="muted runtime-note">监听地址、控制密钥和稳定代理入口由插件管理，无需手工配置。</p></section>
      <section class="panel kernel-overview"><div class="bar"><div><small>第二栏 · ${installed}/${adapters.length} 已下载 · ${enabled}/${adapters.length} 已启用</small><h2>内核资源管理</h2></div><div class="actions"><button id="refresh-kernels">刷新资源状态</button><button id="check-all-kernels">检查全部更新</button></div></div><p class="muted">展开对应内核完成版本选择、下载、更新、卸载或离线安装。启用只代表允许运行，不会自动启动。</p><div class="kernel-resources">${adapters.map(item=>kernelCard(item,item.id===currentId&&process.state==='running')).join('')}</div></section>`
  } else {
    html=`<section class="panel"><h2>连接日志</h2>${state.events.slice().reverse().map(event=>`<div class="log">${esc(event.action)} · ${esc(event.result||'')}<small>${new Date(event.at*1000).toLocaleString()}</small></div>`).join('')||'<p class="muted">暂无事件。</p>'}</section>`
  }
  $('content').innerHTML=html; renderTaskBanner(); bind()
}

function subHtml(item){
  return `<div class="sub-card" data-i="${state.subscriptions.indexOf(item)}" data-id="${esc(item.id)}">
    <div class="table"><input data-k="name" value="${esc(item.name)}" aria-label="订阅名称"><input data-k="url" value="${esc(item.url)}" aria-label="订阅链接"><label><input type="checkbox" data-k="enabled" ${item.enabled?'checked':''}>启用</label><button data-del="subscriptions">删除</button></div>
    <div class="sub-meta"><span>${item.node_ids.length} 节点</span><span>${traffic(item)}</span><span>${expiry(item.expire)}</span><span>更新：${time(item.updated_at)}</span><span>下次：${nextRun(item)}</span><input class="interval" type="number" min="0" max="1440" data-k="interval" value="${item.interval}"><button data-refresh="${esc(item.id)}">刷新</button></div>
    ${item.last_error?`<div class="node-error">${esc(item.last_error)}（连续失败 ${item.consecutive_errors} 次）</div>`:''}
    ${item.last_diff?.at?`<details><summary>最近差异：新增 ${item.last_diff.added?.length||0}、变更 ${item.last_diff.changed?.length||0}、删除 ${item.last_diff.deleted?.length||0}、未变 ${item.last_diff.unchanged?.length||0}</summary><pre>${esc(JSON.stringify(item.last_diff,null,2))}</pre></details>`:''}
    ${item.errors?.length?`<details><summary>错误历史 ${item.errors.length}</summary>${item.errors.slice().reverse().map(error=>`<div class="node-error">${new Date(error.at*1000).toLocaleString()} · ${esc(error.message)}</div>`).join('')}</details>`:''}</div>`
}

function importTraffic(traffic){
  traffic=traffic||{}; const used=(Number(traffic.upload||0)+Number(traffic.download||0));
  if(!used&&!traffic.total)return '无流量信息';
  return traffic.total?`${bytes(used)} / ${bytes(traffic.total)}`:`已用 ${bytes(used)}`
}
function importNodeHtml(node){
  const support=node.support||{}, supported=support.status==='supported', statusLabel=supported?'已支持':support.status==='unsupported'?'不支持':'待验证';
  return `<div class="import-node-row"><div class="import-node-main"><b>${esc(node.name||'未命名节点')}</b><small>${esc(node.endpoint||'连接参数未返回')}</small></div><div class="import-node-tags"><span class="chip">${esc(node.protocol||'unknown')}</span><span class="chip">${esc(node.region||'其他')}</span><span class="chip ${supported?'ok':support.status==='unsupported'?'invalid':'pending'}">${statusLabel}</span>${node.suspected_notice?'<span class="chip pending">疑似提示</span>':''}</div></div>`
}
function importPreviewItemHtml(item,index){
  const summary=item.summary||{}, protocols=Object.entries(summary.protocols||{}), regions=Object.entries(summary.regions||{}), nodes=item.nodes||[], visible=nodes.slice(0,180), valid=Boolean(summary.ok);
  return `<article class="import-preview-card"><div class="import-preview-card-head"><div><small>来源 ${index+1} · ${esc(item.name||'未命名订阅')}</small><h3>${esc(item.url||'订阅连接')}</h3></div><span class="import-preview-status ${valid?'ok':'invalid'}">${valid?'可导入':'无法解析'}</span></div><div class="import-stats"><div><b>${summary.count||0}</b><span>节点</span></div><div><b>${protocols.length}</b><span>协议</span></div><div><b>${regions.length}</b><span>地区</span></div><div><b>${esc(importTraffic(summary.traffic))}</b><span>流量</span></div>${summary.traffic?.expire?`<div><b>${esc(expiry(summary.traffic.expire))}</b><span>到期</span></div>`:''}</div><div class="import-chip-groups"><div><small>协议</small><span>${protocols.map(([name,count])=>`<span class="chip">${esc(name)} · ${count}</span>`).join('')||'<em>未识别</em>'}</span></div><div><small>地区</small><span>${regions.map(([name,count])=>`<span class="chip">${esc(name)} · ${count}</span>`).join('')||'<em>其他</em>'}</span></div></div>${summary.error?`<p class="import-preview-error">${esc(summary.error)}</p>`:''}<div class="import-node-heading"><b>节点清单</b><span>${nodes.length}${nodes.length>180?'（展示前 180 个）':''}</span></div><div class="import-node-list">${visible.map(importNodeHtml).join('')||'<p class="muted">没有可展示的节点。</p>'}</div></article>`
}
function renderImportDialog(){
  const dialog=$('subscription-import-dialog'),body=$('subscription-import-body'),actions=$('subscription-import-actions'); if(!dialog||!body||!actions)return;
  const batch=importMode==='batch'; $('subscription-import-kicker').textContent=batch?'批量导入':'单条导入'; $('subscription-import-title').textContent=importPreview?(batch?'批量预览':'订阅预览'):(batch?'批量导入订阅':'导入订阅');
  if(!importPreview){
    body.innerHTML=batch?`<div class="import-dialog-intro"><b>一次预览多条订阅</b><p>每行填写“名称 | HTTP/HTTPS 链接”。预览会分别列出节点、协议、地区和流量信息。</p></div><label class="field-label" for="batch-sub-links">订阅名称与链接</label><textarea id="batch-sub-links" rows="7" required aria-required="true" placeholder="主力线路 | https://example.com/sub-a\n备用线路 | https://example.com/sub-b">${esc(importDraft.urls)}</textarea>`:`<div class="import-dialog-intro"><b>先看清节点，再确认导入</b><p>单条导入一次只处理一个订阅连接；名称用于节点来源筛选，且不能与已有订阅重复。</p></div><label class="field-label" for="import-name">订阅名称</label><input id="import-name" type="text" required aria-required="true" autocomplete="off" placeholder="例如：主力线路" value="${esc(importDraft.name)}"><label class="field-label" for="import-url">订阅链接</label><input id="import-url" type="url" required aria-required="true" inputmode="url" autocomplete="off" placeholder="https://example.com/sub" value="${esc(importDraft.url)}">`;
    body.innerHTML+=`<div class="import-options"><label class="field-label" for="import-interval">刷新间隔（分钟）<input id="import-interval" type="number" min="0" max="1440" value="${esc(importDraft.interval)}"><small>0 表示手动刷新</small></label></div><p class="import-dialog-note">预览只读取和解析订阅，不会写入配置；确认后才会导入，并自动发起一次节点测速。</p>`;
    actions.innerHTML=`<button id="import-dialog-cancel" type="button" class="quiet">取消</button><button id="import-dialog-preview" type="button" class="primary">${batch?'预览批量订阅':'预览订阅'}</button>`;
  }else{
    const items=importPreview.items||[], total=items.reduce((count,item)=>count+Number(item.summary?.count||0),0), valid=items.filter(item=>item.summary?.ok).length;
    body.innerHTML=`<div class="import-preview-summary"><div><b>${items.length}</b><span>订阅来源</span></div><div><b>${total}</b><span>解析节点</span></div><div><b>${valid}</b><span>可导入来源</span></div><p>确认后将写入配置；已导入节点会自动开始测速。</p></div><div class="import-preview-list">${items.map(importPreviewItemHtml).join('')}</div>`;
    actions.innerHTML=`<button id="import-dialog-back" type="button" class="quiet">返回修改</button><button id="import-dialog-confirm" type="button" class="primary" ${valid?'':'disabled'}>确认导入${valid?`（${valid} 条）`:''}</button>`;
  }
  $('import-dialog-cancel')?.addEventListener('click',closeImportDialog); $('import-dialog-preview')?.addEventListener('click',previewImport); $('import-dialog-back')?.addEventListener('click',()=>{importPreview=null;renderImportDialog()}); $('import-dialog-confirm')?.addEventListener('click',confirmImport)
}
function openImportDialog(mode='single'){
  importDialogReturnFocus=document.activeElement; importMode=mode; importPreview=null; importDraft={url:'',urls:'',name:'',interval:60}; renderImportDialog(); const dialog=$('subscription-import-dialog'); dialog?.showModal(); requestAnimationFrame(()=>$(mode==='batch'?'batch-sub-links':'import-name')?.focus())
}
function closeImportDialog(){
  importPreview=null; const dialog=$('subscription-import-dialog'); if(dialog?.open)dialog.close(); const target=importDialogReturnFocus; importDialogReturnFocus=null; target?.focus?.();
}

function nodeDialogError(message){
  const error=$('node-dialog-error'); if(!error)return
  error.textContent=message||''; error.hidden=!message
}
function nodeGroupNames(node){
  return state.groups.filter(group=>group.node_ids?.includes(node.id)).map(group=>group.name).join('、')||'未加入代理组'
}
function renderNodeDialog(){
  const node=state?.nodes?.find(item=>item.id===nodeDialogNodeId),body=$('proxy-node-body'),actions=$('proxy-node-actions');
  if(!node||!body||!actions)return
  const item=health(node.id),support=node.support||{status:'unverified',reason:'尚未验证'},subscription=node.subscription_id?(state.subscriptions.find(value=>value.id===node.subscription_id)?.name||'未命名订阅'):'手动节点'
  $('proxy-node-title').textContent=node.display_name||node.name||'节点详情'
  body.innerHTML=`<div id="node-dialog-error" class="node-dialog-error" role="alert" aria-live="assertive" hidden></div>
    <div class="node-detail-form"><label class="field-label" for="node-detail-name"><span class="field-title">节点名称</span><input id="node-detail-name" data-node-field="name" type="text" maxlength="120" value="${esc(node.display_name||node.name)}"><small>名称会显示在节点列表、代理组成员和运行状态中。</small></label>
      <label class="field-label" for="node-detail-endpoint"><span class="field-title">连接入口</span><input id="node-detail-endpoint" data-node-field="endpoint" type="text" value="${esc(node.endpoint)}" placeholder="完整连接 URI 或 HTTP/SOCKS 地址"><small>已配置凭据只显示为 [configured]，留空会由后端校验。</small></label></div>
    <label class="node-detail-excluded"><input id="node-detail-excluded" data-node-field="excluded" type="checkbox" ${node.excluded?'checked':''}><span><b>确认排除测速/组选优</b><small>排除后节点仍保留在配置中，但不会参与测速或自动组选优。</small></span></label>
    <div class="node-detail-section"><h3>节点信息</h3><div class="node-detail-facts"><div><span>协议</span><b>${esc(node.protocol||'unknown')}</b></div><div><span>执行内核</span><b>${esc(node.engine||node.executor||'未指定')}</b></div><div><span>支持状态</span><b class="chip ${support.status==='supported'?'ok':'pending'}">${esc({supported:'已支持',unverified:'未验证',unsupported:'不支持'}[support.status]||'未验证')}</b></div><div><span>地区</span><b>${esc(node.region||'其他')}</b></div><div><span>订阅来源</span><b>${esc(subscription)}</b></div><div><span>所属代理组</span><b>${esc(nodeGroupNames(node))}</b></div></div></div>
    <div class="node-detail-section"><h3>最近测速</h3><div class="node-detail-facts"><div><span>状态</span><b class="chip ${node.invalid_reference?'invalid':item.status}">${node.invalid_reference?'引用失效':esc(statusLabel(item))}</b></div><div><span>延迟</span><b>${item.latency_ms??'--'} ms</b></div><div><span>检查时间</span><b>${esc(time(item.checked_at))}</b></div></div>${support.reason?`<p class="node-dialog-note">${esc(support.reason)}</p>`:''}${node.notice_reason?`<p class="node-dialog-note">${esc(node.notice_reason)}</p>`:''}${item.error?`<p class="node-dialog-error node-dialog-error-inline" role="alert">${esc(item.error)}</p>`:''}</div>`
  actions.innerHTML='<button id="proxy-node-cancel" type="button" class="quiet">取消</button><button id="proxy-node-save" type="button" class="primary">保存节点</button>'
  bindNodeDialogFields()
  $('proxy-node-cancel')?.addEventListener('click',closeNodeDialog)
  $('proxy-node-save')?.addEventListener('click',saveNodeDialog)
}
function openNodeDialog(nodeId=''){
  const node=state.nodes.find(item=>item.id===nodeId); if(!node)return
  nodeDialogReturnFocus=document.activeElement; nodeDialogNodeId=nodeId; renderNodeDialog(); const dialog=$('proxy-node-dialog'); dialog?.showModal(); requestAnimationFrame(()=>$('node-detail-name')?.focus())
}
function closeNodeDialog({restore=true}={}){
  const dialog=$('proxy-node-dialog'); if(dialog?.open)dialog.close()
  const target=nodeDialogReturnFocus; nodeDialogReturnFocus=null; nodeDialogNodeId=''
  if(restore)requestAnimationFrame(()=>{if(target?.isConnected)target.focus()})
}
function saveNodeDialog(){
  const node=state.nodes.find(item=>item.id===nodeDialogNodeId),name=String($('node-detail-name')?.value||'').trim(),endpoint=String($('node-detail-endpoint')?.value||'').trim()
  if(!node)return
  if(!name){nodeDialogError('节点名称不能为空。');return}
  node.name=name; node.display_name=name; node.user_alias=name; node.endpoint=endpoint; node.excluded=Boolean($('node-detail-excluded')?.checked)
  const savedId=node.id; closeNodeDialog({restore:false}); render(); requestAnimationFrame(()=>document.querySelector(`[data-node-details="${CSS.escape(savedId)}"]`)?.focus()); note('节点详情已更新')
}

function ruleDraftFrom(source={}){
  const domains=Array.isArray(source.domains)?source.domains:[]
  return {id:source.id||`rules-${Date.now()}`,name:source.name||'新规则组',domains:domains.length?domains.map(item=>({host:item.host||'',match:item.match==='suffix'?'suffix':'exact'})):[{host:'example.com',match:'exact'}],priority:Number(source.priority||100),target:source.target||'direct',enabled:source.enabled!==false}
}
function ruleDomainsText(domains){return (domains||[]).map(item=>`${item.match||'exact'} ${item.host||''}`.trim()).join('\n')}
function ruleDialogError(message){const error=$('rule-group-dialog-error');if(!error)return;error.textContent=message||'';error.hidden=!message}
function renderRuleDialog(){
  const body=$('rule-group-body'),actions=$('rule-group-actions'); if(!body||!actions||!ruleDraft)return
  $('rule-group-title').textContent=editingRuleId?'编辑规则组':'新增规则组'
  body.innerHTML=`<div id="rule-group-dialog-error" class="rule-dialog-error" role="alert" aria-live="assertive" hidden></div>
    <div class="rule-form-grid"><label class="field-label" for="rule-name"><span class="field-title">规则组名称<span class="required-mark">必填</span></span><input id="rule-name" type="text" maxlength="80" autocomplete="off" value="${esc(ruleDraft.name)}"><small>名称用于识别规则组和审计记录。</small></label>
      <label class="field-label rule-enabled" for="rule-enabled"><input id="rule-enabled" type="checkbox" ${ruleDraft.enabled?'checked':''}><span><b>启用规则组</b><small>停用后不会写入运行内核规则。</small></span></label></div>
    <label class="field-label rule-domains-label" for="rule-domains"><span class="field-title">域名成员<span class="required-mark">至少一项</span></span><textarea id="rule-domains" rows="9" placeholder="每行：exact api.example.com 或 suffix example.com">${esc(ruleDomainsText(ruleDraft.domains))}</textarea><small>每行一条；匹配方式填写 exact 或 suffix。优先级可在规则列表中拖动排序。</small></label>`
  actions.innerHTML='<button id="rule-group-cancel" type="button" class="quiet">取消</button><button id="rule-group-save" type="button" class="primary">保存规则组</button>'
  $('rule-group-cancel')?.addEventListener('click',closeRuleDialog); $('rule-group-save')?.addEventListener('click',saveRuleDialog)
}
function openRuleDialog(ruleId=''){
  const source=ruleId?state.rule_groups.find(item=>item.id===ruleId):null
  ruleDialogReturnFocus=document.activeElement; editingRuleId=source?.id||''; ruleDraft=ruleDraftFrom(source||{}); renderRuleDialog()
  const dialog=$('rule-group-dialog'); dialog?.showModal(); requestAnimationFrame(()=>$('rule-name')?.focus())
}
function closeRuleDialog({restore=true}={}){
  const dialog=$('rule-group-dialog'); if(dialog?.open)dialog.close(); const target=ruleDialogReturnFocus; ruleDialogReturnFocus=null; ruleDraft=null; editingRuleId=''; if(restore)requestAnimationFrame(()=>{if(target?.isConnected)target.focus()})
}
function saveRuleDialog(){
  const name=String($('rule-name')?.value||'').trim(), enabled=Boolean($('rule-enabled')?.checked), domains=String($('rule-domains')?.value||'').split(/\r?\n/).map(line=>line.trim()).filter(Boolean).map(line=>{const parts=line.split(/\s+/,2);return {match:parts[0]==='suffix'?'suffix':'exact',host:parts[1]||parts[0]||''}}).filter(item=>item.host)
  if(!name)return ruleDialogError('规则组名称不能为空。')
  if(!domains.length)return ruleDialogError('请至少填写一条域名规则。')
  const isEditing=Boolean(editingRuleId),values={...ruleDraft,id:ruleDraft.id,name,enabled,domains}; const index=state.rule_groups.findIndex(item=>item.id===editingRuleId)
  if(index<0)state.rule_groups.push(values);else state.rule_groups[index]={...state.rule_groups[index],...values}
  const savedId=values.id; closeRuleDialog({restore:false}); render(); requestAnimationFrame(()=>document.querySelector(`[data-edit-rule="${CSS.escape(savedId)}"]`)?.focus()); note(isEditing?'规则组已更新':'规则组已创建')
}

function openConfirmDialog({title='确认操作',message='',confirmLabel='确认操作',danger=false,onConfirm}){
  const dialog=$('action-confirm-dialog'),body=$('action-confirm-body'),submit=$('action-confirm-submit'); if(!dialog||!body||!submit)return
  confirmDialogReturnFocus=document.activeElement; confirmAction=onConfirm; $('action-confirm-title').textContent=title; body.innerHTML=`<p>${esc(message)}</p>`; submit.textContent=confirmLabel; submit.className=danger?'danger':'primary'; dialog.showModal(); requestAnimationFrame(()=>submit.focus())
}
function closeConfirmDialog({restore=true}={}){
  const dialog=$('action-confirm-dialog'); if(dialog?.open)dialog.close(); const target=confirmDialogReturnFocus; confirmDialogReturnFocus=null; confirmAction=null; if(restore)requestAnimationFrame(()=>{if(target?.isConnected)target.focus()})
}

function groupDialogError(message){
  const error=$('group-dialog-error'); if(!error)return
  error.textContent=message||''; error.hidden=!message
}
function normalizeGroupName(value){ return String(value??'').trim().replace(/\s+/g,' ') }
function groupNameKey(value){ return normalizeGroupName(value).toLocaleLowerCase() }
function renderGroupDialog(){
  const dialog=$('proxy-group-dialog'),body=$('proxy-group-body'),actions=$('proxy-group-actions'); if(!dialog||!body||!actions||!groupDraft)return
  const mode=groupDraft.mode, automatic=mode==='url-test'||mode==='fallback', allNodes=state.nodes||[], filteredNodes=allNodes.filter(node=>groupNodeMatches(node,groupNodeQuery)), visibleIds=filteredNodes.map(node=>node.id), visibleSelectedCount=visibleIds.filter(id=>groupDraft.node_ids.includes(id)).length, strategy=groupStrategy(mode)
  $('proxy-group-title').textContent=editingGroupId?'编辑代理组':'新增代理组'
  body.innerHTML=`<div id="group-dialog-error" class="group-dialog-error" role="alert" aria-live="assertive" hidden></div>
    <div class="group-form-grid">
      <label class="field-label" for="group-name"><span class="field-title">代理组名称<span class="required-mark">必填</span></span><input id="group-name" data-group-field="name" type="text" required maxlength="80" autocomplete="off" value="${esc(groupDraft.name)}" placeholder="例如：Telegram 主线路"><small>名称用于规则目标和运行内核显示。</small></label>
      <label class="field-label" for="group-mode"><span class="field-title">选择模式</span><select id="group-mode" data-group-field="mode" aria-describedby="group-mode-help">${groupModes.map(item=>`<option value="${item.value}" ${item.value===mode?'selected':''}>${item.label}</option>`).join('')}</select><small id="group-mode-help">${esc(groupModes.find(item=>item.value===mode)?.help||'')}</small></label>
    </div>
    <fieldset class="group-form-section"><legend>节点成员</legend><div class="field-label"><span class="field-title">选择可用节点<span class="required-mark">至少一项</span></span><div class="group-node-toolbar"><label class="field-label group-node-search" for="group-node-search"><span class="field-title">搜索节点</span><input id="group-node-search" type="search" autocomplete="off" value="${esc(groupNodeQuery)}" placeholder="搜索订阅、节点、协议或地区" aria-controls="group-node-picker"></label><div class="group-node-actions" aria-label="节点批量选择"><button id="group-select-visible" type="button" ${filteredNodes.length?'':'disabled'}>全选</button><button id="group-clear-visible" type="button" ${visibleSelectedCount?'':'disabled'}>全不选</button></div></div><div class="group-node-summary" aria-live="polite">已选 ${groupDraft.node_ids.length} 个 · 显示 ${filteredNodes.length} 个${groupNodeQuery.trim()?` · 当前筛选已选 ${visibleSelectedCount} 个`:''}</div><div id="group-node-picker" class="node-picker" role="group" aria-describedby="group-node-help" aria-label="代理组节点成员">${filteredNodes.map(node=>`<button type="button" class="node-picker-option ${groupDraft.node_ids.includes(node.id)?'selected':''}" aria-pressed="${groupDraft.node_ids.includes(node.id)}" data-group-node="${esc(node.id)}"><span>${esc(nodeLabel(node))}</span><small>${esc(nodeDetails(node))}${node.invalid_reference?' · 引用失效':''}</small><span class="node-picker-speed"><b>${esc(nodeSpeed(node))}</b><small>${esc(statusLabel(health(node.id)))} · ${time(health(node.id).checked_at)}</small></span></button>`).join('')||(groupNodeQuery.trim()?'<span class="muted node-picker-empty">没有匹配节点，请修改关键词。</span>':'<span class="muted node-picker-empty">暂无节点，请先导入订阅</span>')}</div><small id="group-node-help">点击节点即可选中，再次点击取消；全选和全不选只作用于当前筛选结果。</small></div>${automatic?`<div class="group-strategy-summary" role="status"><span class="field-title">自动选择策略</span><b>${esc(strategy.title)}</b><small>${esc(strategy.detail)}</small></div>`:`<label class="field-label" for="group-selected"><span class="field-title">初始节点</span><select id="group-selected" data-group-field="selected" ${groupDraft.node_ids.length?'':'disabled'}><option value="">未指定（成员顺序首个可用）</option>${state.nodes.filter(node=>groupDraft.node_ids.includes(node.id)).map(node=>`<option value="${esc(node.id)}" ${node.id===groupDraft.selected?'selected':''}>${esc(nodeLabel(node))}</option>`).join('')}</select><small>${esc(strategy.detail)}</small></label>`}</fieldset>
    <fieldset class="group-form-section"><legend>自动策略参数</legend><div class="group-form-grid"><label class="field-label" for="group-test-url"><span class="field-title">测速目标</span><input id="group-test-url" data-group-field="test_url" type="url" ${automatic?'':'disabled'} value="${esc(groupDraft.test_url)}" placeholder="https://www.gstatic.com/generate_204"><small>仅自动模式使用 HTTP/HTTPS 目标。</small></label><label class="field-label" for="group-test-interval"><span class="field-title">测速周期（秒）</span><input id="group-test-interval" data-group-field="test_interval" type="number" min="30" max="86400" step="1" ${automatic?'':'disabled'} value="${esc(groupDraft.test_interval)}"><small>范围 30 至 86400 秒。</small></label><label class="field-label" for="group-tolerance"><span class="field-title">切换容差（毫秒）</span><input id="group-tolerance" data-group-field="tolerance" type="number" min="0" max="5000" step="1" ${automatic?'':'disabled'} value="${esc(groupDraft.tolerance)}"><small>延迟差异小于该值时不频繁切换。</small></label><label class="field-label" for="group-failure-policy"><span class="field-title">全部不可用时</span><select id="group-failure-policy" data-group-field="failure_policy" ${automatic?'':'disabled'}><option value="fail-closed" ${groupDraft.failure_policy==='fail-closed'?'selected':''}>失败关闭</option><option value="keep-last" ${groupDraft.failure_policy==='keep-last'?'selected':''}>保留最后选择</option></select><small>不会自动绕过代理改为直连。</small></label></div></fieldset>
    <label class="group-enabled"><input id="group-enabled" data-group-field="enabled" type="checkbox" ${groupDraft.enabled?'checked':''}>启用此代理组</label>`
  actions.innerHTML='<button id="proxy-group-cancel" type="button" class="quiet">取消</button><button id="proxy-group-save" type="button" class="primary">保存代理组</button>'
  groupDialogError('')
  document.querySelectorAll('[data-group-field]').forEach(input=>{
    const update=()=>{const key=input.dataset.groupField; groupDraft[key]=input.type==='checkbox'?input.checked:input.type==='number'?Number(input.value):input.value}
    input.addEventListener(input.tagName==='SELECT'?'change':'input',update)
    if(input.tagName==='SELECT'&&input.dataset.groupField!=='mode')input.addEventListener('change',update)
  })
  document.querySelectorAll('[data-group-node]').forEach(button=>button.addEventListener('click',()=>{
    const id=button.dataset.groupNode
    groupDraft.node_ids=groupDraft.node_ids.includes(id)?groupDraft.node_ids.filter(value=>value!==id):[...groupDraft.node_ids,id]
    if(groupDraft.selected&&!groupDraft.node_ids.includes(groupDraft.selected))groupDraft.selected=''
    renderGroupDialog(); requestAnimationFrame(()=>document.querySelector(`[data-group-node="${CSS.escape(id)}"]`)?.focus())
  }))
  $('group-node-search')?.addEventListener('input',event=>{groupNodeQuery=event.target.value;renderGroupDialog();requestAnimationFrame(()=>{const input=$('group-node-search');input?.focus();input?.setSelectionRange(groupNodeQuery.length,groupNodeQuery.length)})})
  $('group-select-visible')?.addEventListener('click',()=>{groupDraft.node_ids=[...new Set([...groupDraft.node_ids,...visibleIds])];renderGroupDialog();requestAnimationFrame(()=>$('group-select-visible')?.focus())})
  $('group-clear-visible')?.addEventListener('click',()=>{groupDraft.node_ids=groupDraft.node_ids.filter(id=>!visibleIds.includes(id));if(groupDraft.selected&&!groupDraft.node_ids.includes(groupDraft.selected))groupDraft.selected='';renderGroupDialog();requestAnimationFrame(()=>$('group-clear-visible')?.focus())})
  $('group-mode')?.addEventListener('change',event=>{groupDraft.mode=event.target.value;if(groupDraft.mode!=='select')groupDraft.selected='';renderGroupDialog();requestAnimationFrame(()=>$('group-mode')?.focus())})
  $('proxy-group-cancel')?.addEventListener('click',closeGroupDialog)
  $('proxy-group-save')?.addEventListener('click',saveGroupDialog)
}
function openGroupDialog(groupId=''){
  const source=groupId?state.groups.find(group=>group.id===groupId):null
  if(source?.id==='direct')return
  groupDialogReturnFocus=document.activeElement; editingGroupId=groupId||''; groupNodeQuery=''; groupDraft=groupDraftFrom(source); renderGroupDialog()
  const dialog=$('proxy-group-dialog'); dialog?.showModal(); requestAnimationFrame(()=>$('group-name')?.focus())
}
function closeGroupDialog({restore=true}={}){
  const dialog=$('proxy-group-dialog'); if(dialog?.open)dialog.close()
  const target=groupDialogReturnFocus; groupDialogReturnFocus=null; groupNodeQuery=''; groupDraft=null; editingGroupId=null
  if(restore)requestAnimationFrame(()=>{if(target?.isConnected)target.focus()})
}
function readGroupDialog(){
  if(!groupDraft)return null
  const values={...groupDraft}
  values.name=String($('group-name')?.value||'').trim(); values.mode=$('group-mode')?.value||'select'; values.node_ids=[...groupDraft.node_ids]; values.selected=values.mode==='select'?($('group-selected')?.value||''):''
  values.enabled=Boolean($('group-enabled')?.checked); values.test_url=String($('group-test-url')?.value||'').trim(); values.test_interval=Number($('group-test-interval')?.value||300); values.tolerance=Number($('group-tolerance')?.value||0); values.failure_policy=$('group-failure-policy')?.value||'fail-closed'
  return values
}
function saveGroupDialog(){
  const values=readGroupDialog(); if(!values)return
  const isEditing=Boolean(editingGroupId)
  values.name=normalizeGroupName(values.name); const nameKey=groupNameKey(values.name)
  if(!values.name)return groupDialogError('代理组名称不能为空。')
  if(state.groups.some(group=>group.id!==editingGroupId&&groupNameKey(group.name)===nameKey))return groupDialogError('代理组名称已存在，请换一个名称。')
  if(!values.node_ids.length)return groupDialogError('至少选择一个节点后才能保存代理组。')
  if(values.mode==='url-test'||values.mode==='fallback'){
    try{const url=new URL(values.test_url);if(!['http:','https:'].includes(url.protocol))throw new Error()}catch(_){return groupDialogError('自动模式的测速目标必须是 HTTP/HTTPS 地址。')}
    if(!Number.isInteger(values.test_interval)||values.test_interval<30||values.test_interval>86400)return groupDialogError('测速周期必须是 30 至 86400 秒的整数。')
    if(!Number.isInteger(values.tolerance)||values.tolerance<0||values.tolerance>5000)return groupDialogError('切换容差必须是 0 至 5000 毫秒的整数。')
  }
  const index=state.groups.findIndex(group=>group.id===editingGroupId)
  if(index<0)state.groups.push(values);else state.groups[index]={...state.groups[index],...values}
  const savedId=values.id; closeGroupDialog({restore:false}); render(); requestAnimationFrame(()=>{const trigger=[...document.querySelectorAll('[data-edit-group]')].find(button=>button.dataset.editGroup===savedId);(trigger||$('add'))?.focus()}); note(isEditing?'代理组已更新':'代理组已创建')
}

function bindNodeDialogFields(){
  document.querySelectorAll('[data-node-field]').forEach(input=>input.addEventListener('input',()=>{
    const node=state.nodes.find(item=>item.id===nodeDialogNodeId); if(!node)return
    if(input.dataset.nodeField==='name'){$('proxy-node-title').textContent=input.value.trim()||'节点详情';nodeDialogError('')}
  }))
}

function deleteConfigItem(kind,itemId){
  const list=state[kind],index=list?.findIndex(item=>item.id===itemId),item=index>=0?list[index]:null
  if(!item||item.id==='direct')return
  list.splice(index,1)
  if(kind==='groups'){
    state.routes=state.routes.filter(route=>route.target!==item.id)
    state.rule_groups=state.rule_groups.filter(rule=>rule.target!==item.id)
    state.platforms=Object.fromEntries(Object.entries(state.platforms||{}).filter(([,platform])=>platform.group_id!==item.id))
    render(); note('代理组已删除'); return
  }
  if(kind==='rule_groups'){
    if(!state.rule_groups.length)state.routes=[]
    render();note('规则组已删除');return
  }
  if(kind==='nodes'){
    const nodeId=item.id, subscription=item.subscription_id?state.subscriptions.find(value=>value.id===item.subscription_id):null
    selectedProbeNodeIds.delete(nodeId)
    if(subscription){subscription.ignored_node_ids=[...new Set([...(subscription.ignored_node_ids||[]),nodeId])];subscription.node_ids=(subscription.node_ids||[]).filter(value=>value!==nodeId)}
    state.health=Object.fromEntries(Object.entries(state.health||{}).filter(([id])=>id!==nodeId))
    const removedGroups=new Set()
    state.groups.forEach(group=>{group.node_ids=group.node_ids.filter(value=>value!==nodeId);if(group.selected===nodeId)group.selected='';if(group.id!=='direct'&&!group.node_ids.length)removedGroups.add(group.id)})
    state.groups=state.groups.filter(group=>!removedGroups.has(group.id)); state.routes=state.routes.filter(route=>!removedGroups.has(route.target)); state.rule_groups=state.rule_groups.filter(rule=>!removedGroups.has(rule.target)); state.platforms=Object.fromEntries(Object.entries(state.platforms||{}).filter(([,platform])=>!removedGroups.has(platform.group_id)))
    render(); note(subscription?'节点已删除，后续订阅刷新不会重新导入':'节点已删除'); return
  }
  if(kind==='subscriptions'){
    const owned=new Set(state.nodes.filter(node=>node.subscription_id===item.id).map(node=>node.id)); owned.forEach(nodeId=>selectedProbeNodeIds.delete(nodeId)); state.nodes=state.nodes.filter(node=>!owned.has(node.id)); state.health=Object.fromEntries(Object.entries(state.health||{}).filter(([nodeId])=>!owned.has(nodeId)))
    const removedGroups=new Set(); state.groups.forEach(group=>{group.node_ids=group.node_ids.filter(nodeId=>!owned.has(nodeId));if(group.selected&&!group.node_ids.includes(group.selected))group.selected='';if(group.id!=='direct'&&!group.node_ids.length)removedGroups.add(group.id)})
    state.groups=state.groups.filter(group=>!removedGroups.has(group.id)); state.routes=state.routes.filter(route=>!removedGroups.has(route.target)); state.rule_groups=state.rule_groups.filter(rule=>!removedGroups.has(rule.target)); state.platforms=Object.fromEntries(Object.entries(state.platforms||{}).filter(([,platform])=>!removedGroups.has(platform.group_id)))
    render(); note('订阅已删除，同时清理了其节点和失效引用')
  }
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
  document.querySelectorAll('[data-node-details]').forEach(button=>button.addEventListener('click',()=>openNodeDialog(button.dataset.nodeDetails)))
  document.querySelectorAll('[data-group-accordion]').forEach(details=>details.addEventListener('toggle',()=>{const id=details.dataset.groupAccordion;if(details.open)openGroupIds.add(id);else openGroupIds.delete(id)}))
  document.querySelectorAll('[data-del]').forEach(button=>button.addEventListener('click',event=>{
    event.preventDefault(); event.stopPropagation()
    const kind=button.dataset.del,list=state[kind],row=button.closest('[data-i],[data-group-id],[data-rule-id]'),item=kind==='groups'?state.groups.find(value=>value.id===row?.dataset.groupId):list?.find(value=>value.id===row?.dataset.ruleId)||list?.[Number(row?.dataset.i)]
    if(!item||item.id==='direct')return
    const labels={nodes:'节点',subscriptions:'订阅',groups:'代理组',rule_groups:'规则组'}
    openConfirmDialog({title:`删除${labels[kind]||'配置'}`,message:`确定删除“${item.display_name||item.name||item.id}”吗？删除会在保存配置后生效。`,confirmLabel:'确认删除',danger:true,onConfirm:()=>deleteConfigItem(kind,item.id)})
  }))
  $('add')?.addEventListener('click',()=>{
    if(tab==='nodes')state.nodes.push({id:'node-'+Date.now(),name:'新节点',display_name:'新节点',protocol:'http',engine:'direct-http',kind:'http',endpoint:'',connection:{},subscription_id:'',enabled:true,excluded:false,exclusion_reason:''})
    else if(tab==='groups'){openGroupDialog();return}
    else {openRuleDialog();return}
    render(); if(tab==='nodes')requestAnimationFrame(()=>$('content')?.querySelector('[data-node-details]')?.focus())
  })
  document.querySelectorAll('[data-edit-group]').forEach(button=>button.addEventListener('click',()=>openGroupDialog(button.dataset.editGroup)))
  document.querySelectorAll('[data-edit-rule]').forEach(button=>button.addEventListener('click',()=>openRuleDialog(button.dataset.editRule)))
  document.querySelectorAll('[data-rule-target]').forEach(select=>select.addEventListener('change',()=>{
    if(setRuleTarget(select.dataset.ruleTarget,select.value))note(`目标代理组已更新：${state.groups.find(group=>group.id===select.value)?.name||'直连'}`)
  }))
  document.querySelectorAll('[data-rule-move]').forEach(button=>button.addEventListener('click',()=>{
    const rules=orderedRuleGroups(),index=rules.findIndex(rule=>rule.id===button.dataset.ruleId),offset=button.dataset.ruleMove==='up'?-1:1,target=rules[index+offset]
    if(target)moveRuleGroup(button.dataset.ruleId,target.id,offset>0)
  }))
  document.querySelectorAll('[data-rule-drag-handle]').forEach(handle=>{
    handle.addEventListener('dragstart',event=>{event.dataTransfer?.setData('text/plain',handle.dataset.ruleDragHandle);if(event.dataTransfer)event.dataTransfer.effectAllowed='move';handle.closest('[data-rule-id]')?.classList.add('dragging')})
    handle.addEventListener('dragend',()=>document.querySelectorAll('.rule-card.dragging,.rule-card.drag-over').forEach(card=>card.classList.remove('dragging','drag-over')))
  })
  document.querySelectorAll('.rule-card[data-rule-id]').forEach(card=>{
    card.addEventListener('dragover',event=>{if(!document.querySelector('.rule-card.dragging'))return;event.preventDefault();card.classList.add('drag-over')})
    card.addEventListener('dragleave',event=>{if(!card.contains(event.relatedTarget))card.classList.remove('drag-over')})
    card.addEventListener('drop',event=>{event.preventDefault();card.classList.remove('drag-over');const sourceId=event.dataTransfer?.getData('text/plain');if(!sourceId)return;const bounds=card.getBoundingClientRect();moveRuleGroup(sourceId,card.dataset.ruleId,event.clientY>bounds.top+bounds.height/2)})
  })
  $('add-subscription')?.addEventListener('click',()=>{state.subscriptions.push({id:'sub-'+Date.now(),name:'新订阅',url:'',enabled:true,interval:60,node_ids:[],updated_at:0,next_refresh_at:0,upload:0,download:0,total:0,expire:0,last_error:'',consecutive_errors:0,errors:[]});render()})
  $('open-single-import')?.addEventListener('click',()=>openImportDialog('single'))
  $('open-batch-import')?.addEventListener('click',()=>openImportDialog('batch'))
  document.querySelectorAll('[data-refresh]').forEach(button=>button.addEventListener('click',()=>refreshSubscription(button.dataset.refresh)))
  document.querySelectorAll('[data-test]').forEach(button=>button.addEventListener('click',()=>startProbe([button.dataset.test])))
  document.querySelectorAll('[data-probe-select]').forEach(input=>input.addEventListener('change',()=>{const id=input.dataset.probeSelect;if(input.checked)selectedProbeNodeIds.add(id);else selectedProbeNodeIds.delete(id);render();requestAnimationFrame(()=>[...document.querySelectorAll('[data-probe-select]')].find(item=>item.dataset.probeSelect===id)?.focus())}))
  $('select-visible-nodes')?.addEventListener('click',()=>{document.querySelectorAll('[data-probe-select]').forEach(input=>selectedProbeNodeIds.add(input.dataset.probeSelect));render();requestAnimationFrame(()=>$('select-visible-nodes')?.focus())})
  $('clear-visible-nodes')?.addEventListener('click',()=>{document.querySelectorAll('[data-probe-select]').forEach(input=>selectedProbeNodeIds.delete(input.dataset.probeSelect));render();requestAnimationFrame(()=>$('clear-visible-nodes')?.focus())})
  $('test-selected')?.addEventListener('click',()=>startProbe(state.nodes.filter(node=>selectedProbeNodeIds.has(node.id)).map(node=>node.id),'所选节点测速'))
  $('test-all')?.addEventListener('click',()=>startProbe(state.nodes.map(node=>node.id),'全部节点测速'))
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
  document.querySelectorAll('[data-group-probe]').forEach(button=>button.addEventListener('click',()=>runGroupProbe(button.dataset.groupProbe)))
  $('refresh-kernels')?.addEventListener('click',async()=>{await load();if(tab==='control')note('内核资源状态已刷新')})
  $('check-all-kernels')?.addEventListener('click',checkAllKernelUpdates)
  document.querySelectorAll('[data-kernel-check]').forEach(button=>button.addEventListener('click',()=>checkKernelUpdate(button.dataset.kernelCheck)))
  document.querySelectorAll('[data-kernel-enable]').forEach(button=>button.addEventListener('click',()=>toggleKernel(button.dataset.kernelEnable,button.dataset.enabled==='true')))
  document.querySelectorAll('[data-kernel-select]').forEach(button=>button.addEventListener('click',()=>selectKernel(button.dataset.kernelSelect)))
  $('adapter-switch')?.addEventListener('click',()=>{const selected=$('adapter-select').value;openConfirmDialog({title:'切换运行内核',message:`切换到 ${selected} 将停止当前内核，并要求重新安装和应用配置。`,confirmLabel:'确认切换',onConfirm:async()=>{try{await api.apiPost('adapter-select',{adapter:selected});await load();note(`已切换到 ${selected}`)}catch(error){note(error.message,true)}}})})
  document.querySelectorAll('.kernel-resource').forEach(details=>details.addEventListener('toggle',()=>{const adapter=details.dataset.kernelCard;if(details.open)openKernelResources.add(adapter);else openKernelResources.delete(adapter)}))
  document.querySelectorAll('[data-kernel-version]').forEach(select=>select.addEventListener('change',()=>{const item=state.adapters?.find(value=>value.id===select.dataset.kernelVersion);if(item){item.artifact.selected_version=select.value;render()}}))
  document.querySelectorAll('[data-kernel-install]').forEach(button=>button.addEventListener('click',()=>{const select=button.closest('[data-kernel-card]')?.querySelector('[data-kernel-version]');startKernelInstall(button.dataset.kernelInstall,select?.value||button.dataset.version)}))
  document.querySelectorAll('[data-kernel-cancel]').forEach(button=>button.addEventListener('click',()=>cancelKernelInstall(button.dataset.kernelCancel)))
  document.querySelectorAll('[data-kernel-uninstall]').forEach(button=>button.addEventListener('click',()=>uninstallKernel(button.dataset.kernelUninstall)))
  $('kernel-start')?.addEventListener('click',()=>kernelAction('kernel-start','正在启动内核...'))
  $('kernel-stop')?.addEventListener('click',()=>kernelAction('kernel-stop','正在停止内核...'))
  document.querySelectorAll('[data-kernel-upload]').forEach(button=>button.addEventListener('click',()=>uploadKernel(button.dataset.kernelUpload,button.dataset.version)))
  $('runtime-apply')?.addEventListener('click',applyConfiguration)
  document.querySelectorAll('[data-select]').forEach(select=>select.addEventListener('change',async()=>{try{await api.apiPost('control-select',{group_id:select.dataset.select,node_id:select.value});await checkControl();note('代理组已切换')}catch(error){note(error.message,true)}}))
}

const changeSectionDefinitions=[
  {type:'subscriptions',label:'订阅'},
  {type:'nodes',label:'代理节点'},
  {type:'groups',label:'代理组'},
  {type:'routes',label:'分流规则'},
  {type:'rule_groups',label:'规则组'},
  {type:'platforms',label:'平台绑定'},
]
const changeFieldsByType={
  subscriptions:['id','name','url','enabled','interval','node_ids','ignored_node_ids'],
  nodes:['id','display_name','user_alias','protocol','region','endpoint','connection','subscription_id','enabled','excluded','exclusion_reason','invalid_reference'],
  groups:['id','name','mode','node_ids','selected','enabled','test_url','test_interval','tolerance','failure_policy'],
  routes:['id','host','match','target','priority','enabled'],
  rule_groups:['id','name','domains','priority','target','enabled'],
  platforms:['id','name','group_id','enabled'],
}
const changeFieldLabels={
  id:'标识',name:'名称',url:'订阅链接',enabled:'启用状态',interval:'刷新间隔',node_ids:'节点成员',ignored_node_ids:'已排除节点',
  display_name:'节点名称',user_alias:'自定义名称',protocol:'协议',region:'地区',endpoint:'连接参数',connection:'连接参数',subscription_id:'所属订阅',
  excluded:'测速与组选优',exclusion_reason:'排除原因',invalid_reference:'引用状态',mode:'选择模式',selected:'初始节点',test_url:'测速目标',
  test_interval:'测速周期',tolerance:'切换容差',failure_policy:'故障策略',host:'匹配域名',match:'匹配方式',target:'目标代理组',priority:'优先级',domains:'域名成员',group_id:'代理组',
}
function changeRecords(type,snapshot){
  if(type==='platforms')return Object.entries(snapshot?.platforms||{}).filter(([,item])=>item&&typeof item==='object').map(([id,item])=>({...item,id}))
  return Array.isArray(snapshot?.[type])?snapshot[type].filter(item=>item&&item.id).map(item=>item):[]
}
function canonicalChangeValue(value){
  if(Array.isArray(value))return value.map(canonicalChangeValue)
  if(value&&typeof value==='object')return Object.fromEntries(Object.keys(value).sort().map(key=>[key,canonicalChangeValue(value[key])]))
  return value
}
function changeComparable(type,item){
  const source=item||{}, fields=changeFieldsByType[type]||['id']
  return Object.fromEntries(fields.map(field=>[field,canonicalChangeValue(source[field]??null)]))
}
function changeSignature(type,item){return JSON.stringify(changeComparable(type,item))}
function snapshotSubscriptionName(snapshot,id){return snapshot?.subscriptions?.find(item=>item.id===id)?.name||'未命名订阅'}
function snapshotGroupName(snapshot,id){return snapshot?.groups?.find(item=>item.id===id)?.name||'未命名代理组'}
function changeTitle(type,item,snapshot){
  if(type==='nodes')return `${snapshotSubscriptionName(snapshot,item.subscription_id)} · ${item.display_name||item.name||item.id}`
  if(type==='subscriptions')return item.name||'未命名订阅'
  if(type==='groups')return item.name||'未命名代理组'
  if(type==='routes')return item.host||'未命名规则'
  if(type==='rule_groups')return item.name||'未命名规则组'
  return item.name||item.id||'未命名平台'
}
function changeMeta(type,item,snapshot){
  if(type==='subscriptions')return `${Array.isArray(item.node_ids)?item.node_ids.length:0} 个节点 · ${item.interval?'每 '+item.interval+' 分钟':'手动刷新'}`
  if(type==='nodes')return `${item.protocol||'unknown'} · ${item.region||'其他'} · ${item.enabled===false?'已停用':'已启用'}`
  if(type==='groups')return `${groupModeLabels[item.mode]||item.mode||'手动选择'} · ${Array.isArray(item.node_ids)?item.node_ids.length:0} 个节点`
  if(type==='routes')return `${item.match==='suffix'?'后缀匹配':'精确匹配'} · ${snapshotGroupName(snapshot,item.target)} · 优先级 ${item.priority??'--'}`
  if(type==='rule_groups')return `${Array.isArray(item.domains)?item.domains.length:0} 个域名 · ${snapshotGroupName(snapshot,item.target)} · 优先级 ${item.priority??'--'}`
  return `${snapshotGroupName(snapshot,item.group_id)} · ${item.enabled===false?'已停用':'已启用'}`
}
function changeDisplayValue(type,key,value,snapshot){
  if(key==='enabled')return value?'已启用':'已停用'
  if(key==='interval')return Number(value)?`每 ${value} 分钟`:'手动刷新'
  if(key==='node_ids')return `${Array.isArray(value)?value.length:0} 个节点`
  if(key==='ignored_node_ids')return `${Array.isArray(value)?value.length:0} 个已排除节点`
  if(key==='domains')return `${Array.isArray(value)?value.length:0} 个域名`
  if(key==='excluded')return value?'排除测速/组选优':'参与测速/组选优'
  if(key==='invalid_reference')return value?'引用失效':'引用有效'
  if(key==='url'||key==='endpoint'||key==='connection'||key==='test_url')return value?'已配置':'未配置'
  if(key==='subscription_id')return value?snapshotSubscriptionName(snapshot,value):'手动节点'
  if(key==='target'||key==='group_id')return value?snapshotGroupName(snapshot,value):'未指定'
  if(key==='mode')return groupModeLabels[value]||value||'未指定'
  if(key==='failure_policy')return value==='keep-last'?'保留最后选择':'失败关闭'
  if(key==='match')return value==='suffix'?'后缀匹配':'精确匹配'
  if(key==='selected')return value?'已指定节点':'未指定'
  if(value===null||value===undefined||value==='')return '未设置'
  if(typeof value==='object')return Array.isArray(value)?`${value.length} 项`:`${Object.keys(value).length} 项`
  return String(value)
}
function changeFields(type,before,after){
  return (changeFieldsByType[type]||[]).filter(key=>JSON.stringify(canonicalChangeValue(before?.[key]??null))!==JSON.stringify(canonicalChangeValue(after?.[key]??null)))
}
function changeEntryValues(type,entry){
  if(entry.kind==='changed')return `<div class="change-entry-values">${changeFields(type,entry.before,entry.after).map(key=>`<div class="change-value-row"><span class="change-value-label">${esc(changeFieldLabels[key]||key)}</span><span class="change-before">${esc(changeDisplayValue(type,key,entry.before?.[key],entry.beforeSnapshot))}</span><span class="change-arrow" aria-hidden="true">→</span><span class="change-after">${esc(changeDisplayValue(type,key,entry.after?.[key],entry.afterSnapshot))}</span></div>`).join('')}</div>`
  const item=entry.kind==='added'?entry.after:entry.before, snapshot=entry.kind==='added'?entry.afterSnapshot:entry.beforeSnapshot
  return `<div class="change-entry-values"><div class="change-value-row"><span class="change-value-label">${entry.kind==='added'?'当前配置':'原配置'}</span><span class="${entry.kind==='added'?'change-after':'change-before'}">${esc(changeMeta(type,item,snapshot))}</span></div></div>`
}
function collectChangeSection(def,before,after){
  const previous=new Map(changeRecords(def.type,before).map(item=>[item.id,item])), current=new Map(changeRecords(def.type,after).map(item=>[item.id,item])), ids=new Set([...previous.keys(),...current.keys()]), entries=[], counts={added:0,deleted:0,changed:0,unchanged:0}
  ids.forEach(id=>{
    const oldItem=previous.get(id), newItem=current.get(id)
    if(!oldItem){counts.added++;entries.push({kind:'added',id,after:newItem,afterSnapshot:after})}
    else if(!newItem){counts.deleted++;entries.push({kind:'deleted',id,before:oldItem,beforeSnapshot:before})}
    else if(changeSignature(def.type,oldItem)!==changeSignature(def.type,newItem)){counts.changed++;entries.push({kind:'changed',id,before:oldItem,after:newItem,beforeSnapshot:before,afterSnapshot:after})}
    else counts.unchanged++
  })
  return {...def,entries,counts}
}
function renderChangePreview(before,after){
  const sections=changeSectionDefinitions.map(def=>collectChangeSection(def,before||{},after||{})), totals=sections.reduce((result,section)=>{for(const key of Object.keys(result))result[key]+=section.counts[key];return result},{added:0,deleted:0,changed:0,unchanged:0}), changedSections=sections.filter(section=>section.entries.length)
  if(!totals.added&&!totals.deleted&&!totals.changed)return `<div class="change-empty"><b>本轮没有检测到配置变化</b><span>当前配置与上次保存内容一致。</span></div>`
  return `<div class="change-overview"><div class="change-stat added"><b>${totals.added}</b><span>新增</span></div><div class="change-stat deleted"><b>${totals.deleted}</b><span>删除</span></div><div class="change-stat changed"><b>${totals.changed}</b><span>修改</span></div><div class="change-stat unchanged"><b>${totals.unchanged}</b><span>未变</span></div></div><p class="change-preview-note">以下内容是本轮待保存的配置摘要，连接凭据和完整订阅地址不会显示。</p>${changedSections.map(section=>`<section class="change-section"><div class="change-section-head"><h3>${esc(section.label)}</h3><span>${section.entries.length} 项变化</span></div><div class="change-entry-list">${section.entries.map(entry=>`<article class="change-entry ${entry.kind}"><div class="change-entry-head"><div><span class="change-kind">${entry.kind==='added'?'新增':entry.kind==='deleted'?'删除':'修改'}</span><b>${esc(changeTitle(section.type,entry.kind==='deleted'?entry.before:entry.after,entry.kind==='deleted'?entry.beforeSnapshot:entry.afterSnapshot))}</b></div><small>${esc(changeMeta(section.type,entry.kind==='deleted'?entry.before:entry.after,entry.kind==='deleted'?entry.beforeSnapshot:entry.afterSnapshot))}</small></div>${changeEntryValues(section.type,entry)}</article>`).join('')}</div></section>`).join('')}`
}
function readControl(){}
async function saveChanges(){ readControl(); state=await api.apiPost('save',state); original=structuredClone(state) }
async function applyConfiguration(){
  let stage='保存'
  try{
    await saveChanges()
    stage='应用'
    await api.apiPost('runtime-apply',{})
    stage='刷新状态'
    await load()
    if(tab==='groups')controlResult=await api.apiGet('control-status')
    render(); note('配置已保存、应用并完成运行状态核对')
  }catch(error){
    try{await load()}catch{}
    render()
    const message=stage==='保存'?`配置保存失败：${error.message}`:stage==='应用'?`配置已保存，但应用失败：${error.message}`:`配置已成功应用，但状态刷新失败：${error.message}`
    note(message,true)
  }
}
async function previewImport(){
  const batch=importMode==='batch', raw=batch?$('batch-sub-links')?.value||'':$('import-url')?.value||'', interval=Number($('import-interval')?.value||0); let items=[]
  if(batch){
    items=raw.split(/\r?\n/).map(line=>line.trim()).filter(Boolean).map(line=>{const separator=line.indexOf('|');return separator<0?{name:'',url:line}:{name:line.slice(0,separator).trim(),url:line.slice(separator+1).trim()}})
    if(!items.length)return note('请先输入订阅名称和链接',true)
    if(items.some(item=>!item.name||!item.url))return note('批量导入每行都要填写“名称 | 链接”',true)
  }else{
    const name=$('import-name')?.value||'', url=raw.trim(); if(!name.trim())return note('请先填写订阅名称',true); if(!url)return note('请先输入订阅链接',true); items=[{name:name.trim(),url}]
  }
  importDraft={url:batch?'':items[0]?.url||'',urls:batch?raw:'',name:batch?'':items[0]?.name||'',interval}
  const button=$('import-dialog-preview'); if(button)button.disabled=true; note(batch?'正在预览批量订阅...':'正在预览订阅...')
  try{ importPreview=await api.apiPost('subscription-preview',batch?{mode:importMode,items,interval:importDraft.interval}:{mode:importMode,name:importDraft.name,urls:[items[0].url],interval:importDraft.interval});renderImportDialog();note('预览完成，请核对节点后确认导入') }catch(error){note(error.message,true)}finally{if(button)button.disabled=false}
}
async function confirmImport(){
  if(!importPreview||importing)return; importing=true
  try{ const result=await api.apiPost('subscription-import',{preview_id:importPreview.preview_id}), importedNodeIds=result.imported_node_ids||[], snapshot=structuredClone(result.snapshot||result); delete snapshot.imported_node_ids; state=snapshot; original=structuredClone(state); closeImportDialog(); render(); note(importedNodeIds.length?'订阅导入成功，正在测速...':'订阅导入成功，未发现可测速节点'); if(importedNodeIds.length){let probeIds=importedNodeIds;if(requiresKernelProbe(importedNodeIds)){try{note('正在应用内核配置以准备测速...');await api.apiPost('runtime-apply',{});await load()}catch(error){probeIds=importedNodeIds.filter(id=>!requiresKernelProbe([id]));note('内核配置未应用，已跳过原生节点测速：'+error.message,true)}} if(probeIds.length)await startProbe(probeIds,'导入后测速');else note('订阅已导入，但原生节点等待内核配置后再测速',true)} }catch(error){note(error.message,true)}finally{importing=false}
}
async function refreshSubscription(id){
  try{ note('正在刷新订阅...'); const result=await api.apiPost('subscription-refresh',{id});state=result.snapshot;original=structuredClone(state);render();const d=result.result.diff;note(`订阅刷新成功：新增 ${d.added.length}、变更 ${d.changed.length}、删除 ${d.deleted.length}、未变 ${d.unchanged.length}`) }catch(error){note(error.message,true)}
}
async function checkControl(){
  try{ kernelStatus=await api.apiGet('kernel-status');controlResult=kernelStatus.ready?await api.apiGet('control-status'):null;render();note(kernelStatus.message,!kernelStatus.ready) }catch(error){note(error.message,true)}
}
async function refreshGroupStatus(){try{controlResult=await api.apiGet('control-status');render();note('代理组运行状态已刷新')}catch(error){controlResult=null;render();note(error.message,true)}}
async function runGroupProbe(groupId){
  if(groupProbeRunning.has(groupId))return
  groupProbeRunning.add(groupId);delete groupProbeErrors[groupId];render()
  try{await api.apiPost('control-group-probe',{group_id:groupId,timeout:8});controlResult=await api.apiGet('control-status');note('代理组测速完成')}
  catch(error){groupProbeErrors[groupId]=error.message;note(error.message,true)}
  finally{groupProbeRunning.delete(groupId);render()}
}
function formatBytes(value){if(!value)return '0 B';const units=['B','KiB','MiB','GiB'];let size=value,index=0;while(size>=1024&&index<units.length-1){size/=1024;index++}return `${size.toFixed(index?1:0)} ${units[index]}`}
async function startKernelInstall(adapter=state.control?.adapter||kernelStatus.adapter,version=''){try{const payload={adapter};if(version)payload.version=version;const install=await api.apiPost('kernel-install',payload);if(adapter===state.control?.adapter||adapter===kernelStatus.adapter)kernelStatus.install=install;const item=state.adapters?.find(value=>value.id===adapter);if(item)item.install=install;render();note((version?'内核更新':'内核安装')+'任务已创建');pollKernelInstall(adapter)}catch(error){note(error.message,true)}}
async function cancelKernelInstall(adapter=state.control?.adapter||kernelStatus.adapter){try{const result=await api.apiPost('kernel-install-cancel',{adapter});if(adapter===state.control?.adapter||adapter===kernelStatus.adapter)kernelStatus.install=result;const item=state.adapters?.find(value=>value.id===adapter);if(item)item.install=result;render();note('在线安装已取消')}catch(error){note(error.message,true)}}
function uninstallKernel(adapter){openConfirmDialog({title:'卸载内核资源',message:`确定卸载 ${adapter} 内核资源？已启用但未运行的内核会同时停用。`,confirmLabel:'确认卸载',danger:true,onConfirm:async()=>{try{const task=await api.apiPost('kernel-uninstall',{adapter});const item=state.adapters?.find(value=>value.id===adapter);if(item)item.install=task;render();pollKernelInstall(adapter)}catch(error){note(error.message,true)}}})}
async function checkKernelUpdate(adapter){try{note('正在检查 '+adapter+' 更新...');await api.apiPost('kernel-update-check',{adapter});await load();note(adapter+' 更新检查完成')}catch(error){note(error.message,true)}}
async function checkAllKernelUpdates(){try{note('正在检查全部内核更新...');await api.apiPost('kernel-update-check-all',{});await load();note('全部内核更新检查完成')}catch(error){note(error.message,true)}}
async function toggleKernel(adapter,enabled){try{await api.apiPost('core-enable',{adapter,enabled});await load();note(enabled?'内核已启用':'内核已停用')}catch(error){note(error.message,true)}}
async function selectKernel(adapter){try{await api.apiPost('adapter-select',{adapter});await load();note('已切换到 '+adapter)}catch(error){note(error.message,true)}}
async function pollKernelInstall(adapter=state.control?.adapter||kernelStatus.adapter){clearTimeout(resourcePollTimers.get(adapter));try{const install=await api.apiPost('kernel-install-status',{adapter});if(adapter===state.control?.adapter||adapter===kernelStatus.adapter)kernelStatus.install=install;const item=state.adapters?.find(value=>value.id===adapter);if(item)item.install=install;if(install.state==='running'){render();resourcePollTimers.set(adapter,setTimeout(()=>pollKernelInstall(adapter),700))}else{resourcePollTimers.delete(adapter);await load();note(install.message,install.state!=='completed'&&install.state!=='cancelled')}}catch(error){resourcePollTimers.delete(adapter);note(error.message,true)}}
async function kernelAction(route,message){try{note(message);await api.apiPost(route,{});await load();note('内核状态已更新')}catch(error){note(error.message,true)}}
async function uploadKernel(adapter,version){const file=document.querySelector(`[data-kernel-file="${CSS.escape(adapter)}"]`)?.files?.[0];if(!file)return note('请选择与当前平台匹配的固定版本制品',true);if(file.size>64*1024*1024)return note('制品超过 64 MiB 限制',true);const item=state.adapters?.find(value=>value.id===adapter);if(item)item.install={state:'running',operation:'install',phase:'uploading',progress:10,message:'正在上传离线制品'};openKernelResources.add(adapter);render();try{note('正在校验离线制品...');const content=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',',2)[1]);reader.onerror=reject;reader.readAsDataURL(file)});await api.apiPost('kernel-upload',{adapter,version,content});await load();note('离线制品已校验并安装；请在资源栏中启用')}catch(error){await load();note(error.message,true)}}
async function startProbe(nodeIds,label='测速'){
  nodeIds=[...new Set((nodeIds||[]).filter(Boolean))]; if(!nodeIds.length)return note('没有可测速的节点',true)
  try{const started=await api.apiPost('probe-task',{node_ids:nodeIds,timeout:5,concurrency:5});probeLabel=label;probeTask={id:started.task_id,total:started.total,completed:0,status:'running'};render();note(label+'任务已开始');pollProbe()}catch(error){note(error.message,true)}
}
async function pollProbe(){
  if(!probeTask)return
  try{const task=await api.apiPost('probe-task-status',{task_id:probeTask.id});probeTask=task;for(const result of task.results)if(result.health)state.health[result.node_id]=result.health;render();if(task.status==='running')setTimeout(pollProbe,500);else note(`${probeLabel}完成：${task.summary.ok} 可用，${task.summary.error+task.summary.timeout} 失败，${task.summary.skipped} 未执行，${task.summary.cancelled} 已取消`)}catch(error){note(error.message,true)}
}
async function load(){ try{ state=await api.apiGet('state');selectedProbeNodeIds=new Set([...selectedProbeNodeIds].filter(id=>state.nodes.some(node=>node.id===id)));kernelStatus=await api.apiGet('kernel-status');original=structuredClone(state);controlResult=null;importPreview=null;if($('subscription-import-dialog')?.open)$('subscription-import-dialog').close();render();for(const item of (state.adapters||[]))if(item.install?.state==='running')pollKernelInstall(item.id) }catch(error){note(error.message,true)} }

document.querySelectorAll('[data-tab]').forEach(button=>button.addEventListener('click',()=>{tab=button.dataset.tab;render();if(tab==='groups')refreshGroupStatus()}))
  $('reload').addEventListener('click',load)
  $('rollback').addEventListener('click',async()=>{try{state=await api.apiPost('rollback',{});original=structuredClone(state);controlResult=null;importPreview=null;render();note('已恢复上一版配置')}catch(error){note(error.message,true)}})
  $('save').addEventListener('click',()=>{
    const dialog=$('diff'),content=$('diff-content')
    try{
      readControl()
      content.innerHTML=renderChangePreview(original||{},state||{})
      if(!dialog.open)dialog.showModal()
    }catch(error){
      content.innerHTML='<div class="change-empty"><b>无法生成配置预览</b><span>请刷新页面后重试。</span></div>'
      note(error?.message||'配置预览生成失败',true)
      if(!dialog.open)dialog.showModal()
    }
  })
  $('cancel').addEventListener('click',()=>$('diff').close())
  $('save-only').addEventListener('click',async()=>{try{await saveChanges();$('diff').close();render();note('配置已保存；当前内核未应用新配置')}catch(error){note(error.message,true)}})
  $('subscription-import-close').addEventListener('click',closeImportDialog)
  $('proxy-group-close')?.addEventListener('click',closeGroupDialog)
  $('proxy-group-dialog')?.addEventListener('cancel',event=>{event.preventDefault();closeGroupDialog()})
  $('proxy-group-dialog')?.addEventListener('click',event=>{if(event.target===$('proxy-group-dialog'))closeGroupDialog()})
  $('proxy-node-close')?.addEventListener('click',closeNodeDialog)
  $('proxy-node-dialog')?.addEventListener('cancel',event=>{event.preventDefault();closeNodeDialog()})
  $('proxy-node-dialog')?.addEventListener('click',event=>{if(event.target===$('proxy-node-dialog'))closeNodeDialog()})
  $('rule-group-close')?.addEventListener('click',closeRuleDialog)
  $('rule-group-dialog')?.addEventListener('cancel',event=>{event.preventDefault();closeRuleDialog()})
  $('rule-group-dialog')?.addEventListener('click',event=>{if(event.target===$('rule-group-dialog'))closeRuleDialog()})
  $('action-confirm-close')?.addEventListener('click',closeConfirmDialog)
  $('action-confirm-cancel')?.addEventListener('click',closeConfirmDialog)
  $('action-confirm-dialog')?.addEventListener('cancel',event=>{event.preventDefault();closeConfirmDialog()})
  $('action-confirm-dialog')?.addEventListener('click',event=>{if(event.target===$('action-confirm-dialog'))closeConfirmDialog()})
  $('action-confirm-submit')?.addEventListener('click',()=>{const action=confirmAction;closeConfirmDialog({restore:false});if(action)action()})
  $('confirm').addEventListener('click',async()=>{$('diff').close();await applyConfiguration()})
;(async()=>{try{await api.ready(); await load()}catch(error){note(error.message || '页面初始化失败，请重新打开插件页面',true)}})()
}

// AstrBot 在页面脚本之后注入 bridge；只在 DOM 与 bridge 都就绪后初始化。
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initialize, {once:true})
} else {
  initialize()
}
