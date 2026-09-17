from __future__ import annotations

import copy
import hashlib
import re

from .constants import DIRECT, HTTP_PROTOCOLS, KINDS, MATCHES, MODES
from .identity import canonical_connection, infer_protocol, parameter_version, protocol_support, region_of, stable_node_id, suspected_notice
from .security import ident, safe_proxy_endpoint, safe_url


def executor_for(protocol: str, item: dict|None=None) -> tuple[str,list[str]]:
    from proxy_manager.cores.registry import adapters_for, default_executor
    adapters=adapters_for(protocol)
    stored=str((item or {}).get('executor') or '')
    if stored in adapters: return stored,adapters
    return default_executor(protocol),adapters


def compatibility_kind(protocol: str, executor: str) -> str:
    if protocol in {'http','https','socks5','socks5h'}: return protocol
    if protocol=='socks': return 'socks5'
    return executor or 'mihomo'


def compatibility_engine(protocol: str, executor: str) -> str:
    if protocol in HTTP_PROTOCOLS: return executor or 'direct-http'
    return executor or 'mihomo'


def normalize_node(item: dict, aliases: dict[str,str]) -> dict|None:
    if not isinstance(item,dict) or not ident(item.get('id')): return None
    endpoint=str(item.get('endpoint','')).strip()
    protocol=infer_protocol(item); connection=copy.deepcopy(item.get('connection')) if isinstance(item.get('connection'),dict) else {}
    if not connection and endpoint: connection={'uri':endpoint}
    old_id=ident(item['id']); subscription_id=ident(item.get('subscription_id'))
    node_id=stable_node_id(subscription_id,endpoint,protocol,connection) if subscription_id else old_id
    aliases[old_id]=node_id
    support_status,support_reason=protocol_support(protocol)
    support=item.get('support') if isinstance(item.get('support'),dict) else {}
    notice,notice_reason=suspected_notice(item.get('display_name',item.get('name',item['id'])))
    source_info=item.get('source') if isinstance(item.get('source'),dict) else {}
    executor,adapters=executor_for(protocol,item)
    return {
        'id':node_id, 'name':str(item.get('name',item.get('display_name',item['id'])))[:120],
        'display_name':str(item.get('display_name',item.get('name',item['id'])))[:120],
        'source_name':str(item.get('source_name',item.get('name',item['id'])))[:120],
        'user_alias':str(item.get('user_alias',''))[:120],
        'region':str(item.get('region') or region_of(str(item.get('source_name',item.get('display_name',item.get('name',''))))))[:40],
        'protocol':protocol[:24],
        'executor':executor[:24],
        'adapters':adapters,
        'engine':str(item.get('engine') or compatibility_engine(protocol,executor))[:24],
        'kind':str(item.get('kind') or compatibility_kind(protocol,executor))[:24],
        'endpoint':endpoint, 'connection':connection,
        'subscription_id':subscription_id, 'enabled':bool(item.get('enabled',True)),
        'excluded':bool(item.get('excluded',False)), 'exclusion_reason':str(item.get('exclusion_reason',''))[:160],
        'invalid_reference':bool(item.get('invalid_reference',False)),
        'parameter_version':parameter_version(protocol,endpoint,connection),
        'kernel_name':'node-'+node_id,
        'source':{'type':source_info.get('type','subscription' if subscription_id else 'manual'),
                  'subscription_id':subscription_id,'format':str(source_info.get('format','legacy'))[:24],
                  'index':int(source_info.get('index',-1) if source_info.get('index') is not None else -1)},
        'support':{'status':support_status,'reason':str(support.get('reason') or support_reason)[:200]},
        'suspected_notice':bool(item.get('suspected_notice',notice)),
        'notice_reason':str(item.get('notice_reason',notice_reason))[:160],
    }


def normalize_state(raw: object) -> tuple[dict,dict[str,str]]:
    source=raw if isinstance(raw,dict) else {}
    node_by_id={}; aliases={}
    values=source.get('nodes',[]) if isinstance(source.get('nodes',[]),list) else []
    for item in values:
        normalized_node=normalize_node(item,aliases)
        if not normalized_node: continue
        existing=node_by_id.get(normalized_node['id'])
        if not existing or (normalized_node['display_name'],normalized_node['parameter_version']) < (existing['display_name'],existing['parameter_version']):
            node_by_id[normalized_node['id']]=normalized_node
    nodes=list(node_by_id.values())

    group_values=source.get('groups') if isinstance(source.get('groups'),list) else []
    if not group_values:
        profile_values=source.get('profiles',[]) if isinstance(source.get('profiles',[]),list) else []
        for item in profile_values:
            if not isinstance(item,dict) or not item.get('id'): continue
            item_id=ident(item['id']); node_id='legacy-'+item_id
            if item.get('endpoint'):
                endpoint=str(item['endpoint']).strip(); protocol=infer_protocol(item)
                support_status,support_reason=protocol_support(protocol)
                executor,adapters=executor_for(protocol,item)
                nodes.append({'id':node_id,'name':str(item.get('name',item_id))[:120],'display_name':str(item.get('name',item_id))[:120],
                              'source_name':str(item.get('name',item_id))[:120],'user_alias':'',
                              'protocol':protocol,'executor':executor,'adapters':adapters,
                              'engine': compatibility_engine(protocol,executor),
                              'kind':str(item.get('kind') or compatibility_kind(protocol,executor))[:24],
                              'endpoint':endpoint,'connection':{'uri':endpoint},
                              'subscription_id':'','enabled':True,'excluded':False,'exclusion_reason':'','invalid_reference':False,
                              'parameter_version':parameter_version(protocol,endpoint,{}),
                              'kernel_name':'node-'+node_id,'source':{'type':'legacy','subscription_id':'','format':'profile','index':-1},
                              'support':{'status':support_status,'reason':support_reason},'suspected_notice':False,'notice_reason':''})
            group_values.append({'id':item_id,'name':item.get('name',item_id),
                                 'mode':'direct' if item.get('kind')=='direct' else 'select',
                                 'node_ids':[node_id] if item.get('endpoint') else [],
                                 'selected':node_id if item.get('endpoint') else ''})
    groups=[]
    for item in group_values:
        if not isinstance(item,dict) or not ident(item.get('id')): continue
        groups.append({
            'id':ident(item['id']), 'name':str(item.get('name',item['id']))[:80],
            'kernel_name':'DIRECT' if ident(item['id'])=='direct' else 'group-'+ident(item['id']),
            'mode':item.get('mode') if item.get('mode') in MODES else 'select',
            'node_ids':list(dict.fromkeys(aliases.get(ident(value),ident(value)) for value in item.get('node_ids',[]) if ident(value))),
            'selected':aliases.get(ident(item.get('selected')),ident(item.get('selected'))), 'enabled':bool(item.get('enabled',True)),
            'test_url':str(item.get('test_url','https://www.gstatic.com/generate_204'))[:500],
            'test_interval':max(30,min(int(item.get('test_interval',300) or 300),86400)),
            'tolerance':max(0,min(int(item.get('tolerance',50) or 0),5000)),
            'failure_policy':item.get('failure_policy') if item.get('failure_policy') in {'fail-closed','keep-last'} else 'fail-closed',
        })
    if not any(group['id']=='direct' for group in groups): groups.insert(0,dict(DIRECT))
    group_ids={group['id'] for group in groups}

    routes=[]
    route_values=source.get('routes',[]) if isinstance(source.get('routes',[]),list) else []
    for index,item in enumerate(route_values):
        if not isinstance(item,dict): continue
        target=ident(item.get('target') or item.get('profile_id'))
        if target not in group_ids: continue
        try:
            from .security import safe_host
            host=safe_host(item.get('host'))
        except ValueError:
            continue
        routes.append({'id':ident(item.get('id')) or f'rule-{index+1}','host':host,
                       'match':item.get('match') if item.get('match') in MATCHES else 'exact',
                       'target':target,'priority':max(1,min(int(item.get('priority',100) or 100),10000)),
                       'enabled':bool(item.get('enabled',True))})
    routes.sort(key=lambda item:item['priority'])

    from .security import safe_host
    rule_groups=[]
    values=source.get('rule_groups',[]) if isinstance(source.get('rule_groups'),list) else []
    for index,item in enumerate(values):
        if not isinstance(item,dict): continue
        target=ident(item.get('target'))
        if target not in group_ids: continue
        domains=[]
        for domain in item.get('domains',[]) if isinstance(item.get('domains'),list) else []:
            try:
                if isinstance(domain,str): host=safe_host(domain); match='suffix'
                else: host=safe_host(domain.get('host')); match=domain.get('match') if domain.get('match') in MATCHES else 'exact'
                domains.append({'host':host,'match':match})
            except (ValueError,AttributeError): continue
        if domains:
            rule_groups.append({'id':ident(item.get('id')) or f'rules-{index+1}','name':str(item.get('name','规则组'))[:80],
                                'domains':domains,'priority':max(1,min(int(item.get('priority',100) or 100),10000)),
                                'target':target,'enabled':bool(item.get('enabled',True))})
    if not rule_groups:
        for route in routes:
            rule_groups.append({'id':route['id'],'name':route['host'],'domains':[{'host':route['host'],'match':route['match']}],
                                'priority':route['priority'],'target':route['target'],'enabled':route['enabled']})

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
            'node_ids':list(dict.fromkeys(aliases.get(ident(value),ident(value)) for value in item.get('node_ids',[]) if ident(value))),
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
        'version':3, 'migration':{'stable_identity':1,'core_adapter':1}, 'name':str(source.get('name','默认配置'))[:80],
        'nodes':nodes, 'groups':groups, 'routes':routes, 'rule_groups':rule_groups, 'platforms':platforms,
        'subscriptions':subscriptions,
        'control':{'enabled':bool(control.get('enabled',False)),'url':str(control.get('url','')).rstrip('/')[:300],
                   'secret':str(control.get('secret',''))[:500],'timeout':max(3,min(timeout,30)),
                   'deployment':control.get('deployment') if control.get('deployment') in {'existing','dedicated'} else 'existing',
                   'scope':control.get('scope') if control.get('scope') in {'providers-groups-rules','full'} else 'providers-groups-rules',
                   'listen':str(control.get('listen','127.0.0.1:9090')).strip()[:200]},
        'proxy_entry':{'http_url':str(entry.get('http_url','')).rstrip('/')[:300],
                       'socks_url':str(entry.get('socks_url','')).rstrip('/')[:300],
                       'source':entry.get('source') if entry.get('source') in {'configured','detected','unknown'} else 'unknown'},
    }, aliases


def validate_state(value: object) -> dict:
    if not isinstance(value,dict): raise ValueError('配置格式无效')
    for key in ('nodes','groups','routes','subscriptions'):
        if not isinstance(value.get(key),list): raise ValueError(key+' 必须是数组')
    for item in value['subscriptions']:
        if not isinstance(item,dict) or not safe_url(item.get('url')):
            raise ValueError('订阅地址无效，只允许 HTTP 或 HTTPS')
    state,_=normalize_state(value); node_ids=set(); group_ids=set()
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
        if group['mode'] in {'url-test','fallback'} and not safe_url(group['test_url']):
            raise ValueError('代理组测速目标无效：'+group['id'])
    seen=set()
    for route in state['routes']:
        if route['target'] not in group_ids: raise ValueError('规则引用不存在代理组：'+route['target'])
        key=(route['host'],route['match'],route['priority'])
        if key in seen: raise ValueError('相同优先级存在重复规则：'+route['host'])
        seen.add(key)
    for key,platform in state['platforms'].items():
        if platform['group_id'] not in group_ids: raise ValueError('平台引用不存在代理组：'+key)
    seen_domains={}
    for rule_group in state['rule_groups']:
        if rule_group['target'] not in group_ids: raise ValueError('规则组引用不存在代理组：'+rule_group['id'])
        if not rule_group['enabled']: continue
        for domain in rule_group['domains']:
            key=(domain['host'],domain['match'],rule_group['priority'])
            if key in seen_domains: raise ValueError('规则冲突：'+domain['host']+' 与 '+seen_domains[key])
            seen_domains[key]=rule_group['name']
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


def compiled_rules(state:dict) -> list[dict]:
    compiled=[]
    for rule_group in state.get('rule_groups',[]):
        if not rule_group['enabled']: continue
        for position,domain in enumerate(rule_group['domains']):
            compiled.append({'rule_group_id':rule_group['id'],'rule_group':rule_group['name'],'host':domain['host'],
                             'match':domain['match'],'target':rule_group['target'],'priority':rule_group['priority'],
                             'position':position})
    compiled.sort(key=lambda item:(item['priority'],item['position'],item['rule_group_id']))
    return compiled


def match_rule(state:dict, host:str) -> dict|None:
    for route in compiled_rules(state):
        base=route['host'].removeprefix('*.')
        if (route['match']=='exact' and base==host) or (route['match']=='suffix' and (host==base or host.endswith('.'+base))):
            return route
    return None
