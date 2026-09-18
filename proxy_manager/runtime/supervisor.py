from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import time
import uuid
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows has no /proc or flock.
    fcntl = None


class _AttachedProcess:
    """Small Popen-compatible handle for a core started by another plugin instance."""

    def __init__(self, pid: int):
        self.pid = pid
        self.returncode = None

    def poll(self):
        try:
            os.kill(self.pid, 0)
        except ProcessLookupError:
            self.returncode = 1
            return self.returncode
        except PermissionError:
            return None
        return None

    def terminate(self):
        try:
            os.kill(self.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass

    def kill(self):
        try:
            os.kill(self.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass

    def wait(self):
        while self.poll() is None:
            time.sleep(.05)
        return self.returncode


class KernelSupervisor:
    def __init__(self,data_dir:Path,health_check,listener_check=None):
        self.root=data_dir/'runtime'; self.root.mkdir(parents=True,exist_ok=True); self.root.chmod(0o700)
        self.pid_path=self.root/'kernel.pid.json'; self.log_path=self.root/'kernel.log'
        self.health_check=health_check; self.listener_check=listener_check
        self.process=None; self.monitor_task=None; self.stopping=False
        self.binary=None; self.config=None; self.command=None; self.last_error=''; self.restarts=0; self.orphan_record=None
        self.owner=uuid.uuid4().hex
        self.lock_path=self.root/'kernel.lock'
        try: self.lock_path.touch(mode=0o600,exist_ok=True); self.lock_path.chmod(0o600)
        except OSError: pass
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

    @staticmethod
    def _read_cmdline(pid: int) -> list[str]:
        try:
            raw=(Path('/proc')/str(pid)/'cmdline').read_bytes()
        except (OSError,ValueError):
            return []
        return [item.decode('utf-8',errors='replace') for item in raw.split(b'\0') if item]

    def _owned_pids(self, binary: Path, config: Path) -> list[int]:
        """Find exact plugin-owned core processes left by a reload or crash."""
        if not Path('/proc').is_dir():
            return []
        expected=[str(binary),'-d',str(self.root),'-f',str(config)]
        result=[]
        try:
            entries=list(Path('/proc').iterdir())
        except OSError:
            return []
        for entry in entries:
            if not entry.name.isdigit():
                continue
            try: pid=int(entry.name)
            except ValueError:
                continue
            if self._read_cmdline(pid)==expected:
                result.append(pid)
        return result

    async def _acquire_lock(self):
        """Serialize lifecycle operations across hot-reloaded plugin instances."""
        if fcntl is None:
            return None
        try:
            fd=os.open(self.lock_path,os.O_RDWR|os.O_CREAT,0o600)
        except OSError:
            return None
        while True:
            try:
                fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
                return fd
            except BlockingIOError:
                await asyncio.sleep(.05)
            except OSError:
                os.close(fd)
                return None

    @staticmethod
    def _release_lock(fd):
        if fd is None:
            return
        try:
            fcntl.flock(fd,fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def _write_pid_record(self,pid:int):
        self.pid_path.write_text(json.dumps({'pid':pid,'binary':str(self.binary),
                                              'started_at':int(time.time()),'owner':self.owner}),encoding='utf-8')
        self.pid_path.chmod(0o600)

    def _pid_record_belongs_to(self,pid:int) -> bool:
        try:
            record=json.loads(self.pid_path.read_text(encoding='utf-8'))
        except (OSError,ValueError,TypeError):
            return False
        owner=str(record.get('owner',''))
        return int(record.get('pid',0) or 0)==pid and (not owner or owner==self.owner)

    def _clear_pid_record(self,pid:int):
        if not self._pid_record_belongs_to(pid):
            return
        try: self.pid_path.unlink()
        except FileNotFoundError: pass

    async def _adopt_or_clear_owned(self) -> bool:
        """Adopt one healthy existing core, or clear every stale exact-match process."""
        candidates=self._owned_pids(self.binary,self.config)
        if not candidates:
            return False
        healthy=[]
        for pid in candidates:
            try:
                if await self.health_check() and (
                    self.listener_check is None
                    or await self.listener_check(pid,self.config)
                ):
                    healthy.append(pid)
            except Exception:
                continue
        selected=healthy[0] if healthy else None
        for pid in candidates:
            if pid != selected:
                await self._terminate_pid(pid)
        if selected is None:
            return False
        self.process=_AttachedProcess(selected)
        self._write_pid_record(selected)
        self.orphan_record=None
        self.last_error=''
        return True

    async def _terminate_pid(self, pid: int):
        if pid <= 1:
            return
        try:
            os.killpg(pid,signal.SIGTERM)
        except ProcessLookupError:
            return
        except PermissionError:
            try: os.kill(pid,signal.SIGTERM)
            except (ProcessLookupError,PermissionError): return
        for _attempt in range(50):
            await asyncio.sleep(.1)
            try: os.kill(pid,0)
            except ProcessLookupError: return
            except PermissionError: break
        try: os.killpg(pid,signal.SIGKILL)
        except ProcessLookupError: return
        except PermissionError:
            try: os.kill(pid,signal.SIGKILL)
            except (ProcessLookupError,PermissionError): return

    async def start(self,binary:Path,config:Path,timeout:float=12,command:list[str]|None=None):
        if self.process and self.process.poll() is None: return self.status()
        self.binary=binary; self.config=config
        self.command=command or [str(binary),'-d',str(self.root),'-f',str(config)]
        self.stopping=False; self.last_error=''
        try: await self._spawn(timeout)
        except Exception as exc:
            self.last_error=str(exc)
            if self.process:
                self._clear_pid_record(self.process.pid)
            raise
        if not self.monitor_task or self.monitor_task.done(): self.monitor_task=asyncio.create_task(self._monitor())
        return self.status()

    async def _spawn(self,timeout:float):
        lock_fd=await self._acquire_lock()
        try:
            if self.orphan_record:
                recorded=Path(str(self.orphan_record.get('binary','')))
                if recorded!=self.binary:
                    raise RuntimeError('PID 记录中的内核路径与当前固定制品不一致，拒绝终止未知进程')
            if await self._adopt_or_clear_owned():
                return
            self._rotate_log(); log=self.log_path.open('ab',buffering=0)
            process=None
            try:
                process=subprocess.Popen(self.command,stdin=subprocess.DEVNULL,
                                         stdout=log,stderr=subprocess.STDOUT,cwd=self.root,close_fds=True,start_new_session=True)
            finally: log.close()
            self.process=process
            self._write_pid_record(process.pid)
            deadline=time.monotonic()+timeout
            while time.monotonic()<deadline:
                if process.poll() is not None:
                    raise RuntimeError('内核在启动期间退出，代码 '+str(process.returncode))
                try:
                    if await self.health_check() and (
                        self.listener_check is None
                        or await self.listener_check(process.pid,self.config)
                    ):
                        self.orphan_record=None
                        self.last_error=''
                        return
                except Exception:
                    pass
                await asyncio.sleep(.25)
            await self._terminate_process(process)
            raise RuntimeError('内核启动健康检查超时')
        finally:
            self._release_lock(lock_fd)

    async def _monitor(self):
        while not self.stopping:
            await asyncio.sleep(1)
            if self.process and self.process.poll() is not None:
                if self.pid_path.exists() and not self._pid_record_belongs_to(self.process.pid):
                    self.last_error='内核生命周期已由其他插件实例接管'
                    break
                self.last_error='内核异常退出，代码 '+str(self.process.returncode)
                if self.stopping or not self.binary or not self.config: break
                await asyncio.sleep(min(30,2**min(self.restarts,4))); self.restarts+=1
                try: await self._spawn(12)
                except Exception as exc: self.last_error=str(exc)

    async def _terminate_process(self,process=None):
        process=process or self.process
        if not process or process.poll() is not None: return
        process.terminate()
        try: await asyncio.wait_for(asyncio.to_thread(process.wait),5)
        except asyncio.TimeoutError: process.kill(); await asyncio.to_thread(process.wait)

    async def stop(self):
        self.stopping=True
        if self.monitor_task:
            self.monitor_task.cancel(); await asyncio.gather(self.monitor_task,return_exceptions=True); self.monitor_task=None
        lock_fd=await self._acquire_lock()
        try:
            process=self.process
            if process and self._pid_record_belongs_to(process.pid):
                await self._terminate_process(process)
                try: self.pid_path.unlink()
                except FileNotFoundError: pass
            self.process=None
        finally:
            self._release_lock(lock_fd)
        self.last_error=''
        return self.status()
