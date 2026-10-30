"""Explicit formal submission of one immutable personal input, never approval."""
from urllib.parse import urlencode

from . import school_draft_handoff as handoff
from .school_draft_compare_web import REASONS, link as compare_link
from .school_draft_restore_web import origin
from .school_compare_web import STYLE
from .school_export_web import link as event_link
from .school_review_web import _hidden
from .school_saved_queue_web import query
from .school_review import BLOCK_LABELS, RELATION_LABELS
from .web import esc

REASONS={**REASONS,
    'handoff_migration_required':'임시 입력의 정식 저장 연결에는 명시적 마이그레이션이 필요합니다.',
    'reason_required':'저장된 임시 사유가 비어 있습니다. 입력을 복구해 사유를 작성하고 다시 임시 저장하세요.',
    'invalid_review_input':'미완성·유효하지 않은 입력이거나 현재 검수본과 차이가 없습니다. 입력을 복구해 확인하고 다시 임시 저장하세요.',
    'already_submitted':'이미 이 임시 입력으로 정식 수정본을 저장했습니다. 아래 기록에서 확인하세요.',
}


def link(ident): return '/structure-draft/submit?'+urlencode({'id':ident})


def receipt_html(receipt, *, formal=False):
    if not receipt: return ''
    text='<section class="card" data-draft-handoff-receipt><p>본인에게만 표시하는 임시 입력 연결 · 정식 검수 #'+str(receipt['sequence'])+'</p>'
    if formal:
        text+='<a data-handoff-source href="'+esc(link(receipt['draft_id']))+'">내가 정식 저장한 임시 입력 확인</a>'
    else:
        for route,label in (('/structure-review','저장된 정식 수정본'),('/review-preview','이 수정본의 HTML·다운로드'),('/structure-confirm','확인 전 변경 요약·HTML 검토')):
            text+='<a data-handoff-review href="'+esc(event_link(route,receipt['notice_version'],receipt['conversion_revision'],receipt['review_id']))+'">'+label+'</a> · '
    return text+'</section>'


def private_status(store, principal, ident):
    return receipt_html(handoff.for_draft(store,principal,ident))+'<p><a data-draft-submit href="'+esc(link(ident))+'">저장된 입력 정식 저장 전 확인</a></p>'


def render(handler, raw):
    q=query(raw,{'id'}); data=handoff.preview(handler.store,handler._principal(),q.get('id',[''])[0])
    row=data['draft']; action=row['payload']['action']
    content=STYLE+'<section class="comparison" id="draft-submit-preview"><h1>저장된 임시 입력 정식 저장 전 확인</h1>'
    content+='<p>'+esc(row['title'])+' · 임시 #'+str(row['sequence'])+' · '+esc(row['created_at'])+'</p>'
    content+='<p class="compare-note">이 저장본의 항목과 사유만 학교의 정식 수정본에 추가합니다. 다른 탭에서 작성 중인 미저장 내용은 포함되지 않습니다. '
    content+='저장 후에도 담당자 확인이 필요하며 공개 승인은 아닙니다. 원본·기존 검수본·임시 입력은 보존합니다.</p>'
    content+=origin(row)+receipt_html(data['receipt'])
    content+='<div class="compare-grid"><section class="card"><h2>현재 검수본</h2>'
    if not data['comparable']: content+='<p>동일 기준에서 비교할 수 없습니다. 아래 차단 사유를 확인하세요.</p>'
    elif action['kind']=='block':
        content+='<p>'+esc(BLOCK_LABELS[data['fields']['kind']['current']])+'</p><pre>'+esc(data['fields']['text']['current'])+'</pre>'
    else: content+='<p>같은 관계 근거 '+str(data['relation']['current_count'])+'개</p>'
    content+='</section><section class="card" data-handoff-saved><h2>정식 저장할 임시 입력</h2>'
    if action['kind']=='block':
        content+='<p>'+esc(action['block_id'])+' · '+esc(BLOCK_LABELS[action['block_kind']])+'</p><pre>'+esc(action['text'])+'</pre>'
    else:
        edge=action['relation']; content+='<p>'+('관계 추가' if action['kind']=='relation_add' else '관계 제거')+' · '+esc(RELATION_LABELS[edge['kind']])+'</p>'
        content+='<p>'+esc(edge['source'] or '(미입력)')+' → '+esc(edge['target'] or '(미입력)')+'</p>'
    content+='<h3>정식 기록에 포함할 사유</h3><pre>'+esc(row['payload']['comment'])+'</pre></section></div>'
    if data['can_submit']:
        fields={'draft_id':row['id'],'base_hash':row['base_hash'],'expected_review':row['base_review']}
        content+='<form action="/structure-draft/submit" method="post" data-draft-submit-form>'+handler._csrf_field()
        content+=''.join(_hidden(k,v) for k,v in fields.items())
        content+='<label><input type="checkbox" name="acknowledge" value="yes" required> 이 항목과 사유가 학교의 정식 검수 기록에 추가됨을 확인했습니다.</label>'
        content+='<button>이 저장본으로 정식 수정본 저장</button></form>'
    else:
        content+='<section class="card"><h2>지금은 정식 저장할 수 없습니다</h2><ul>'
        for reason in data['submit_reasons']: content+='<li data-handoff-reason="'+reason+'">'+REASONS[reason]+'</li>'
        content+='</ul></section>'
    content+='<p>저장 직전에 권한·기준 검수·최신 임시 입력을 다시 검사합니다. 변경되면 저장을 중단합니다.</p>'
    content+='<nav><a href="'+esc(compare_link(row['id']))+'">임시 입력 비교·복구로</a> · <a href="/structure-drafts">내 임시 저장 입력</a></nav></section>'
    return handler._send(handler._shell(content))


def submit(handler, form):
    fields={'csrf','draft_id','base_hash','expected_review','acknowledge'}
    if set(form)!=fields or any(len(v)!=1 for v in form.values()) or form['acknowledge'][0]!='yes':
        raise ValueError('Explicit formal submission confirmation required')
    ident=form['draft_id'][0]
    handoff.submit(handler.store,handler._principal(),ident,
        base_hash=form['base_hash'][0],expected_review=form['expected_review'][0])
    return handler._redirect(link(ident))
