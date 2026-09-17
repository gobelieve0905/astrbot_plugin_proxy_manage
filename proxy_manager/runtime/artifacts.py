from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import os
import platform
import stat
import zipfile
from io import BytesIO
from pathlib import Path

import httpx

MAX_ARCHIVE_SIZE=64*1024*1024
MAX_BINARY_SIZE=128*1024*1024


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
        return {**item,'key':key,'version':self.manifest['version']} if isinstance(item,dict) else None

    @property
    def binary(self) -> Path:
        return self.root/('core.exe' if self.platform['os']=='windows' else 'core')

    @property
    def metadata_path(self) -> Path: return self.root/'installed.json'

    def status(self) -> dict:
        item=self.selected(); base={'adapter':self.manifest.get('adapter',''),'platform':self.platform,'version':self.manifest.get('version','')}
        if not item: return {**base,'state':'unsupported','ready':False,'message':'当前操作系统或 CPU 架构没有固定制品'}
        if not self.binary.is_file() or not self.metadata_path.is_file():
            return {**base,'state':'not_installed','ready':False,'message':'内核尚未安装','artifact':item['name']}
        try:
            meta=json.loads(self.metadata_path.read_text(encoding='utf-8')); digest=hashlib.sha256(self.binary.read_bytes()).hexdigest()
        except (OSError,ValueError): return {**base,'state':'invalid','ready':False,'message':'内核安装记录损坏'}
        if meta.get('archive_sha256')!=item['sha256'] or meta.get('binary_sha256')!=digest:
            return {**base,'state':'invalid','ready':False,'message':'内核文件摘要与安装记录不一致'}
        if self.platform['os']!='windows' and not os.access(self.binary,os.X_OK):
            return {**base,'state':'invalid','ready':False,'message':'内核文件不可执行'}
        return {**base,'state':'installed','ready':True,'message':'固定版本内核已校验','artifact':item['name'],
                'source':meta.get('source',''),'binary_sha256':digest}

    async def download(self) -> dict:
        item=self.selected()
        if not item: raise ValueError('当前平台没有可用的固定内核制品')
        async def fetch() -> bytes:
            async with httpx.AsyncClient(timeout=30,follow_redirects=True,trust_env=True) as client:
                async with client.stream('GET',item['url']) as response:
                    response.raise_for_status()
                    length=int(response.headers.get('content-length','0') or 0)
                    if length>MAX_ARCHIVE_SIZE: raise ValueError('官方内核制品超过 64 MiB 限制')
                    chunks=[]; size=0
                    async for chunk in response.aiter_bytes():
                        size+=len(chunk)
                        if size>MAX_ARCHIVE_SIZE: raise ValueError('官方内核制品超过 64 MiB 限制')
                        chunks.append(chunk)
                    return b''.join(chunks)
        try: archive=await asyncio.wait_for(fetch(),120)
        except asyncio.TimeoutError as exc: raise RuntimeError('官方内核制品下载超时') from exc
        return self.install(archive,'official')

    def install(self,archive:bytes,source:str='offline') -> dict:
        item=self.selected()
        if not item: raise ValueError('当前平台没有可用的固定内核制品')
        if not archive or len(archive)>MAX_ARCHIVE_SIZE: raise ValueError('内核制品为空或超过 64 MiB 限制')
        digest=hashlib.sha256(archive).hexdigest()
        if digest!=item['sha256']: raise ValueError('内核制品 SHA-256 与固定清单不一致')
        if item['format']=='gz':
            with gzip.GzipFile(fileobj=BytesIO(archive)) as stream: binary=stream.read(MAX_BINARY_SIZE+1)
        elif item['format']=='zip':
            with zipfile.ZipFile(BytesIO(archive)) as package:
                files=[info for info in package.infolist() if not info.is_dir()]
                if len(files)!=1 or files[0].file_size>MAX_BINARY_SIZE: raise ValueError('内核 ZIP 内容不安全')
                binary=package.read(files[0])
        else: raise ValueError('固定清单中的压缩格式不受支持')
        if not binary or len(binary)>MAX_BINARY_SIZE: raise ValueError('解压后的内核为空或过大')
        temp=self.binary.with_suffix(self.binary.suffix+'.tmp')
        with temp.open('wb') as stream: stream.write(binary); stream.flush(); os.fsync(stream.fileno())
        temp.chmod(stat.S_IRUSR|stat.S_IWUSR|stat.S_IXUSR); temp.replace(self.binary)
        metadata={'adapter':self.manifest['adapter'],'version':self.manifest['version'],'artifact':item['name'],
                  'archive_sha256':digest,'binary_sha256':hashlib.sha256(binary).hexdigest(),'source':source}
        meta_temp=self.metadata_path.with_suffix('.tmp')
        meta_temp.write_text(json.dumps(metadata,indent=2),encoding='utf-8'); meta_temp.chmod(0o600); meta_temp.replace(self.metadata_path)
        return self.status()
