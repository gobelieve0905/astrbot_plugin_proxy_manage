"""AstrBot 代理管理中心插件入口。"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import ipaddress
import json
import time
import uuid
from urllib.parse import urlparse, urlsplit

import httpx
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.star import Context, Star, StarTools
from astrbot.api.web import error_response, json_response, request

from .cores.registry import current_adapter
from .domain.constants import CONFIGURED, TEMPLATES
from .domain.identity import stable_node_id
from .domain.model import compiled_rules, ident, match_rule, normalize_state, validate_state
from .domain.security import redact_config, redact_diagnostics, restore_config, safe_error, safe_host, safe_url
from .importers.subscription import parse_subscription, summary, traffic_header
from .runtime.transaction import verified_recovery_document


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
        self.previews={}; self.probe_tasks={}; self.auto_task=None
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
        state,aliases=normalize_state(raw)
        self._id_aliases=aliases
        return state

    def _validate(self, value: object) -> dict:
        return validate_state(value)

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
            ('probe-task',self.probe_task,['POST']), ('probe-task-status',self.probe_task_status,['POST']),
            ('probe-task-cancel',self.probe_task_cancel,['POST']),
            ('runtime-config',self.runtime_config,['GET']), ('runtime-apply',self.runtime_apply,['POST']),
            ('kernel-status',self.kernel_status,['GET']),
            ('verify-outbound',self.verify_outbound,['POST']),
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
        result['application']={key:application.get(key) for key in (
            'status','saved_revision','applied_revision','updated_at','message','verification'
        )}
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
        if hasattr(self,'runtime_application') and self.runtime_application.get('status') not in {'restore_failed','fail_closed'}:
            try: saved_revision=self._adapter().revision(self._runtime_document())
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

    def _adapter(self):
        return current_adapter(getattr(self,'state',None))

    def _compiled_rules(self) -> list[dict]:
        return compiled_rules(self.state)

    def _match_rule(self,host:str) -> dict|None:
        return match_rule(self.state,host)

    def _record_outbound_verification(self,result:dict):
        """Persist the last redacted verification evidence without changing its runtime revision."""
        application=copy.deepcopy(getattr(self,'runtime_application',{}))
        application['verification']=redact_diagnostics({**result,'at':int(time.time())})
        try:
            self._persist_runtime_application(application)
        except OSError:
            logger.warning('实际出站验证状态写入失败')

    async def preview(self):
        try:
            host=safe_host((await request.json()).get('host'))
            route=self._match_rule(host)
            group,node=self.resolve(route['target'] if route else 'direct')
            return json_response({'host':host,'matched':route,'group':group,
                                  'node':node and {'id':node['id'],'name':node['name'],'kind':node['kind']}})
        except (ValueError,TypeError) as exc: return error_response(str(exc))

    async def verify_outbound(self):
        result={'verified':False,'entry':{'state':'not_started','message':'尚未发起请求'},
                'rule':{'state':'not_started','message':'尚未核对运行规则'},
                'exit':{'state':'unconfirmed','message':'尚未取得可验证出口证据'}}
        try:
            payload=await request.json(); url=str(payload.get('url','')); parsed=urlsplit(url)
            if parsed.scheme!='https' or not parsed.hostname: raise ValueError('实际出站验证只允许 HTTPS 地址')
            host=safe_host(parsed.hostname); route=self._match_rule(host); target=route['target'] if route else 'direct'
            group=next((item for item in self.state['groups'] if item['id']==target),None)
            if not group: raise ValueError('规则目标代理组不存在')
            proxy=self.state.get('proxy_entry',{}).get('http_url')
            if not proxy: raise ValueError('尚未配置统一 HTTP 代理入口')
            started=time.monotonic()
            async with httpx.AsyncClient(proxy=proxy,trust_env=False,follow_redirects=False,timeout=15) as client:
                response=await client.get(url,headers={'User-Agent':'astrbot-proxy-route-verifier/0.3.1'})
            response.raise_for_status()
            result.update({'host':host,'status_code':response.status_code,
                           'elapsed_ms':round((time.monotonic()-started)*1000),'matched_rule':route,
                           'group':{'id':group['id'],'name':group['name'],'kernel_name':group.get('kernel_name')}})
            result['entry']={'state':'passed','message':'统一代理入口已返回 HTTPS 响应'}
            kernel=await self._kernel_status()
            if kernel.get('state')!='applied':
                result['rule']={'state':'unconfirmed','message':'入口请求成功，但内核运行状态未通过核对：'+str(kernel.get('message','未知'))}
                self._record_outbound_verification(result)
                self.event({'action':'verify_outbound','result':'unconfirmed','host':host,'reason':'kernel_not_applied'})
                return json_response(result)
            result['rule']={'state':'matched' if route else 'default','message':'命中显式规则' if route else '命中受管理的默认 MATCH 规则'}
            try:
                body=response.json()
                value=body.get('ip') if isinstance(body,dict) else None
                exit_ip=str(ipaddress.ip_address(str(value))) if value else ''
            except (ValueError,TypeError,json.JSONDecodeError):
                exit_ip=''
            if not exit_ip:
                result['exit']={'state':'unconfirmed','message':'目标未返回可验证的出口 IP；入口和规则状态不能证明实际出口'}
                self._record_outbound_verification(result)
                self.event({'action':'verify_outbound','result':'unconfirmed','host':host,'reason':'exit_ip_missing'})
                return json_response(result)
            actual_selection='DIRECT'
            if group['id']!='direct':
                try:
                    actual_selection=await self._adapter().group_selection(self.state, group)
                except (ValueError,httpx.HTTPError,OSError,TypeError):
                    actual_selection=''
            if not actual_selection:
                result['exit']={'state':'unconfirmed','message':'已取得出口 IP，但无法回读运行代理组的实际选择'}
                self._record_outbound_verification(result)
                self.event({'action':'verify_outbound','result':'unconfirmed','host':host,'reason':'selection_unavailable'})
                return json_response(result)
            result['actual_selection']=actual_selection
            result['exit']={'state':'confirmed','ip':exit_ip,'message':'出口 IP 与运行代理组实际选择均已取得'}
            result['verified']=True
            self._record_outbound_verification(result)
            self.event({'action':'verify_outbound','result':'confirmed','host':host,'selection':actual_selection})
            return json_response(result)
        except ValueError as exc:
            return error_response(str(exc))
        except (httpx.HTTPError,OSError) as exc:
            result['entry']={'state':'failed','message':'统一代理入口请求失败'}
            result['error']=safe_error(exc)
            self._record_outbound_verification(result)
            self.event({'action':'verify_outbound','result':'failed','message':safe_error(exc)})
            return json_response(result)

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
        from proxy_manager.importers.subscription import decode_subscription
        return decode_subscription(text)

    def _parse_subscription(self,text:str,subscription_id:str):
        return parse_subscription(text,subscription_id)

    @staticmethod
    def _summary(nodes:list[dict],discovered:set[str]):
        return summary(nodes,discovered)

    @staticmethod
    def _traffic_header(headers:httpx.Headers):
        return traffic_header(headers)

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
                    response=await client.get(url,headers={'User-Agent':'astrbot-plugin-proxy-manage/0.3.1'})
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
        return stable_node_id(subscription_id,endpoint,protocol,connection)


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
                response=await client.get(subscription['url'],headers={'User-Agent':'astrbot-plugin-proxy-manage/0.3.1'})
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
        return self._adapter().control(self.state)

    async def _kernel_status(self) -> dict:
        adapter=self._adapter(); control=self.state['control']
        if not control['enabled'] or not control['url']:
            return adapter.inspect(self.state, getattr(self,'runtime_application',{}))
        try:
            fetched=await adapter.fetch_runtime(self.state)
            if fetched.get('state'): return fetched
            return adapter.inspect(self.state, getattr(self,'runtime_application',{}), fetched.get('runtime'), fetched.get('proxies'), fetched.get('rules'), fetched.get('version',''))
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in {401,403}:
                return {'state':'auth_failed','ready':False,'adapter':adapter.id,'message':'内核控制接口认证失败'}
            return {'state':'connection_failed','ready':False,'adapter':adapter.id,'message':'内核控制接口请求失败'}
        except (httpx.HTTPError,OSError,ValueError,TypeError):
            return {'state':'connection_failed','ready':False,'adapter':adapter.id,'message':'无法连接内核控制接口'}

    async def kernel_status(self):
        return json_response(await self._kernel_status())

    def _runtime_document(self) -> dict:
        adapter=self._adapter()
        document=adapter.render(self.state, self._compiled_rules())
        adapter.validate(document)
        return document

    def _runtime_revision(self, document: dict) -> str:
        return self._adapter().revision(document)

    def _validate_runtime_document(self, document: dict):
        self._adapter().validate(document)

    def _expected_rules(self, document: dict):
        return self._adapter().expected_rules(document)

    def _verify_runtime_data(self, document: dict, runtime: object, proxies: object, rules: object) -> list[str]:
        return self._adapter().verify(document, runtime, proxies, rules)

    def _fail_closed_document(self, control: dict, entry: dict) -> dict:
        return self._adapter().fail_closed_document(control, entry)

    def _verified_recovery_document(self, application: object):
        return verified_recovery_document(application, self._adapter())

    def _mihomo_proxy(self, node: dict) -> dict:
        return self._adapter().render_proxy(node)

    async def runtime_config(self):
        try:
            adapter=self._adapter(); document=self._runtime_document()
            return json_response({'config':adapter.redact(document),'mapping':{
                'nodes':{node['id']:{'name':node['name'],'kernel_name':node.get('kernel_name'),'subscription_id':node['subscription_id'],'executor':node.get('executor')}
                         for node in self.state['nodes'] if node['enabled']},
                'groups':{group['id']:{'name':group['name'],'kernel_name':group.get('kernel_name')}
                          for group in self.state['groups'] if group['enabled']},
            },'saved_revision':adapter.revision(document),
               'applied_revision':getattr(self,'runtime_application',{}).get('applied_revision',''),
               'status':getattr(self,'runtime_application',{}).get('status','saved'),
               'adapter':adapter.id})
        except (ValueError,TypeError) as exc:
            return error_response(str(exc))

    async def runtime_apply(self):
        async with self.apply_lock:
            try:
                kernel=await self._kernel_status()
                if kernel['state'] in {'not_configured','connection_failed','auth_failed','version_unsupported'}:
                    raise ValueError('无法应用配置：'+kernel['message'])
                adapter=self._adapter(); document=self._runtime_document(); adapter.validate(document)
                revision=adapter.revision(document); control,_headers=adapter.control(self.state)
                if control.get('deployment')!='dedicated' or control.get('scope')!='full':
                    raise ValueError('共享内核缺少可信完整基线，禁止写入；请使用插件专用实例和完整配置范围')
                previous=getattr(self,'runtime_application',{})
                recovery=self._verified_recovery_document(previous)
                recovery_kind='previous_verified' if recovery else 'fail_closed'
                if recovery is None:
                    recovery=adapter.fail_closed_document(control,self.state['proxy_entry'])
                adapter.validate(recovery)
                self._persist_runtime_application({'status':'applying','saved_revision':revision,
                                                   'applied_revision':previous.get('applied_revision',''),
                                                   'document':previous.get('document'),'updated_at':int(time.time()),'message':'正在应用候选配置'})
                try:
                    await adapter.apply(self.state, document)
                except (ValueError,httpx.HTTPError,OSError) as apply_error:
                    restored=False; restore_message=''
                    try:
                        await adapter.apply(self.state, recovery)
                        restored=True
                    except (ValueError,httpx.HTTPError,OSError) as restore_error:
                        restore_message=safe_error(restore_error)
                    status='pending_apply' if restored and recovery_kind=='previous_verified' else ('fail_closed' if restored else 'restore_failed')
                    message=(
                        '候选配置应用或核对失败，已恢复上一份已验证配置并重新核对'
                        if restored and recovery_kind=='previous_verified' else
                        ('候选配置失败，未找到已验证配置；已写入并核对 MATCH,REJECT 失败关闭配置' if restored else '候选配置失败，且运行配置恢复核对失败')
                    )
                    self._persist_runtime_application({'status':status,'saved_revision':revision,
                                                       'applied_revision':previous.get('applied_revision','') if recovery_kind=='previous_verified' else '',
                                                       'document':recovery if restored else previous.get('document'),
                                                       'updated_at':int(time.time()),'message':message})
                    self.event({'action':'runtime_apply','result':status,'message':safe_error(apply_error),'restore':safe_error(restore_message)})
                    return error_response(message,500)
                application={'status':'applied','saved_revision':revision,'applied_revision':revision,
                             'document':document,'updated_at':int(time.time()),'message':'候选配置已应用并完整核对'}
                self._persist_runtime_application(application)
                self.event({'action':'runtime_apply','result':'ok','revision':revision,'groups':len(document.get('proxy-groups',[])),'rules':len(document.get('rules',[]))})
                return json_response({'applied':True,'status':'applied','saved_revision':revision,'applied_revision':revision,'adapter':adapter.id})
            except (ValueError,httpx.HTTPError,OSError) as exc:
                self.event({'action':'runtime_apply','result':'failed','message':safe_error(exc)})
                return error_response(str(exc) if isinstance(exc,ValueError) else '内核配置应用失败，请检查控制接口和内核日志')

    def _set_health(self,node:dict,status:str,latency:int|None=None,error:str=''):
        executor=node.get('executor') or ('direct-http' if node.get('protocol') in {'http','https','socks','socks5','socks5h'} else self._adapter().id)
        self.health[node['id']]={'status':status,'latency_ms':latency,'error':str(error)[:300],
                                 'checked_at':int(time.time()),'target':executor+'-control' if executor not in {'','direct-http'} else 'direct-http'}
        self.persist_health()

    async def node_probe(self):
        try: return json_response(await self._probe_one(await request.json()))
        except (ValueError,httpx.HTTPError,OSError) as exc: return error_response(str(exc) if isinstance(exc,ValueError) else '测速失败或超时')

    async def _probe_node(self,payload:dict):
        result=await self._probe_one(payload)
        if result.get('status')=='skipped': result['skipped']=True
        return json_response(result)

    async def _probe_one(self,payload:dict) -> dict:
            if not isinstance(payload,dict): raise ValueError('请求格式无效')
            node_id=ident(payload.get('node_id')); target=str(payload.get('url','https://www.gstatic.com/generate_204'))
            node=next((item for item in self.state['nodes'] if item['id']==node_id),None)
            if not node: return {'node_id':node_id,'status':'skipped','reason':'节点不存在'}
            reason=''
            if not node.get('enabled'): reason='节点已禁用'
            elif node.get('excluded'): reason='节点已排除'
            elif node.get('invalid_reference'): reason='节点引用已失效'
            elif node.get('support',{}).get('status')!='supported': reason=node.get('support',{}).get('reason') or '协议不支持'
            if reason: return {'node_id':node_id,'status':'skipped','reason':reason}
            if not safe_url(target): raise ValueError('测速目标只允许 HTTP 或 HTTPS 地址')
            native=urlparse(node['endpoint']).scheme not in {'http','https','socks5','socks5h'}
            if native:
                kernel=await self._kernel_status()
                if kernel['state']!='applied': return {'node_id':node_id,'status':'skipped','reason':'内核未就绪：'+kernel['message']}
            started=time.monotonic()
            try:
              timeout=max(1,min(int(payload.get('timeout',5) or 5),15))
              if native:
                latency=await self._adapter().probe(self.state, node, target, timeout)
              else:
                async with httpx.AsyncClient(proxy=node['endpoint'],trust_env=False,follow_redirects=False,timeout=timeout) as client:
                    response=await client.get(target)
                if response.status_code>=400: raise ValueError('HTTP 状态码 '+str(response.status_code))
                latency=round((time.monotonic()-started)*1000)
              self._set_health(node,'ok',latency)
              return {'node_id':node_id,'status':'ok','latency_ms':latency,'health':self.health[node_id]}
            except (ValueError,httpx.HTTPError,OSError) as exc:
              error=str(exc) if isinstance(exc,ValueError) else '测速失败或超时'
              status='timeout' if 'timeout' in type(exc).__name__.lower() else 'error'; self._set_health(node,status,None,error)
              return {'node_id':node_id,'status':status,'reason':error,'health':self.health[node_id]}

    async def _run_probe_task(self,task_id:str,node_ids:list[str],target:str,timeout:int,concurrency:int):
        task=self.probe_tasks[task_id]; semaphore=asyncio.Semaphore(concurrency)
        async def run(node_id):
            async with semaphore:
                if task['cancelled']: return {'node_id':node_id,'status':'cancelled','reason':'任务已取消'}
                return await self._probe_one({'node_id':node_id,'url':target,'timeout':timeout})
        pending=[asyncio.create_task(run(node_id)) for node_id in node_ids]
        try:
            for future in asyncio.as_completed(pending):
                result=await future; task['results'].append(result); task['completed']+=1
                if task['cancelled']:
                    for item in pending:
                        if not item.done(): item.cancel()
                    break
        finally:
            if task['cancelled']:
                finished={item['node_id'] for item in task['results']}
                task['results'].extend({'node_id':node_id,'status':'cancelled','reason':'任务已取消'} for node_id in node_ids if node_id not in finished)
                task['completed']=len(task['results'])
            task['status']='cancelled' if task['cancelled'] else 'completed'; task['finished_at']=int(time.time())

    async def probe_task(self):
        try:
            payload=await request.json(); requested=payload.get('node_ids')
            node_ids=[node['id'] for node in self.state['nodes']] if not isinstance(requested,list) else list(dict.fromkeys(ident(value) for value in requested))
            if not node_ids: raise ValueError('没有可测速的节点')
            target=str(payload.get('url','https://www.gstatic.com/generate_204')); timeout=max(1,min(int(payload.get('timeout',5)),15)); concurrency=max(1,min(int(payload.get('concurrency',5)),20))
            if not safe_url(target): raise ValueError('测速目标只允许 HTTP 或 HTTPS 地址')
            task_id=uuid.uuid4().hex; self.probe_tasks[task_id]={'id':task_id,'status':'running','total':len(node_ids),'completed':0,'results':[],'cancelled':False,'started_at':int(time.time())}
            asyncio.create_task(self._run_probe_task(task_id,node_ids,target,timeout,concurrency))
            return json_response({'task_id':task_id,'total':len(node_ids)})
        except (ValueError,TypeError) as exc: return error_response(str(exc))

    async def probe_task_status(self):
        payload=await request.json(); task=self.probe_tasks.get(str(payload.get('task_id','')))
        if not task: return error_response('测速任务不存在或已过期')
        results=list(task['results']); summary={key:sum(item['status']==key for item in results) for key in ('ok','error','timeout','skipped','cancelled')}
        return json_response({**task,'summary':summary})

    async def probe_task_cancel(self):
        payload=await request.json(); task=self.probe_tasks.get(str(payload.get('task_id','')))
        if not task: return error_response('测速任务不存在或已过期')
        task['cancelled']=True
        return json_response({'task_id':task['id'],'status':'cancelling'})

    async def nodes_probe(self):
        return await self.probe_task()

    async def control_status(self):
        try:
            adapter=self._adapter(); data=await adapter.proxies(self.state)
            runtime=data.get('proxies',{})
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
                                    'kernel_name':node.get('kernel_name'),'executor':node.get('executor'),'available':available})
                selected=nodes.get(current.get('now')) if isinstance(current,dict) else None
                groups.append({'id':group['id'],'display_name':group['name'],'kernel_name':kernel_name,
                               'type':current.get('type','') if isinstance(current,dict) else '',
                               'selected_node_id':selected.get('id','') if selected else '',
                               'selected_display_name':selected.get('display_name',selected.get('name','')) if selected else '',
                               'members':members})
            return json_response({'version':data.get('version'),'adapter':adapter.id,'groups':groups})
        except (ValueError,httpx.HTTPError,TypeError): return error_response('控制接口连接失败，请检查地址、密钥和网络')

    async def control_select(self):
        try:
            payload=await request.json(); adapter=self._adapter(); control,_=adapter.control(self.state)
            if control.get('deployment')!='dedicated': raise ValueError('共享内核当前只允许状态核对，不能切换代理组')
            group_id=ident(payload.get('group_id')); node_id=ident(payload.get('node_id'))
            group=next((item for item in self.state['groups'] if item['id']==group_id and item['id']!='direct'),None)
            node=next((item for item in self.state['nodes'] if item['id']==node_id),None)
            if not group or not node or node_id not in group.get('node_ids',[]): raise ValueError('代理组或节点引用无效')
            if not node.get('enabled') or node.get('excluded') or node.get('invalid_reference') or node.get('support',{}).get('status','supported')!='supported':
                raise ValueError('节点当前不可用于切换')
            await adapter.select(self.state, group, node)
            group['selected']=node_id; await self.persist(self.state)
            self.event({'action':'control_select','group_id':group_id,'node_id':node_id,'result':'ok','adapter':adapter.id})
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
        logger.info('代理管理中心 0.3.1 已加载')

    async def terminate(self):
        if self.auto_task:
            self.auto_task.cancel()
            try: await self.auto_task
            except asyncio.CancelledError: pass
            self.auto_task=None

    async def on_message(self,event:AstrMessageEvent): return
