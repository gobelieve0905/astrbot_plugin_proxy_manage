"""兼容旧调用方的流量清单入口。"""

from .registry import TRAFFIC_INVENTORY, TRAFFIC_REGISTRY, TrafficRegistry


# 保留旧名称，插件外部调用方可以平滑迁移到 TrafficRegistry。
def traffic_inventory(state: dict, application: dict, environ: dict | None = None,
                      astrbot: dict | None = None, audit: dict | None = None) -> list[dict]:
    return TRAFFIC_REGISTRY.snapshot(state, application, environ, astrbot, audit)


__all__ = ['TRAFFIC_INVENTORY', 'TRAFFIC_REGISTRY', 'TrafficRegistry', 'traffic_inventory']
