from __future__ import annotations

import copy

from ..cores.registry import current_adapter
from ..domain.model import compiled_rules


def runtime_revision(document: dict, adapter=None) -> str:
    adapter=adapter or current_adapter()
    return adapter.revision(document)


def verified_recovery_document(application: object, adapter=None) -> dict|None:
    adapter=adapter or current_adapter()
    if not isinstance(application,dict) or application.get('status') not in {'applied','pending_apply'}:
        return None
    levels=adapter.verification_levels()
    if levels.get('runtime') != 'full' or levels.get('connection') != 'full':
        return None
    recorded=str(application.get('adapter') or 'mihomo')
    if recorded!=adapter.id: return None
    document=application.get('document')
    if not isinstance(document,dict) or not application.get('applied_revision'):
        return None
    try:
        adapter.validate(document)
    except (TypeError,ValueError):
        return None
    return copy.deepcopy(document) if adapter.revision(document)==application['applied_revision'] else None


def render_document(state: dict, adapter=None) -> dict:
    adapter=adapter or current_adapter(state)
    document=adapter.render(state, compiled_rules(state))
    adapter.validate(document)
    return document


async def apply_runtime(state: dict, previous: dict, persist, event, adapter=None, runtime: dict|None=None):
    adapter=adapter or current_adapter(state)
    runtime=runtime or {}
    control=state['control']
    if control.get('deployment')!='dedicated' or control.get('scope')!='full':
        raise ValueError('共享内核缺少可信完整基线，禁止写入；请使用插件专用实例和完整配置范围')
    document=render_document(state, adapter)
    revision=adapter.revision(document)
    recovery=verified_recovery_document(previous, adapter)
    recovery_kind='previous_verified' if recovery else 'fail_closed'
    if recovery is None:
        recovery=adapter.fail_closed_document(control, state.get('proxy_entry',{}))
    adapter.validate(recovery)
    persist({'status':'applying','adapter':adapter.id,'saved_revision':revision,
             'applied_revision':previous.get('applied_revision',''),
             'document':previous.get('document'),'message':'正在应用候选配置'})
    try:
        apply_result=await adapter.apply(state, document, **runtime)
    except Exception as apply_error:
        restored=False; restore_message=''; restore_result={}
        try:
            restore_result=adapter.application_result(await adapter.apply(state, recovery, **runtime))
            restored=True
        except Exception as restore_error:
            restore_message=str(restore_error)
        status='running_unverified' if restored and restore_result.get('status')=='running_unverified' else (
            'pending_apply' if restored and recovery_kind=='previous_verified' else ('fail_closed' if restored else 'restore_failed'))
        message=(
            '候选配置应用或核对失败，已恢复上一份配置但运行证据不足'
            if restored and restore_result.get('status')=='running_unverified' else
            ('候选配置应用或核对失败，已恢复上一份已验证配置并重新核对'
            if restored and recovery_kind=='previous_verified' else
            ('候选配置失败，未找到已验证配置；已写入并核对 MATCH,REJECT 失败关闭配置' if restored else '候选配置失败，且运行配置恢复核对失败'))
        )
        persist({'status':status,'adapter':adapter.id,'saved_revision':revision,
                 'applied_revision':previous.get('applied_revision','') if recovery_kind=='previous_verified' and status!='running_unverified' else '',
                 'document':recovery if restored else None,
                 'verification':restore_result.get('verification',{}),
                 'message':message})
        event({'action':'runtime_apply','result':status,'message':str(apply_error),'restore':restore_message})
        return {'ok':False,'status':status,'message':message}
    outcome=adapter.application_result(apply_result)
    status=str(outcome.get('status') or 'applied')
    message=outcome.get('message') or '候选配置已应用并完整核对'
    persist({'status':status,'adapter':adapter.id,'saved_revision':revision,'applied_revision':revision,
             'document':document,'verification':outcome.get('verification',{}),'message':message})
    event({'action':'runtime_apply','result':status,'revision':revision,'adapter':adapter.id})
    return {'ok':status=='applied','applied':status=='applied','status':status,
            'saved_revision':revision,'applied_revision':revision,'message':message}
