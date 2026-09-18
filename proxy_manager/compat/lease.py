from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable


@dataclass(frozen=True)
class ComponentLease:
    """The only proxy information an official component adapter receives."""

    component_id: str
    http_proxy: str
    socks_proxy: str = ""
    no_proxy: tuple[str, ...] = ()
    revision: str = ""
    fail_policy: str = "closed"

    @classmethod
    def from_entry(
        cls,
        component_id: str,
        entry: dict | None,
        revision: str = "",
    ) -> "ComponentLease":
        entry = entry if isinstance(entry, dict) else {}
        return cls(
            component_id=str(component_id)[:120],
            http_proxy=str(entry.get("http_url") or ""),
            socks_proxy=str(entry.get("socks_url") or ""),
            no_proxy=("localhost", "127.0.0.1", "::1"),
            revision=str(revision)[:120],
        )

    def for_component(self, component_id: str) -> "ComponentLease":
        return ComponentLease(
            component_id=str(component_id)[:120],
            http_proxy=self.http_proxy,
            socks_proxy=self.socks_proxy,
            no_proxy=self.no_proxy,
            revision=self.revision,
            fail_policy=self.fail_policy,
        )

    def as_public_dict(self) -> dict:
        result = asdict(self)
        result["no_proxy"] = list(self.no_proxy)
        return result


def normalize_no_proxy(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))
