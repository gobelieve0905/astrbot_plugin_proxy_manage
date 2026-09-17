from __future__ import annotations


class KernelSupervisor:
    """P2 will start, stop and supervise plugin-owned kernel processes."""

    def status(self) -> dict:
        return {'state':'external','message':'当前版本仍连接外部准备的内核实例，进程监督尚未启用'}
