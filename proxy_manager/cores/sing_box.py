from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

import httpx

from .base import CoreAdapter
from ..domain.model import compiled_rules


class SingBoxAdapter(CoreAdapter):
    id='sing-box'

    def capabilities(self) -> dict:
        return {'id':self.id,'protocols':{'anytls','http','https','socks','socks5','socks5h'},
                'groups':{'select','url-test'},'rules':{'exact','suffix'},'probe':True,
                'hot_reload':True,'inspect':True,'platforms':['linux','darwin','windows']}

    def artifact(self) -> dict:
        return json.loads(Path(__file__).with_name('sing_box_artifacts.json').read_text(encoding='utf-8'))

    def config_filename(self) -> str: return 'config.json'
    def serialize(self, document: dict) -> bytes:
        return (json.dumps(document,ensure_ascii=False,indent=2)+'\n').encode()
    def command(self, binary: Path, config: Path) -> list[str]:
        return [str(binary),'run','-c',str(config)]

    @staticmethod
    def _node(node: dict) -> dict:
        protocol=node.get('protocol',''); connection=node.get('connection') or {}
        parsed=urlsplit(node.get('endpoint','')); server=connection.get('server') or parsed.hostname
        port=connection.get('port') or parsed.port
        if not server or not port: raise ValueError('节点连接参数缺少服务器或端口：'+node['id'])
        tag=node.get('kernel_name','node-'+node['id'])
        username=str(connection.get('username') or unquote(parsed.username or ''))
        password=str(connection.get('password') or unquote(parsed.password or ''))
        if protocol in {'http','https'}:
            item={'type':'http','tag':tag,'server':server,'server_port':int(port)}
            if username: item['username']=username
            if password: item['password']=password
            if protocol=='https': item['tls']={'enabled':True,'server_name':connection.get('sni') or server}
            return item
        if protocol in {'socks','socks5','socks5h'}:
            item={'type':'socks','tag':tag,'server':server,'server_port':int(port),'version':'5'}
            if username: item['username']=username
            if password: item['password']=password
            return item
        if protocol=='anytls':
            credential=str(connection.get('password') or password or username)
            if not credential: raise ValueError('AnyTLS 节点缺少密码：'+node['id'])
            tls={'enabled':True,'server_name':connection.get('sni') or server}
            if connection.get('skip-cert-verify'): tls['insecure']=True
            return {'type':'anytls','tag':tag,'server':server,'server_port':int(port),'password':credential,'tls':tls}
        raise ValueError('sing-box 不支持节点协议：'+str(protocol))

    def render(self, state: dict, compiled: list[dict]|None=None) -> dict:
        compiled=compiled if compiled is not None else compiled_rules(state)
        runnable={node['id']:node for node in state['nodes'] if node['enabled'] and not node.get('excluded')
                  and self.id in node.get('adapters',[]) and not node.get('invalid_reference')}
        outbounds=[self._node(node) for node in sorted(runnable.values(),key=lambda value:value['id'])]
        outbounds.extend([{'type':'direct','tag':'DIRECT'},{'type':'block','tag':'REJECT'}])
        for group in state['groups']:
            if group['id']=='direct': continue
            if group['mode']=='fallback': raise ValueError('sing-box 不支持 fallback 代理组')
            members=[runnable[node_id]['kernel_name'] for node_id in group['node_ids'] if node_id in runnable]
            if not members: raise ValueError('代理组没有可应用的 sing-box 节点：'+group['name'])
            tag=group.get('kernel_name','group-'+group['id'])
            if group['mode']=='url-test':
                outbounds.append({'type':'urltest','tag':tag,'outbounds':members,
                                  'url':group.get('test_url','https://www.gstatic.com/generate_204'),
                                  'interval':str(group.get('test_interval',300))+'s','tolerance':group.get('tolerance',50)})
            else:
                outbounds.append({'type':'selector','tag':tag,'outbounds':members,
                                  'default':runnable[group['selected']]['kernel_name'] if group.get('selected') in runnable else members[0]})
        names={group['id']:('DIRECT' if group['id']=='direct' else group.get('kernel_name','group-'+group['id'])) for group in state['groups']}
        rules=[]
        for route in compiled:
            target=names.get(route['target'])
            if not target: continue
            key='domain' if route['match']=='exact' else 'domain_suffix'
            rules.append({key:[route['host'].removeprefix('*.')],'action':'route','outbound':target})
        control=state['control']; entry=state['proxy_entry']; port=urlsplit(entry.get('http_url','')).port
        return {'log':{'level':'warn'},'inbounds':[{'type':'mixed','tag':'proxy-entry','listen':'127.0.0.1','listen_port':port}],
                'outbounds':outbounds,'route':{'rules':rules,'final':'DIRECT','auto_detect_interface':True},
                'experimental':{'clash_api':{'external_controller':control.get('listen','127.0.0.1:19090'),
                                               'secret':control.get('secret','')}}}

    def validate(self, document: dict) -> None:
        if not isinstance(document,dict) or not isinstance(document.get('route'),dict):
            raise ValueError('sing-box 候选配置格式无效')
        tags=[item.get('tag') for item in document.get('outbounds',[])]
        if any(not tag for tag in tags) or len(tags)!=len(set(tags)): raise ValueError('sing-box 出站标签为空或重复')
        available=set(tags)
        for item in document.get('outbounds',[]):
            if item.get('type') in {'selector','urltest'} and (not item.get('outbounds') or any(x not in available for x in item['outbounds'])):
                raise ValueError('sing-box 代理组成员无效：'+str(item.get('tag','')))
        if not document.get('inbounds') or document['route'].get('final') not in available:
            raise ValueError('sing-box 稳定入口或默认路由无效')
        if any(rule.get('outbound') not in available for rule in document['route'].get('rules',[])):
            raise ValueError('sing-box 分流规则目标无效')

    def expected_rules(self, document: dict) -> list[tuple[str,str,str]]:
        result=[]
        for rule in document.get('route',{}).get('rules',[]):
            if rule.get('domain'): kind,payload='DOMAIN',rule['domain'][0]
            elif rule.get('domain_suffix'): kind,payload='DOMAIN-SUFFIX',rule['domain_suffix'][0]
            else: kind,payload='',''
            result.append((kind,payload,rule.get('outbound','')))
        result.append(('MATCH','',document.get('route',{}).get('final','')))
        return result

    def verify(self, document: dict, runtime: object, proxies: object, rules: object) -> list[str]:
        errors=[]
        if not isinstance(proxies,dict): return ['无法读取 sing-box 运行出站']
        for item in document.get('outbounds',[]):
            if item.get('type') in {'direct','block'}: continue
            actual=proxies.get(item['tag'])
            if not isinstance(actual,dict): errors.append('缺少 sing-box 出站 '+item['tag']); continue
            if item.get('type') in {'selector','urltest'}:
                members=actual.get('all') or []
                if members!=item.get('outbounds'): errors.append('sing-box 代理组成员不一致 '+item['tag'])
                if actual.get('now') not in item.get('outbounds',[]): errors.append('sing-box 代理组选择状态无效 '+item['tag'])
        expected=self.expected_rules(document)
        if rules is not None and rules!=expected: errors.append('sing-box 运行规则内容或顺序不一致')
        return errors

    def fail_closed_document(self, control: dict, entry: dict) -> dict:
        port=urlsplit(entry.get('http_url','')).port
        return {'log':{'level':'warn'},'inbounds':[{'type':'mixed','tag':'proxy-entry','listen':'127.0.0.1','listen_port':port}],
                'outbounds':[{'type':'block','tag':'REJECT'}],'route':{'rules':[],'final':'REJECT','auto_detect_interface':True},
                'experimental':{'clash_api':{'external_controller':control.get('listen','127.0.0.1:19090'),'secret':control.get('secret','')}}}

    def control(self, state: dict) -> tuple[dict,dict]:
        control=state['control']
        if not control.get('enabled') or not control.get('url'): raise ValueError('尚未配置 sing-box 控制接口')
        return control,({'Authorization':'Bearer '+control['secret']} if control.get('secret') else {})

    def inspect(self, state: dict, application: dict, runtime: object=None, proxies: object=None, rules: object=None, version: str='') -> dict:
        expected=self.render(state); saved=self.revision(expected); applied=application.get('applied_revision','')
        base={'ready':True,'version':version,'adapter':self.id,'deployment':'dedicated','scope':'full',
              'saved_revision':saved,'applied_revision':applied}
        if application.get('status')=='restore_failed': return {**base,'state':'restore_failed','ready':False,'message':'sing-box 恢复失败'}
        if not applied: return {**base,'state':'saved','message':'sing-box 配置尚未应用'}
        if saved!=applied: return {**base,'state':'pending_apply','message':'sing-box 配置等待应用'}
        errors=self.verify(expected,runtime,proxies,rules)
        if errors: return {**base,'state':'runtime_inconsistent','message':errors[0]}
        return {**base,'state':'applied','message':'sing-box 运行配置与已应用修订一致','proxy_entry':state['proxy_entry']}

    async def fetch_runtime(self, state: dict) -> dict:
        control,headers=self.control(state)
        async with httpx.AsyncClient(base_url=control['url'],headers=headers,timeout=control['timeout'],trust_env=False) as client:
            version=await client.get('/version'); version.raise_for_status(); raw=str(version.json().get('version',''))
            match=re.search(r'(\d+)\.(\d+)\.(\d+)',raw)
            if not match or tuple(map(int,match.groups()))<(1,12,0):
                return {'state':'version_unsupported','ready':False,'adapter':self.id,'message':'需要 sing-box 1.12.0 或更高版本','version':raw}
            response=await client.get('/proxies'); response.raise_for_status(); proxies=response.json().get('proxies',{})
        return {'version':raw,'runtime':{'mode':'rule'},'proxies':proxies,'rules':None}

    async def apply(self, state: dict, document: dict, *, supervisor=None, binary=None, config=None):
        if supervisor is None or binary is None or config is None:
            raise ValueError('sing-box 应用配置需要受监督重启上下文')
        await self.restart(supervisor,binary,config)
        fetched=await self.fetch_runtime(state); errors=self.verify(document,fetched['runtime'],fetched['proxies'],fetched['rules'])
        if errors: raise ValueError('；'.join(errors))

    async def select(self, state: dict, group: dict, node: dict):
        control,headers=self.control(state); name=group.get('kernel_name','group-'+group['id'])
        async with httpx.AsyncClient(base_url=control['url'],headers=headers,timeout=control['timeout'],trust_env=False) as client:
            response=await client.put('/proxies/'+quote(name,safe=''),json={'name':node['kernel_name']}); response.raise_for_status()

    async def probe(self, state: dict, node: dict, target: str, timeout: int) -> int:
        control,headers=self.control(state)
        async with httpx.AsyncClient(base_url=control['url'],headers=headers,timeout=timeout,trust_env=False) as client:
            response=await client.get('/proxies/'+quote(node['kernel_name'],safe='')+'/delay',params={'url':target,'timeout':timeout*1000})
            response.raise_for_status(); delay=response.json().get('delay')
        if not isinstance(delay,int): raise ValueError('sing-box 控制接口未返回延迟')
        return delay

    async def proxies(self, state: dict) -> dict:
        fetched=await self.fetch_runtime(state)
        return {'version':fetched.get('version'),'proxies':fetched.get('proxies',{})}

    async def group_selection(self, state: dict, group: dict) -> str:
        data=await self.proxies(state); item=data['proxies'].get(group.get('kernel_name'),{})
        return item.get('now','') if isinstance(item,dict) else ''

    async def connection_snapshot(self, state: dict, host: str) -> list[dict]:
        control,headers=self.control(state)
        async with httpx.AsyncClient(base_url=control['url'],headers=headers,timeout=control['timeout'],trust_env=False) as client:
            response=await client.get('/connections'); response.raise_for_status()
        result=[]
        for item in response.json().get('connections') or []:
            metadata=item.get('metadata') if isinstance(item.get('metadata'),dict) else {}
            observed=str(metadata.get('host') or metadata.get('destinationIP') or '').rstrip('.').lower()
            if observed!=host.rstrip('.').lower(): continue
            result.append({'id':str(item.get('id','')),'host':observed,'destination_port':str(metadata.get('destinationPort','')),
                           'network':str(metadata.get('network','')),'rule':str(item.get('rule','')).upper().replace('_','-'),
                           'rule_payload':str(item.get('rulePayload','')).rstrip('.').lower(),
                           'chains':[str(value) for value in item.get('chains',[]) if isinstance(value,str)]})
        return result
