"""Clash Verge 风格的 AstrBot 代理管理中心。"""
from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import json
import re
import time
import uuid
from urllib.parse import quote, unquote, urlparse

import httpx
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.api.web import error_response, json_response, request


DIRECT = {"id":"direct","name":"直连","mode":"direct","node_ids":[],"selected":"","enabled":True}
TEMPLATES = {
    "telegram":{"name":"Telegram","hosts":["api.telegram.org"]},
    "meta":{"name":"Meta","hosts":["graph.facebook.com","graph-video.facebook.com"]},
    "github":{"name":"GitHub","hosts":["api.github.com","github.com","raw.githubusercontent.com"]},
}
KINDS={"http","https","socks5","socks5h","mihomo"}
MODES={"direct","select","url-test","fallback"}
MATCHES={"exact","suffix"}
ADVANCED_SCHEMES={"ss","ssr","vmess","vless","trojan","hysteria","hysteria2","tuic","anytls"}
REGIONS=[
    ("HK",r"香港|港|hk|hong\s*kong"), ("TW",r"台湾|臺灣|台|tw|taiwan"),
    ("JP",r"日本|日|jp|japan"), ("SG",r"新加坡|狮城|sg|singapore"),
    ("US",r"美国|美國|us|usa|united\s*states"), ("KR",r"韩国|韓國|kr|korea"),
    ("DE",r"德国|德國|de|germany"), ("UK",r"英国|英國|uk|britain"),
    ("MY",r"马来西亚|my|malaysia"), ("TH",r"泰国|th|thailand"),
    ("VN",r"越南|vn|vietnam"), ("PH",r"菲律宾|ph|philippines"),
    ("IN",r"印度|in|india"), ("AU",r"澳大利亚|澳洲|au|australia"),
]


def safe_url(value: object, credentials: bool=False) -> bool:
    try:
        parsed=urlparse(str(value or '')); _=parsed.port
    except (TypeError,ValueError):
        return False
    return parsed.scheme in {"http","https","socks5","socks5h"} and bool(parsed.hostname) and (
        credentials or not parsed.username and not parsed.password
    )


def safe_proxy_endpoint(value: object) -> bool:
    value=str(value or '').strip()
    if safe_url(value, credentials=True):
        return True
    try:
        scheme=urlparse(value).scheme.lower()
    except ValueError:
        return False
    return scheme in ADVANCED_SCHEMES and value.startswith(scheme + '://')


def safe_host(value: object) -> str:
    host=str(value or '').strip().lower().rstrip('.')
    if not host or len(host)>253 or any(char.isspace() for char in host):
        raise ValueError('请输入有效域名')
    if not re.fullmatch(r'(?:\*\.)?[a-z0-9.-]+',host) or '..' in host:
        raise ValueError('域名只能包含字母、数字、点、短横线或通配符')
    return host


def ident(value: object) -> str:
    return re.sub(r'[^a-zA-Z0-9_-]','-',str(value or '').strip())[:64]


def safe_error(value: object) -> str:
    """Keep diagnostics useful without persisting subscription credentials or URLs."""
    text=str(value or '')[:300]
    return re.sub(r'(?i)(https?|socks5h?)://[^\s]+', lambda m: m.group(1)+'://[redacted]', text)


def region_of(name: str) -> str:
    lowered=name.lower()
    for code,pattern in REGIONS:
        if re.search(pattern,lowered,re.I):
            return code
    return '其他'


@register('astrbot_plugin_proxy_manage','gobelieve','Clash Verge 风格代理管理中心','0.2.4')
class ProxyManager(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.context=context; self.config=config
        self.data_dir=StarTools.get_data_dir('astrbot_plugin_proxy_manage')
        self.data_dir.mkdir(parents=True,exist_ok=True)
        self.path=self.data_dir/'config.json'
        self.backup=self.data_dir/'config.previous.json'
        self.health_path=self.data_dir/'health.json'
        self.events_path=self.data_dir/'events.jsonl'
        self.lock=asyncio.Lock(); self.refresh_lock=asyncio.Lock()
        self.state=self._load(); self.health=self._load_health(); self.events=self._load_events()
        self.previews={}; self.auto_task=None
        self._register_routes()

    def _load(self) -> dict:
        try:
            raw=json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError,ValueError):
            try: raw=json.loads(self.config.get('config_json','{}'))
            except (TypeError,ValueError): raw={}
        return self._normalize(raw)

    def _normalize(self, raw: object) -> dict:
        source=raw if isinstance(raw,dict) else {}
        nodes=[]
        values=source.get('nodes',[]) if isinstance(source.get('nodes',[]),list) else []
        for item in values:
            if not isinstance(item,dict) or not ident(item.get('id')):
                continue
            nodes.append({
                'id':ident(item['id']), 'name':str(item.get('name',item['id']))[:80],
                'kind':str(item.get('kind','http'))[:24], 'endpoint':str(item.get('endpoint',''))[:300],
                'subscription_id':ident(item.get('subscription_id')), 'enabled':bool(item.get('enabled',True)),
            })

        group_values=source.get('groups') if isinstance(source.get('groups'),list) else []
        if not group_values:
            profile_values=source.get('profiles',[]) if isinstance(source.get('profiles',[]),list) else []
            for item in profile_values:
                if not isinstance(item,dict) or not item.get('id'):
                    continue
                item_id=ident(item['id']); node_id='legacy-'+item_id
                if item.get('endpoint'):
                    nodes.append({'id':node_id,'name':str(item.get('name',item_id))[:80],'kind':str(item.get('kind','http'))[:24],
                                  'endpoint':str(item['endpoint'])[:300],'subscription_id':'','enabled':True})
                group_values.append({'id':item_id,'name':item.get('name',item_id),
                                     'mode':'direct' if item.get('kind')=='direct' else 'select',
                                     'node_ids':[node_id] if item.get('endpoint') else [],
                                     'selected':node_id if item.get('endpoint') else ''})
        groups=[]
        for item in group_values:
            if not isinstance(item,dict) or not ident(item.get('id')):
                continue
            groups.append({
                'id':ident(item['id']), 'name':str(item.get('name',item['id']))[:80],
                'mode':item.get('mode') if item.get('mode') in MODES else 'select',
                'node_ids':[ident(value) for value in item.get('node_ids',[]) if ident(value)],
                'selected':ident(item.get('selected')), 'enabled':bool(item.get('enabled',True)),
            })
        if not any(group['id']=='direct' for group in groups): groups.insert(0,dict(DIRECT))
        group_ids={group['id'] for group in groups}

        routes=[]
        route_values=source.get('routes',[]) if isinstance(source.get('routes',[]),list) else []
        for index,item in enumerate(route_values):
            if not isinstance(item,dict): continue
            target=ident(item.get('target') or item.get('profile_id'))
            if target not in group_ids: continue
            try: host=safe_host(item.get('host'))
            except ValueError: continue
            routes.append({'id':ident(item.get('id')) or f'rule-{index+1}','host':host,
                           'match':item.get('match') if item.get('match') in MATCHES else 'exact',
                           'target':target,'priority':max(1,min(int(item.get('priority',100) or 100),10000)),
                           'enabled':bool(item.get('enabled',True))})
        routes.sort(key=lambda item:item['priority'])

        platforms={}
        if isinstance(source.get('platforms'),dict):
            for key,item in source['platforms'].items():
                if isinstance(item,dict):
                    platforms[ident(key)]={'name':str(item.get('name',key))[:80],
                                           'group_id':ident(item.get('group_id')) or 'direct',
                                           'enabled':bool(item.get('enabled',True))}

        subscriptions=[]
        sub_values=source.get('subscriptions',[]) if isinstance(source.get('subscriptions',[]),list) else []
        for index,item in enumerate(sub_values):
            if not isinstance(item,dict) or not safe_url(item.get('url')): continue
            interval=int(item.get('interval',60) or 60)
            interval=0 if interval<=0 else max(5,min(interval,1440))
            errors=[]
            for error in item.get('errors',[]) if isinstance(item.get('errors',[]),list) else []:
                if isinstance(error,dict):
                    errors.append({'at':int(error.get('at',0) or 0),'message':str(error.get('message',''))[:300]})
            subscriptions.append({
                'id':ident(item.get('id')) or f'sub-{index+1}', 'name':str(item.get('name',f'订阅 {index+1}'))[:80],
                'url':str(item['url'])[:1000], 'group':str(item.get('group','默认'))[:40] or '默认',
                'enabled':bool(item.get('enabled',True)), 'interval':interval,
                'node_ids':[ident(value) for value in item.get('node_ids',[]) if ident(value)],
                'updated_at':int(item.get('updated_at',0) or 0), 'next_refresh_at':int(item.get('next_refresh_at',0) or 0),
                'upload':max(0,int(item.get('upload',0) or 0)), 'download':max(0,int(item.get('download',0) or 0)),
                'total':max(0,int(item.get('total',0) or 0)), 'expire':int(item.get('expire',0) or 0),
                'last_error':str(item.get('last_error',''))[:300], 'consecutive_errors':max(0,int(item.get('consecutive_errors',0) or 0)),
                'errors':errors[-20:],
            })

        control=source.get('control') if isinstance(source.get('control'),dict) else {}
        timeout=int(control.get('timeout',8) or 8)
        return {
            'version':2, 'name':str(source.get('name','默认配置'))[:80], 'nodes':nodes, 'groups':groups,
            'routes':routes, 'platforms':platforms, 'subscriptions':subscriptions,
            'control':{'enabled':bool(control.get('enabled',False)),'url':str(control.get('url','')).rstrip('/')[:300],
                       'secret':str(control.get('secret',''))[:500],'timeout':max(3,min(timeout,30))},
        }

    def _validate(self, value: object) -> dict:
        if not isinstance(value,dict): raise ValueError('配置格式无效')
        for key in ('nodes','groups','routes','subscriptions'):
            if not isinstance(value.get(key),list): raise ValueError(key+' 必须是数组')
        for item in value['subscriptions']:
            if not isinstance(item,dict) or not safe_url(item.get('url')):
                raise ValueError('订阅地址无效，只允许 HTTP 或 HTTPS')
        state=self._normalize(value); node_ids=set(); group_ids=set()
        for node in state['nodes']:
            if node['id'] in node_ids: raise ValueError('节点 ID 重复：'+node['id'])
            node_ids.add(node['id'])
            if node['kind'] not in KINDS or not safe_proxy_endpoint(node['endpoint']):
                raise ValueError('节点入口无效：'+node['id'])
        for group in state['groups']:
            if group['id'] in group_ids: raise ValueError('代理组 ID 重复：'+group['id'])
            group_ids.add(group['id'])
            missing=set(group['node_ids'])-node_ids
            if missing: raise ValueError('代理组引用不存在节点：'+next(iter(missing)))
            if group['mode']!='direct' and not group['node_ids']: raise ValueError('代理组至少需要一个节点：'+group['id'])
            if group['selected'] and group['selected'] not in group['node_ids']:
                raise ValueError('代理组当前节点无效：'+group['id'])
        seen=set()
        for route in state['routes']:
            if route['target'] not in group_ids: raise ValueError('规则引用不存在代理组：'+route['target'])
            key=(route['host'],route['match'],route['priority'])
            if key in seen: raise ValueError('相同优先级存在重复规则：'+route['host'])
            seen.add(key)
        for key,platform in state['platforms'].items():
            if platform['group_id'] not in group_ids: raise ValueError('平台引用不存在代理组：'+key)
        sub_ids=set()
        for subscription in state['subscriptions']:
            if subscription['id'] in sub_ids: raise ValueError('订阅 ID 重复：'+subscription['id'])
            sub_ids.add(subscription['id'])
            if not safe_url(subscription['url']): raise ValueError('订阅地址只允许 HTTP 或 HTTPS')
            if subscription['interval'] and not 5<=subscription['interval']<=1440:
                raise ValueError('订阅自动刷新间隔必须在 5 到 1440 分钟之间')
        if state['control']['enabled'] and not safe_url(state['control']['url']):
            raise ValueError('控制接口地址无效，只允许 HTTP 或 HTTPS')
        return state

    def _load_health(self) -> dict:
        try:
            raw=json.loads(self.health_path.read_text(encoding='utf-8'))
            return raw if isinstance(raw,dict) else {}
        except (OSError,ValueError):
            return {}

    def _load_events(self) -> list[dict]:
        try:
            return [json.loads(line) for line in self.events_path.read_text(encoding='utf-8').splitlines()[-100:] if line]
        except (OSError,ValueError):
            return []

    def _register_routes(self):
        base='/astrbot_plugin_proxy_manage'
        routes=(
            ('state',self.state_page,['GET']), ('save',self.save,['POST']),
            ('preview',self.preview,['POST']), ('probe',self.probe,['POST']),
            ('events',self.events_page,['GET']), ('templates',self.templates,['GET']),
            ('rollback',self.rollback,['POST']), ('subscription-preview',self.subscription_preview,['POST']),
            ('subscription-import',self.subscription_import,['POST']), ('subscription-refresh',self.subscription_refresh,['POST']),
            ('control-status',self.control_status,['GET']), ('control-select',self.control_select,['POST']),
            ('node-probe',self.node_probe,['POST']), ('nodes-probe',self.nodes_probe,['POST']),
        )
        for name,handler,methods in routes:
            self.context.register_web_api(base+'/'+name,handler,methods,'代理管理中心')

    def snapshot(self) -> dict:
        result=json.loads(json.dumps(self.state))
        for node in result['nodes']:
            if node['endpoint']: node['endpoint']=urlparse(node['endpoint']).scheme+'://[configured]'
        for subscription in result['subscriptions']:
            subscription['url']=urlparse(subscription['url']).scheme+'://[configured]'
        result['control']['secret']='[configured]' if result['control']['secret'] else ''
        result['health']=self.health
        result['events']=self.events[-50:]; result['templates']=TEMPLATES
        return result

    async def persist(self,state:dict):
        normalized=self._normalize(state)
        if self.path.exists(): self.backup.write_text(self.path.read_text(encoding='utf-8'),encoding='utf-8')
        temp=self.path.with_suffix('.tmp')
        temp.write_text(json.dumps(normalized,ensure_ascii=False,indent=2),encoding='utf-8')
        try: temp.chmod(0o600)
        except OSError: pass
        temp.replace(self.path); self.state=normalized

    def persist_health(self):
        try:
            temp=self.health_path.with_suffix('.tmp')
            temp.write_text(json.dumps(self.health,ensure_ascii=False,indent=2),encoding='utf-8')
            temp.replace(self.health_path)
        except OSError:
            logger.warning('节点健康状态写入失败')

    def event(self,data:dict):
        item={'at':int(time.time()),**data}
        self.events=(self.events+[item])[-100:]
        try:
            with self.events_path.open('a',encoding='utf-8') as stream:
                stream.write(json.dumps(item,ensure_ascii=False)+'\n')
        except OSError:
            logger.warning('代理中心事件写入失败')

    def _restore_redacted(self,payload:dict):
        old_nodes={node['id']:node for node in self.state['nodes']}
        for node in payload.get('nodes',[]):
            if isinstance(node,dict) and node.get('id') in old_nodes and str(node.get('endpoint','')).endswith('://[configured]'):
                node['endpoint']=old_nodes[node['id']]['endpoint']
        old_subs={item['id']:item for item in self.state['subscriptions']}
        for item in payload.get('subscriptions',[]):
            if isinstance(item,dict) and item.get('id') in old_subs and str(item.get('url','')).endswith('://[configured]'):
                item['url']=old_subs[item['id']]['url']
        control=payload.get('control')
        if isinstance(control,dict) and control.get('secret')=='[configured]':
            control['secret']=self.state['control']['secret']

    async def state_page(self): return json_response(self.snapshot())
    async def events_page(self): return json_response({'events':self.events[-100:]})
    async def templates(self): return json_response({'templates':TEMPLATES})

    async def save(self):
        try:
            payload=await request.json()
            if not isinstance(payload,dict): raise ValueError('配置格式无效')
            self._restore_redacted(payload); candidate=self._validate(payload)
            async with self.lock:
                previous=self.state
                try: await self.persist(candidate)
                except Exception:
                    self.state=previous; raise
            self.event({'action':'save','result':'ok'})
            return json_response(self.snapshot())
        except (ValueError,TypeError) as exc: return error_response(str(exc))
        except OSError: return error_response('配置保存失败，已保留上一版配置',500)

    async def rollback(self):
        try:
            if not self.backup.exists(): raise ValueError('没有可恢复的上一版配置')
            async with self.lock:
                candidate=self._validate(json.loads(self.backup.read_text(encoding='utf-8')))
                await self.persist(candidate)
            return json_response(self.snapshot())
        except (OSError,ValueError,TypeError) as exc: return error_response(str(exc))

    def resolve(self,group_id:str):
        group=next((item for item in self.state['groups'] if item['id']==group_id and item['enabled']),None)
        if not group: raise ValueError('代理组不存在或未启用')
        if group['mode']=='direct': return group,None
        nodes=[node for node in self.state['nodes'] if node['id'] in group['node_ids'] and node['enabled']]
        if not nodes: raise ValueError('代理组没有可用节点')
        selected=next((node for node in nodes if node['id']==group['selected']),nodes[0])
        if group['mode'] in {'url-test','fallback'}:
            healthy=[]
            now=int(time.time())
            for node in nodes:
                item=self.health.get(node['id'],{})
                if item.get('status')=='ok' and now-int(item.get('checked_at',0) or 0)<86400:
                    healthy.append(node)
            if healthy:
                selected=min(healthy,key=lambda node:self.health[node['id']].get('latency_ms',99999)) if group['mode']=='url-test' else healthy[0]
        return group,selected

    async def preview(self):
        try:
            host=safe_host((await request.json()).get('host'))
            matches=[route for route in self.state['routes'] if route['enabled'] and (
                (route['match']=='exact' and route['host'].removeprefix('*.')==host) or
                (route['match']=='suffix' and (host==route['host'].removeprefix('*.') or host.endswith('.'+route['host'].removeprefix('*.'))))
            )]
            route=min(matches,key=lambda item:item['priority'],default=None)
            group,node=self.resolve(route['target'] if route else 'direct')
            return json_response({'host':host,'matched':route,'group':group,
                                  'node':node and {'id':node['id'],'name':node['name'],'kind':node['kind']}})
        except (ValueError,TypeError) as exc: return error_response(str(exc))

    async def probe(self):
        try:
            payload=await request.json(); group,node=self.resolve(str(payload.get('group_id','direct')))
            target=str(payload.get('url','https://www.gstatic.com/generate_204'))
            if not safe_url(target): raise ValueError('诊断目标只允许 HTTP 或 HTTPS 地址')
            started=time.monotonic()
            async with httpx.AsyncClient(proxy=node and node['endpoint'],trust_env=False,timeout=12) as client:
                response=await client.get(target)
            result={'group_id':group['id'],'group':group['name'],'node':node and node['name'] or 'DIRECT',
                    'status':response.status_code,'elapsed_ms':round((time.monotonic()-started)*1000)}
            self.event({'action':'probe','result':'ok',**result})
            return json_response(result)
        except (ValueError,httpx.HTTPError): return error_response('连通性检测失败，请检查代理组、节点和目标站点')

    def _decode_subscription(self,text:str) -> str:
        decoded=text
        compact=''.join(text.split())
        try:
            candidate=base64.b64decode(compact,validate=False).decode('utf-8')
            if not (candidate and ('://' in candidate or 'proxies:' in candidate)):
                candidate=base64.urlsafe_b64decode(compact+'===').decode('utf-8')
            if candidate and ('://' in candidate or 'proxies:' in candidate): decoded=candidate
        except (ValueError,UnicodeError):
            pass
        return decoded

    def _parse_subscription(self,text:str,subscription_id:str):
        decoded=self._decode_subscription(text); nodes=[]; discovered=set()
        if 'proxies:' in decoded:
            try:
                import yaml
                data=yaml.safe_load(decoded)
            except Exception:
                data=None
            if isinstance(data,dict) and isinstance(data.get('proxies'),list):
                for index,item in enumerate(data['proxies']):
                    if not isinstance(item,dict) or item.get('type') not in {'http','socks5'}: continue
                    host=item.get('server'); port=item.get('port')
                    if not host or not port: continue
                    scheme=str(item['type']); auth=''
                    if item.get('username'): auth=str(item['username'])+':'+str(item.get('password',''))+'@'
                    nodes.append({'id':f'{subscription_id}-{index+1}','name':str(item.get('name',f'{subscription_id}-{index+1}'))[:80],
                                  'kind':scheme,'endpoint':f'{scheme}://{auth}{host}:{port}','subscription_id':subscription_id,'enabled':True})
        for index,line in enumerate(decoded.splitlines()):
            value=line.strip(); scheme=value.split('://',1)[0].lower() if '://' in value else ''
            if scheme: discovered.add(scheme)
            if not value or scheme not in ADVANCED_SCHEMES|{'http','https','socks5','socks5h','socks'}: continue
            if scheme=='socks': scheme='socks5'; value='socks5://'+value.split('://',1)[1]
            kind=scheme if scheme in {'http','https','socks5','socks5h'} else 'mihomo'
            name=f'{subscription_id}-{index+1}'
            if scheme=='vmess':
                try:
                    payload=json.loads(base64.b64decode(value.split('://',1)[1]+'===').decode('utf-8'))
                    if isinstance(payload,dict) and payload.get('ps'): name=str(payload['ps'])
                except (ValueError,UnicodeError): pass
            elif '#' in value: name=unquote(value.rsplit('#',1)[1])[:80]
            node_id=subscription_id+'-'+hashlib.sha1(value.encode()).hexdigest()[:10]
            if any(node['id']==node_id for node in nodes): node_id+='-'+str(index+1)
            if safe_proxy_endpoint(value):
                nodes.append({'id':node_id,'name':name[:80],'kind':kind,'endpoint':value[:300],
                              'subscription_id':subscription_id,'enabled':True})
        for node in nodes: node['region']=region_of(node['name'])
        return nodes,discovered

    @staticmethod
    def _summary(nodes:list[dict],discovered:set[str]):
        protocols={}; regions={}; names=[]
        for node in nodes:
            protocols[node['kind']]=protocols.get(node['kind'],0)+1
            region=node.get('region','其他'); regions[region]=regions.get(region,0)+1
            if len(names)<8: names.append(node['name'])
        return {'count':len(nodes),'protocols':protocols,'regions':regions,'names':names,
                'discovered':sorted(discovered),'naming':'URI 节点名称' if names else '未识别名称'}

    @staticmethod
    def _traffic_header(headers:httpx.Headers):
        values=headers.get_list('subscription-userinfo')
        data={}
        for value in values:
            for key,text in re.findall(r'(upload|download|total|expire)=([^;]+)',value,re.I):
                try: data[key.lower()]=int(text.strip())
                except ValueError: pass
        return data

    def _cache_preview(self,items:list[dict]):
        preview_id=uuid.uuid4().hex; now=int(time.time())
        self.previews={key:value for key,value in self.previews.items() if now-value.get('at',0)<900}
        self.previews[preview_id]={'at':now,'items':items}
        for key in list(self.previews)[:-20]: self.previews.pop(key,None)
        return preview_id

    async def subscription_preview(self):
        try:
            payload=await request.json(); urls=payload.get('urls',[])
            if not isinstance(urls,list) or not urls: raise ValueError('请输入订阅链接')
            if len(urls)>10: raise ValueError('每次最多预览 10 个订阅')
            group=str(payload.get('group','默认'))[:40] or '默认'
            interval=int(payload.get('interval',60) or 60)
            interval=0 if interval<=0 else max(5,min(interval,1440))
            items=[]; preview_id=''
            for index,url in enumerate(urls):
                url=str(url).strip()
                if not safe_url(url): raise ValueError('订阅地址无效：第 '+str(index+1)+' 行')
                async with httpx.AsyncClient(timeout=20,follow_redirects=True,trust_env=False,limits=httpx.Limits(max_connections=4)) as client:
                    response=await client.get(url,headers={'User-Agent':'astrbot-plugin-proxy-manage/0.2.4'})
                if response.status_code>=400 or len(response.content)>10*1024*1024:
                    raise ValueError('订阅请求失败或响应过大：'+str(index+1))
                nodes,discovered=self._parse_subscription(response.text,'preview-'+str(index+1))
                traffic=self._traffic_header(response.headers)
                summary=self._summary(nodes,discovered); summary['traffic']=traffic; summary['ok']=bool(nodes)
                if not nodes:
                    summary['error']='未解析出支持的代理节点（发现协议：'+', '.join(sorted(discovered))+'）'
                items.append({'name':'订阅 '+str(index+1),'url':url,'group':group,'interval':interval,
                              'nodes':nodes,'summary':summary})
            preview_id=self._cache_preview(items)
            return json_response({'preview_id':preview_id,'items':[
                {'name':item['name'],'url':urlparse(item['url']).scheme+'://[configured]',
                 'group':item['group'],'interval':item['interval'],'summary':item['summary']} for item in items]})
        except (ValueError,httpx.HTTPError,OSError) as exc:
            return error_response(str(exc) if isinstance(exc,ValueError) else '订阅预览请求失败')

    def _replace_subscription_nodes(self,subscription:dict,nodes:list[dict]):
        old=set(subscription['node_ids'])
        self.state['nodes']=[node for node in self.state['nodes'] if node['id'] not in old]+nodes
        subscription['node_ids']=[node['id'] for node in nodes]

    @staticmethod
    def _stable_node_id(subscription_id:str, endpoint:str) -> str:
        return subscription_id+'-'+hashlib.sha256(endpoint.strip().encode()).hexdigest()[:16]

    def _record_subscription_error(self,subscription:dict,message:str):
        now=int(time.time())
        subscription['last_error']=safe_error(message)
        subscription['consecutive_errors']=int(subscription.get('consecutive_errors',0))+1
        subscription['errors']=(subscription.get('errors',[])+[{'at':now,'message':safe_error(message)}])[-20:]
        interval=max(int(subscription.get('interval',60) or 60),5)
        subscription['next_refresh_at']=now+min(max(interval,300),3600)

    async def _refresh_subscription(self,subscription_id:str):
        async with self.refresh_lock:
            subscription=next((item for item in self.state['subscriptions'] if item['id']==subscription_id),None)
            if not subscription: raise ValueError('订阅不存在')
            async with httpx.AsyncClient(timeout=20,follow_redirects=True,trust_env=False,limits=httpx.Limits(max_connections=4)) as client:
                response=await client.get(subscription['url'],headers={'User-Agent':'astrbot-plugin-proxy-manage/0.2.4'})
            if response.status_code>=400 or len(response.content)>10*1024*1024:
                raise ValueError('订阅请求失败或响应过大')
            nodes,discovered=self._parse_subscription(response.text,subscription['id'])
            if not nodes:
                raise ValueError('未解析出支持的代理节点（发现协议：'+', '.join(sorted(discovered))+'）')
            async with self.lock:
                subscription=next((item for item in self.state['subscriptions'] if item['id']==subscription_id),None)
                if not subscription: raise ValueError('订阅已在刷新时被删除')
                previous=copy.deepcopy(self.state)
                try:
                    for node in nodes:
                        node['id']=self._stable_node_id(subscription_id,node['endpoint'])
                        node['subscription_id']=subscription_id
                    self._replace_subscription_nodes(subscription,nodes)
                    now=int(time.time()); traffic=self._traffic_header(response.headers)
                    subscription.update({'updated_at':now,'upload':max(0,int(traffic.get('upload',subscription.get('upload',0)) or 0)),
                                         'download':max(0,int(traffic.get('download',subscription.get('download',0)) or 0)),
                                         'total':max(0,int(traffic.get('total',subscription.get('total',0)) or 0)),
                                         'expire':int(traffic.get('expire',subscription.get('expire',0)) or 0),
                                         'last_error':'','consecutive_errors':0})
                    interval=int(subscription.get('interval',60) or 60)
                    subscription['next_refresh_at']=now+interval*60 if interval else 0
                    self.state=self._validate(self.state); await self.persist(self.state)
                except Exception:
                    self.state=previous; raise
            self.event({'action':'subscription_refresh','subscription_id':subscription_id,'result':'ok','count':len(nodes)})
            return {'count':len(nodes),'summary':self._summary(nodes,set())}

    async def _refresh_with_retry(self,subscription_id:str,attempts:int=2):
        last_error=''
        for attempt in range(max(1,min(attempts,3))):
            try: return await self._refresh_subscription(subscription_id)
            except Exception as exc:
                last_error=exc
                if attempt+1<attempts: await asyncio.sleep(2)
        subscription=next((item for item in self.state['subscriptions'] if item['id']==subscription_id),None)
        if subscription:
            async with self.lock:
                self._record_subscription_error(subscription,str(last_error))
                await self.persist(self.state)
        self.event({'action':'subscription_refresh','subscription_id':subscription_id,'result':'failed','message':safe_error(last_error)[:200]})
        raise last_error

    async def subscription_refresh(self):
        try:
            payload=await request.json(); result=await self._refresh_with_retry(ident(payload.get('id')),2)
            return json_response({'snapshot':self.snapshot(),'result':result})
        except (ValueError,httpx.HTTPError,OSError) as exc:
            return error_response(str(exc) if isinstance(exc,ValueError) else '订阅刷新失败')

    async def subscription_import(self):
        try:
            payload=await request.json(); preview=self.previews.get(str(payload.get('preview_id','')))
            if not preview: raise ValueError('导入预览已过期，请重新预览')
            imported=[]
            async with self.lock:
                previous=copy.deepcopy(self.state)
                try:
                    for item in preview['items']:
                        if not item['nodes']: continue
                        existing=next((sub for sub in self.state['subscriptions'] if sub['url']==item['url']),None)
                        if existing:
                            subscription=existing
                        else:
                            subscription={'id':'sub-'+hashlib.sha1(item['url'].encode()).hexdigest()[:12],
                                          'name':item['name'],'url':item['url'],'group':item['group'],
                                          'enabled':True,'interval':item['interval'],'node_ids':[],'updated_at':0,
                                          'next_refresh_at':0,'upload':0,'download':0,'total':0,'expire':0,
                                          'last_error':'','consecutive_errors':0,'errors':[]}
                            self.state['subscriptions'].append(subscription)
                        subscription.update({'name':item['name'],'group':item['group'],'interval':item['interval'],'enabled':True})
                        for node in item['nodes']:
                            node['subscription_id']=subscription['id']
                            node['id']=self._stable_node_id(subscription['id'],node['endpoint'])
                        self._replace_subscription_nodes(subscription,item['nodes'])
                        now=int(time.time()); interval=int(item['interval'])
                        subscription['updated_at']=now
                        subscription['next_refresh_at']=now+interval*60 if interval else 0
                        imported.append(subscription['id'])
                    self.state=self._validate(self.state); await self.persist(self.state)
                except Exception:
                    self.state=previous; raise
            self.event({'action':'subscription_import','result':'ok','count':len(imported)})
            return json_response(self.snapshot())
        except (ValueError,OSError) as exc: return error_response(str(exc))

    def _control(self):
        control=self.state['control']
        if not control['enabled'] or not control['url']: raise ValueError('Mihomo 协议测速需要先启用外部控制接口')
        return control,({'Authorization':'Bearer '+control['secret']} if control['secret'] else {})

    def _set_health(self,node:dict,status:str,latency:int|None=None,error:str=''):
        self.health[node['id']]={'status':status,'latency_ms':latency,'error':str(error)[:300],
                                 'checked_at':int(time.time()),'target':'mihomo-control' if node['kind']=='mihomo' else 'direct-http'}
        self.persist_health()

    async def node_probe(self):
        payload=await request.json()
        return await self._probe_node(payload)

    async def _probe_node(self,payload:dict):
        try:
            if not isinstance(payload,dict): raise ValueError('请求格式无效')
            node_id=ident(payload.get('node_id')); target=str(payload.get('url','https://www.gstatic.com/generate_204'))
            node=next((item for item in self.state['nodes'] if item['id']==node_id and item['enabled']),None)
            if not node: raise ValueError('节点不存在或未启用')
            if not safe_url(target): raise ValueError('测速目标只允许 HTTP 或 HTTPS 地址')
            started=time.monotonic()
            if node['kind']=='mihomo':
                control,headers=self._control(); timeout=max(1,min(int(payload.get('timeout',5) or 5),15))
                async with httpx.AsyncClient(base_url=control['url'],headers=headers,timeout=timeout,trust_env=False) as client:
                    response=await client.get('/proxies/'+quote(node['name'],safe='')+'/delay',params={'url':target,'timeout':timeout*1000})
                response.raise_for_status(); data=response.json()
                if not isinstance(data.get('delay'),int): raise ValueError('外部控制接口未返回延迟')
                latency=int(data['delay'])
            else:
                async with httpx.AsyncClient(proxy=node['endpoint'],trust_env=False,follow_redirects=False,timeout=10) as client:
                    response=await client.get(target)
                if response.status_code>=400: raise ValueError('HTTP 状态码 '+str(response.status_code))
                latency=round((time.monotonic()-started)*1000)
            self._set_health(node,'ok',latency)
            return json_response({'node_id':node_id,'health':self.health[node_id]})
        except (ValueError,httpx.HTTPError,OSError) as exc:
            node=next((item for item in self.state['nodes'] if item['id']==ident(payload.get('node_id'))),None) if isinstance(payload,dict) else None
            error=exc if isinstance(exc,ValueError) else '测速失败或超时'
            if node: self._set_health(node,'timeout' if 'timeout' in type(exc).__name__.lower() else 'error',None,str(error))
            return error_response(str(error))

    async def nodes_probe(self):
        try:
            payload=await request.json(); requested=payload.get('node_ids')
            nodes=[node for node in self.state['nodes'] if node['enabled']]
            if isinstance(requested,list): nodes=[node for node in nodes if node['id'] in {ident(value) for value in requested}]
            if not nodes: raise ValueError('没有可测速的节点')
            semaphore=asyncio.Semaphore(5)
            async def test(node):
                async with semaphore:
                    try: await self._probe_node({'node_id':node['id'],'url':payload.get('url','https://www.gstatic.com/generate_204'),'timeout':payload.get('timeout',5)})
                    except Exception: pass
            await asyncio.gather(*(test(node) for node in nodes[:100]))
            tested=min(len(nodes),100)
            results=[self.health.get(node['id'],{}) for node in nodes[:tested]]
            return json_response({'health':self.health,'tested':tested,
                                  'succeeded':sum(item.get('status')=='ok' for item in results),
                                  'failed':sum(item.get('status') in {'error','timeout'} for item in results)})
        except (ValueError,TypeError) as exc: return error_response(str(exc))

    async def control_status(self):
        try:
            control,headers=self._control()
            async with httpx.AsyncClient(base_url=control['url'],headers=headers,timeout=control['timeout'],trust_env=False) as client:
                version=await client.get('/version'); version.raise_for_status(); proxies=await client.get('/proxies'); proxies.raise_for_status()
            return json_response({'version':version.json(),'proxies':proxies.json().get('proxies',{})})
        except (ValueError,httpx.HTTPError,TypeError): return error_response('控制接口连接失败，请检查地址、密钥和网络')

    async def control_select(self):
        try:
            payload=await request.json(); control,headers=self._control()
            name=str(payload.get('name','')); node=str(payload.get('node',''))
            if not name or not node: raise ValueError('代理组名称和节点名称不能为空')
            async with httpx.AsyncClient(base_url=control['url'],headers=headers,timeout=control['timeout'],trust_env=False) as client:
                response=await client.request('PUT','/proxies/'+quote(name,safe=''),json={'name':node}); response.raise_for_status()
            self.event({'action':'control_select','group':name,'node':node,'result':'ok'})
            return json_response({'ok':True})
        except (ValueError,httpx.HTTPError): return error_response('代理组切换失败，请检查控制接口权限')

    async def _auto_loop(self):
        while True:
            now=int(time.time())
            due=[item for item in self.state['subscriptions'] if item['enabled'] and item['interval'] and item['next_refresh_at']<=now]
            for subscription in due:
                try: await self._refresh_with_retry(subscription['id'],2)
                except Exception: pass
            await asyncio.sleep(30)

    async def initialize(self):
        if self.auto_task and not self.auto_task.done(): self.auto_task.cancel()
        self.auto_task=asyncio.create_task(self._auto_loop())
        logger.info('代理管理中心 0.2.4 已加载')

    async def terminate(self):
        if self.auto_task:
            self.auto_task.cancel()
            try: await self.auto_task
            except asyncio.CancelledError: pass
            self.auto_task=None

    async def on_message(self,event:AstrMessageEvent): return
