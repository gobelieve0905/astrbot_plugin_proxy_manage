from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import os
import platform
import stat
import time
import zipfile
from io import BytesIO
from pathlib import Path

import httpx

MAX_ARCHIVE_SIZE=64*1024*1024
MAX_BINARY_SIZE=128*1024*1024
DOWNLOAD_ATTEMPTS=2
DOWNLOAD_TOTAL_TIMEOUT=120


def detect_platform() -> dict:
    system={'linux':'linux','darwin':'darwin','windows':'windows'}.get(platform.system().lower(),'unsupported')
    machine=platform.machine().lower()
    arch={'x86_64':'amd64','amd64':'amd64','aarch64':'arm64','arm64':'arm64'}.get(machine,'unsupported')
    libc='none'
    if system=='linux':
        name,_version=platform.libc_ver()
        libc='musl' if name.lower()=='musl' or Path('/etc/alpine-release').exists() else 'glibc'
    return {'os':system,'arch':arch,'libc':libc,'machine':machine}


class ArtifactManager:
    def __init__(self,data_dir:Path,adapter_id:str):
        self.root=data_dir/'runtime'/'artifacts'/adapter_id
        self.root.mkdir(parents=True,exist_ok=True); self.root.chmod(0o700)
        manifest_path=Path(__file__).resolve().parents[1]/'cores'/(adapter_id+'_artifacts.json')
        self.manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
        self.platform=detect_platform()

    def selected(self) -> dict|None:
        key=self.platform['os']+'-'+self.platform['arch']
        item=self.manifest.get('artifacts',{}).get(key)
        if not isinstance(item,dict): return None
        sources=item.get('sources') or [{'id':'github','name':'GitHub 官方源','url':item.get('url','')}]
        sources=[{'id':str(source.get('id') or 'source-'+str(index+1)),'name':str(source.get('name') or '受信下载源'),
                  'url':source['url']} for index,source in enumerate(sources) if isinstance(source,dict) and source.get('url')]
        return {**item,'sources':sources,
                'key':key,'version':self.manifest['version']}

    @property
    def binary(self) -> Path:
        return self.root/('core.exe' if self.platform['os']=='windows' else 'core')

    @property
    def metadata_path(self) -> Path: return self.root/'installed.json'

    def status(self) -> dict:
        item=self.selected(); base={'adapter':self.manifest.get('adapter',''),'platform':self.platform,'version':self.manifest.get('version','')}
        if not item: return {**base,'state':'unsupported','ready':False,'message':'当前操作系统或 CPU 架构没有固定制品'}
        official=next((source for source in item['sources'] if source['id']=='github'),item['sources'][-1] if item['sources'] else {})
        public={'artifact':item['name'],'expected_sha256':item['sha256'],'size':item.get('size',0),
                'download_url':official.get('url','')}
        if not self.binary.is_file() or not self.metadata_path.is_file():
            return {**base,**public,'state':'not_installed','ready':False,'message':'内核尚未安装'}
        try:
            meta=json.loads(self.metadata_path.read_text(encoding='utf-8')); digest=hashlib.sha256(self.binary.read_bytes()).hexdigest()
        except (OSError,ValueError): return {**base,**public,'state':'invalid','ready':False,'message':'内核安装记录损坏'}
        if meta.get('archive_sha256')!=item['sha256'] or meta.get('binary_sha256')!=digest:
            return {**base,**public,'state':'invalid','ready':False,'message':'内核文件摘要与安装记录不一致'}
        if self.platform['os']!='windows' and not self.binary.stat().st_mode & stat.S_IXUSR:
            return {**base,**public,'state':'invalid','ready':False,'message':'内核文件不可执行'}
        return {**base,**public,'state':'installed','ready':True,'message':'固定版本内核已校验',
                'source':meta.get('source',''),'binary_sha256':digest}

    async def download(self,progress=None,cancel_event:asyncio.Event|None=None) -> dict:
        item=self.selected()
        if not item: raise ValueError('当前平台没有可用的固定内核制品')
        errors=[]
        for source_index,source in enumerate(item['sources']):
            for attempt in range(1,DOWNLOAD_ATTEMPTS+1):
                self._check_cancel(cancel_event)
                if progress: progress({'phase':'connecting','source':source.get('name',source['id']),'source_id':source['id'],
                                       'source_index':source_index+1,'source_count':len(item['sources']),
                                       'attempt':attempt,'attempts':DOWNLOAD_ATTEMPTS,'downloaded':0,'total':item.get('size',0),
                                       'message':'正在连接'+source.get('name',source['id'])})
                try:
                    archive=await self._download_source(item,source,progress,cancel_event,source_index,attempt)
                    result=self.install(archive,source['id'])
                    if progress: progress({'phase':'installed','downloaded':len(archive),'total':len(archive),
                                           'source':source.get('name',source['id']),'source_id':source['id'],'message':'制品校验和安装完成'})
                    return result
                except asyncio.CancelledError: raise
                except ValueError as exc:
                    if 'SHA-256' in str(exc): raise
                    errors.append(source.get('name',source['id'])+'：'+str(exc))
                except (httpx.HTTPError,OSError,RuntimeError) as exc:
                    errors.append(source.get('name',source['id'])+'：'+self._download_error(exc))
                if progress: progress({'phase':'retrying' if attempt<DOWNLOAD_ATTEMPTS else 'switching',
                                       'source':source.get('name',source['id']),'source_id':source['id'],
                                       'attempt':attempt,'attempts':DOWNLOAD_ATTEMPTS,
                                       'message':'下载失败，'+('准备重试' if attempt<DOWNLOAD_ATTEMPTS else '准备切换下载源')})
                if attempt<DOWNLOAD_ATTEMPTS: await asyncio.sleep(min(4,2**(attempt-1)))
        raise RuntimeError('所有受信下载源均不可用；请使用页面提供的官方地址下载后离线上传。'+('；'.join(errors[-3:])))

    @staticmethod
    def _check_cancel(cancel_event):
        if cancel_event and cancel_event.is_set(): raise asyncio.CancelledError

    @staticmethod
    def _download_error(exc:Exception) -> str:
        if isinstance(exc,asyncio.TimeoutError): return '超过 120 秒总时限'
        if isinstance(exc,httpx.ConnectTimeout): return '连接超时'
        if isinstance(exc,httpx.ReadTimeout): return '连续 20 秒未收到数据'
        if isinstance(exc,RuntimeError): return str(exc)
        return type(exc).__name__

    async def _download_source(self,item,source,progress,cancel_event,source_index,attempt) -> bytes:
        async def fetch() -> bytes:
            chunks=[]; size=0
            timeout=httpx.Timeout(20,connect=10,write=20,pool=10)
            async with httpx.AsyncClient(timeout=timeout,follow_redirects=True,trust_env=True) as client:
                async with client.stream('GET',source['url']) as response:
                    response.raise_for_status()
                    length=int(response.headers.get('content-length','0') or 0)
                    if length>MAX_ARCHIVE_SIZE: raise ValueError('内核制品超过 64 MiB 限制')
                    async for chunk in response.aiter_bytes():
                        self._check_cancel(cancel_event); size+=len(chunk)
                        if size>MAX_ARCHIVE_SIZE: raise ValueError('内核制品超过 64 MiB 限制')
                        chunks.append(chunk)
                        if progress: progress({'phase':'downloading','source':source.get('name',source['id']),
                                               'source_id':source['id'],'source_index':source_index+1,
                                               'source_count':len(item['sources']),'attempt':attempt,
                                               'attempts':DOWNLOAD_ATTEMPTS,'downloaded':size,
                                               'total':length or item.get('size',0),'message':'正在下载固定版本内核'})
            return b''.join(chunks)
        try: return await asyncio.wait_for(fetch(),DOWNLOAD_TOTAL_TIMEOUT)
        except asyncio.TimeoutError as exc: raise RuntimeError('下载超过 120 秒总时限') from exc

    def install(self,archive:bytes,source:str='offline') -> dict:
        item=self.selected()
        if not item: raise ValueError('当前平台没有可用的固定内核制品')
        if not archive or len(archive)>MAX_ARCHIVE_SIZE: raise ValueError('内核制品为空或超过 64 MiB 限制')
        digest=hashlib.sha256(archive).hexdigest()
        if digest!=item['sha256']: raise ValueError('内核制品 SHA-256 与固定清单不一致')
        try:
            if item['format']=='gz':
                with gzip.GzipFile(fileobj=BytesIO(archive)) as stream: binary=stream.read(MAX_BINARY_SIZE+1)
            elif item['format']=='zip':
                with zipfile.ZipFile(BytesIO(archive)) as package:
                    files=[info for info in package.infolist() if not info.is_dir()]
                    if len(files)!=1 or files[0].file_size>MAX_BINARY_SIZE: raise ValueError('内核 ZIP 内容不安全')
                    binary=package.read(files[0])
            else: raise ValueError('固定清单中的压缩格式不受支持')
        except (gzip.BadGzipFile,EOFError,zipfile.BadZipFile) as exc:
            raise ValueError('内核制品压缩内容损坏') from exc
        if not binary or len(binary)>MAX_BINARY_SIZE: raise ValueError('解压后的内核为空或过大')
        temp=self.binary.with_suffix(self.binary.suffix+'.tmp')
        with temp.open('wb') as stream: stream.write(binary); stream.flush(); os.fsync(stream.fileno())
        temp.chmod(stat.S_IRUSR|stat.S_IWUSR|stat.S_IXUSR); temp.replace(self.binary)
        metadata={'adapter':self.manifest['adapter'],'version':self.manifest['version'],'artifact':item['name'],
                  'archive_sha256':digest,'binary_sha256':hashlib.sha256(binary).hexdigest(),'source':source}
        meta_temp=self.metadata_path.with_suffix('.tmp')
        meta_temp.write_text(json.dumps(metadata,indent=2),encoding='utf-8'); meta_temp.chmod(0o600); meta_temp.replace(self.metadata_path)
        return self.status()


class ArtifactInstallTask:
    def __init__(self,manager:ArtifactManager,on_installed):
        self.manager=manager; self.on_installed=on_installed; self.task=None; self.cancel_event=None
        self.state={'state':'idle','phase':'idle','message':'尚未开始在线安装','updated_at':int(time.time())}

    def status(self) -> dict: return dict(self.state)

    def _update(self,values:dict):
        self.state={**self.state,**values,'state':'running','updated_at':int(time.time())}

    def start(self) -> dict:
        if self.task and not self.task.done(): return self.status()
        self.cancel_event=asyncio.Event()
        self.state={'state':'running','phase':'queued','message':'安装任务已创建','downloaded':0,'total':0,
                    'updated_at':int(time.time())}
        self.task=asyncio.create_task(self._run())
        return self.status()

    async def _run(self):
        try:
            artifact=await self.manager.download(self._update,self.cancel_event)
        except asyncio.CancelledError:
            self.state={**self.state,'state':'cancelled','phase':'cancelled','message':'在线安装已取消','updated_at':int(time.time())}
            return
        except Exception as exc:
            self.state={**self.state,'state':'waiting_upload','phase':'waiting_upload',
                        'message':str(exc),'updated_at':int(time.time())}
            return
        try:
            process=await self.on_installed()
            self.record_completed(artifact,process)
        except Exception as exc:
            self.state={**self.state,'state':'failed','phase':'start_failed',
                        'message':'制品已安装，但内核启动失败：'+str(exc),'artifact':artifact,'updated_at':int(time.time())}

    def record_completed(self,artifact,process,message='内核安装并启动成功'):
        self.state={**self.state,'state':'completed','phase':'completed','message':message,
                    'artifact':artifact,'process':process,'updated_at':int(time.time())}

    async def cancel(self) -> dict:
        if self.cancel_event: self.cancel_event.set()
        if self.task and not self.task.done():
            self.task.cancel(); await asyncio.gather(self.task,return_exceptions=True)
        return self.status()

    async def stop(self): await self.cancel()
