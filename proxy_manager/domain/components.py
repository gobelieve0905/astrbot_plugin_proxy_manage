from __future__ import annotations

import hashlib


def component_tag(component_id: str) -> str:
    return 'component-' + hashlib.sha256(component_id.encode()).hexdigest()[:24]


def normalize_component_routes(values: object) -> list[dict]:
    if not isinstance(values, list):
        raise ValueError('component_routes 必须是数组')
    result, ids, ports = [], set(), set()
    for item in values:
        if not isinstance(item, dict):
            raise ValueError('组件策略必须是对象')
        component_id = item.get('id')
        kind = item.get('kind')
        if not isinstance(component_id, str) or not component_id.startswith(str(kind)+'-') or len(component_id) > 300:
            raise ValueError('组件标识无效')
        if kind not in {'plugin', 'mcp'} or component_id in ids:
            raise ValueError('组件类型无效或标识重复')
        if not isinstance(item.get('enabled'), bool):
            raise ValueError('组件管理开关必须是布尔值')
        scope = item.get('scope', 'local')
        if scope not in {'local', 'private'}:
            raise ValueError('组件入口范围无效')
        port = item.get('port')
        if port is None:
            port = next((value for value in range(18000, 19000) if value not in ports and
                         value not in {entry.get('port') for entry in values if isinstance(entry, dict)}), None)
        if type(port) is not int or not 18000 <= port < 19000 or port in ports:
            raise ValueError('组件入口端口无效、重复或已耗尽')
        target = item.get('target', '')
        if not isinstance(target, str):
            raise ValueError('组件代理组无效')
        result.append({'id': component_id, 'kind': kind, 'target': target,
                       'enabled': item['enabled'], 'port': port, 'scope': scope})
        ids.add(component_id); ports.add(port)
    return result


def component_entries(state: dict) -> list[dict]:
    """Keep disabled/deleted-group entries listening with a reject policy for stale clients."""
    groups = {group['id']: group for group in state.get('groups', [])}
    private = state.get('proxy_entry', {}).get('private', {})
    result = []
    for route in state.get('component_routes', []):
        group = groups.get(route['target'])
        target = (group.get('kernel_name', 'group-'+group['id']) if group and group['id'] != 'direct' else 'DIRECT')
        if not route['enabled'] or not group or not group.get('enabled', True):
            target = 'REJECT'
        entry = {**route, 'tag': component_tag(route['id']), 'outbound': target, 'listen': '127.0.0.1'}
        if route.get('scope') == 'private':
            if not private.get('enabled') or not private.get('username') or not private.get('password'):
                raise ValueError('组件私网入口缺少受信网络或认证凭据')
            entry.update({'listen': private.get('listen', '0.0.0.0'),
                          'username': private['username'], 'password': private['password']})
        result.append(entry)
    return result
