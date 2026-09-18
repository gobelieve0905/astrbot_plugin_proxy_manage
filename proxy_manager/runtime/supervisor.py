from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import time
from pathlib import Path


class KernelSupervisor:
    def __init__(self,data_dir:Path,health_check,listener_check=None):
        self.root=data_dir/'runtime'; self.root.mkdir(parents=True,exist_ok=True); self.root.chmod(0o700)
        self.pid_path=self.root/'kernel.pid.json'; self.log_path=self.root/'kernel.log'
        self.health_check=health_check; self.listener_check=listener_check
        self.process=None; self.monitor_task=None; self.stopping=False
        self.binary=None; self.config=None; self.command=None; self.last_error=''; self.restarts=0; self.orphan_record=None
        self._recover_stale_pid()

    def _recover_stale_pid(self):
        try:
            record=json.loads(self.pid_path.read_text(encoding='utf-8')); pid=int(record.get('pid',0))
            if pid>1:
                os.kill(pid,0)
                recorded=Path(str(record.get('binary','')))
                proc_cmdline=Path('/proc')/str(pid)/'cmdline'
                try: executable=Path(proc_cmdline.read_bytes().split(b'\0',1)[0].decode())
                except (OSError,UnicodeError): executable=Path()
                if executable==recorded:
                    self.orphan_record=record
                    self.last_error='检测到上次运行遗留的内核进程，启动前将安全回收'
                    return
        except (FileNotFoundError,ValueError,TypeError,json.JSONDecodeError,ProcessLookupError,PermissionError):
            pass
        try: self.pid_path.unlink()
        except FileNotFoundError: pass

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

    async def start(self,binary:Path,config:Path,timeout:float=12,command:list[str]|None=None):
        if self.process and self.process.poll() is None: return self.status()
        if self.orphan_record:
            recorded=Path(str(self.orphan_record.get('binary','')))
            if recorded!=binary:
                raise RuntimeError('PID 记录中的内核路径与当前固定制品不一致，拒绝终止未知进程')
            pid=int(self.orphan_record['pid'])
            try:
                os.killpg(pid,signal.SIGTERM)
                for _attempt in range(20):
                    await asyncio.sleep(.1)
                    try: os.kill(pid,0)
                    except ProcessLookupError: break
                else: os.killpg(pid,signal.SIGKILL)
            except ProcessLookupError: pass
            self.orphan_record=None
            try: self.pid_path.unlink()
            except FileNotFoundError: pass
        self.binary=binary; self.config=config
        self.command=command or [str(binary),'-d',str(self.root),'-f',str(config)]
        self.stopping=False; self.last_error=''
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
            self.process=subprocess.Popen(self.command,stdin=subprocess.DEVNULL,
                                          stdout=log,stderr=subprocess.STDOUT,cwd=self.root,close_fds=True,start_new_session=True)
        finally: log.close()
        self.pid_path.write_text(json.dumps({'pid':self.process.pid,'binary':str(self.binary),'started_at':int(time.time())}),encoding='utf-8')
        self.pid_path.chmod(0o600); deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            if self.process.poll() is not None: raise RuntimeError('内核在启动期间退出，代码 '+str(self.process.returncode))
            try:
                if await self.health_check() and (
                    self.listener_check is None
                    or await self.listener_check(self.process.pid, self.config)
                ):
                    self.last_error=''; return
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
