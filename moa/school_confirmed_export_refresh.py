"""Read-only saved/current comparison and explicitly pinned fresh preparation."""
import hmac
import re

from . import school_confirmed_export as batch, school_confirmed_export_saved as saved
from . import school_review as review, school_service as svc
from .core import digest, encode

REASONS = {
    'receiving_changed':'새 수신본·정정본이 있습니다.',
    'conversion_changed':'변환 버전이 달라졌습니다.',
    'confirmation_changed':'확인 기록이 달라졌습니다.',
    'confirmation_pending':'최신본의 담당자 확인이 끝나지 않았습니다.',
    'processing':'최신본을 변환 중입니다.',
    'conversion_failed':'최신본 변환에 실패했습니다.',
    'unavailable':'수신본·분석·확인 근거를 읽을 수 없습니다.',
    'snapshot_changed':'저장 당시 내용 또는 원본 목록이 달라졌습니다. 근거를 다시 확인하세요.',
}


def _item(store, principal, pin):
    result={'saved':pin,'current':None,'reasons':[],'candidate':None}
    reasons=result['reasons']
    try:
        old=svc.get_version(store,principal,pin['id'])
        post=store.db.execute('SELECT latest_version FROM school_service_posts WHERE school=? AND id=?',
            (principal.school,old['post_id'])).fetchone()
        if not post: raise LookupError('Post not found')
        current=svc.get_version(store,principal,post['latest_version'])
        if current['post_id']!=old['post_id']: raise review.Conflict('Receiving scope changed')
        result['current']={k:current[k] for k in ('id','revision','sequence','title','state')}
        if current['id']!=pin['id']: reasons.append('receiving_changed')
        if current['revision']!=pin['revision']: reasons.append('conversion_changed')
        if current['state'] in ('processing','conversion_failed'):
            reasons.append(current['state']);return result
        if not current['revision']: reasons.append('unavailable');return result
        view=review.view(store,principal,current['id'],revision_id=current['revision'])
        event=view['latest']
        result['current']['review']=({k:event[k] for k in ('id','sequence','status','created_at')} if event else None)
        if event and event['id']!=pin['event']: reasons.append('confirmation_changed')
        if not event or view['status']!='confirmed' or view['stale'] or view['migration_required']:
            reasons.append('confirmation_pending');return result
        candidate={'id':current['id'],'revision':current['revision'],'event':event['id']}
        if not reasons:
            # An unchanged event with changed evidence cannot be blessed by
            # merely calculating another hash. Keep the old list blocked.
            try: batch.prepare(store,principal,[{k:pin[k] for k in ('id','revision','event','snapshot_sha256')}],expected=True)
            except (review.Conflict,LookupError):
                reasons.append('snapshot_changed');return result
        else:
            # Check each replacement independently so an unavailable original
            # is attributed to its document, without excluding it from a ZIP.
            try: batch.prepare(store,principal,[candidate])
            except (review.Conflict,LookupError):
                reasons.append('unavailable');return result
        result['candidate']=candidate
    except (review.Conflict,LookupError,ValueError,OSError):
        reasons.append('unavailable')
    return result


def inspect(store, principal, ident):
    """Inspect at most ten documents in one SQLite snapshot; never migrate/write."""
    with batch._snapshot(store):
        row=saved.get(store,principal,ident)
        items=[_item(store,principal,pin) for pin in row['items']]
        data=None;limit=False
        if all(item['candidate'] for item in items):
            try: data=batch.prepare(store,principal,[item['candidate'] for item in items])
            except batch.Limit: limit=True
            except (review.Conflict,LookupError,ValueError,OSError): pass
        expected=digest(encode({'source':ident,'saved':row['pins'],'current':data['pins']}).encode()) if data else ''
        return {'saved':row,'items':items,'can_prepare':bool(data),'limit':limit,'prepared':data,
                'expected':expected,'changed':any(item['reasons'] for item in items)}


def prepare(store, principal, ident, *, expected):
    """Require a writer and the exact current pins shown on the comparison page."""
    if not isinstance(expected,str) or not re.fullmatch(r'[0-9a-f]{64}',expected):
        raise ValueError('Invalid current comparison')
    with batch._snapshot(store):
        svc.require_member(store,principal,write=True)
        result=inspect(store,principal,ident)
        if not result['can_prepare'] or not hmac.compare_digest(result['expected'],expected):
            raise review.Conflict('Current confirmation changed; inspect again')
        return result
