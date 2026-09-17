from __future__ import annotations

import json
import os
import time
from pathlib import Path


INTERNAL_NO_PROXY = ('localhost', '127.0.0.1', '::1', 'applovin', 'applovin-account2')
PROXY_KEYS = ('http_proxy', 'no_proxy')


class AstrBotProxyTransaction:
    """Persist AstrBot's global proxy change separately from plugin policy state."""

    def __init__(self, data_dir: Path, config_path: Path | None = None):
        self.path = data_dir / 'astrbot-proxy-transaction.json'
        root = Path(os.environ.get('ASTRBOT_ROOT', '/astrbot'))
        self.config_path = config_path or root / 'data' / 'cmd_config.json'
        if self.path.exists():
            try:
                self.path.chmod(0o600)
            except OSError:
                pass

    def _read_json(self, path: Path) -> dict:
        try:
            value = json.loads(path.read_text(encoding='utf-8-sig'))
        except (OSError, ValueError) as exc:
            raise ValueError('AstrBot 全局配置不可读取或格式无效') from exc
        if not isinstance(value, dict):
            raise ValueError('AstrBot 全局配置格式无效')
        return value

    @staticmethod
    def _write_json(path: Path, value: dict):
        stat = path.stat()
        temp = path.with_suffix(path.suffix + '.proxy-tmp')
        temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        os.chmod(temp, stat.st_mode & 0o777)
        try:
            os.chown(temp, stat.st_uid, stat.st_gid)
        except PermissionError:
            pass
        temp.replace(path)

    def _load(self) -> dict:
        try:
            value = json.loads(self.path.read_text(encoding='utf-8'))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def _store(self, value: dict):
        temp = self.path.with_suffix('.tmp')
        temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        temp.chmod(0o600)
        temp.replace(self.path)

    @staticmethod
    def _snapshot(config: dict) -> dict:
        return {key: {'present': key in config, 'value': config.get(key)} for key in PROXY_KEYS}

    @staticmethod
    def _restore(config: dict, backup: dict) -> dict:
        restored = dict(config)
        for key in PROXY_KEYS:
            item = backup.get(key, {}) if isinstance(backup, dict) else {}
            if item.get('present'):
                restored[key] = item.get('value')
            else:
                restored.pop(key, None)
        return restored

    @staticmethod
    def _managed(config: dict, entry: str) -> bool:
        return (str(config.get('http_proxy') or '').rstrip('/') == entry.rstrip('/') and
                tuple(config.get('no_proxy') or []) == INTERNAL_NO_PROXY)

    def status(self, entry: str, environ: dict | None = None) -> dict:
        environ = environ if environ is not None else os.environ
        record = self._load()
        try:
            config = self._read_json(self.config_path)
            configured = self._managed(config, entry)
        except ValueError:
            config = {}; configured = False
        expected = entry.rstrip('/')
        effective = configured and all(str(environ.get(key, '')).rstrip('/') == expected for key in ('http_proxy', 'https_proxy'))
        status = record.get('status', 'not_connected')
        if status == 'active' and not effective:
            status = 'restart_required' if configured else 'drifted'
        elif status in {'pending_restart', 'restore_pending_restart'}:
            status = status
        elif effective:
            status = 'active'
        return {
            'status': status,
            'configured': configured,
            'effective': effective,
            'restart_required': status in {'pending_restart', 'restore_pending_restart', 'restart_required', 'drifted'},
            'entry': entry,
            'no_proxy': list(config.get('no_proxy') or []),
            'backup_available': bool(record.get('backup')),
            'message': {
                'active': 'AstrBot 当前进程已使用插件稳定入口',
                'pending_restart': '全局代理配置已写入，等待重启 AstrBot 生效',
                'restore_pending_restart': '旧全局代理配置已恢复，等待重启 AstrBot 生效',
                'restart_required': '配置已写入但当前进程尚未重启',
                'drifted': 'AstrBot 全局代理配置与当前进程环境不一致',
            }.get(status, 'AstrBot 尚未接入插件稳定入口'),
        }

    def enable(self, entry: str) -> dict:
        config = self._read_json(self.config_path)
        record = self._load()
        if not record.get('backup'):
            record['backup'] = self._snapshot(config)
            record['created_at'] = int(time.time())
        candidate = dict(config)
        candidate['http_proxy'] = entry
        candidate['no_proxy'] = list(INTERNAL_NO_PROXY)
        self._write_json(self.config_path, candidate)
        self._store({**record, 'status': 'pending_restart', 'entry': entry, 'updated_at': int(time.time())})
        return self.status(entry)

    def restore(self, entry: str) -> dict:
        record = self._load()
        if not isinstance(record.get('backup'), dict):
            raise ValueError('没有可恢复的 AstrBot 全局代理备份')
        config = self._read_json(self.config_path)
        self._write_json(self.config_path, self._restore(config, record['backup']))
        self._store({**record, 'status': 'restore_pending_restart', 'updated_at': int(time.time())})
        return self.status(entry)

    def mark_started(self, entry: str) -> dict:
        status = self.status(entry)
        record = self._load()
        if status['effective'] and record.get('status') == 'pending_restart':
            self._store({**record, 'status': 'active', 'activated_at': int(time.time())})
            return self.status(entry)
        if not status['configured'] and record.get('status') == 'restore_pending_restart':
            self._store({**record, 'status': 'restored', 'restored_at': int(time.time())})
            return self.status(entry)
        return status
