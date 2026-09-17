from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urljoin, urlsplit

import httpx

MAX_REDIRECTS=5


async def resolve_public_host(host: str, port: int) -> tuple[str,...]:
    try:
        values=await asyncio.get_running_loop().getaddrinfo(host,port,type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError('目标域名无法解析') from exc
    addresses=tuple(dict.fromkeys(value[4][0] for value in values))
    if not addresses:
        raise ValueError('目标域名没有可用地址')
    for address in addresses:
        try: parsed=ipaddress.ip_address(address)
        except ValueError as exc: raise ValueError('目标域名返回了无效地址') from exc
        if not parsed.is_global:
            raise ValueError('禁止访问回环、内网、链路本地、保留地址或云元数据地址')
    return addresses


async def validate_public_url(value: object, *, https_only: bool=False) -> str:
    text=str(value or '').strip()
    try:
        parsed=urlsplit(text); port=parsed.port
    except ValueError as exc:
        raise ValueError('目标地址无效') from exc
    schemes={'https'} if https_only else {'http','https'}
    if parsed.scheme.lower() not in schemes or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('目标地址只允许不含认证信息的 '+('/'.join(sorted(schemes))).upper()+' URL')
    host=parsed.hostname.rstrip('.').lower()
    if host=='localhost' or host.endswith(('.localhost','.local','.internal')):
        raise ValueError('禁止访问本机或内部域名')
    await resolve_public_host(host,port or (443 if parsed.scheme.lower()=='https' else 80))
    return text


async def fetch_public_url(url: object, *, headers: dict|None=None, timeout: float=20,
                           max_bytes: int=10*1024*1024) -> httpx.Response:
    current=str(url or '').strip()
    async with httpx.AsyncClient(timeout=timeout,follow_redirects=False,trust_env=False,
                                 limits=httpx.Limits(max_connections=4)) as client:
        for redirect in range(MAX_REDIRECTS+1):
            current=await validate_public_url(current)
            request=client.build_request('GET',current,headers=headers)
            response=await client.send(request,stream=True)
            try:
                if response.status_code in {301,302,303,307,308}:
                    if redirect==MAX_REDIRECTS: raise ValueError('目标重定向次数过多')
                    location=response.headers.get('location','').strip()
                    if not location: raise ValueError('目标返回了无地址的重定向')
                    current=urljoin(current,location); continue
                content=bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content)>max_bytes: raise ValueError('目标响应超过大小限制')
                return httpx.Response(response.status_code,headers=response.headers,content=bytes(content),request=request)
            finally:
                await response.aclose()
    raise ValueError('目标请求失败')
