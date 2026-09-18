from __future__ import annotations

import json


class _RequestsFacade:
    """Keep explicit proxy arguments local to the SDK module."""

    def __init__(self, requests_module, proxy: str):
        self._requests = requests_module
        self._proxy = proxy

    def __getattr__(self, name):
        return getattr(self._requests, name)

    def post(self, *args, **kwargs):
        kwargs.setdefault("proxies", {"http": self._proxy, "https": self._proxy})
        return self._requests.post(*args, **kwargs)

    def request(self, *args, **kwargs):
        kwargs.setdefault("proxies", {"http": self._proxy, "https": self._proxy})
        return self._requests.request(*args, **kwargs)


def _transport_execute(transport, proxy: str):
    def execute(conf, req, option=None):
        if option is None:
            option = transport.RequestOption()
        url = transport._build_url(conf.domain, req.uri, req.paths)
        headers = transport._build_header(req, option, conf)
        data = req.body
        if data is not None and not isinstance(data, transport.MultipartEncoder):
            data = transport.JSON.marshal(req.body).encode(transport.UTF_8)
        response = transport.requests.request(
            str(req.http_method.name),
            url,
            headers=headers,
            params=req.queries,
            data=data,
            timeout=conf.timeout,
            proxies={"http": proxy, "https": proxy},
        )
        result = transport.RawResponse()
        result.status_code = response.status_code
        result.headers = dict(response.headers)
        result.content = response.content
        return result

    return execute


def _transport_aexecute(transport, proxy: str):
    async def aexecute(conf, req, option=None):
        if option is None:
            option = transport.RequestOption()
        url = transport._build_url(conf.domain, req.uri, req.paths)
        headers = transport._build_header(req, option, conf)
        json_value, files, data = None, None, None
        if req.files:
            files = req.files
            if req.body is not None:
                json_value = None
                data = json.loads(transport.JSON.marshal(req.body))
        elif req.body is not None:
            json_value = json.loads(transport.JSON.marshal(req.body))
        async with transport.httpx.AsyncClient(proxy=proxy) as client:
            response = await client.request(
                str(req.http_method.name),
                url,
                headers=headers,
                params=req.queries,
                json=json_value,
                data=data,
                files=files,
                timeout=conf.timeout,
            )
        result = transport.RawResponse()
        result.status_code = response.status_code
        result.headers = dict(response.headers)
        result.content = response.content
        return result

    return aexecute


def install_sdk_patch(lease):
    """Patch the exact Lark SDK paths used by AstrBot 4.28.1.

    The returned callback restores every module-level hook. No AstrBot source
    file is modified and no global ``requests`` or ``httpx`` module is changed.
    """

    import importlib

    ws_client = importlib.import_module("lark_oapi.ws.client")
    transport = importlib.import_module("lark_oapi.core.http.transport")
    previous = getattr(ws_client, "_proxy_manager_patch", None)
    if isinstance(previous, dict) and callable(previous.get("restore")):
        previous["restore"]()
    originals = {
        "ws_kwargs": ws_client._ws_connect_kwargs,
        "ws_requests": ws_client.requests,
        "execute": transport.Transport.execute,
        "aexecute": transport.Transport.aexecute,
        "transport_requests": transport.requests,
    }

    def ws_kwargs():
        params = __import__("inspect").signature(ws_client.websockets.connect).parameters
        if "proxy" in params:
            return {"proxy": lease.http_proxy or None}
        return {}

    ws_client._ws_connect_kwargs = ws_kwargs
    if lease.http_proxy:
        ws_client.requests = _RequestsFacade(ws_client.requests, lease.http_proxy)
        transport.requests = _RequestsFacade(transport.requests, lease.http_proxy)
        transport.Transport.execute = staticmethod(_transport_execute(transport, lease.http_proxy))
        transport.Transport.aexecute = staticmethod(_transport_aexecute(transport, lease.http_proxy))

    def restore():
        ws_client._ws_connect_kwargs = originals["ws_kwargs"]
        ws_client.requests = originals["ws_requests"]
        transport.Transport.execute = originals["execute"]
        transport.Transport.aexecute = originals["aexecute"]
        transport.requests = originals["transport_requests"]

    ws_client._proxy_manager_patch = {"restore": restore}

    return restore


def build_proxy_adapter(base, lease):
    class ProxyLarkPlatformAdapter(base):
        _proxy_manager_lease = lease
        _proxy_manager_base = base

    ProxyLarkPlatformAdapter.__name__ = "ProxyManagedLarkPlatformAdapter"
    ProxyLarkPlatformAdapter.__qualname__ = "ProxyManagedLarkPlatformAdapter"
    return ProxyLarkPlatformAdapter
