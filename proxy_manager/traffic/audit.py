from __future__ import annotations

import json
from pathlib import Path


AUDIT_VERSION='AstrBot 4.28.1'


class AstrBotTrafficAudit:
    """Read current AstrBot configuration without exposing credentials or changing it."""

    def __init__(self, root: Path):
        self.root=root
        self.config_path=root/'data'/'cmd_config.json'

    def _config(self) -> dict:
        try:
            value=json.loads(self.config_path.read_text(encoding='utf-8-sig'))
            return value if isinstance(value,dict) else {}
        except (OSError,ValueError):
            return {}

    @staticmethod
    def _items(value: object) -> list[dict]:
        if isinstance(value,list): return [item for item in value if isinstance(item,dict)]
        if isinstance(value,dict): return [item for item in value.values() if isinstance(item,dict)]
        return []

    @staticmethod
    def _name(item: dict, fallback: str) -> str:
        return str(item.get('id') or item.get('name') or item.get('provider') or item.get('type') or fallback)[:80]

    @staticmethod
    def _proxy_state(item: dict, entry: str) -> str:
        value=str(item.get('proxy') or item.get('discord_proxy') or '').rstrip('/')
        return 'stable_entry' if value and value==entry.rstrip('/') else ('other_proxy' if value else 'unset')

    def snapshot(self, entry: str) -> dict:
        config=self._config()
        providers=[]
        for source in ('provider_sources','provider','provider_tts_settings','provider_stt_settings','provider_embedding_settings'):
            for index,item in enumerate(self._items(config.get(source))):
                providers.append({'id':source+'-'+str(index),'name':self._name(item,source+' '+str(index+1)),
                                  'type':str(item.get('type') or item.get('provider_type') or item.get('provider') or 'unknown')[:80],
                                  'enabled':bool(item.get('enable',True)),'proxy':self._proxy_state(item,entry)})
        platforms=[]
        for index,item in enumerate(self._items(config.get('platform'))):
            platforms.append({'id':str(item.get('id') or 'platform-'+str(index+1))[:80],
                              'name':self._name(item,'平台 '+str(index+1)),
                              'type':str(item.get('type') or 'unknown')[:80],
                              'enabled':bool(item.get('enable',True)),'proxy':self._proxy_state(item,entry)})
        plugins=config.get('plugin_set')
        plugin_count=len(plugins) if isinstance(plugins,(dict,list)) else 0
        runner=config.get('agent_runner') if isinstance(config.get('agent_runner'),dict) else {}
        return {'version':AUDIT_VERSION,'readable':self.config_path.is_file(),'providers':providers,
                'platforms':platforms,'plugin_count':plugin_count,
                'agent_runner':str(runner.get('runner_type') or 'unknown')[:80],
                'computer_runtime':str((config.get('provider_settings') or {}).get('computer_use_runtime') or 'unknown')[:80]}
