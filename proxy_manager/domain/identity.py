from __future__ import annotations

import base64
import hashlib
import json
import re
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit

from .constants import ADVANCED_SCHEMES, HTTP_PROTOCOLS, NOTICE_PATTERNS, REGIONS, SUPPORTED_PROTOCOLS


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
    if declared and declared not in {'mihomo','direct-http'}: return 'socks5' if declared=='socks' else declared
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
    protocol=str(protocol or '').lower(); endpoint=str(endpoint or '').strip()
    if not protocol and '://' in endpoint:
        protocol=endpoint.split('://',1)[0].lower()
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
    protocol=str(protocol or '').lower(); endpoint=str(endpoint or '').strip()
    if not protocol and '://' in endpoint:
        protocol=endpoint.split('://',1)[0].lower()
    if endpoint and '://' in endpoint and protocol!='vmess':
        try:
            parsed=urlsplit(endpoint); host=(parsed.hostname or '').lower(); port=parsed.port
            principal=unquote(parsed.username or '')
            if host and port and (principal or protocol in HTTP_PROTOCOLS):
                return json.dumps([protocol,host,port,principal],separators=(',',':'))
        except (TypeError,ValueError): pass
    if isinstance(connection,dict):
        server=str(connection.get('server','')).lower(); port=connection.get('port')
        principal=connection.get('username') or connection.get('user') or connection.get('uuid')
        if server and port and (principal or protocol in HTTP_PROTOCOLS):
            return json.dumps([protocol,server,port,str(principal or '')],separators=(',',':'))
    return canonical_connection(protocol,endpoint,connection)


def stable_node_id(subscription_id:str, endpoint:str, protocol:str='', connection:object=None) -> str:
    identity=identity_material(protocol,endpoint,connection)
    return subscription_id+'-'+hashlib.sha256(identity.encode()).hexdigest()[:16]


def parameter_version(protocol:str, endpoint:str, connection:object=None) -> str:
    return hashlib.sha256(canonical_connection(protocol,endpoint,connection).encode()).hexdigest()[:16]
