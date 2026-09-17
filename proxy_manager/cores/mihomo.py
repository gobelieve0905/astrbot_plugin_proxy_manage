from __future__ import annotations

import copy
import re
from urllib.parse import parse_qsl, quote, unquote, urlsplit

import httpx

from .base import CoreAdapter
from ..domain.model import compiled_rules


class MihomoAdapter(CoreAdapter):
    id='mihomo'

    def capabilities(self) -> dict:
        return {
            'id':self.id,
            'protocols':{'anytls','http','https','socks','socks5','socks5h'},
            'groups':{'select','url-test','fallback'},
            'rules':{'exact','suffix'},
            'probe':True,'hot_reload':True,'inspect':True,
            'platforms':['linux','darwin','windows'],
        }

    def artifact(self) -> dict:
        return {'status':'managed','manifest':'mihomo_artifacts.json'}

    @staticmethod
    def _typed_query_value(value:str):
        if str(value).lower() in {'true','yes','1'}: return True
        if str(value).lower() in {'false','no','0'}: return False
        try: return int(value)
        except (TypeError,ValueError): return value

    def render_proxy(self, node: dict) -> dict:
        protocol=node.get('protocol',''); connection=copy.deepcopy(node.get('connection',{}))
        if node.get('support',{}).get('status','supported')!='supported':
            raise ValueError('节点协议尚未验证，不能生成运行配置：'+node.get('display_name',node['id']))
        if connection and connection.get('server') and connection.get('port'):
            proxy={key:value for key,value in connection.items() if key not in {'uri','display_name'}}
            proxy['name']=node['kernel_name']; proxy['type']='socks5' if protocol in {'socks','socks5','socks5h'} else ('http' if protocol in {'http','https'} else protocol)
            return proxy
        parsed=urlsplit(node['endpoint']); host=parsed.hostname; port=parsed.port
        if not host or not port: raise ValueError('节点连接参数缺少服务器或端口：'+node['id'])
        proxy={'name':node['kernel_name'],'type':'socks5' if protocol in {'socks','socks5','socks5h'} else ('http' if protocol in {'http','https'} else protocol),
               'server':host,'port':port}
        username=unquote(parsed.username or ''); password=unquote(parsed.password or '')
        if protocol in {'http','https','socks','socks5','socks5h'}:
            if username: proxy['username']=username
            if password: proxy['password']=password
            if protocol=='https': proxy['tls']=True
        elif protocol=='anytls':
            credential=password or username
            if not credential: raise ValueError('AnyTLS 节点缺少密码：'+node['id'])
            proxy['password']=credential
        for key,value in parse_qsl(parsed.query,keep_blank_values=True):
            target={'insecure':'skip-cert-verify','allow-insecure':'skip-cert-verify','servername':'sni'}.get(key,key.replace('_','-'))
            proxy[target]=self._typed_query_value(value)
        return proxy

    def render(self, state: dict, compiled: list[dict]|None=None) -> dict:
        try:
            import yaml
        except ImportError as exc:
            raise ValueError('缺少 PyYAML，无法生成 Mihomo 配置') from exc
        compiled=compiled if compiled is not None else compiled_rules(state)
        runnable={node['id']:node for node in state['nodes'] if node['enabled'] and not node.get('excluded')
                  and not node.get('invalid_reference') and node.get('support',{}).get('status','supported')=='supported'}
        proxies=[self.render_proxy(node) for node in sorted(runnable.values(),key=lambda item:item['id'])]
        groups=[]
        for group in state['groups']:
            if group['id']=='direct': continue
            mode={'select':'select','url-test':'url-test','fallback':'fallback'}.get(group['mode'],'select')
            members=[runnable[node_id]['kernel_name'] for node_id in group['node_ids'] if node_id in runnable]
            if not members: raise ValueError('代理组没有可应用的已验证节点：'+group['name'])
            item={'name':group.get('kernel_name','group-'+group['id']),'type':mode,'proxies':members}
            if group['mode'] in {'url-test','fallback'}:
                item.update({'url':group.get('test_url','https://www.gstatic.com/generate_204'),
                             'interval':group.get('test_interval',300),'tolerance':group.get('tolerance',50)})
                if group.get('failure_policy','fail-closed')=='fail-closed': item['lazy']=False
            groups.append(item)
        names={item['id']:('DIRECT' if item['id']=='direct' else item.get('kernel_name','group-'+item['id'])) for item in state['groups']}
        rules=[]
        for route in compiled:
            if route['target'] not in names: continue
            host=route['host'].removeprefix('*.')
            rules.append(('DOMAIN' if route['match']=='exact' else 'DOMAIN-SUFFIX')+','+host+','+names[route['target']])
        rules.append('MATCH,'+names.get('direct','DIRECT'))
        control=state['control']; entry=state.get('proxy_entry',{})
        document={'mode':'rule','log-level':'silent','proxies':proxies,
                  'proxy-providers':{},'proxy-groups':groups,'rules':rules}
        if control.get('deployment')=='dedicated':
            document['external-controller']=control.get('listen','127.0.0.1:9090')
            document['secret']=control.get('secret','')
            port=urlsplit(entry.get('http_url','')).port if entry.get('http_url') else None
            if port: document.update({'mixed-port':port,'allow-lan':False,'bind-address':'127.0.0.1'})
        yaml.safe_load(yaml.safe_dump(document,allow_unicode=True,sort_keys=False))
        return document

    def validate(self, document: dict) -> None:
        proxy_names=[item.get('name') for item in document.get('proxies',[])]
        group_names=[item.get('name') for item in document.get('proxy-groups',[])]
        if len(proxy_names)!=len(set(proxy_names)) or any(not name for name in proxy_names):
            raise ValueError('候选配置中的内核节点名称为空或重复')
        if len(group_names)!=len(set(group_names)) or any(not name for name in group_names):
            raise ValueError('候选配置中的内核代理组名称为空或重复')
        available=set(proxy_names)|{'DIRECT','REJECT'}
        for group in document.get('proxy-groups',[]):
            members=group.get('proxies',[])
            if not members or any(member not in available for member in members):
                raise ValueError('候选配置中的代理组成员无效：'+str(group.get('name','')))
            available.add(group['name'])
        for rule in document.get('rules',[]):
            if not isinstance(rule,str) or ',' not in rule or rule.rsplit(',',1)[1] not in available:
                raise ValueError('候选配置中的分流规则目标无效')

    @staticmethod
    def expected_rules(document:dict) -> list[tuple[str,str,str]]:
        result=[]
        for value in document.get('rules',[]):
            parts=value.split(','); kind=parts[0].upper(); target=parts[-1]
            result.append((kind,','.join(parts[1:-1]),target))
        return result

    def verify(self, document: dict, runtime: object, proxies: object, rules: object) -> list[str]:
        errors=[]
        if not isinstance(runtime,dict) or runtime.get('mode')!='rule': errors.append('运行模式不是 rule')
        if not isinstance(proxies,dict): return errors+['无法读取运行代理']
        for proxy in document.get('proxies',[]):
            actual=proxies.get(proxy['name'])
            if not isinstance(actual,dict): errors.append('缺少内核节点 '+proxy['name']); continue
            if str(actual.get('type','')).lower().replace('-','') != str(proxy.get('type','')).lower().replace('-',''):
                errors.append('节点类型不一致 '+proxy['name'])
        type_names={'select':'Selector','url-test':'URLTest','fallback':'Fallback'}
        for group in document.get('proxy-groups',[]):
            actual=proxies.get(group['name'])
            if not isinstance(actual,dict): errors.append('缺少代理组 '+group['name']); continue
            if actual.get('type')!=type_names.get(group.get('type')): errors.append('代理组类型不一致 '+group['name'])
            if actual.get('all')!=group.get('proxies'): errors.append('代理组成员不一致 '+group['name'])
            if actual.get('now') not in group.get('proxies',[]): errors.append('代理组选择状态无效 '+group['name'])
        actual_rules=[]
        if isinstance(rules,list):
            for rule in rules:
                if isinstance(rule,dict): actual_rules.append((str(rule.get('type','')).upper(),str(rule.get('payload','')),str(rule.get('proxy',''))))
        if actual_rules!=self.expected_rules(document): errors.append('运行规则内容或顺序不一致')
        return errors

    def fail_closed_document(self, control: dict, entry: dict) -> dict:
        document={'mode':'rule','log-level':'silent','proxies':[],'proxy-providers':{},'proxy-groups':[],
                  'rules':['MATCH,REJECT'],'external-controller':control.get('listen','127.0.0.1:9090'),'secret':control.get('secret','')}
        port=urlsplit(entry.get('http_url','')).port if entry.get('http_url') else None
        if port: document['mixed-port']=port
        if port: document.update({'allow-lan':False,'bind-address':'127.0.0.1'})
        return document

    def control(self, state: dict) -> tuple[dict,dict]:
        control=state['control']
        if not control['enabled'] or not control['url']: raise ValueError('尚未配置内核控制接口地址和密钥')
        return control,({'Authorization':'Bearer '+control['secret']} if control['secret'] else {})

    def inspect(self, state: dict, application: dict, runtime: object=None, proxies: object=None, rules: object=None, version: str='') -> dict:
        control=state['control']
        if not control['enabled'] or not control['url']:
            return {'state':'not_configured','ready':False,'message':'尚未配置内核控制接口地址和密钥',
                    'adapter':self.id,'deployment':control.get('deployment','existing'),'scope':control.get('scope','providers-groups-rules')}
        try:
            expected=self.render(state)
        except ValueError:
            expected=None
        saved_revision=self.revision(expected) if expected else ''
        base={'ready':True,'version':version,'adapter':self.id,'deployment':control.get('deployment','existing'),
              'scope':control.get('scope','providers-groups-rules'),'saved_revision':saved_revision,
              'applied_revision':application.get('applied_revision','')}
        if application.get('status')=='restore_failed':
            return {**base,'state':'restore_failed','message':'上次应用失败且运行配置恢复失败，请立即检查专用内核'}
        if application.get('status')=='fail_closed':
            recovery=application.get('document')
            errors=self.verify(recovery,runtime,proxies,rules) if isinstance(recovery,dict) else ['失败关闭配置缺失']
            if errors:
                return {**base,'state':'runtime_inconsistent','message':'失败关闭配置与运行状态不一致：'+errors[0]}
            return {**base,'state':'fail_closed','ready':False,'message':'候选配置失败；当前仅保留 MATCH,REJECT 的失败关闭配置'}
        if control.get('deployment')!='dedicated':
            return {**base,'state':'saved','message':'共享内核仅允许检查；缺少可信完整基线，已禁止写入'}
        if not application.get('applied_revision'):
            return {**base,'state':'saved','message':'插件配置已保存，尚未应用到专用内核'}
        if saved_revision!=application.get('applied_revision'):
            return {**base,'state':'pending_apply','message':'插件配置已变更，等待应用到专用内核'}
        errors=self.verify(expected,runtime,proxies,rules) if expected else ['候选配置无效']
        if errors:
            return {**base,'state':'runtime_inconsistent','message':'运行配置与已应用修订不一致：'+errors[0]}
        return {**base,'state':'applied','message':'专用内核已连接，运行配置与已应用修订一致',
                'proxy_entry':state['proxy_entry']}

    async def fetch_runtime(self, state: dict):
        control,headers=self.control(state)
        async with httpx.AsyncClient(base_url=control['url'],headers=headers,timeout=control['timeout'],trust_env=False) as client:
            version_response=await client.get('/version'); version_response.raise_for_status()
            version=version_response.json()
            raw_version=str(version.get('version',''))
            match=re.search(r'(\d+)\.(\d+)\.(\d+)',raw_version)
            if not version.get('meta') or not match or tuple(map(int,match.groups())) < (1,19,0):
                return {'state':'version_unsupported','ready':False,'adapter':self.id,'message':'需要 Mihomo Meta 1.19.0 或更高版本','version':raw_version,
                        'deployment':control.get('deployment','existing'),'scope':control.get('scope','providers-groups-rules')}
            configs=await client.get('/configs'); configs.raise_for_status(); runtime=configs.json()
            proxies_response=await client.get('/proxies'); proxies_response.raise_for_status(); proxies=proxies_response.json().get('proxies',{})
            rules_response=await client.get('/rules'); rules_response.raise_for_status(); runtime_rules=rules_response.json().get('rules',[])
        return {'version':raw_version,'runtime':runtime,'proxies':proxies,'rules':runtime_rules}

    async def apply(self, state: dict, document: dict):
        import yaml
        control,headers=self.control(state)
        payload={'path':'','payload':yaml.safe_dump(document,allow_unicode=True,sort_keys=False)}
        async with httpx.AsyncClient(base_url=control['url'],headers=headers,timeout=control['timeout'],trust_env=False) as client:
            response=await client.put('/configs?force=true',json=payload); response.raise_for_status()
            running=await client.get('/configs'); running.raise_for_status()
            proxies_response=await client.get('/proxies'); proxies_response.raise_for_status()
            rules_response=await client.get('/rules'); rules_response.raise_for_status()
        errors=self.verify(document,running.json(),proxies_response.json().get('proxies',{}),rules_response.json().get('rules',[]))
        if errors: raise ValueError('；'.join(errors))

    async def select(self, state: dict, group: dict, node: dict):
        control,headers=self.control(state)
        name=group.get('kernel_name','group-'+group['id']); kernel_node=node.get('kernel_name','node-'+node['id'])
        async with httpx.AsyncClient(base_url=control['url'],headers=headers,timeout=control['timeout'],trust_env=False) as client:
            response=await client.request('PUT','/proxies/'+quote(name,safe=''),json={'name':kernel_node}); response.raise_for_status()

    async def probe(self, state: dict, node: dict, target: str, timeout: int) -> int:
        control,headers=self.control(state)
        async with httpx.AsyncClient(base_url=control['url'],headers=headers,timeout=timeout,trust_env=False) as client:
            response=await client.get('/proxies/'+quote(node.get('kernel_name',node['id']),safe='')+'/delay',params={'url':target,'timeout':timeout*1000})
        response.raise_for_status(); data=response.json()
        if not isinstance(data.get('delay'),int): raise ValueError('内核控制接口未返回延迟')
        return int(data['delay'])

    async def group_selection(self, state: dict, group: dict) -> str:
        control,headers=self.control(state)
        async with httpx.AsyncClient(base_url=control['url'],headers=headers,timeout=control['timeout'],trust_env=False) as client:
            proxies=await client.get('/proxies'); proxies.raise_for_status()
        runtime_group=proxies.json().get('proxies',{}).get(group.get('kernel_name'),{})
        return runtime_group.get('now','') if isinstance(runtime_group,dict) else ''

    async def proxies(self, state: dict) -> dict:
        control,headers=self.control(state)
        async with httpx.AsyncClient(base_url=control['url'],headers=headers,timeout=control['timeout'],trust_env=False) as client:
            version=await client.get('/version'); version.raise_for_status(); proxies=await client.get('/proxies'); proxies.raise_for_status()
        return {'version':version.json(),'proxies':proxies.json().get('proxies',{})}

    async def connection_snapshot(self, state: dict, host: str) -> list[dict]:
        control,headers=self.control(state)
        async with httpx.AsyncClient(base_url=control['url'],headers=headers,timeout=control['timeout'],trust_env=False) as client:
            response=await client.get('/connections'); response.raise_for_status()
        result=[]
        for item in response.json().get('connections',[]):
            if not isinstance(item,dict): continue
            metadata=item.get('metadata') if isinstance(item.get('metadata'),dict) else {}
            observed=str(metadata.get('host') or metadata.get('destinationIP') or '').rstrip('.').lower()
            if observed!=host.rstrip('.').lower(): continue
            result.append({
                'id':str(item.get('id','')),
                'host':observed,
                'destination_port':str(metadata.get('destinationPort','')),
                'network':str(metadata.get('network','')),
                'rule':str(item.get('rule','')).upper().replace('_','-'),
                'rule_payload':str(item.get('rulePayload','')).rstrip('.').lower(),
                'chains':[str(value) for value in item.get('chains',[]) if isinstance(value,str)],
            })
        return result
