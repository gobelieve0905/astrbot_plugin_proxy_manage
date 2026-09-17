from __future__ import annotations

import copy
import re
from urllib.parse import urlparse

from .constants import ADVANCED_SCHEMES, CONFIGURED, SENSITIVE_KEYS


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
    text=str(value or '')[:300]
    text=re.sub(r'(?i)(https?|socks5h?|ss|ssr|vmess|vless|trojan|hysteria2?|tuic|anytls)://[^\s]+',
                lambda m: m.group(1)+'://[redacted]',text)
    text=re.sub(r'(?i)(authorization\s*:\s*bearer|bearer)\s+[^\s,;]+',r'\1 [redacted]',text)
    return re.sub(r'(?i)\b(password|passwd|secret|token|uuid|username)\s*[=:]\s*[^\s,;]+',
                  lambda m:m.group(1)+'=[redacted]',text)


def redact_config(value: object, key: str='') -> object:
    if isinstance(value,dict):
        return {name:redact_config(item,str(name).lower()) for name,item in value.items()}
    if isinstance(value,list): return [redact_config(item,key) for item in value]
    if isinstance(value,str):
        if key in SENSITIVE_KEYS and value: return CONFIGURED
        if '://' in value and safe_proxy_endpoint(value):
            return urlparse(value).scheme+'://'+CONFIGURED
    return value


def restore_config(value: object, previous: object) -> object:
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
