"""Clash Verge 风格的 AstrBot 代理管理中心。"""
from __future__ import annotations
import asyncio, base64, hashlib, json, re, time
from urllib.parse import quote, unquote, urlparse
import httpx
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.api.web import error_response, json_response, request

DIRECT = {"id":"direct","name":"直连","mode":"direct","node_ids":[],"selected":"","enabled":True}
TEMPLATES = {"telegram":{"name":"Telegram","hosts":["api.telegram.org"]},"meta":{"name":"Meta","hosts":["graph.facebook.com","graph-video.facebook.com"]},"github":{"name":"GitHub","hosts":["api.github.com","github.com","raw.githubusercontent.com"]}}
KINDS={"http","https","socks5","socks5h","mihomo"}; MODES={"direct","select","url-test","fallback"}; MATCHES={"exact","suffix"}
ADVANCED_SCHEMES={"ss","ssr","vmess","vless","trojan","hysteria","hysteria2","tuic","anytls"}
def safe_url(v, credentials=False):
    try:
        p=urlparse(v); _=p.port
    except (TypeError,ValueError): return False
    return p.scheme in {"http","https","socks5","socks5h"} and bool(p.hostname) and (credentials or not p.username and not p.password)
def safe_proxy_endpoint(v):
    value=str(v or '').strip()
    if safe_url(value, credentials=True): return True
    try: scheme=urlparse(value).scheme.lower()
    except ValueError: return False
    return scheme in ADVANCED_SCHEMES and value.startswith(scheme + '://')
def safe_host(v):
    h=str(v or '').strip().lower().rstrip('.')
    if not h or len(h)>253 or any(c.isspace() for c in h) or not re.fullmatch(r'(?:\*\.)?[a-z0-9.-]+',h) or '..' in h: raise ValueError('请输入有效域名')
    return h
def ident(v): return re.sub(r'[^a-zA-Z0-9_-]','-',str(v or '').strip())[:64]

@register('astrbot_plugin_proxy_manage','gobelieve','Clash Verge 风格代理管理中心','0.2.2')
class ProxyManager(Star):
    def __init__(self, context:Context, config:AstrBotConfig):
        super().__init__(context); self.context=context; self.config=config; self.data_dir=StarTools.get_data_dir('astrbot_plugin_proxy_manage'); self.data_dir.mkdir(parents=True,exist_ok=True); self.path=self.data_dir/'config.json'; self.backup=self.data_dir/'config.previous.json'; self.events_path=self.data_dir/'events.jsonl'; self.lock=asyncio.Lock(); self.state=self._load(); self.events=self._events(); self._routes()
    def _load(self):
        try: raw=json.loads(self.path.read_text())
        except (OSError,ValueError):
            try: raw=json.loads(self.config.get('config_json','{}'))
            except (TypeError,ValueError): raw={}
        return self._normalize(raw)
    def _normalize(self, raw):
        s=raw if isinstance(raw,dict) else {}; nodes=[]
        for x in s.get('nodes',[]) if isinstance(s.get('nodes',[]),list) else []:
            if isinstance(x,dict) and ident(x.get('id')): nodes.append({'id':ident(x['id']),'name':str(x.get('name',x['id']))[:80],'kind':str(x.get('kind','http')),'endpoint':str(x.get('endpoint',''))[:300],'enabled':bool(x.get('enabled',True))})
        groups=s.get('groups') if isinstance(s.get('groups'),list) else []
        if not groups:
            for x in s.get('profiles',[]) if isinstance(s.get('profiles',[]),list) else []:
                if isinstance(x,dict) and x.get('id'):
                    i=ident(x['id']); n='legacy-'+i
                    if x.get('endpoint'): nodes.append({'id':n,'name':str(x.get('name',i))[:80],'kind':x.get('kind','http'),'endpoint':x['endpoint'],'enabled':True})
                    groups.append({'id':i,'name':x.get('name',i),'mode':'direct' if x.get('kind')=='direct' else 'select','node_ids':[] if not x.get('endpoint') else [n],'selected':n if x.get('endpoint') else ''})
        out=[]
        for x in groups:
            if isinstance(x,dict) and ident(x.get('id')): out.append({'id':ident(x['id']),'name':str(x.get('name',x['id']))[:80],'mode':x.get('mode') if x.get('mode') in MODES else 'select','node_ids':[ident(i) for i in x.get('node_ids',[]) if ident(i)],'selected':ident(x.get('selected')),'enabled':bool(x.get('enabled',True))})
        if not any(x['id']=='direct' for x in out): out.insert(0,dict(DIRECT))
        gids={x['id'] for x in out}; routes=[]
        for i,x in enumerate(s.get('routes',[]) if isinstance(s.get('routes',[]),list) else []):
            if isinstance(x,dict) and ident(x.get('target') or x.get('profile_id')) in gids:
                try: h=safe_host(x.get('host'))
                except ValueError: continue
                routes.append({'id':ident(x.get('id')) or f'rule-{i+1}','host':h,'match':x.get('match') if x.get('match') in MATCHES else 'exact','target':ident(x.get('target') or x.get('profile_id')),'priority':int(x.get('priority',100)),'enabled':bool(x.get('enabled',True))})
        platforms={}
        for k,x in s.get('platforms',{}).items() if isinstance(s.get('platforms'),dict) else []:
            if isinstance(x,dict): platforms[ident(k)]={'name':str(x.get('name',k))[:80],'group_id':ident(x.get('group_id')) or 'direct','enabled':bool(x.get('enabled',True))}
        subscriptions=[]
        for i,x in enumerate(s.get('subscriptions',[]) if isinstance(s.get('subscriptions',[]),list) else []):
            if isinstance(x,dict) and safe_url(str(x.get('url',''))):
                subscriptions.append({'id':ident(x.get('id')) or f'sub-{i+1}','name':str(x.get('name',f'订阅 {i+1}'))[:80],'url':str(x['url'])[:1000],'enabled':bool(x.get('enabled',True)),'node_ids':[ident(n) for n in x.get('node_ids',[]) if ident(n)],'updated_at':int(x.get('updated_at',0) or 0)})
        control=s.get('control') if isinstance(s.get('control'),dict) else {}
        control={'enabled':bool(control.get('enabled',False)),'url':str(control.get('url','')).rstrip('/')[:300],'secret':str(control.get('secret',''))[:500],'timeout':max(3,min(int(control.get('timeout',8) or 8),30))}
        return {'version':2,'name':str(s.get('name','默认配置'))[:80],'nodes':nodes,'groups':out,'routes':sorted(routes,key=lambda x:x['priority']),'platforms':platforms,'subscriptions':subscriptions,'control':control}
    def _validate(self,v):
        if not isinstance(v,dict): raise ValueError('配置格式无效')
        for k in ('nodes','groups','routes'):
            if not isinstance(v.get(k),list): raise ValueError(k+' 必须是数组')
        for x in v.get('subscriptions',[]):
            if not isinstance(x,dict) or not safe_url(str(x.get('url',''))): raise ValueError('订阅地址无效，只允许 HTTP 或 HTTPS')
        s=self._normalize(v); nids=set(); gids=set()
        for n in s['nodes']:
            if n['id'] in nids: raise ValueError('节点 ID 重复：'+n['id'])
            nids.add(n['id'])
            if n['kind'] not in KINDS or not safe_proxy_endpoint(n['endpoint']): raise ValueError('节点入口无效：'+n['id'])
        for g in s['groups']:
            if g['id'] in gids: raise ValueError('代理组 ID 重复：'+g['id'])
            gids.add(g['id']); missing=set(g['node_ids'])-nids
            if missing: raise ValueError('代理组引用不存在节点：'+next(iter(missing)))
            if g['mode']!='direct' and not g['node_ids']: raise ValueError('代理组至少需要一个节点：'+g['id'])
            if g['selected'] and g['selected'] not in g['node_ids']: raise ValueError('代理组当前节点无效：'+g['id'])
        seen=set()
        for r in s['routes']:
            if r['target'] not in gids: raise ValueError('规则引用不存在代理组：'+r['target'])
            key=(r['host'],r['match'],r['priority'])
            if key in seen: raise ValueError('相同优先级存在重复规则：'+r['host'])
            seen.add(key)
        for k,x in s['platforms'].items():
            if x['group_id'] not in gids: raise ValueError('平台引用不存在代理组：'+k)
        sub_ids=set()
        for sub in s['subscriptions']:
            if sub['id'] in sub_ids: raise ValueError('订阅 ID 重复：'+sub['id'])
            sub_ids.add(sub['id'])
            if not safe_url(sub['url']): raise ValueError('订阅地址只允许 HTTP 或 HTTPS：'+sub['name'])
        if s['control']['enabled'] and (not s['control']['url'] or not safe_url(s['control']['url'])): raise ValueError('控制接口地址无效，只允许 HTTP 或 HTTPS')
        return s
    def _events(self):
        try: return [json.loads(x) for x in self.events_path.read_text().splitlines()[-100:] if x]
        except (OSError,ValueError): return []
    def _routes(self):
        b='/astrbot_plugin_proxy_manage'
        for n,h,m in [('state',self.state_page,['GET']),('save',self.save,['POST']),('preview',self.preview,['POST']),('probe',self.probe,['POST']),('events',self.events_page,['GET']),('templates',self.templates,['GET']),('rollback',self.rollback,['POST']),('subscription-refresh',self.subscription_refresh,['POST']),('control-status',self.control_status,['GET']),('control-select',self.control_select,['POST'])]: self.context.register_web_api(b+'/'+n,h,m,'代理管理中心')
    def snapshot(self):
        x=json.loads(json.dumps(self.state))
        for n in x['nodes']:
            if n['endpoint']: n['endpoint']=urlparse(n['endpoint']).scheme+'://[configured]'
        for sub in x['subscriptions']: sub['url']=urlparse(sub['url']).scheme+'://[configured]'
        x['control']['secret']='[configured]' if x['control']['secret'] else ''
        x['events']=self.events[-50:]; x['templates']=TEMPLATES; return x
    async def persist(self,s):
        if self.path.exists(): self.backup.write_text(self.path.read_text())
        t=self.path.with_suffix('.tmp'); t.write_text(json.dumps(s,ensure_ascii=False,indent=2)); t.replace(self.path); self.state=s
    def event(self,x):
        e={'at':int(time.time()),**x}; self.events=(self.events+[e])[-100:]
        try:
            with self.events_path.open('a') as f: f.write(json.dumps(e,ensure_ascii=False)+'\n')
        except OSError: logger.warning('代理中心事件写入失败')
    async def state_page(self): return json_response(self.snapshot())
    async def events_page(self): return json_response({'events':self.events[-100:]})
    async def templates(self): return json_response({'templates':TEMPLATES})
    async def save(self):
        try:
            p=await request.json(); old={n['id']:n for n in self.state['nodes']}
            old_sub={x['id']:x for x in self.state['subscriptions']}
            for n in p.get('nodes',[]):
                if n.get('id') in old and str(n.get('endpoint','')).endswith('://[configured]'): n['endpoint']=old[n['id']]['endpoint']
            for x in p.get('subscriptions',[]):
                if x.get('id') in old_sub and str(x.get('url','')).endswith('://[configured]'): x['url']=old_sub[x['id']]['url']
            if isinstance(p.get('control'),dict) and p['control'].get('secret')=='[configured]': p['control']['secret']=self.state['control']['secret']
            c=self._validate(p)
            async with self.lock: await self.persist(c); self.event({'action':'save','result':'ok'})
            return json_response(self.snapshot())
        except (ValueError,TypeError) as e: return error_response(str(e))
        except OSError: return error_response('配置保存失败，已保留上一版配置',500)
    async def rollback(self):
        try:
            if not self.backup.exists(): raise ValueError('没有可恢复的上一版配置')
            async with self.lock:
                c=self._validate(json.loads(self.backup.read_text())); await self.persist(c)
            return json_response(self.snapshot())
        except (OSError,ValueError,TypeError) as e: return error_response(str(e))
    def resolve(self,gid):
        g=next((x for x in self.state['groups'] if x['id']==gid and x['enabled']),None)
        if not g: raise ValueError('代理组不存在或未启用')
        if g['mode']=='direct': return g,None
        ns=[n for n in self.state['nodes'] if n['id'] in g['node_ids'] and n['enabled']]
        if not ns: raise ValueError('代理组没有可用节点')
        return g,next((n for n in ns if n['id']==g['selected']),ns[0])
    async def preview(self):
        try:
            h=safe_host((await request.json()).get('host'))
            rs = [r for r in self.state['routes'] if r['enabled'] and (
                (r['match'] == 'exact' and r['host'].removeprefix('*.') == h) or
                (r['match'] == 'suffix' and (h == r['host'].removeprefix('*.') or h.endswith('.' + r['host'].removeprefix('*.'))))
            )]
            r=min(rs,key=lambda x:x['priority'],default=None); g,n=self.resolve(r['target'] if r else 'direct'); return json_response({'host':h,'matched':r,'group':g,'node':n and {'id':n['id'],'name':n['name'],'kind':n['kind']}})
        except (ValueError,TypeError) as e: return error_response(str(e))
    async def probe(self):
        try:
            p=await request.json(); g,n=self.resolve(str(p.get('group_id','direct'))); u=str(p.get('url','https://www.gstatic.com/generate_204'))
            if not safe_url(u): raise ValueError('诊断目标只允许 HTTP 或 HTTPS 地址')
            st=time.monotonic()
            if n and urlparse(n['endpoint']).scheme.lower() not in {'http','https','socks5','socks5h'}: raise ValueError('该节点协议不支持 HTTP 探测，请通过 Mihomo 控制接口测试')
            async with httpx.AsyncClient(proxy=n and n['endpoint'],trust_env=False,timeout=12) as c: r=await c.get(u)
            out={'group_id':g['id'],'group':g['name'],'node':n and n['name'] or 'DIRECT','status':r.status_code,'elapsed_ms':round((time.monotonic()-st)*1000)}; self.event({'action':'probe','result':'ok',**out}); return json_response(out)
        except (ValueError,httpx.HTTPError): return error_response('连通性检测失败，请检查代理组、节点和目标站点')
    def _parse_subscription(self,text,sid):
        decoded=text
        try:
            compact=''.join(text.split())
            candidate=base64.b64decode(compact,validate=False).decode('utf-8')
            if not (candidate and ('://' in candidate or 'proxies:' in candidate)):
                candidate=base64.urlsafe_b64decode(compact + '===').decode('utf-8')
            if candidate and ('://' in candidate or 'proxies:' in candidate): decoded=candidate
        except (ValueError,UnicodeError): pass
        nodes=[]; discovered=set()
        if 'proxies:' in decoded:
            try:
                import yaml
                data=yaml.safe_load(decoded)
            except Exception: data=None
            if isinstance(data,dict) and isinstance(data.get('proxies'),list):
                for i,item in enumerate(data['proxies']):
                    if not isinstance(item,dict) or item.get('type') not in {'http','socks5'}: continue
                    host=item.get('server'); port=item.get('port')
                    if not host or not port: continue
                    scheme=item['type']; auth=''
                    if item.get('username'): auth=str(item['username'])+':'+str(item.get('password',''))+'@'
                    nodes.append({'id':f'{sid}-{i+1}','name':str(item.get('name',f'{sid}-{i+1}'))[:80],'kind':scheme,'endpoint':f'{scheme}://{auth}{host}:{port}','enabled':True})
        for i,line in enumerate(decoded.splitlines()):
            value=line.strip(); scheme=value.split('://',1)[0].lower() if '://' in value else ''
            if scheme: discovered.add(scheme)
            if not value or scheme not in ADVANCED_SCHEMES | {'http','https','socks5','socks5h','socks'}: continue
            if scheme == 'socks': scheme='socks5'; value='socks5://'+value.split('://',1)[1]
            kind=scheme if scheme in {'http','https','socks5','socks5h'} else 'mihomo'; name=f'{sid}-{i+1}'
            if scheme == 'vmess':
                try:
                    payload=json.loads(base64.b64decode(value.split('://',1)[1] + '===').decode('utf-8'))
                    if isinstance(payload,dict) and payload.get('ps'): name=str(payload['ps'])
                except (ValueError,UnicodeError): pass
            elif '#' in value: name=unquote(value.rsplit('#',1)[1])[:80]
            node_id=sid+'-'+hashlib.sha1(value.encode()).hexdigest()[:10]
            if any(n['id']==node_id for n in nodes): node_id+='-'+str(i+1)
            if safe_proxy_endpoint(value): nodes.append({'id':node_id,'name':name,'kind':kind,'endpoint':value[:300],'enabled':True})
        return nodes
    async def subscription_refresh(self):
        try:
            payload=await request.json(); sid=ident(payload.get('id')); sub=next((x for x in self.state['subscriptions'] if x['id']==sid),None)
            if not sub: raise ValueError('订阅不存在')
            async with httpx.AsyncClient(timeout=20,follow_redirects=True,trust_env=False,limits=httpx.Limits(max_connections=4)) as client:
                response=await client.get(sub['url'],headers={'User-Agent':'astrbot-plugin-proxy-manage/0.2.2'})
            if response.status_code>=400 or len(response.content)>10*1024*1024: raise ValueError('订阅请求失败或响应过大')
            nodes=self._parse_subscription(response.text,sid)
            if not nodes: raise ValueError('未解析出支持的代理节点（发现协议：' + ', '.join(sorted(discovered)) + '）')
            async with self.lock:
                previous=json.loads(json.dumps(self.state))
                try:
                    old=set(sub['node_ids']); self.state['nodes']=[n for n in self.state['nodes'] if n['id'] not in old]+nodes; sub['node_ids']=[n['id'] for n in nodes]; sub['updated_at']=int(time.time())
                    self.state=self._validate(self.state); await self.persist(self.state); self.event({'action':'subscription_refresh','subscription_id':sid,'result':'ok','count':len(nodes)})
                except Exception:
                    self.state=previous
                    raise
            return json_response(self.snapshot())
        except (ValueError,httpx.HTTPError,OSError) as e: return error_response(str(e) if isinstance(e,ValueError) else '订阅请求或保存失败')
    def _control(self):
        c=self.state['control']
        if not c['enabled'] or not c['url']: raise ValueError('控制接口未启用或未配置')
        return c,({'Authorization':'Bearer '+c['secret']} if c['secret'] else {})
    async def control_status(self):
        try:
            c,h=self._control()
            async with httpx.AsyncClient(base_url=c['url'],headers=h,timeout=c['timeout'],trust_env=False) as client:
                version=await client.get('/version'); version.raise_for_status(); proxies=await client.get('/proxies'); proxies.raise_for_status()
            data=proxies.json(); return json_response({'version':version.json(),'proxies':data.get('proxies',{})})
        except (ValueError,httpx.HTTPError,TypeError): return error_response('控制接口连接失败，请检查地址、密钥和网络')
    async def control_select(self):
        try:
            p=await request.json(); c,h=self._control(); name=str(p.get('name','')); node=str(p.get('node',''))
            if not name or not node: raise ValueError('代理组名称和节点名称不能为空')
            async with httpx.AsyncClient(base_url=c['url'],headers=h,timeout=c['timeout'],trust_env=False) as client:
                response=await client.request('PUT','/proxies/'+quote(name,safe=''), json={'name':node}); response.raise_for_status()
            self.event({'action':'control_select','group':name,'node':node,'result':'ok'}); return json_response({'ok':True})
        except (ValueError,httpx.HTTPError): return error_response('代理组切换失败，请检查控制接口权限')
    async def initialize(self): logger.info('代理管理中心 0.2.2 已加载')
    async def terminate(self): pass
    async def on_message(self,event:AstrMessageEvent): return
