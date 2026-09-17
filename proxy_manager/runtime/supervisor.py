from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
from pathlib import Path


class KernelSupervisor:
    def __init__(self,data_dir:Path,health_check):
        self.root=data_dir/'runtime'; self.root.mkdir(parents=True,exist_ok=True); self.root.chmod(0o700)
        self.pid_path=self.root/'kernel.pid.json'; self.log_path=self.root/'kernel.log'
        self.health_check=health_check; self.process=None; self.monitor_task=None; self.stopping=False
        self.binary=None; self.config=None; self.last_error=''; self.restarts=0

    def status(self) -> dict:
        running=bool(self.process and self.process.poll() is None)
        return {'state':'running' if running else ('failed' if self.last_error else 'stopped'),'ready':running,
                'pid':self.process.pid if running else None,'restarts':self.restarts,
                'message':self.last_error or ('内核正在运行' if running else '内核未运行')}

    def _rotate_log(self):
        if self.log_path.exists() and self.log_path.stat().st_size>2*1024*1024:
            backup=self.log_path.with_suffix('.log.1')
            if backup.exists(): backup.unlink()
            self.log_path.replace(backup)

    async def start(self,binary:Path,config:Path,timeout:float=12):
        if self.process and self.process.poll() is None: return self.status()
        self.binary=binary; self.config=config; self.stopping=False; self.last_error=''
        try: await self._spawn(timeout)
        except Exception as exc:
            self.last_error=str(exc)
            try: self.pid_path.unlink()
            except FileNotFoundError: pass
            raise
        if not self.monitor_task or self.monitor_task.done(): self.monitor_task=asyncio.create_task(self._monitor())
        return self.status()

    async def _spawn(self,timeout:float):
        self._rotate_log(); log=self.log_path.open('ab',buffering=0)
        try:
            self.process=subprocess.Popen([str(self.binary),'-d',str(self.root),'-f',str(self.config)],stdin=subprocess.DEVNULL,
                                          stdout=log,stderr=subprocess.STDOUT,cwd=self.root,close_fds=True,start_new_session=True)
        finally: log.close()
        self.pid_path.write_text(json.dumps({'pid':self.process.pid,'binary':str(self.binary),'started_at':int(time.time())}),encoding='utf-8')
        self.pid_path.chmod(0o600); deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            if self.process.poll() is not None: raise RuntimeError('内核在启动期间退出，代码 '+str(self.process.returncode))
            try:
                if await self.health_check(): self.last_error=''; return
            except Exception: pass
            await asyncio.sleep(.25)
        await self._terminate_process(); raise RuntimeError('内核启动健康检查超时')

    async def _monitor(self):
        while not self.stopping:
            await asyncio.sleep(1)
            if self.process and self.process.poll() is not None:
                self.last_error='内核异常退出，代码 '+str(self.process.returncode)
                if self.stopping or not self.binary or not self.config: break
                await asyncio.sleep(min(30,2**min(self.restarts,4))); self.restarts+=1
                try: await self._spawn(12)
                except Exception as exc: self.last_error=str(exc)

    async def _terminate_process(self):
        process=self.process
        if not process or process.poll() is not None: return
        process.terminate()
        try: await asyncio.wait_for(asyncio.to_thread(process.wait),5)
        except asyncio.TimeoutError: process.kill(); await asyncio.to_thread(process.wait)

    async def stop(self):
        self.stopping=True
        if self.monitor_task:
            self.monitor_task.cancel(); await asyncio.gather(self.monitor_task,return_exceptions=True); self.monitor_task=None
        await self._terminate_process(); self.process=None
        try: self.pid_path.unlink()
        except FileNotFoundError: pass
        self.last_error=''
        return self.status()
