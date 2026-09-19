from __future__ import annotations

import asyncio
import copy
import gzip
import hashlib
import json
import os
import platform
import re
import stat
import tarfile
import time
import zipfile
from io import BytesIO
from pathlib import Path

import httpx

MAX_ARCHIVE_SIZE=64*1024*1024
MAX_BINARY_SIZE=128*1024*1024
DOWNLOAD_ATTEMPTS=2
DOWNLOAD_TOTAL_TIMEOUT=120


def _version_key(value: object) -> tuple[int, ...]:
    """Compare fixed semantic versions without accepting arbitrary URLs or tags."""
    parts=[]
    for part in str(value or '').split('.'):
        digits=''.join(character for character in part if character.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts or [0])


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
    def __init__(self,data_dir:Path,manifest:dict,version:str|None=None):
        if not isinstance(manifest,dict) or not manifest.get('adapter'):
            raise ValueError('内核适配器未提供有效的固定制品清单')
        adapter_id=str(manifest['adapter'])
        self.root=data_dir/'runtime'/'artifacts'/adapter_id
        self.root.mkdir(parents=True,exist_ok=True); self.root.chmod(0o700)
        self.catalog=copy.deepcopy(manifest)
        versions=self.available_versions()
        requested=str(version or self.catalog.get('recommended_version') or self.catalog.get('version') or '')
        if requested not in versions:
            raise ValueError('内核版本不在固定清单中：'+requested)
        self.version=requested
        self.manifest=manifest if not manifest.get('versions') and requested==str(manifest.get('version') or '') else self._manifest_for_version(requested)
        self.platform=detect_platform()

    def available_versions(self) -> list[str]:
        versions={}
        base=str(self.catalog.get('version') or '')
        if base:
            versions[base]=self.catalog
        raw=self.catalog.get('versions')
        if isinstance(raw,dict):
            for version,release in raw.items():
                if isinstance(release,dict): versions[str(version)]=release
        elif isinstance(raw,list):
            for release in raw:
                if isinstance(release,dict) and release.get('version'):
                    versions[str(release['version'])]=release
        return sorted(versions,key=_version_key,reverse=True)

    def _manifest_for_version(self,version:str) -> dict:
        base=copy.deepcopy(self.catalog)
        raw=self.catalog.get('versions')
        release=None
        if isinstance(raw,dict): release=raw.get(version)
        elif isinstance(raw,list):
            release=next((item for item in raw if isinstance(item,dict) and str(item.get('version'))==version),None)
        if isinstance(release,dict):
            base.update(copy.deepcopy(release))
        base['adapter']=self.catalog['adapter']; base['version']=version
        return base

    def for_version(self,version:str|None=None) -> 'ArtifactManager':
        return ArtifactManager(self.root.parent.parent.parent,self.catalog,version or self.version)

    @property
    def recommended_version(self) -> str:
        requested=str(self.catalog.get('recommended_version') or self.catalog.get('version') or '')
        return requested if requested in self.available_versions() else self.available_versions()[0]

    def _platform_key(self,manifest:dict|None=None) -> str:
        manifest=manifest or self.manifest
        base=self.platform['os']+'-'+self.platform['arch']
        libc_key=base+'-'+self.platform['libc'] if self.platform['os']=='linux' else ''
        if libc_key in manifest.get('artifacts',{}): return libc_key
        return base

    def _selected_from(self,manifest:dict) -> dict|None:
        key=self._platform_key(manifest)
        item=manifest.get('artifacts',{}).get(key)
        if not isinstance(item,dict): return None
        sources=item.get('sources') or [{'id':'github','name':'GitHub 官方源','url':item.get('url','')}]
        sources=[{'id':str(source.get('id') or 'source-'+str(index+1)),'name':str(source.get('name') or '受信下载源'),
                  'url':source['url']} for index,source in enumerate(sources) if isinstance(source,dict) and source.get('url')]
        return {**item,'sources':sources,'key':key,'version':str(manifest.get('version') or '')}

    def selected(self) -> dict|None:
        return self._selected_from(self.manifest)

    @property
    def binary(self) -> Path:
        return self.root/('core.exe' if self.platform['os']=='windows' else 'core')

    @property
    def metadata_path(self) -> Path: return self.root/'installed.json'

    @property
    def update_check_path(self) -> Path: return self.root/'update-check.json'

    @property
    def previous_binary(self) -> Path: return self.root/(self.binary.name+'.previous')

    @property
    def previous_metadata(self) -> Path: return self.root/'installed.previous.json'

    def status(self) -> dict:
        item=self.selected(); versions=self.available_versions()
        version_resources={}
        for version in versions:
            resource=self._selected_from(self._manifest_for_version(version))
            if not resource: continue
            official=next((source for source in resource['sources'] if source['id']=='github'),
                          resource['sources'][-1] if resource['sources'] else {})
            version_resources[version]={'artifact':resource['name'],'expected_sha256':resource['sha256'],
                                        'size':resource.get('size',0),'download_url':official.get('url',''),
                                        'resource_key':resource.get('key','')}
        base={'adapter':self.manifest.get('adapter',''),'platform':self.platform,'version':self.version,
              'recommended_version':self.recommended_version,'available_versions':versions,
              'version_resources':version_resources,
              'available_platforms':sorted(str(key) for key in self.manifest.get('artifacts',{})),
              'resource_key':item.get('key','') if item else self._platform_key(),
              'release_api_url':str(self.catalog.get('release_api_url') or ''),
              'update_check':self.update_check(), 'installed_version':'','update_available':False}
        if not item:
            return {**base,'state':'unsupported','ready':False,'resource_state':'missing',
                    'message':'当前平台没有固定制品（需要 '+self._platform_key()+'，清单提供：'+', '.join(base['available_platforms'])+'）'}
        official=next((source for source in item['sources'] if source['id']=='github'),item['sources'][-1] if item['sources'] else {})
        public={'artifact':item['name'],'expected_sha256':item['sha256'],'size':item.get('size',0),
                'download_url':official.get('url',''),'artifact_version':item.get('version',self.version)}
        if not self.binary.is_file() or not self.metadata_path.is_file():
            return {**base,**public,'state':'not_installed','resource_state':'available','ready':False,'message':'内核尚未安装'}
        try:
            meta=json.loads(self.metadata_path.read_text(encoding='utf-8')); digest=hashlib.sha256(self.binary.read_bytes()).hexdigest()
            installed_version=str(meta.get('version') or '')
            installed_manifest=(self.manifest if installed_version==self.version else self._manifest_for_version(installed_version)) if installed_version in self.available_versions() else None
            installed_item=self._selected_from(installed_manifest) if installed_manifest else None
        except (OSError,ValueError,TypeError):
            return {**base,**public,'state':'invalid','resource_state':'available','ready':False,'message':'内核安装记录损坏'}
        if not installed_item or meta.get('artifact')!=installed_item['name'] or meta.get('archive_sha256')!=installed_item['sha256'] or meta.get('binary_sha256')!=digest:
            return {**base,**public,'state':'invalid','resource_state':'available','ready':False,
                    'installed_version':installed_version,'message':'内核文件摘要与安装记录不一致'}
        if self.platform['os']!='windows' and not self.binary.stat().st_mode & stat.S_IXUSR:
            return {**base,**public,'state':'invalid','resource_state':'available','ready':False,
                    'installed_version':installed_version,'message':'内核文件不可执行'}
        update_available=installed_version!=self.recommended_version and _version_key(self.recommended_version)>_version_key(installed_version)
        state='update_available' if update_available else 'installed'
        message=('已安装 '+installed_version+'，可更新到 '+self.recommended_version) if update_available else '固定版本内核已校验'
        return {**base,**public,'state':state,'resource_state':'available','ready':True,'installed_version':installed_version,
                'update_available':update_available,'message':message,'source':meta.get('source',''),
                'installed_artifact':installed_item['name'],'installed_sha256':meta.get('binary_sha256',''),
                'installed_at':meta.get('installed_at',0),'binary_sha256':digest}

    def uninstall(self) -> dict:
        """Remove only managed installation files and keep update-check history."""
        installed=bool(self.binary.exists() or self.metadata_path.exists())
        for path in (self.binary,self.metadata_path,self.previous_binary,self.previous_metadata):
            try: path.unlink()
            except FileNotFoundError: pass
        return {'state':'uninstalled' if installed else 'not_installed','ready':False,
                'adapter':self.manifest.get('adapter',''),
                'message':'内核资源已卸载' if installed else '内核资源尚未安装'}

    def update_check(self) -> dict:
        try:
            value=json.loads(self.update_check_path.read_text(encoding='utf-8'))
            return value if isinstance(value,dict) else {}
        except (OSError,ValueError,TypeError):
            return {}

    def _store_update_check(self, value: dict) -> dict:
        temp=self.update_check_path.with_suffix('.tmp')
        temp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
        temp.chmod(0o600); temp.replace(self.update_check_path)
        return value

    async def check_update(self) -> dict:
        """Check the fixed official release endpoint without downloading anything."""
        url=str(self.catalog.get('release_api_url') or '')
        checked_at=int(time.time())
        base={'checked_at':checked_at,'latest_version':'','recommended_version':self.recommended_version,
              'installed_version':self._installed_version(),'state':'check_failed','message':'未配置官方版本检查地址'}
        if not url.startswith('https://api.github.com/repos/') or not url.endswith('/releases/latest'):
            return self._store_update_check(base)
        try:
            timeout=httpx.Timeout(20,connect=10,write=20,pool=10)
            async with httpx.AsyncClient(timeout=timeout,follow_redirects=False,trust_env=True,
                                          headers={'Accept':'application/vnd.github+json','User-Agent':'astrbot-plugin-proxy-manage'}) as client:
                response=await client.get(url)
                response.raise_for_status()
                payload=response.json()
            tag=str(payload.get('tag_name') or '').strip()
            match=re.search(r'(?<!\d)(\d+(?:\.\d+){1,3})(?!\d)',tag)
            if not match: raise ValueError('官方版本响应缺少有效版本号')
            latest=match.group(1)
            fixed=latest in self.available_versions()
            current=self.recommended_version
            if _version_key(latest)<=_version_key(current):
                state='up_to_date'; message='官方稳定版不高于当前固定版本'
            elif fixed:
                state='update_available'; message='发现已审核固定资源，可更新到 '+latest
            else:
                state='pending_review'; message='发现官方新版本，等待固定资源清单审核'
            return self._store_update_check({**base,'latest_version':latest,'state':state,'message':message,
                                              'release_name':str(payload.get('name') or ''),
                                              'release_url':str(payload.get('html_url') or '')})
        except (httpx.HTTPError,OSError,ValueError,TypeError,RuntimeError) as exc:
            return self._store_update_check({**base,'message':'手动检查更新失败：'+self._download_error(exc)})

    def _installed_version(self) -> str:
        try:
            value=json.loads(self.metadata_path.read_text(encoding='utf-8'))
            return str(value.get('version') or '')
        except (OSError,ValueError,TypeError):
            return ''

    def _backup_current(self):
        if self.binary.exists(): self.binary.replace(self.previous_binary)
        if self.metadata_path.exists(): self.metadata_path.replace(self.previous_metadata)

    def commit(self):
        for path in (self.previous_binary,self.previous_metadata):
            try: path.unlink()
            except FileNotFoundError: pass

    def rollback(self):
        if self.previous_binary.exists():
            if self.binary.exists(): self.binary.unlink()
            self.previous_binary.replace(self.binary)
        elif self.binary.exists() and not self.previous_metadata.exists():
            self.binary.unlink()
        if self.previous_metadata.exists():
            if self.metadata_path.exists(): self.metadata_path.unlink()
            self.previous_metadata.replace(self.metadata_path)
        elif self.metadata_path.exists() and not self.previous_binary.exists():
            self.metadata_path.unlink()

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
                    if progress: progress({'phase':'installed','version':self.version,'downloaded':len(archive),'total':len(archive),
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
                    path=str(item.get('binary_path',''))
                    files=[info for info in package.infolist() if not info.is_dir() and (not path or info.filename==path)]
                    if len(files)!=1 or files[0].file_size>MAX_BINARY_SIZE: raise ValueError('内核 ZIP 内容不安全')
                    binary=package.read(files[0])
            elif item['format']=='tar.gz':
                with tarfile.open(fileobj=BytesIO(archive),mode='r:gz') as package:
                    path=str(item.get('binary_path',''))
                    files=[member for member in package.getmembers() if member.isfile() and member.name==path]
                    if len(files)!=1 or files[0].size>MAX_BINARY_SIZE: raise ValueError('内核 TAR 内容不安全')
                    stream=package.extractfile(files[0]); binary=stream.read(MAX_BINARY_SIZE+1) if stream else b''
            else: raise ValueError('固定清单中的压缩格式不受支持')
        except (gzip.BadGzipFile,EOFError,zipfile.BadZipFile,tarfile.TarError) as exc:
            raise ValueError('内核制品压缩内容损坏') from exc
        if not binary or len(binary)>MAX_BINARY_SIZE: raise ValueError('解压后的内核为空或过大')
        self._backup_current()
        temp=self.binary.with_suffix(self.binary.suffix+'.tmp')
        with temp.open('wb') as stream: stream.write(binary); stream.flush(); os.fsync(stream.fileno())
        temp.chmod(stat.S_IRUSR|stat.S_IWUSR|stat.S_IXUSR); temp.replace(self.binary)
        metadata={'adapter':self.manifest['adapter'],'version':self.version,'artifact':item['name'],
                  'archive_sha256':digest,'binary_sha256':hashlib.sha256(binary).hexdigest(),'source':source}
        metadata['installed_at']=int(time.time())
        meta_temp=self.metadata_path.with_suffix('.tmp')
        meta_temp.write_text(json.dumps(metadata,indent=2),encoding='utf-8'); meta_temp.chmod(0o600); meta_temp.replace(self.metadata_path)
        return self.status()


class ArtifactInstallTask:
    def __init__(self,manager:ArtifactManager,on_installed,on_rollback=None):
        self.manager=manager; self.on_installed=on_installed; self.on_rollback=on_rollback
        self.task=None; self.cancel_event=None
        self.state={'state':'idle','operation':'install','phase':'idle','progress':0,
                    'message':'尚未开始资源任务','updated_at':int(time.time())}

    def status(self) -> dict: return dict(self.state)

    def _update(self,values:dict):
        downloaded=int(values.get('downloaded',0) or 0); total=int(values.get('total',0) or 0)
        progress=values.get('progress',min(95,round(downloaded*100/total)) if total else self.state.get('progress',5))
        self.state={**self.state,**values,'state':'running','progress':progress,'updated_at':int(time.time())}

    def start(self) -> dict:
        if self.task and not self.task.done(): return self.status()
        self.cancel_event=asyncio.Event()
        self.state={'state':'running','operation':'install','phase':'queued','progress':2,
                    'message':'安装任务已创建','downloaded':0,'total':0,
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
            self.manager.commit()
            self.record_completed(artifact,process)
        except Exception as exc:
            try: self.manager.rollback()
            except OSError: pass
            if self.on_rollback:
                try: await self.on_rollback()
                except Exception: pass
            self.state={**self.state,'state':'failed','phase':'start_failed',
                        'message':'制品已安装，但内核启动失败：'+str(exc),'artifact':artifact,'updated_at':int(time.time())}

    def record_completed(self,artifact,process,message='内核安装并启动成功'):
        self.state={**self.state,'state':'completed','phase':'completed','progress':100,'message':message,
                    'artifact':artifact,'process':process,'updated_at':int(time.time())}

    def start_uninstall(self,on_uninstall) -> dict:
        if self.task and not self.task.done(): return self.status()
        self.cancel_event=None
        self.state={'state':'running','operation':'uninstall','phase':'preparing','progress':10,
                    'message':'正在准备卸载内核资源','updated_at':int(time.time())}
        self.task=asyncio.create_task(self._run_uninstall(on_uninstall))
        return self.status()

    async def _run_uninstall(self,on_uninstall):
        try:
            self._update({'operation':'uninstall','phase':'validating','progress':35,'message':'正在检查内核运行状态'})
            result=await on_uninstall(self._update)
            self.state={**self.state,'state':'completed','phase':'completed','progress':100,
                        'message':result.get('message','内核资源已卸载'),'artifact':result,
                        'updated_at':int(time.time())}
        except Exception as exc:
            self.state={**self.state,'state':'failed','phase':'failed','message':str(exc),'updated_at':int(time.time())}

    async def cancel(self) -> dict:
        if self.cancel_event: self.cancel_event.set()
        if self.task and not self.task.done():
            self.task.cancel(); await asyncio.gather(self.task,return_exceptions=True)
        return self.status()

    async def stop(self): await self.cancel()
