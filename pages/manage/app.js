const $=id=>document.getElementById(id);const bridge=window.AstrBotPluginPage;let state=null;
function notice(text,error=false){$('notice').textContent=text;$('notice').className='notice'+(error?' error':'');$('notice').hidden=!text}
function esc(text){return String(text??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
async function load(){try{state=await bridge.apiGet('state');render();notice('')}catch(e){notice(e.message,true)}}
function render(){ $('profile-count').textContent=state.profiles.length;$('route-count').textContent=state.routes.length;$('node-count').textContent=state.nodes.length;$('event-count').textContent=state.events.length;
 $('profiles').innerHTML=state.profiles.map((p,i)=>`<div class="row"><input data-i="${i}" data-k="name" value="${esc(p.name)}"><span>${esc(p.kind)}</span><input data-i="${i}" data-k="endpoint" placeholder="代理入口（可选）" value="${esc(p.endpoint||'')}"><label><input type="checkbox" data-i="${i}" data-k="enabled" ${p.enabled?'checked':''}> 启用</label><button data-probe="${esc(p.id)}">诊断</button></div>`).join('');
 const select=$('probe-profile');select.innerHTML=state.profiles.map(p=>`<option value="${esc(p.id)}">${esc(p.name)}（${esc(p.kind)}）</option>`).join('');
 $('events').innerHTML=state.events.slice().reverse().map(e=>`<div class="event"><span>${esc(e.action)} · ${esc(e.profile_id||e.result||'')}</span><span>${new Date((e.at||0)*1000).toLocaleString()}</span></div>`).join('')||'<p class="muted">暂无事件</p>';
 document.querySelectorAll('[data-probe]').forEach(b=>b.onclick=()=>{$('probe-profile').value=b.dataset.probe;$('probe').click()});document.querySelectorAll('[data-i]').forEach(el=>el.onchange=()=>{const p=state.profiles[el.dataset.i];if(el.dataset.k==='enabled')p.enabled=el.checked;else p[el.dataset.k]=el.value});}
async function save(){try{await bridge.apiPost('save',state);notice('配置已保存')}catch(e){notice(e.message,true)}}
async function preview(){try{const r=await bridge.apiPost('preview',{host:$('host').value});$('preview-result').textContent=JSON.stringify(r,null,2)}catch(e){$('preview-result').textContent=e.message}}
async function probe(){try{$('probe').disabled=true;$('probe-result').textContent='检测中…';const r=await bridge.apiPost('probe',{profile_id:$('probe-profile').value,url:$('probe-url').value});$('probe-result').textContent=JSON.stringify(r,null,2);await load()}catch(e){$('probe-result').textContent=e.message}finally{$('probe').disabled=false}}
$('reload').onclick=load;$('save').onclick=save;$('preview').onclick=preview;$('probe').onclick=probe;load();
