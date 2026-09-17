from __future__ import annotations


def current_artifact(adapter) -> dict:
    """P2 will download, verify and store kernel binaries. P1 only exposes the contract."""
    info=adapter.artifact()
    return {'adapter':getattr(adapter,'id',''), **info}
