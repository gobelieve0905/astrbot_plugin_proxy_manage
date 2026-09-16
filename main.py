"""AstrBot Proxy Manager: safe, provider-neutral egress management."""
from __future__ import annotations

import asyncio
import json
import re
import time
from urllib.parse import urlparse

import httpx
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.api.web import error_response, json_response, request

DEFAULT = {
    "profiles": [{"id": "direct", "name": "直连", "kind": "direct", "enabled": True}],
    "routes": [],
    "nodes": [],
}


def _safe_url(value: str, *, allow_credentials: bool = False) -> bool:
    try:
        parsed = urlparse(value)
        _ = parsed.port
        hostname = parsed.hostname
    except (TypeError, ValueError):
        return False
    return (
        parsed.scheme in {"http", "https", "socks5", "socks5h"}
        and bool(hostname)
        and (allow_credentials or (not parsed.username and not parsed.password))
    )


def _safe_host(value: object) -> str:
    host = str(value or "").strip().lower().rstrip(".")
    if not host or len(host) > 253 or any(char.isspace() for char in host):
        raise ValueError("请输入有效域名")
    if not re.fullmatch(r"(?:\*\.)?[a-z0-9.-]+", host) or ".." in host:
        raise ValueError("域名只能包含字母、数字、点、短横线或通配符")
    return host


@register("astrbot_plugin_proxy_manage", "gobelieve", "可视化管理 AstrBot 代理出口", "0.1.1")
class ProxyManager(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.context = context
        self.config = config
        self.data_dir = StarTools.get_data_dir("astrbot_plugin_proxy_manage")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.data_dir / "config.json"
        self.event_path = self.data_dir / "events.jsonl"
        self.lock = asyncio.Lock()
        self.state = self._load()
        self.events: list[dict] = self._load_events()
        self._register_routes()

    def _load(self) -> dict:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return self._normalize(raw)
        except (OSError, ValueError):
            try:
                raw = json.loads(self.config.get("config_json", "{}"))
            except (TypeError, ValueError):
                raw = {}
            return self._normalize(raw)

    def _normalize(self, raw: object) -> dict:
        source = raw if isinstance(raw, dict) else {}
        profiles = []
        profile_values = source.get("profiles", DEFAULT["profiles"])
        if not isinstance(profile_values, list):
            profile_values = []
        for item in profile_values:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            kind = item.get("kind", "direct")
            if kind not in {"direct", "http", "socks5", "mihomo"}:
                continue
            profile_id = str(item["id"]).strip()[:64]
            if not profile_id or any(p["id"] == profile_id for p in profiles):
                continue
            profiles.append({
                "id": profile_id, "name": str(item.get("name", profile_id))[:80],
                "kind": kind, "node_id": str(item.get("node_id", ""))[:64],
                "endpoint": str(item.get("endpoint", ""))[:300],
                "enabled": bool(item.get("enabled", True)),
                "fail_closed": bool(item.get("fail_closed", kind != "direct")),
            })
        if not any(p["id"] == "direct" for p in profiles):
            profiles.insert(0, dict(DEFAULT["profiles"][0]))
        profile_ids = {p["id"] for p in profiles}
        routes = []
        route_values = source.get("routes", [])
        if isinstance(route_values, list):
            for item in route_values:
                if not isinstance(item, dict) or item.get("profile_id") not in profile_ids:
                    continue
                try:
                    host = _safe_host(item.get("host"))
                except ValueError:
                    continue
                routes.append({
                    "host": host,
                    "profile_id": str(item["profile_id"])[:64],
                    "match": item.get("match") if item.get("match") in {"exact", "suffix"} else "exact",
                    "enabled": bool(item.get("enabled", True)),
                })
        nodes = []
        node_values = source.get("nodes", [])
        if isinstance(node_values, list):
            for item in node_values:
                endpoint = str(item.get("endpoint", "")) if isinstance(item, dict) else ""
                if not isinstance(item, dict) or not item.get("id") or not _safe_url(endpoint, allow_credentials=True):
                    continue
                nodes.append({
                    "id": str(item["id"])[:64],
                    "name": str(item.get("name", item["id"]))[:80],
                    "kind": str(item.get("kind", "unknown"))[:32],
                    "endpoint": endpoint[:300],
                    "profile_id": str(item.get("profile_id", ""))[:64],
                    "enabled": bool(item.get("enabled", True)),
                })
        return {"profiles": profiles, "routes": routes, "nodes": nodes}

    def _load_events(self) -> list[dict]:
        if not self.event_path.exists():
            return []
        try:
            lines = self.event_path.read_text(encoding="utf-8").splitlines()[-100:]
            return [json.loads(line) for line in lines if line]
        except (OSError, ValueError):
            return []

    def _register_routes(self):
        base = "/astrbot_plugin_proxy_manage"
        for suffix, handler, methods in (
            ("state", self.page_state, ["GET"]),
            ("save", self.page_save, ["POST"]),
            ("preview", self.page_preview, ["POST"]),
            ("probe", self.page_probe, ["POST"]),
            ("events", self.page_events, ["GET"]),
        ):
            self.context.register_web_api(base + "/" + suffix, handler, methods, "代理管理中心")

    def _snapshot(self) -> dict:
        profiles = []
        for p in self.state["profiles"]:
            item = dict(p)
            if item.get("endpoint"):
                item["endpoint"] = urlparse(item["endpoint"]).scheme + "://[configured]"
            profiles.append(item)
        nodes = []
        for node in self.state["nodes"]:
            item = dict(node)
            item["endpoint"] = urlparse(item["endpoint"]).scheme + "://[configured]"
            nodes.append(item)
        return {
            "profiles": profiles,
            "routes": self.state["routes"],
            "nodes": nodes,
            "events": self.events[-50:],
        }

    async def _persist(self, state: dict):
        text = json.dumps(self._normalize(state), ensure_ascii=False, indent=2)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(text, encoding="utf-8")
        temp.replace(self.path)
        self.state = self._normalize(state)

    def _event(self, event: dict):
        safe = {"at": int(time.time()), **event}
        self.events = (self.events + [safe])[-100:]
        try:
            with self.event_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(safe, ensure_ascii=False) + "\n")
        except OSError:
            logger.warning("代理中心事件写入失败")

    async def page_state(self):
        return json_response(self._snapshot())

    async def page_events(self):
        return json_response({"events": self.events[-100:]})

    async def page_save(self):
        try:
            payload = await request.json()
            if not isinstance(payload, dict):
                raise ValueError("配置格式无效")
            # The UI receives a redacted endpoint. Preserve the stored value when it is unchanged.
            previous = {p["id"]: p for p in self.state["profiles"]}
            incoming_profiles = payload.get("profiles", [])
            if not isinstance(incoming_profiles, list):
                raise ValueError("策略配置格式无效")
            for profile in incoming_profiles:
                if not isinstance(profile, dict):
                    raise ValueError("策略配置格式无效")
                old = previous.get(profile.get("id"))
                endpoint = profile.get("endpoint", "")
                if old and isinstance(endpoint, str) and endpoint.endswith("://[configured]"):
                    profile["endpoint"] = old.get("endpoint", "")
            async with self.lock:
                await self._persist(payload)
                self._event({"action": "save", "result": "ok", "message": "配置已保存"})
            return json_response(self._snapshot())
        except (ValueError, TypeError) as exc:
            return error_response(str(exc))
        except OSError:
            return error_response("配置保存失败", status_code=500)

    async def page_preview(self):
        try:
            payload = await request.json()
            host = _safe_host(payload.get("host"))

            def matches(route):
                route_host = route["host"].removeprefix("*.")
                return route["host"] == host or (
                    route.get("match") == "suffix" and host.endswith("." + route_host)
                )

            route = next(
                (r for r in self.state["routes"] if r.get("enabled", True) and matches(r)),
                None,
            )
            profile_id = route["profile_id"] if route else "direct"
            profile = next((p for p in self.state["profiles"] if p["id"] == profile_id), None)
            return json_response({
                "host": host,
                "matched": route,
                "profile": profile or {"id": "direct", "kind": "direct"},
                "fail_closed": bool(profile and profile.get("fail_closed")),
            })
        except (ValueError, TypeError) as exc:
            return error_response(str(exc))

    async def page_probe(self):
        profile_id = "direct"
        try:
            payload = await request.json()
            profile_id = str(payload.get("profile_id", "direct"))
            profile = next((p for p in self.state["profiles"] if p["id"] == profile_id), None)
            if not profile or not profile.get("enabled", True):
                raise ValueError("代理策略不存在")
            target = str(payload.get("url", "https://www.gstatic.com/generate_204"))
            if not _safe_url(target) or urlparse(target).scheme not in {"http", "https"}:
                raise ValueError("诊断目标只允许 HTTP 或 HTTPS 地址")
            proxy = None if profile["kind"] == "direct" else profile.get("endpoint")
            if profile["kind"] != "direct" and not _safe_url(proxy or "", allow_credentials=True):
                raise ValueError("该策略尚未配置有效代理入口")
            started = time.monotonic()
            result = {"profile_id": profile_id, "url": target, "stages": []}
            result["stages"].append({"name": "策略解析", "ok": True})
            async with httpx.AsyncClient(proxy=proxy, trust_env=False, follow_redirects=False, timeout=12) as client:
                response = await client.get(target)
            result["stages"].extend([
                {"name": "TCP/TLS/HTTP", "ok": True, "status": response.status_code},
                {"name": "完成", "ok": True, "elapsed_ms": round((time.monotonic() - started) * 1000)},
            ])
            self._event({"action": "probe", "profile_id": profile_id, "result": "ok", "status": response.status_code})
            return json_response(result)
        except (ValueError, httpx.HTTPError) as exc:
            self._event({
                "action": "probe",
                "profile_id": profile_id,
                "result": "failed",
                "error_type": type(exc).__name__,
            })
            return error_response("连通性检测失败，请检查策略、节点和目标站点")

    async def initialize(self):
        logger.info("代理管理中心已加载")

    async def terminate(self):
        pass

    async def on_message(self, event: AstrMessageEvent):
        return
