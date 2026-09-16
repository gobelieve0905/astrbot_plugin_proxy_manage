"""Clash Verge 风格的 AstrBot 代理管理中心。"""
from __future__ import annotations
import asyncio, json, re, time
from urllib.parse import urlparse
import httpx
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.api.web import error_response, json_response, request

DIRECT = {"id":"direct","name":"直连","mode":"direct","node_ids":[],"selected":"","enabled":True}
TEMPLATES = {"telegram":{"name":"Telegram","hosts":["api.telegram.org"]},"meta":{"name":"Meta","hosts":["graph.facebook.com","graph-video.facebook.com"]},"github":{"name":"GitHub","hosts":["api.github.com","github.com","raw.githubusercontent.com"]}}
KINDS={"http","https","socks5","socks5h","mihomo"}; MODES={"direct","select","url-test","fallback"}; MATCHES={"exact","suffix"}
def safe_url(v, credentials=False):
    try:
        p=urlparse(v); _=p.port
    except (TypeError,ValueError): return False
    return p.scheme in {"http","https","socks5","socks5h"} and bool(p.hostname) and (credentials or not p.username and not p.password)
def safe_host(v):
    h=str(v or '').strip().lower().rstrip('.')
    if not h or len(h)>253 or any(c.isspace() for c in h) or not re.fullmatch(r'(?:\*\.)?[a-z0-9.-]+',h) or '..' in h: raise ValueError('请输入有效域名')
    return h
def ident(v): return re.sub(r'[^a-zA-Z0-9_-]','-',str(v or '').strip())[:64]

@register('astrbot_plugin_proxy_manage','gobelieve','Clash Verge 风格代理管理中心','0.2.0')
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
        return {'version':2,'name':str(s.get('name','默认配置'))[:80],'nodes':nodes,'groups':out,'routes':sorted(routes,key=lambda x:x['priority']),'platforms':platforms}
    def _validate(self,v):
        if not isinstance(v,dict): raise ValueError('配置格式无效')
        for k in ('nodes','groups','routes'):
            if not isinstance(v.get(k),list): raise ValueError(k+' 必须是数组')
        s=self._normalize(v); nids=set(); gids=set()
        for n in s['nodes']:
            if n['id'] in nids: raise ValueError('节点 ID 重复：'+n['id'])
            nids.add(n['id'])
            if n['kind'] not in KINDS or not safe_url(n['endpoint'],True): raise ValueError('节点入口无效：'+n['id'])
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
        return s
    def _events(self):
        try: return [json.loads(x) for x in self.events_path.read_text().splitlines()[-100:] if x]
        except (OSError,ValueError): return []
    def _routes(self):
        b='/astrbot_plugin_proxy_manage'
        for n,h,m in [('state',self.state_page,['GET']),('save',self.save,['POST']),('preview',self.preview,['POST']),('probe',self.probe,['POST']),('events',self.events_page,['GET']),('templates',self.templates,['GET']),('rollback',self.rollback,['POST'])]: self.context.register_web_api(b+'/'+n,h,m,'代理管理中心')
    def snapshot(self):
        x=json.loads(json.dumps(self.state))
        for n in x['nodes']:
            if n['endpoint']: n['endpoint']=urlparse(n['endpoint']).scheme+'://[configured]'
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
            for n in p.get('nodes',[]):
                if n.get('id') in old and str(n.get('endpoint','')).endswith('://[configured]'): n['endpoint']=old[n['id']]['endpoint']
            c=self._validate(p)
            async with self.lock: await self.persist(c); self.event({'action':'save','result':'ok'})
            return json_response(self.snapshot())
        except (ValueError,TypeError) as e: return error_response(str(e))
        except OSError: return error_response('配置保存失败，已保留上一版配置',500)
    async def rollback(self):
        try:
            if not self.backup.exists(): raise ValueError('没有可恢复的上一版配置')
            c=self._validate(json.loads(self.backup.read_text())); await self.persist(c); return json_response(self.snapshot())
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
            async with httpx.AsyncClient(proxy=n and n['endpoint'],trust_env=False,timeout=12) as c: r=await c.get(u)
            out={'group_id':g['id'],'group':g['name'],'node':n and n['name'] or 'DIRECT','status':r.status_code,'elapsed_ms':round((time.monotonic()-st)*1000)}; self.event({'action':'probe','result':'ok',**out}); return json_response(out)
        except (ValueError,httpx.HTTPError): return error_response('连通性检测失败，请检查代理组、节点和目标站点')
    async def initialize(self): logger.info('代理管理中心 0.2.0 已加载')
    async def terminate(self): pass
    async def on_message(self,event:AstrMessageEvent): return
