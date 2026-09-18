from __future__ import annotations

import json
from pathlib import Path


PROTOCOL = 'astrbot.proxy-manager/v1'
DECLARATION_FILE = 'proxy_manager_integration.json'
MODES = {'astrbot-environment', 'private-network', 'manual'}
RESTARTS = {'none', 'process', 'container', 'astrbot'}
DECLARATION_KEYS = {'protocol', 'mode', 'protocols', 'restart', 'auto_apply'}


def declaration(value: object) -> dict:
    """Normalize a public egress declaration without retaining credentials."""
    if not isinstance(value, dict):
        return {'state': 'missing', 'mode': '', 'protocols': [], 'restart': ''}
    if set(value) - DECLARATION_KEYS:
        return {'state': 'invalid', 'mode': '', 'protocols': [], 'restart': '',
                'reason': '声明包含不允许的字段'}
    if value.get('protocol') != PROTOCOL:
        return {'state': 'invalid', 'mode': '', 'protocols': [], 'restart': '',
                'reason': '未声明 '+PROTOCOL}
    mode = str(value.get('mode') or '')
    if mode not in MODES:
        return {'state': 'invalid', 'mode': '', 'protocols': [], 'restart': '',
                'reason': '入口模式无效'}
    protocols = sorted({str(item).lower()[:24] for item in value.get('protocols', [])
                        if isinstance(item, str) and item.lower() in {'http', 'https', 'websocket', 'tcp', 'udp'}})
    if not protocols:
        return {'state': 'invalid', 'mode': '', 'protocols': [], 'restart': '',
                'reason': '未声明受支持的出站协议'}
    restart = str(value.get('restart') or 'process')
    if restart not in RESTARTS: restart = 'process'
    return {'state': 'compatible', 'mode': mode, 'protocols': protocols, 'restart': restart,
            'auto_apply': bool(value.get('auto_apply', False))}


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
        path = plugin_root / name / DECLARATION_FILE
        raw = None
        try:
            if path.is_file() and path.stat().st_size <= 64 * 1024:
                raw = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            raw = {'protocol': ''}
        result.append({'id': 'plugin-'+name[:80], 'name': name[:80], 'kind': 'plugin',
                       'declaration': declaration(raw)})
    return result
