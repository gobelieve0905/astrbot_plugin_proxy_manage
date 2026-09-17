from __future__ import annotations

from abc import ABC, abstractmethod


class CoreAdapter(ABC):
    """Kernel-agnostic execution contract. Business code must not import a concrete core."""

    id: str

    @abstractmethod
    def capabilities(self) -> dict: ...

    def artifact(self) -> dict:
        return {'status':'unsupported','message':'当前适配器尚未提供插件自管制品'}

    @abstractmethod
    def render(self, state: dict, compiled: list[dict]) -> dict: ...

    @abstractmethod
    def validate(self, document: dict) -> None: ...

    @abstractmethod
    def inspect(self, state: dict, application: dict, runtime: object, proxies: object, rules: object, version: str='') -> dict: ...

    @abstractmethod
    def verify(self, document: dict, runtime: object, proxies: object, rules: object) -> list[str]: ...

    @abstractmethod
    def fail_closed_document(self, control: dict, entry: dict) -> dict: ...

    def revision(self, document: dict) -> str:
        import hashlib, json
        return hashlib.sha256(json.dumps(document,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()[:16]

    def redact(self, document: dict) -> dict:
        from proxy_manager.domain.security import redact_config
        import copy
        return redact_config(copy.deepcopy(document))

    def control(self, state: dict) -> tuple[dict,dict]:
        raise NotImplementedError

    async def apply(self, state: dict, document: dict):
        raise NotImplementedError

    async def select(self, state: dict, group: dict, node: dict):
        raise NotImplementedError

    async def probe(self, state: dict, node: dict, target: str, timeout: int) -> int:
        raise NotImplementedError

    async def group_selection(self, state: dict, group: dict) -> str:
        return ''
