from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path

PROTOCOL = 'astrbot.proxy-manager/v1'
DECLARATION_FILE = 'proxy_manager_integration.json'
MODES = {'astrbot-environment', 'private-network', 'manual'}
RESTARTS = {'none', 'process', 'container', 'astrbot'}
DECLARATION_KEYS = {'protocol', 'mode', 'protocols', 'restart', 'auto_apply'}
SUPPORTED_PROTOCOLS = {'http', 'https', 'websocket', 'tcp', 'udp'}


def _invalid(reason: str) -> dict:
    return {'state': 'invalid', 'mode': '', 'protocols': [], 'restart': '',
            'reason': reason}


def declaration(value: object) -> dict:
    """Normalize a public egress declaration without retaining credentials."""
    if value is None:
        return {'state': 'missing', 'mode': '', 'protocols': [], 'restart': ''}
    if not isinstance(value, dict):
        return _invalid('声明必须是对象')
    if set(value) - DECLARATION_KEYS:
        return _invalid('声明包含不允许的字段')
    if value.get('protocol') != PROTOCOL:
        return _invalid('未声明 '+PROTOCOL)
    mode = value.get('mode')
    if not isinstance(mode, str) or mode not in MODES:
        return _invalid('入口模式无效')
    raw_protocols = value.get('protocols')
    if not isinstance(raw_protocols, list):
        return _invalid('protocols 必须是数组')
    if any(not isinstance(item, str) for item in raw_protocols):
        return _invalid('protocols 只能包含字符串')
    protocols = sorted({item.lower() for item in raw_protocols})
    if not protocols or any(item not in SUPPORTED_PROTOCOLS for item in protocols):
        return _invalid('未声明受支持的出站协议')
    restart = value.get('restart', 'process')
    if not isinstance(restart, str) or restart not in RESTARTS:
        return _invalid('重启要求无效')
    auto_apply = value.get('auto_apply', False)
    if not isinstance(auto_apply, bool):
        return _invalid('auto_apply 必须是布尔值')
    return {'state': 'compatible', 'mode': mode, 'protocols': protocols, 'restart': restart,
            'auto_apply': auto_apply}


def managed_http_proxy(value: object, *, enabled: bool,
                       environ: Mapping[str, str] | None = None) -> str | None:
    """Resolve an opt-in HTTP entry; never fall back when management is enabled."""
    if not isinstance(enabled, bool):
        raise ValueError('代理管理开关必须是布尔值')
    if not enabled:
        return None
    declared = declaration(value)
    if declared['state'] != 'compatible' or declared['mode'] != 'astrbot-environment':
        raise ValueError('HTTP 接入需要有效的 astrbot-environment 声明')
    if not set(declared['protocols']) & {'http', 'https', 'websocket'}:
        raise ValueError('声明未包含 HTTP 或 WebSocket 接入能力')
    environ = os.environ if environ is None else environ
    expected = 'http://127.0.0.1:17890'
    # Requiring both schemes and checking aliases prevents a partial or stale lease.
    for scheme in ('http', 'https'):
        values = [str(environ[key]).rstrip('/') for key in
                  (scheme + '_proxy', scheme.upper() + '_PROXY') if environ.get(key)]
        if not values or any(proxy != expected for proxy in values):
            raise ValueError('代理管理中心环境入口缺失或冲突，请加载代理管理中心后重建客户端')
    return expected


def plugin_declarations(root: Path, configured: object) -> list[dict]:
    """Discover only the explicit, data-only integration file from installed plugins."""
    names = set()
    if isinstance(configured, dict): names.update(str(key) for key in configured)
    elif isinstance(configured, list):
        names.update(str(item.get('name') or item.get('id') or '') if isinstance(item, dict) else str(item) for item in configured)
    plugin_root = root / 'data' / 'plugins'
    if plugin_root.is_dir():
        names.update(path.name for path in plugin_root.iterdir() if path.is_dir())
    result = []
    for name in sorted(value for value in names if value and value != 'astrbot_plugin_proxy_manage'):
        if name in {'.', '..'} or '/' in name or '\\' in name:
            continue
        plugin_path = (plugin_root / name).resolve()
        try:
            plugin_path.relative_to(plugin_root.resolve())
        except ValueError:
            continue
        path = plugin_path / DECLARATION_FILE
        raw = None
        try:
            path.resolve().relative_to(plugin_path)
            if path.is_file() and path.stat().st_size <= 64 * 1024:
                raw = json.loads(path.read_text(encoding='utf-8'))
            elif path.exists():
                raw = {'protocol': ''}
        except (OSError, ValueError):
            raw = {'protocol': ''}
        result.append({'id': 'plugin-'+name, 'name': name[:80], 'kind': 'plugin',
                       'declaration': declaration(raw)})
    return result
