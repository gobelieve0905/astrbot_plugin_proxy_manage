from __future__ import annotations

import base64
import copy
import json
import re
from urllib.parse import quote, unquote, urlencode

import httpx

from proxy_manager.domain.identity import parameter_version, protocol_support, region_of, stable_node_id, suspected_notice
from proxy_manager.domain.model import compatibility_engine, compatibility_kind, executor_for


def decode_subscription(text:str) -> str:
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


def parse_subscription(text:str,subscription_id:str):
    decoded=decode_subscription(text); nodes=[]; discovered=set()
    def add_node(protocol:str,endpoint:str,connection:dict,name:str,index:int,source_format:str):
        protocol='socks5' if protocol=='socks' else protocol.lower()
        status,reason=protocol_support(protocol); notice,notice_reason=suspected_notice(name)
        node_id=stable_node_id(subscription_id,endpoint,protocol,connection)
        executor,adapters=executor_for(protocol)
        node={'id':node_id,'name':str(name)[:120],'display_name':str(name)[:120],'protocol':protocol,
              'source_name':str(name)[:120],'user_alias':'',
              'executor':executor,'adapters':adapters,
              'engine':compatibility_engine(protocol,executor),
              'kind':compatibility_kind(protocol,executor),
              'endpoint':endpoint,'connection':copy.deepcopy(connection),'subscription_id':subscription_id,
              'enabled':status=='supported','excluded':False,'exclusion_reason':'','invalid_reference':False,
              'parameter_version':parameter_version(protocol,endpoint,connection),
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


def summary(nodes:list[dict],discovered:set[str]):
    protocols={}; regions={}; names=[]
    for node in nodes:
        protocols[node['protocol']]=protocols.get(node['protocol'],0)+1
        region=node.get('region','其他'); regions[region]=regions.get(region,0)+1
        if len(names)<8: names.append(node['name'])
    return {'count':len(nodes),'protocols':protocols,'regions':regions,'names':names,
            'discovered':sorted(discovered),'naming':'URI 节点名称' if names else '未识别名称'}


def traffic_header(headers:httpx.Headers):
    values=headers.get_list('subscription-userinfo')
    data={}
    for value in values:
        for key,text in re.findall(r'(upload|download|total|expire)=([^;]+)',value,re.I):
            try: data[key.lower()]=int(text.strip())
            except ValueError: pass
    return data
