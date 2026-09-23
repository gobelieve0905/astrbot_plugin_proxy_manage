from __future__ import annotations

import copy
import hashlib
import json
from abc import ABC, abstractmethod
from pathlib import Path


class CoreAdapter(ABC):
    """Complete core contract. Concrete-core details must not escape this boundary."""

    id: str

    @abstractmethod
    def capabilities(self) -> dict: ...
    @abstractmethod
    def artifact(self) -> dict: ...
    @abstractmethod
    def render(self, state: dict, compiled: list[dict]) -> dict: ...
    @abstractmethod
    def validate(self, document: dict) -> None: ...
    @abstractmethod
    def serialize(self, document: dict) -> bytes: ...
    @abstractmethod
    def config_filename(self) -> str: ...
    @abstractmethod
    def command(self, binary: Path, config: Path) -> list[str]: ...
    @abstractmethod
    async def fetch_runtime(self, state: dict) -> dict: ...
    @abstractmethod
    def inspect(self, state: dict, application: dict, runtime: object=None,
                proxies: object=None, rules: object=None, version: str='') -> dict: ...
    @abstractmethod
    def verify(self, document: dict, runtime: object, proxies: object, rules: object) -> list[str]: ...
    @abstractmethod
    def expected_rules(self, document: dict) -> list: ...
    @abstractmethod
    def fail_closed_document(self, control: dict, entry: dict) -> dict: ...
    @abstractmethod
    def control(self, state: dict) -> tuple[dict,dict]: ...
    @abstractmethod
    async def apply(self, state: dict, document: dict, **runtime): ...
    @abstractmethod
    async def select(self, state: dict, group: dict, node: dict): ...
    @abstractmethod
    async def probe(self, state: dict, node: dict, target: str, timeout: int) -> int: ...
    @abstractmethod
    async def probe_group(self, state: dict, group: dict, target: str, timeout: int) -> dict: ...
    @abstractmethod
    async def proxies(self, state: dict) -> dict: ...
    @abstractmethod
    async def group_selection(self, state: dict, group: dict) -> str: ...
    @abstractmethod
    async def connection_snapshot(self, state: dict, host: str) -> list[dict]: ...

    def write_config(self, root: Path, document: dict) -> Path:
        self.validate(document)
        path=root/self.config_filename(); temp=path.with_suffix(path.suffix+'.tmp')
        temp.write_bytes(self.serialize(document)); temp.chmod(0o600); temp.replace(path)
        return path

    async def start(self, supervisor, binary: Path, config: Path):
        return await supervisor.start(binary,config,command=self.command(binary,config))

    async def stop(self, supervisor): return await supervisor.stop()

    async def restart(self, supervisor, binary: Path, config: Path):
        await self.stop(supervisor)
        return await self.start(supervisor,binary,config)

    async def healthy(self, state: dict) -> bool:
        fetched=await self.fetch_runtime(state)
        return not fetched.get('state')

    def revision(self, document: dict) -> str:
        value=json.dumps(document,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
        return hashlib.sha256(value).hexdigest()[:16]

    def redact(self, document: dict) -> dict:
        from ..domain.security import redact_config
        return redact_config(copy.deepcopy(document))

    @staticmethod
    def public_entry(entry: dict) -> dict:
        private=entry.get('private') if isinstance(entry.get('private'),dict) else {}
        return {'source':entry.get('source','unknown'),'private':{
            'enabled':bool(private.get('enabled')),'port':private.get('port'),
            'authenticated':bool(private.get('username') and private.get('password')),
            'exposure':'private-network'}}


class UnsupportedAdapter(CoreAdapter):
    def __init__(self, adapter_id: str): self.id=adapter_id or 'unknown'
    def _fail(self, *_args, **_kwargs): raise ValueError('不支持或未知的内核适配器：'+self.id)
    async def _afail(self, *_args, **_kwargs): self._fail()
    def capabilities(self): return {'id':self.id,'supported':False,'protocols':set(),'groups':set(),'rules':set()}
    def artifact(self): return {'adapter':self.id,'version':'','artifacts':{},'status':'unsupported'}
    render=validate=serialize=config_filename=command=inspect=verify=expected_rules=fail_closed_document=control=_fail
    fetch_runtime=apply=select=probe=probe_group=proxies=group_selection=connection_snapshot=_afail
