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
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlparse, urlsplit, urlunsplit

import httpx
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.api.web import error_response, json_response, request


DIRECT = {"id":"direct","name":"直连","kernel_name":"DIRECT","mode":"direct","node_ids":[],"selected":"","enabled":True}
TEMPLATES = {
    "telegram":{"name":"Telegram","hosts":["api.telegram.org"]},
    "meta":{"name":"Meta","hosts":["graph.facebook.com","graph-video.facebook.com"]},
    "github":{"name":"GitHub","hosts":["api.github.com","github.com","raw.githubusercontent.com"]},
}
KINDS={"http","https","socks5","socks5h","mihomo"}
MODES={"direct","select","url-test","fallback"}
MATCHES={"exact","suffix"}
ADVANCED_SCHEMES={"ss","ssr","vmess","vless","trojan","hysteria","hysteria2","tuic","anytls"}
SUPPORTED_PROTOCOLS={'anytls','http','https','socks','socks5','socks5h'}
NOTICE_PATTERNS=(
    r'剩余流量|流量剩余|已用流量|套餐流量|traffic',
    r'距离.*重置|下次重置|重置剩余|reset',
    r'套餐到期|到期时间|有效期|过期时间|expire|expiry',
)
CONFIGURED='[configured]'
SENSITIVE_KEYS={
    'authorization','auth','password','passwd','secret','token','username','user','uuid','id',
    'api-key','api_key','apikey','client-id','client_id',
    'private-key','private_key','client-key','client_key','psk','credential','credentials',
}
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
    text=re.sub(r'(?i)(https?|socks5h?|ss|ssr|vmess|vless|trojan|hysteria2?|tuic|anytls)://[^\s]+',
                lambda m: m.group(1)+'://[redacted]',text)
    text=re.sub(r'(?i)(authorization\s*:\s*bearer|bearer)\s+[^\s,;]+',r'\1 [redacted]',text)
    return re.sub(r'(?i)\b(password|passwd|secret|token|uuid|username)\s*[=:]\s*[^\s,;]+',
                  lambda m:m.group(1)+'=[redacted]',text)


def redact_config(value: object, key: str='') -> object:
    """Create a browser-safe copy while preserving enough shape for editing."""
    if isinstance(value,dict):
        return {name:redact_config(item,str(name).lower()) for name,item in value.items()}
    if isinstance(value,list): return [redact_config(item,key) for item in value]
    if isinstance(value,str):
        if key in SENSITIVE_KEYS and value: return CONFIGURED
        if '://' in value and safe_proxy_endpoint(value):
            return urlparse(value).scheme+'://'+CONFIGURED
    return value


def restore_config(value: object, previous: object) -> object:
    """A mask keeps the old value; an empty or new value explicitly clears/replaces it."""
    if isinstance(value,str) and (value==CONFIGURED or value.endswith('://'+CONFIGURED)):
        return copy.deepcopy(previous)
    if isinstance(value,dict) and isinstance(previous,dict):
        return {key:restore_config(item,previous.get(key)) for key,item in value.items()}
    if isinstance(value,list) and isinstance(previous,list):
        return [restore_config(item,previous[index] if index<len(previous) else None)
                for index,item in enumerate(value)]
    return value


def redact_diagnostics(value: object) -> object:
    if isinstance(value,dict): return {key:redact_diagnostics(item) for key,item in value.items()}
    if isinstance(value,list): return [redact_diagnostics(item) for item in value]
    if isinstance(value,(str,Exception)): return safe_error(value)
    return value


def region_of(name: str) -> str:
    lowered=name.lower()
    for code,pattern in REGIONS:
        if re.search(pattern,lowered,re.I):
            return code
    return '其他'


def protocol_support(protocol: str) -> tuple[str,str]:
    protocol=str(protocol or '').lower()
    if protocol in SUPPORTED_PROTOCOLS: return 'supported',''
    if protocol in ADVANCED_SCHEMES: return 'unverified','协议已识别，但尚未完成本插件运行验证'
    return 'unsupported','订阅协议暂不支持'


def infer_protocol(item: dict) -> str:
    declared=str(item.get('protocol','')).lower()
    if declared and declared!='mihomo': return 'socks5' if declared=='socks' else declared
    endpoint=str(item.get('endpoint','')).strip()
    if '://' in endpoint:
        scheme=endpoint.split('://',1)[0].lower()
        if scheme: return 'socks5' if scheme=='socks' else scheme
    connection=item.get('connection')
    if isinstance(connection,dict):
        declared=str(connection.get('type','')).lower()
        if declared: return 'socks5' if declared=='socks' else declared
        uri=str(connection.get('uri',''))
        if '://' in uri: return uri.split('://',1)[0].lower()
    legacy=str(item.get('kind','http')).lower()
    return 'unknown' if legacy=='mihomo' else ('socks5' if legacy=='socks' else legacy)


def suspected_notice(name: str) -> tuple[bool,str]:
    for pattern in NOTICE_PATTERNS:
        if re.search(pattern,str(name or ''),re.I): return True,'名称疑似订阅流量、重置或到期提示'
    return False,''


def canonical_connection(protocol: str, endpoint: str='', connection: object=None) -> str:
    """Canonical secret-bearing connection material; display-only fields are excluded."""
    protocol=str(protocol or '').lower(); endpoint=str(endpoint or '').strip()
    if protocol=='vmess' and endpoint.startswith('vmess://'):
        try:
            payload=json.loads(base64.b64decode(endpoint.split('://',1)[1]+'===').decode())
            if isinstance(payload,dict):
                payload={key:value for key,value in payload.items() if key not in {'ps','name','remark'}}
                return 'vmess:'+json.dumps(payload,sort_keys=True,separators=(',',':'),ensure_ascii=False)
        except (ValueError,UnicodeError,json.JSONDecodeError): pass
    if endpoint and '://' in endpoint:
        try:
            parsed=urlsplit(endpoint)
            host=(parsed.hostname or '').lower(); port=parsed.port
            user=unquote(parsed.username or ''); password=unquote(parsed.password or '')
            auth=quote(user,safe='')
            if password: auth+=':'+quote(password,safe='')
            if auth: auth+='@'
            display_host='['+host+']' if ':' in host and not host.startswith('[') else host
            netloc=auth+display_host+((':'+str(port)) if port else '')
            query=urlencode(sorted(parse_qsl(parsed.query,keep_blank_values=True)),doseq=True)
            return urlunsplit((parsed.scheme.lower(),netloc,parsed.path,query,''))
        except (TypeError,ValueError): pass
    if isinstance(connection,dict):
        clean={key:value for key,value in connection.items() if str(key).lower() not in {'name','ps','remark','display_name'}}
        return protocol+':'+json.dumps(clean,sort_keys=True,separators=(',',':'),ensure_ascii=False)
    return protocol+':'+endpoint


def identity_material(protocol: str, endpoint: str='', connection: object=None) -> str:
    """Prefer stable address/principal fields; uncertain identities retain full canonical material."""
    protocol=str(protocol or '').lower(); endpoint=str(endpoint or '').strip()
    if endpoint and '://' in endpoint and protocol!='vmess':
        try:
            parsed=urlsplit(endpoint); host=(parsed.hostname or '').lower(); port=parsed.port
            principal=unquote(parsed.username or '')
            if host and port and (principal or protocol in {'http','https','socks','socks5','socks5h'}):
                return json.dumps([protocol,host,port,principal],separators=(',',':'))
        except (TypeError,ValueError): pass
    if isinstance(connection,dict):
        server=str(connection.get('server','')).lower(); port=connection.get('port')
        principal=connection.get('username') or connection.get('user') or connection.get('uuid')
        if server and port and (principal or protocol in {'http','https','socks','socks5','socks5h'}):
            return json.dumps([protocol,server,port,str(principal or '')],separators=(',',':'))
    return canonical_connection(protocol,endpoint,connection)


@register('astrbot_plugin_proxy_manage','gobelieve','Clash Verge 风格代理管理中心','0.2.10')
class ProxyManager(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.context=context; self.config=config
        self.data_dir=StarTools.get_data_dir('astrbot_plugin_proxy_manage')
        self.data_dir.mkdir(parents=True,exist_ok=True)
        self.path=self.data_dir/'config.json'
        self.backup=self.data_dir/'config.previous.json'
        self.migration_backup=self.data_dir/'config.pre-v3.json'
        self.health_path=self.data_dir/'health.json'
        self.events_path=self.data_dir/'events.jsonl'
        self.runtime_path=self.data_dir/'runtime-application.json'
        self.runtime_backup=self.data_dir/'runtime-application.previous.json'
        for private_path in (self.path,self.backup,self.migration_backup,self.health_path,self.events_path,self.runtime_path,self.runtime_backup):
            if private_path.exists():
                try: private_path.chmod(0o600)
                except OSError: logger.warning('代理中心私有文件权限收紧失败：'+private_path.name)
        self.lock=asyncio.Lock(); self.refresh_lock=asyncio.Lock(); self.apply_lock=asyncio.Lock()
        self.state=self._load(); self.health=self._load_health()
        self.runtime_application=self._load_runtime_application()
        health_changed=False
        for old_id,new_id in getattr(self,'_id_aliases',{}).items():
            if old_id != new_id and old_id in self.health and new_id not in self.health:
                self.health[new_id]=self.health.pop(old_id); health_changed=True
        if health_changed: self.persist_health()
        self.events=self._load_events()
        self.previews={}; self.auto_task=None
        self._register_routes()

    def _load(self) -> dict:
        from_disk=False
        try:
            raw=json.loads(self.path.read_text(encoding='utf-8')); from_disk=True
        except (OSError,ValueError):
            try: raw=json.loads(self.config.get('config_json','{}'))
            except (TypeError,ValueError): raw={}
        normalized=self._normalize(raw)
        if from_disk and isinstance(raw,dict) and int(raw.get('version',0) or 0)<3:
            try:
                if not self.migration_backup.exists():
                    self.migration_backup.write_text(json.dumps(raw,ensure_ascii=False,indent=2),encoding='utf-8')
                    self.migration_backup.chmod(0o600)
                temp=self.path.with_suffix('.migration.tmp')
                temp.write_text(json.dumps(normalized,ensure_ascii=False,indent=2),encoding='utf-8'); temp.chmod(0o600)
                temp.replace(self.path)
            except OSError:
                logger.warning('节点模型 v3 迁移写入失败，继续使用内存中的兼容配置')
        return normalized

    def _normalize(self, raw: object) -> dict:
        source=raw if isinstance(raw,dict) else {}
        nodes=[]; node_by_id={}; self._id_aliases={}
        values=source.get('nodes',[]) if isinstance(source.get('nodes',[]),list) else []
        for item in values:
            if not isinstance(item,dict) or not ident(item.get('id')):
                continue
            endpoint=str(item.get('endpoint','')).strip()
            protocol=infer_protocol(item); connection=copy.deepcopy(item.get('connection')) if isinstance(item.get('connection'),dict) else {}
            if not connection and endpoint: connection={'uri':endpoint}
            old_id=ident(item['id']); subscription_id=ident(item.get('subscription_id'))
            node_id=self._stable_node_id(subscription_id,endpoint,protocol,connection) if subscription_id else old_id
            self._id_aliases[old_id]=node_id
            support_status,support_reason=protocol_support(protocol)
            support=item.get('support') if isinstance(item.get('support'),dict) else {}
            notice,notice_reason=suspected_notice(item.get('display_name',item.get('name',item['id'])))
            source_info=item.get('source') if isinstance(item.get('source'),dict) else {}
            normalized_node={
                'id':node_id, 'name':str(item.get('name',item.get('display_name',item['id'])))[:120],
                'display_name':str(item.get('display_name',item.get('name',item['id'])))[:120],
                'source_name':str(item.get('source_name',item.get('name',item['id'])))[:120],
                'user_alias':str(item.get('user_alias',''))[:120],
                'protocol':protocol[:24], 'engine':str(item.get('engine') or ('direct-http' if protocol in {'http','https','socks5','socks5h'} else 'mihomo'))[:24],
                'kind':str(item.get('kind') or ('mihomo' if protocol in ADVANCED_SCHEMES else protocol))[:24],
                'endpoint':endpoint, 'connection':connection,
                'subscription_id':subscription_id, 'enabled':bool(item.get('enabled',True)),
                'excluded':bool(item.get('excluded',False)), 'exclusion_reason':str(item.get('exclusion_reason',''))[:160],
                'invalid_reference':bool(item.get('invalid_reference',False)),
                'parameter_version':hashlib.sha256(canonical_connection(protocol,endpoint,connection).encode()).hexdigest()[:16],
                'kernel_name':'node-'+node_id,
                'source':{'type':source_info.get('type','subscription' if subscription_id else 'manual'),
                          'subscription_id':subscription_id,'format':str(source_info.get('format','legacy'))[:24],
                          'index':int(source_info.get('index',-1) if source_info.get('index') is not None else -1)},
                'support':{'status':support_status,'reason':str(support.get('reason') or support_reason)[:200]},
                'suspected_notice':bool(item.get('suspected_notice',notice)),
                'notice_reason':str(item.get('notice_reason',notice_reason))[:160],
            }
            existing=node_by_id.get(node_id)
            if not existing or (normalized_node['display_name'],normalized_node['parameter_version']) < (existing['display_name'],existing['parameter_version']):
                node_by_id[node_id]=normalized_node
        nodes=list(node_by_id.values())

        group_values=source.get('groups') if isinstance(source.get('groups'),list) else []
        if not group_values:
            profile_values=source.get('profiles',[]) if isinstance(source.get('profiles',[]),list) else []
            for item in profile_values:
                if not isinstance(item,dict) or not item.get('id'):
                    continue
                item_id=ident(item['id']); node_id='legacy-'+item_id
                if item.get('endpoint'):
                    endpoint=str(item['endpoint']).strip(); protocol=infer_protocol(item); support_status,support_reason=protocol_support(protocol)
                    nodes.append({'id':node_id,'name':str(item.get('name',item_id))[:120],'display_name':str(item.get('name',item_id))[:120],
                                  'source_name':str(item.get('name',item_id))[:120],'user_alias':'',
                                  'protocol':protocol,'engine': 'mihomo' if protocol in ADVANCED_SCHEMES else 'direct-http',
                                  'kind':str(item.get('kind') or protocol)[:24], 'endpoint':endpoint,'connection':{'uri':endpoint},
                                  'subscription_id':'','enabled':True,'excluded':False,'exclusion_reason':'','invalid_reference':False,
                                  'parameter_version':hashlib.sha256(canonical_connection(protocol,endpoint,{}).encode()).hexdigest()[:16],
                                  'kernel_name':'node-'+node_id,'source':{'type':'legacy','subscription_id':'','format':'profile','index':-1},
                                  'support':{'status':support_status,'reason':support_reason},'suspected_notice':False,'notice_reason':''})
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
                'kernel_name':'DIRECT' if ident(item['id'])=='direct' else 'group-'+ident(item['id']),
                'mode':item.get('mode') if item.get('mode') in MODES else 'select',
                'node_ids':list(dict.fromkeys(self._id_aliases.get(ident(value),ident(value)) for value in item.get('node_ids',[]) if ident(value))),
                'selected':self._id_aliases.get(ident(item.get('selected')),ident(item.get('selected'))), 'enabled':bool(item.get('enabled',True)),
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
            interval=int(item['interval']) if item.get('interval') is not None else 60
            interval=0 if interval<=0 else max(5,min(interval,1440))
            errors=[]
            for error in item.get('errors',[]) if isinstance(item.get('errors',[]),list) else []:
                if isinstance(error,dict):
                    errors.append({'at':int(error.get('at',0) or 0),'message':str(error.get('message',''))[:300]})
            subscriptions.append({
                'id':ident(item.get('id')) or f'sub-{index+1}', 'name':str(item.get('name',f'订阅 {index+1}'))[:80],
                'url':str(item['url'])[:1000], 'group':str(item.get('group','默认'))[:40] or '默认',
                'enabled':bool(item.get('enabled',True)), 'interval':interval,
                'node_ids':list(dict.fromkeys(self._id_aliases.get(ident(value),ident(value)) for value in item.get('node_ids',[]) if ident(value))),
                'updated_at':int(item.get('updated_at',0) or 0), 'next_refresh_at':int(item.get('next_refresh_at',0) or 0),
                'upload':max(0,int(item.get('upload',0) or 0)), 'download':max(0,int(item.get('download',0) or 0)),
                'total':max(0,int(item.get('total',0) or 0)), 'expire':int(item.get('expire',0) or 0),
                'last_error':str(item.get('last_error',''))[:300], 'consecutive_errors':max(0,int(item.get('consecutive_errors',0) or 0)),
                'errors':errors[-20:], 'last_diff':copy.deepcopy(item.get('last_diff')) if isinstance(item.get('last_diff'),dict) else {},
            })

        control=source.get('control') if isinstance(source.get('control'),dict) else {}
        timeout=int(control.get('timeout',8) or 8)
        entry=source.get('proxy_entry') if isinstance(source.get('proxy_entry'),dict) else {}
        return {
            'version':3, 'migration':{'stable_identity':1}, 'name':str(source.get('name','默认配置'))[:80], 'nodes':nodes, 'groups':groups,
            'routes':routes, 'platforms':platforms, 'subscriptions':subscriptions,
            'control':{'enabled':bool(control.get('enabled',False)),'url':str(control.get('url','')).rstrip('/')[:300],
                       'secret':str(control.get('secret',''))[:500],'timeout':max(3,min(timeout,30)),
                       'deployment':control.get('deployment') if control.get('deployment') in {'existing','dedicated'} else 'existing',
                       'scope':control.get('scope') if control.get('scope') in {'providers-groups-rules','full'} else 'providers-groups-rules',
                       'listen':str(control.get('listen','127.0.0.1:9090')).strip()[:200]},
            'proxy_entry':{'http_url':str(entry.get('http_url','')).rstrip('/')[:300],
                           'socks_url':str(entry.get('socks_url','')).rstrip('/')[:300],
                           'source':entry.get('source') if entry.get('source') in {'configured','detected','unknown'} else 'unknown'},
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
            if node['kind'] not in KINDS:
                raise ValueError('节点执行类型无效：'+node['id'])
            if node['support']['status']=='supported' and not safe_proxy_endpoint(node['endpoint']):
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
        for key in ('http_url','socks_url'):
            value=state['proxy_entry'][key]
            if value and not safe_url(value,credentials=True):
                raise ValueError('代理入口地址无效：'+key)
        if state['control']['listen'] and not re.fullmatch(r'(?:\[[0-9a-fA-F:]+\]|[^:\s]+):\d{1,5}',state['control']['listen']):
            raise ValueError('内核控制监听地址格式无效')
        return state

    def _load_runtime_application(self) -> dict:
        try:
            value=json.loads(self.runtime_path.read_text(encoding='utf-8'))
            return value if isinstance(value,dict) else {}
        except (OSError,ValueError):
            return {}

    def _persist_runtime_application(self,value:dict):
        if self.runtime_path.exists():
            self.runtime_backup.write_text(self.runtime_path.read_text(encoding='utf-8'),encoding='utf-8')
            try: self.runtime_backup.chmod(0o600)
            except OSError: pass
        temp=self.runtime_path.with_suffix('.tmp')
        temp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
        try: temp.chmod(0o600)
        except OSError: pass
        temp.replace(self.runtime_path); self.runtime_application=value

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
            ('runtime-config',self.runtime_config,['GET']), ('runtime-apply',self.runtime_apply,['POST']),
            ('kernel-status',self.kernel_status,['GET']),
        )
        for name,handler,methods in routes:
            self.context.register_web_api(base+'/'+name,handler,methods,'代理管理中心')

    def snapshot(self) -> dict:
        result=json.loads(json.dumps(self.state))
        for node in result['nodes']:
            if node['endpoint']: node['endpoint']=urlparse(node['endpoint']).scheme+'://'+CONFIGURED
            node['connection']=redact_config(node.get('connection',{}),'connection')
        for subscription in result['subscriptions']:
            subscription['url']=urlparse(subscription['url']).scheme+'://'+CONFIGURED
        result['control']['secret']=CONFIGURED if result['control']['secret'] else ''
        result.setdefault('proxy_entry', {'http_url':'', 'socks_url':'', 'source':'unknown'})
        for key in ('http_url','socks_url'):
            if result['proxy_entry'][key]: result['proxy_entry'][key]=urlparse(result['proxy_entry'][key]).scheme+'://'+CONFIGURED
        result['health']=redact_diagnostics(self.health)
        result['events']=redact_diagnostics(self.events[-50:]); result['templates']=TEMPLATES
        application=getattr(self,'runtime_application',{})
        result['application']={key:application.get(key) for key in ('status','saved_revision','applied_revision','updated_at','message')}
        return result

    async def persist(self,state:dict):
        normalized=self._normalize(state)
        if self.path.exists():
            self.backup.write_text(self.path.read_text(encoding='utf-8'),encoding='utf-8')
            try: self.backup.chmod(0o600)
            except OSError: pass
        temp=self.path.with_suffix('.tmp')
        temp.write_text(json.dumps(normalized,ensure_ascii=False,indent=2),encoding='utf-8')
        try: temp.chmod(0o600)
        except OSError: pass
        temp.replace(self.path); self.state=normalized
        if hasattr(self,'runtime_application') and self.runtime_application.get('status')!='restore_failed':
            try: saved_revision=self._runtime_revision(self._runtime_document())
            except (ValueError,TypeError): saved_revision=''
            applied_revision=self.runtime_application.get('applied_revision','')
            status='applied' if saved_revision and saved_revision==applied_revision else ('pending_apply' if applied_revision else 'saved')
            application={**self.runtime_application,'status':status,'saved_revision':saved_revision,'updated_at':int(time.time()),
                         'message':'配置已应用' if status=='applied' else ('配置已变更，等待应用' if status=='pending_apply' else '配置已保存，尚未应用')}
            try: self._persist_runtime_application(application)
            except OSError: logger.warning('运行配置修订状态写入失败')

    def persist_health(self):
        try:
            temp=self.health_path.with_suffix('.tmp')
            temp.write_text(json.dumps(self.health,ensure_ascii=False,indent=2),encoding='utf-8')
            try: temp.chmod(0o600)
            except OSError: pass
            temp.replace(self.health_path)
        except OSError:
            logger.warning('节点健康状态写入失败')

    def event(self,data:dict):
        item={'at':int(time.time()),**{
            key:safe_error(value) if isinstance(value,(str,Exception)) else value for key,value in data.items()
        }}
        self.events=(self.events+[item])[-100:]
        try:
            with self.events_path.open('a',encoding='utf-8') as stream:
                stream.write(json.dumps(item,ensure_ascii=False)+'\n')
            try: self.events_path.chmod(0o600)
            except OSError: pass
        except OSError:
            logger.warning('代理中心事件写入失败')

    def _restore_redacted(self,payload:dict):
        old_nodes={node['id']:node for node in self.state['nodes']}
        for node in payload.get('nodes',[]):
            if isinstance(node,dict) and node.get('id') in old_nodes:
                previous=old_nodes[node['id']]
                node['endpoint']=restore_config(node.get('endpoint',''),previous.get('endpoint',''))
                node['connection']=restore_config(node.get('connection',{}),previous.get('connection',{}))
        old_subs={item['id']:item for item in self.state['subscriptions']}
        for item in payload.get('subscriptions',[]):
            if isinstance(item,dict) and item.get('id') in old_subs:
                item['url']=restore_config(item.get('url',''),old_subs[item['id']].get('url',''))
        control=payload.get('control')
        if isinstance(control,dict):
            control['secret']=restore_config(control.get('secret',''),self.state['control'].get('secret',''))
        entry=payload.get('proxy_entry')
        if isinstance(entry,dict):
            previous=self.state.get('proxy_entry',{})
            for key in ('http_url','socks_url'):
                entry[key]=restore_config(entry.get(key,''),previous.get(key,''))

    async def state_page(self): return json_response(self.snapshot())
    async def events_page(self): return json_response({'events':redact_diagnostics(self.events[-100:])})
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
        if group['selected'] and group['selected'] not in {node['id'] for node in self.state['nodes']}:
            raise ValueError('代理组手动选择已失效：'+group['name'])
        nodes=[node for node in self.state['nodes'] if node['id'] in group['node_ids'] and node['enabled']
               and not node.get('excluded') and node.get('support',{}).get('status','supported')=='supported']
        if not nodes: raise ValueError('代理组没有可用节点')
        if group['selected'] and not any(node['id']==group['selected'] for node in nodes):
            raise ValueError('代理组手动选择不可用或已失效：'+group['name'])
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
        def add_node(protocol:str,endpoint:str,connection:dict,name:str,index:int,source_format:str):
            protocol='socks5' if protocol=='socks' else protocol.lower()
            status,reason=protocol_support(protocol); notice,notice_reason=suspected_notice(name)
            node_id=self._stable_node_id(subscription_id,endpoint,protocol,connection)
            node={'id':node_id,'name':str(name)[:120],'display_name':str(name)[:120],'protocol':protocol,
                  'source_name':str(name)[:120],'user_alias':'',
                  'engine':'direct-http' if protocol in {'http','https','socks5','socks5h'} else 'mihomo',
                  'kind':protocol if protocol in {'http','https','socks5','socks5h'} else 'mihomo',
                  'endpoint':endpoint,'connection':copy.deepcopy(connection),'subscription_id':subscription_id,
                  'enabled':status=='supported','excluded':False,'exclusion_reason':'','invalid_reference':False,
                  'parameter_version':hashlib.sha256(canonical_connection(protocol,endpoint,connection).encode()).hexdigest()[:16],
                  'kernel_name':'node-'+node_id,
                  'source':{'type':'subscription','subscription_id':subscription_id,'format':source_format,'index':index},
                  'support':{'status':status,'reason':reason},'suspected_notice':notice,'notice_reason':notice_reason,
                  'region':region_of(str(name))}
            existing=next((item for item in nodes if item['id']==node_id),None)
            if not existing: nodes.append(node)
            elif (node['display_name'],node['parameter_version']) < (existing['display_name'],existing['parameter_version']):
                nodes[nodes.index(existing)]=node

        if 'proxies:' in decoded:
            try:
                import yaml
                data=yaml.safe_load(decoded)
            except Exception:
                data=None
            if isinstance(data,dict) and isinstance(data.get('proxies'),list):
                for index,item in enumerate(data['proxies']):
                    if not isinstance(item,dict): continue
                    protocol=str(item.get('type','unknown')).lower(); discovered.add(protocol)
                    host=item.get('server'); port=item.get('port')
                    name=str(item.get('name',f'{subscription_id}-{index+1}'))
                    if not host or not port:
                        add_node(protocol,'',item,name,index,'clash-yaml'); continue
                    display_host='['+str(host)+']' if ':' in str(host) and not str(host).startswith('[') else str(host)
                    if protocol in {'http','socks','socks5'}:
                        scheme='socks5' if protocol in {'socks','socks5'} else ('https' if item.get('tls') else 'http'); auth=''
                        if item.get('username') is not None:
                            auth=quote(str(item.get('username','')),safe='')+':'+quote(str(item.get('password','')),safe='')+'@'
                        endpoint=f'{scheme}://{auth}{display_host}:{port}'
                    elif protocol=='anytls':
                        credential=str(item.get('password') or item.get('username') or '')
                        query={str(key).replace('_','-'):value for key,value in item.items()
                               if key not in {'name','type','server','port','password','username'}}
                        endpoint=f'anytls://{quote(credential,safe="")}@{display_host}:{port}'
                        if query: endpoint+='?'+urlencode(sorted(query.items()),doseq=True)
                    else:
                        endpoint=f'{protocol}://{display_host}:{port}'
                    add_node(scheme if protocol=='http' and item.get('tls') else protocol,endpoint,item,name,index,'clash-yaml')
        for index,line in enumerate(decoded.splitlines()):
            value=line.strip(); scheme=value.split('://',1)[0].lower() if '://' in value else ''
            if scheme: discovered.add(scheme)
            if not value or not re.fullmatch(r'[a-z][a-z0-9+.-]*',scheme): continue
            if scheme=='socks': scheme='socks5'; value='socks5://'+value.split('://',1)[1]
            name=f'{subscription_id}-{index+1}'
            if scheme=='vmess':
                try:
                    payload=json.loads(base64.b64decode(value.split('://',1)[1]+'===').decode('utf-8'))
                    if isinstance(payload,dict) and payload.get('ps'): name=str(payload['ps'])
                except (ValueError,UnicodeError): pass
            elif '#' in value: name=unquote(value.rsplit('#',1)[1])[:80]
            add_node(scheme,value,{'uri':value},name,index,'uri')
        return nodes,discovered

    @staticmethod
    def _summary(nodes:list[dict],discovered:set[str]):
        protocols={}; regions={}; names=[]
        for node in nodes:
            protocols[node['protocol']]=protocols.get(node['protocol'],0)+1
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
            interval=int(payload['interval']) if payload.get('interval') is not None else 60
            interval=0 if interval<=0 else max(5,min(interval,1440))
            items=[]; preview_id=''
            for index,url in enumerate(urls):
                url=str(url).strip()
                if not safe_url(url): raise ValueError('订阅地址无效：第 '+str(index+1)+' 行')
                async with httpx.AsyncClient(timeout=20,follow_redirects=True,trust_env=False,limits=httpx.Limits(max_connections=4)) as client:
                    response=await client.get(url,headers={'User-Agent':'astrbot-plugin-proxy-manage/0.2.10'})
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
        old=set(subscription['node_ids']); incoming={node['id'] for node in nodes}
        existing={node['id']:node for node in self.state['nodes'] if node['id'] in old}
        changed=[]; unchanged=[]
        for node in nodes:
            previous=existing.get(node['id'])
            if previous:
                for key in ('excluded','exclusion_reason'):
                    node[key]=previous.get(key,node.get(key))
                alias=previous.get('user_alias')
                if not alias and previous.get('display_name')!=previous.get('source_name'):
                    alias=previous.get('display_name')
                if alias:
                    node['user_alias']=alias; node['display_name']=alias; node['name']=alias
                if node.get('support',{}).get('status')=='supported': node['enabled']=previous.get('enabled',node['enabled'])
                node['invalid_reference']=False
                target=changed if previous.get('parameter_version')!=node.get('parameter_version') else unchanged
                target.append(self._node_ref(node))
                if target is changed: self.health.pop(node['id'],None)
        for node in self.state['nodes']:
            if node['id'] in old and node['id'] not in incoming:
                node['enabled']=False; node['invalid_reference']=True; node['exclusion_reason']='订阅已删除或节点参数已变更'
        self.state['nodes']=[node for node in self.state['nodes'] if node['id'] not in incoming]+nodes
        subscription['node_ids']=[node['id'] for node in nodes]
        diff={'at':int(time.time()),
              'added':[self._node_ref(node) for node in nodes if node['id'] not in old],
              'changed':changed,
              'deleted':[self._node_ref(existing[node_id]) for node_id in old-incoming if node_id in existing],
              'unchanged':unchanged}
        subscription['last_diff']=diff
        return diff

    @staticmethod
    def _node_ref(node:dict) -> dict:
        return {'id':node['id'],'name':node.get('display_name',node.get('name',node['id'])),
                'protocol':node.get('protocol','unknown'),'parameter_version':node.get('parameter_version','')}

    @staticmethod
    def _stable_node_id(subscription_id:str, endpoint:str, protocol:str='', connection:object=None) -> str:
        protocol=protocol or (endpoint.split('://',1)[0].lower() if '://' in endpoint else 'unknown')
        identity=identity_material(protocol,endpoint,connection)
        return subscription_id+'-'+hashlib.sha256(identity.encode()).hexdigest()[:16]

    def _record_subscription_error(self,subscription:dict,message:str):
        now=int(time.time())
        subscription['last_error']=safe_error(message)
        subscription['consecutive_errors']=int(subscription.get('consecutive_errors',0))+1
        subscription['errors']=(subscription.get('errors',[])+[{'at':now,'message':safe_error(message)}])[-20:]
        interval=int(subscription.get('interval',60) if subscription.get('interval') is not None else 60)
        subscription['next_refresh_at']=now+min(max(interval*60,300),3600) if interval>0 else 0

    async def _refresh_subscription(self,subscription_id:str):
        async with self.refresh_lock:
            subscription=next((item for item in self.state['subscriptions'] if item['id']==subscription_id),None)
            if not subscription: raise ValueError('订阅不存在')
            async with httpx.AsyncClient(timeout=20,follow_redirects=True,trust_env=False,limits=httpx.Limits(max_connections=4)) as client:
                response=await client.get(subscription['url'],headers={'User-Agent':'astrbot-plugin-proxy-manage/0.2.10'})
            if response.status_code>=400 or len(response.content)>10*1024*1024:
                raise ValueError('订阅请求失败或响应过大')
            nodes,discovered=self._parse_subscription(response.text,subscription['id'])
            if not nodes:
                raise ValueError('未解析出支持的代理节点（发现协议：'+', '.join(sorted(discovered))+'）')
            async with self.lock:
                subscription=next((item for item in self.state['subscriptions'] if item['id']==subscription_id),None)
                if not subscription: raise ValueError('订阅已在刷新时被删除')
                previous=copy.deepcopy(self.state); previous_health=copy.deepcopy(self.health)
                try:
                    for node in nodes:
                        node['id']=self._stable_node_id(subscription_id,node['endpoint'],node.get('protocol',''),node.get('connection'))
                        node['kernel_name']='node-'+node['id']
                        node['subscription_id']=subscription_id
                    diff=self._replace_subscription_nodes(subscription,nodes)
                    now=int(time.time()); traffic=self._traffic_header(response.headers)
                    subscription.update({'updated_at':now,'upload':max(0,int(traffic.get('upload',subscription.get('upload',0)) or 0)),
                                         'download':max(0,int(traffic.get('download',subscription.get('download',0)) or 0)),
                                         'total':max(0,int(traffic.get('total',subscription.get('total',0)) or 0)),
                                         'expire':int(traffic.get('expire',subscription.get('expire',0)) or 0),
                                         'last_error':'','consecutive_errors':0})
                    interval=int(subscription.get('interval',60) if subscription.get('interval') is not None else 60)
                    subscription['next_refresh_at']=now+interval*60 if interval else 0
                    self.state=self._validate(self.state); await self.persist(self.state); self.persist_health()
                except Exception:
                    self.state=previous; self.health=previous_health; raise
            self.event({'action':'subscription_refresh','subscription_id':subscription_id,'result':'ok','count':len(nodes),
                        'added':len(diff['added']),'changed':len(diff['changed']),'deleted':len(diff['deleted']),'unchanged':len(diff['unchanged'])})
            return {'count':len(nodes),'summary':self._summary(nodes,set()),'diff':diff}

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
            payload=await request.json(); preview_id=str(payload.get('preview_id','')); imported=[]
            async with self.lock:
                preview=self.previews.get(preview_id)
                if not preview or int(time.time())-int(preview.get('at',0))>=900:
                    self.previews.pop(preview_id,None); raise ValueError('导入预览已过期，请重新预览')
                preview=copy.deepcopy(preview)
                previous=copy.deepcopy(self.state); previous_health=copy.deepcopy(self.health)
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
                            node['id']=self._stable_node_id(subscription['id'],node['endpoint'],node.get('protocol',''),node.get('connection'))
                            node['kernel_name']='node-'+node['id']
                        self._replace_subscription_nodes(subscription,item['nodes'])
                        now=int(time.time()); interval=int(item['interval'])
                        subscription['updated_at']=now
                        subscription['next_refresh_at']=now+interval*60 if interval else 0
                        imported.append(subscription['id'])
                    self.state=self._validate(self.state); await self.persist(self.state); self.persist_health()
                    self.previews.pop(preview_id,None)
                except Exception:
                    self.state=previous; self.health=previous_health; raise
            self.event({'action':'subscription_import','result':'ok','count':len(imported)})
            return json_response(self.snapshot())
        except (ValueError,OSError) as exc: return error_response(str(exc))

    def _control(self):
        control=self.state['control']
        if not control['enabled'] or not control['url']: raise ValueError('尚未配置 Mihomo 控制接口地址和密钥')
        return control,({'Authorization':'Bearer '+control['secret']} if control['secret'] else {})

    async def _kernel_status(self) -> dict:
        control=self.state['control']
        if not control['enabled'] or not control['url']:
            return {'state':'not_configured','ready':False,'message':'尚未配置 Mihomo 控制接口地址和密钥',
                    'deployment':control.get('deployment','existing'),'scope':control.get('scope','providers-groups-rules')}
        try:
            headers={'Authorization':'Bearer '+control['secret']} if control['secret'] else {}
            async with httpx.AsyncClient(base_url=control['url'],headers=headers,timeout=control['timeout'],trust_env=False) as client:
                version_response=await client.get('/version'); version_response.raise_for_status()
                version=version_response.json()
                raw_version=str(version.get('version',''))
                match=re.search(r'(\d+)\.(\d+)\.(\d+)',raw_version)
                if not version.get('meta') or not match or tuple(map(int,match.groups())) < (1,19,0):
                    return {'state':'version_unsupported','ready':False,'message':'需要 Mihomo Meta 1.19.0 或更高版本','version':raw_version,
                            'deployment':control.get('deployment','existing'),'scope':control.get('scope','providers-groups-rules')}
                configs=await client.get('/configs'); configs.raise_for_status(); runtime=configs.json()
                proxies_response=await client.get('/proxies'); proxies_response.raise_for_status(); proxies=proxies_response.json().get('proxies',{})
                rules_response=await client.get('/rules'); rules_response.raise_for_status(); runtime_rules=rules_response.json().get('rules',[])
            try:
                expected=self._runtime_document()
            except ValueError:
                expected=None
            saved_revision=self._runtime_revision(expected) if expected else ''
            application=getattr(self,'runtime_application',{})
            base={'ready':True,'version':raw_version,'deployment':control.get('deployment','existing'),
                  'scope':control.get('scope','providers-groups-rules'),'saved_revision':saved_revision,
                  'applied_revision':application.get('applied_revision','')}
            if application.get('status')=='restore_failed':
                return {**base,'state':'restore_failed','message':'上次应用失败且运行配置恢复失败，请立即检查专用内核'}
            if control.get('deployment')!='dedicated':
                return {**base,'state':'saved','message':'共享内核仅允许检查；缺少可信完整基线，已禁止写入'}
            if not application.get('applied_revision'):
                return {**base,'state':'saved','message':'插件配置已保存，尚未应用到专用内核'}
            if saved_revision!=application.get('applied_revision'):
                return {**base,'state':'pending_apply','message':'插件配置已变更，等待应用到专用内核'}
            errors=self._verify_runtime_data(expected,runtime,proxies,runtime_rules) if expected else ['候选配置无效']
            if errors:
                return {**base,'state':'runtime_inconsistent','message':'运行配置与已应用修订不一致：'+errors[0]}
            return {**base,'state':'applied','message':'专用 Mihomo 已连接，运行配置与已应用修订一致',
                    'deployment':control.get('deployment','existing'),'scope':control.get('scope','providers-groups-rules'),
                    'proxy_entry':self.state['proxy_entry']}
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in {401,403}:
                return {'state':'auth_failed','ready':False,'message':'Mihomo 控制接口认证失败'}
            return {'state':'connection_failed','ready':False,'message':'Mihomo 控制接口请求失败'}
        except (httpx.HTTPError,OSError,ValueError,TypeError):
            return {'state':'connection_failed','ready':False,'message':'无法连接 Mihomo 控制接口'}

    async def kernel_status(self):
        return json_response(await self._kernel_status())

    @staticmethod
    def _typed_query_value(value:str):
        if str(value).lower() in {'true','yes','1'}: return True
        if str(value).lower() in {'false','no','0'}: return False
        try: return int(value)
        except (TypeError,ValueError): return value

    def _mihomo_proxy(self,node:dict) -> dict:
        protocol=node.get('protocol',''); connection=copy.deepcopy(node.get('connection',{}))
        if node.get('support',{}).get('status','supported')!='supported':
            raise ValueError('节点协议尚未验证，不能生成运行配置：'+node.get('display_name',node['id']))
        if connection and connection.get('server') and connection.get('port'):
            proxy={key:value for key,value in connection.items() if key not in {'uri','display_name'}}
            proxy['name']=node['kernel_name']; proxy['type']='socks5' if protocol in {'socks','socks5','socks5h'} else ('http' if protocol in {'http','https'} else protocol)
            return proxy
        parsed=urlsplit(node['endpoint']); host=parsed.hostname; port=parsed.port
        if not host or not port: raise ValueError('节点连接参数缺少服务器或端口：'+node['id'])
        proxy={'name':node['kernel_name'],'type':'socks5' if protocol in {'socks','socks5','socks5h'} else ('http' if protocol in {'http','https'} else protocol),
               'server':host,'port':port}
        username=unquote(parsed.username or ''); password=unquote(parsed.password or '')
        if protocol in {'http','https','socks','socks5','socks5h'}:
            if username: proxy['username']=username
            if password: proxy['password']=password
            if protocol=='https': proxy['tls']=True
        elif protocol=='anytls':
            credential=password or username
            if not credential: raise ValueError('AnyTLS 节点缺少密码：'+node['id'])
            proxy['password']=credential
        for key,value in parse_qsl(parsed.query,keep_blank_values=True):
            target={'insecure':'skip-cert-verify','allow-insecure':'skip-cert-verify','servername':'sni'}.get(key,key.replace('_','-'))
            proxy[target]=self._typed_query_value(value)
        return proxy

    def _runtime_document(self) -> dict:
        """Build a complete candidate for a plugin-dedicated Mihomo instance."""
        try:
            import yaml
        except ImportError as exc:
            raise ValueError('缺少 PyYAML，无法生成 Mihomo 配置') from exc
        providers={}
        runnable={node['id']:node for node in self.state['nodes'] if node['enabled'] and not node.get('excluded')
                  and not node.get('invalid_reference') and node.get('support',{}).get('status','supported')=='supported'}
        proxies=[self._mihomo_proxy(node) for node in sorted(runnable.values(),key=lambda item:item['id'])]
        groups=[]
        for group in self.state['groups']:
            if group['id']=='direct':
                continue
            mode={'select':'select','url-test':'url-test','fallback':'fallback'}.get(group['mode'],'select')
            members=[runnable[node_id]['kernel_name'] for node_id in group['node_ids'] if node_id in runnable]
            if not members: raise ValueError('代理组没有可应用的已验证节点：'+group['name'])
            item={'name':group.get('kernel_name','group-'+group['id']),'type':mode,'proxies':members}
            if group['mode'] in {'url-test','fallback'}:
                item.update({'url':'https://www.gstatic.com/generate_204','interval':300,'tolerance':50})
            groups.append(item)
        names={item['id']:('DIRECT' if item['id']=='direct' else item.get('kernel_name','group-'+item['id'])) for item in self.state['groups']}
        rules=[]
        for route in self.state['routes']:
            if not route['enabled'] or route['target'] not in names: continue
            host=route['host'].removeprefix('*.')
            rules.append(('DOMAIN' if route['match']=='exact' else 'DOMAIN-SUFFIX')+','+host+','+names[route['target']])
        rules.append('MATCH,'+names.get('direct','DIRECT'))
        control=self.state['control']; entry=self.state.get('proxy_entry',{})
        document={'mode':'rule','log-level':'silent','proxies':proxies,
                  'proxy-providers':providers,'proxy-groups':groups,'rules':rules}
        if control.get('deployment')=='dedicated':
            document['external-controller']=control.get('listen','127.0.0.1:9090')
            document['secret']=control.get('secret','')
            port=urlsplit(entry.get('http_url','')).port if entry.get('http_url') else None
            if port: document['mixed-port']=port
        # Validate serialization before handing the document to the controller.
        yaml.safe_load(yaml.safe_dump(document,allow_unicode=True,sort_keys=False))
        return document

    @staticmethod
    def _runtime_revision(document:dict) -> str:
        return hashlib.sha256(json.dumps(document,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()[:16]

    @staticmethod
    def _validate_runtime_document(document:dict):
        proxy_names=[item.get('name') for item in document.get('proxies',[])]
        group_names=[item.get('name') for item in document.get('proxy-groups',[])]
        if not proxy_names or len(proxy_names)!=len(set(proxy_names)) or any(not name for name in proxy_names):
            raise ValueError('候选配置中的内核节点名称为空或重复')
        if len(group_names)!=len(set(group_names)) or any(not name for name in group_names):
            raise ValueError('候选配置中的内核代理组名称为空或重复')
        available=set(proxy_names)|{'DIRECT','REJECT'}
        for group in document.get('proxy-groups',[]):
            members=group.get('proxies',[])
            if not members or any(member not in available for member in members):
                raise ValueError('候选配置中的代理组成员无效：'+str(group.get('name','')))
            available.add(group['name'])
        for rule in document.get('rules',[]):
            if not isinstance(rule,str) or ',' not in rule or rule.rsplit(',',1)[1] not in available:
                raise ValueError('候选配置中的分流规则目标无效')

    @staticmethod
    def _expected_rules(document:dict) -> list[tuple[str,str,str]]:
        result=[]
        for value in document.get('rules',[]):
            parts=value.split(','); kind=parts[0].upper(); target=parts[-1]
            result.append((kind,','.join(parts[1:-1]),target))
        return result

    @classmethod
    def _verify_runtime_data(cls,document:dict,runtime:object,proxies:object,rules:object) -> list[str]:
        errors=[]
        if not isinstance(runtime,dict) or runtime.get('mode')!='rule': errors.append('运行模式不是 rule')
        if not isinstance(proxies,dict): return errors+['无法读取运行代理']
        for proxy in document.get('proxies',[]):
            actual=proxies.get(proxy['name'])
            if not isinstance(actual,dict): errors.append('缺少内核节点 '+proxy['name']); continue
            if str(actual.get('type','')).lower().replace('-','') != str(proxy.get('type','')).lower().replace('-',''):
                errors.append('节点类型不一致 '+proxy['name'])
        type_names={'select':'Selector','url-test':'URLTest','fallback':'Fallback'}
        for group in document.get('proxy-groups',[]):
            actual=proxies.get(group['name'])
            if not isinstance(actual,dict): errors.append('缺少代理组 '+group['name']); continue
            if actual.get('type')!=type_names.get(group.get('type')): errors.append('代理组类型不一致 '+group['name'])
            if actual.get('all')!=group.get('proxies'): errors.append('代理组成员不一致 '+group['name'])
            if actual.get('now') not in group.get('proxies',[]): errors.append('代理组选择状态无效 '+group['name'])
        actual_rules=[]
        if isinstance(rules,list):
            for rule in rules:
                if isinstance(rule,dict): actual_rules.append((str(rule.get('type','')).upper(),str(rule.get('payload','')),str(rule.get('proxy',''))))
        if actual_rules!=cls._expected_rules(document): errors.append('运行规则内容或顺序不一致')
        return errors

    @staticmethod
    def _recovery_document(control:dict,entry:dict) -> dict:
        document={'mode':'rule','log-level':'silent','proxies':[],'proxy-providers':{},'proxy-groups':[],
                  'rules':['MATCH,DIRECT'],'external-controller':control.get('listen','127.0.0.1:9090'),'secret':control.get('secret','')}
        port=urlsplit(entry.get('http_url','')).port if entry.get('http_url') else None
        if port: document['mixed-port']=port
        return document

    async def runtime_config(self):
        try:
            document=self._runtime_document()
            preview=redact_config(copy.deepcopy(document))
            return json_response({'config':preview,'mapping':{
                'nodes':{node['id']:{'name':node['name'],'kernel_name':node.get('kernel_name'),'subscription_id':node['subscription_id']}
                         for node in self.state['nodes'] if node['enabled']},
                'groups':{group['id']:{'name':group['name'],'kernel_name':group.get('kernel_name')}
                          for group in self.state['groups'] if group['enabled']},
            },'saved_revision':self._runtime_revision(document),
               'applied_revision':getattr(self,'runtime_application',{}).get('applied_revision',''),
               'status':getattr(self,'runtime_application',{}).get('status','saved')})
        except (ValueError,TypeError) as exc:
            return error_response(str(exc))

    async def runtime_apply(self):
        async with self.apply_lock:
            try:
                kernel=await self._kernel_status()
                if kernel['state'] in {'not_configured','connection_failed','auth_failed','version_unsupported'}:
                    raise ValueError('无法应用配置：'+kernel['message'])
                document=self._runtime_document(); self._validate_runtime_document(document)
                revision=self._runtime_revision(document); control,headers=self._control()
                if control.get('deployment')!='dedicated' or control.get('scope')!='full':
                    raise ValueError('共享内核缺少可信完整基线，禁止写入；请使用插件专用实例和完整配置范围')
                previous=getattr(self,'runtime_application',{})
                recovery=copy.deepcopy(previous.get('document')) if isinstance(previous.get('document'),dict) else self._recovery_document(control,self.state['proxy_entry'])
                import yaml
                candidate_payload={'path':'','payload':yaml.safe_dump(document,allow_unicode=True,sort_keys=False)}
                recovery_payload={'path':'','payload':yaml.safe_dump(recovery,allow_unicode=True,sort_keys=False)}
                self._persist_runtime_application({'status':'applying','saved_revision':revision,
                                                   'applied_revision':previous.get('applied_revision',''),
                                                   'document':previous.get('document'),'updated_at':int(time.time()),'message':'正在应用候选配置'})
                async with httpx.AsyncClient(base_url=control['url'],headers=headers,timeout=control['timeout'],trust_env=False) as client:
                    try:
                        response=await client.put('/configs?force=true',json=candidate_payload); response.raise_for_status()
                        running=await client.get('/configs'); running.raise_for_status()
                        proxies_response=await client.get('/proxies'); proxies_response.raise_for_status()
                        rules_response=await client.get('/rules'); rules_response.raise_for_status()
                        errors=self._verify_runtime_data(document,running.json(),proxies_response.json().get('proxies',{}),rules_response.json().get('rules',[]))
                        if errors: raise ValueError('；'.join(errors))
                    except (ValueError,httpx.HTTPError,OSError) as apply_error:
                        restored=False; restore_message=''
                        try:
                            response=await client.put('/configs?force=true',json=recovery_payload); response.raise_for_status()
                            running=await client.get('/configs'); running.raise_for_status()
                            proxies_response=await client.get('/proxies'); proxies_response.raise_for_status()
                            rules_response=await client.get('/rules'); rules_response.raise_for_status()
                            restore_errors=self._verify_runtime_data(recovery,running.json(),proxies_response.json().get('proxies',{}),rules_response.json().get('rules',[]))
                            if restore_errors: raise ValueError('；'.join(restore_errors))
                            restored=True
                        except (ValueError,httpx.HTTPError,OSError) as restore_error:
                            restore_message=safe_error(restore_error)
                        status=('pending_apply' if previous.get('applied_revision') else 'saved') if restored else 'restore_failed'
                        message='候选配置应用或核对失败，已恢复并重新核对' if restored else '候选配置失败，且运行配置恢复核对失败'
                        self._persist_runtime_application({'status':status,'saved_revision':revision,
                                                           'applied_revision':previous.get('applied_revision',''),
                                                           'document':recovery if restored else previous.get('document'),
                                                           'updated_at':int(time.time()),'message':message})
                        self.event({'action':'runtime_apply','result':status,'message':safe_error(apply_error),'restore':safe_error(restore_message)})
                        return error_response(message,500)
                application={'status':'applied','saved_revision':revision,'applied_revision':revision,
                             'document':document,'updated_at':int(time.time()),'message':'候选配置已应用并完整核对'}
                self._persist_runtime_application(application)
                self.event({'action':'runtime_apply','result':'ok','revision':revision,'groups':len(document['proxy-groups']),'rules':len(document['rules'])})
                return json_response({'applied':True,'status':'applied','saved_revision':revision,'applied_revision':revision})
            except (ValueError,httpx.HTTPError,OSError) as exc:
                self.event({'action':'runtime_apply','result':'failed','message':safe_error(exc)})
                return error_response(str(exc) if isinstance(exc,ValueError) else 'Mihomo 配置应用失败，请检查控制接口和内核日志')

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
            if node['kind']=='mihomo' and urlparse(node['endpoint']).scheme not in {'http','https','socks5','socks5h'}:
                kernel=await self._kernel_status()
                if kernel['state']!='applied':
                    self._set_health(node,'pending',None,kernel['message'])
                    return json_response({'node_id':node_id,'health':self.health[node_id],'skipped':True,'kernel':kernel})
            started=time.monotonic()
            if node['kind']=='mihomo' and urlparse(node['endpoint']).scheme not in {'http','https','socks5','socks5h'}:
                # Native Mihomo protocol URIs are measured by the running core when a
                # controller is configured. HTTP/SOCKS-compatible entries can be tested
                # directly and must not be blocked by the optional controller setting.
                control,headers=self._control(); timeout=max(1,min(int(payload.get('timeout',5) or 5),15))
                async with httpx.AsyncClient(base_url=control['url'],headers=headers,timeout=timeout,trust_env=False) as client:
                    response=await client.get('/proxies/'+quote(node.get('kernel_name',node['id']),safe='')+'/delay',params={'url':target,'timeout':timeout*1000})
                response.raise_for_status(); data=response.json()
                if not isinstance(data.get('delay'),int): raise ValueError('Mihomo 控制接口未返回延迟')
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
            nodes=[node for node in self.state['nodes'] if node['enabled'] and not node.get('excluded')]
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
                                  'failed':sum(item.get('status') in {'error','timeout'} for item in results),
                                  'skipped':sum(item.get('status')=='pending' for item in results)})
        except (ValueError,TypeError) as exc: return error_response(str(exc))

    async def control_status(self):
        try:
            control,headers=self._control()
            async with httpx.AsyncClient(base_url=control['url'],headers=headers,timeout=control['timeout'],trust_env=False) as client:
                version=await client.get('/version'); version.raise_for_status(); proxies=await client.get('/proxies'); proxies.raise_for_status()
            runtime=proxies.json().get('proxies',{})
            nodes={node.get('kernel_name'):node for node in self.state['nodes']}
            groups=[]
            for group in self.state['groups']:
                if group['id']=='direct' or not group.get('enabled',True): continue
                kernel_name=group.get('kernel_name','group-'+group['id']); current=runtime.get(kernel_name,{})
                members=[]
                for node_id in group.get('node_ids',[]):
                    node=next((item for item in self.state['nodes'] if item['id']==node_id),None)
                    if not node: continue
                    available=bool(node.get('enabled') and not node.get('excluded') and not node.get('invalid_reference')
                                   and node.get('support',{}).get('status','supported')=='supported')
                    members.append({'id':node['id'],'display_name':node.get('display_name',node.get('name',node['id'])),
                                    'kernel_name':node.get('kernel_name'),'available':available})
                selected=nodes.get(current.get('now')) if isinstance(current,dict) else None
                groups.append({'id':group['id'],'display_name':group['name'],'kernel_name':kernel_name,
                               'type':current.get('type','') if isinstance(current,dict) else '',
                               'selected_node_id':selected.get('id','') if selected else '',
                               'selected_display_name':selected.get('display_name',selected.get('name','')) if selected else '',
                               'members':members})
            return json_response({'version':version.json(),'groups':groups})
        except (ValueError,httpx.HTTPError,TypeError): return error_response('控制接口连接失败，请检查地址、密钥和网络')

    async def control_select(self):
        try:
            payload=await request.json(); control,headers=self._control()
            if control.get('deployment')!='dedicated': raise ValueError('共享内核当前只允许状态核对，不能切换代理组')
            group_id=ident(payload.get('group_id')); node_id=ident(payload.get('node_id'))
            group=next((item for item in self.state['groups'] if item['id']==group_id and item['id']!='direct'),None)
            node=next((item for item in self.state['nodes'] if item['id']==node_id),None)
            if not group or not node or node_id not in group.get('node_ids',[]): raise ValueError('代理组或节点引用无效')
            if not node.get('enabled') or node.get('excluded') or node.get('invalid_reference') or node.get('support',{}).get('status','supported')!='supported':
                raise ValueError('节点当前不可用于切换')
            name=group.get('kernel_name','group-'+group_id); kernel_node=node.get('kernel_name','node-'+node_id)
            async with httpx.AsyncClient(base_url=control['url'],headers=headers,timeout=control['timeout'],trust_env=False) as client:
                response=await client.request('PUT','/proxies/'+quote(name,safe=''),json={'name':kernel_node}); response.raise_for_status()
            group['selected']=node_id; await self.persist(self.state)
            self.event({'action':'control_select','group_id':group_id,'node_id':node_id,'result':'ok'})
            return json_response({'ok':True})
        except ValueError as exc: return error_response(str(exc))
        except httpx.HTTPError: return error_response('代理组切换失败，请检查控制接口权限')

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
        logger.info('代理管理中心 0.2.10 已加载')

    async def terminate(self):
        if self.auto_task:
            self.auto_task.cancel()
            try: await self.auto_task
            except asyncio.CancelledError: pass
            self.auto_task=None

    async def on_message(self,event:AstrMessageEvent): return
