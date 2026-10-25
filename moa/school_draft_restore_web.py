"""Read-only preview and explicit CSRF-protected private-input reuse."""
from urllib.parse import urlencode

from . import school_draft_restore as restore, school_draft_web
from .school_draft_compare_web import REASONS, link as current_link
from .school_draft_history_web import diff_link, link as history_link, _stamp, LABELS
from .school_compare_web import STYLE
from .school_review_web import _hidden
from .school_saved_queue_web import query
from .web import esc

RESTORE_REASONS = {**REASONS,
    'restore_migration_required':'이전 입력 가져오기에는 명시적 마이그레이션이 필요합니다. 조회로 DB를 변경하지 않습니다.',
    'already_latest':'이미 최신 개인 입력입니다. 기존 입력 복구에서 계속할 수 있습니다.',
    'head_base_changed':'최신 개인 입력과 작성 기준이 다릅니다. 이전 기준으로 되돌려 쓰지 않습니다.',
}


def link(ident): return '/structure-draft/restore?'+urlencode({'id':ident})


def origin(row):
    source = row.get('restored_from')
    if not source: return ''
    return '<p>이 입력은 이전 개인 저장본을 가져와 추가한 버전입니다. <a data-draft-origin href="'+esc(current_link(source))+'">가져온 원래 입력 확인</a></p>'


def render(handler, raw):
    q = query(raw, {'id'})
    data = restore.preview(handler.store, handler._principal(), q.get('id',[''])[0])
    source, head = data['draft'], data['head_draft']
    content = STYLE+'<section class="comparison" id="draft-restore-preview"><h1>이전 입력을 새 임시 버전으로 가져오기</h1>'
    content += '<p>학교·담당자별 비공개 · '+esc(source['title'])+' · '+LABELS[source['action_kind']]+'</p>'
    content += '<p class="compare-note">선택한 개인 입력을 복사해 새 임시 버전으로 추가합니다. 원래 입력과 이후 입력을 모두 보존합니다. '
    content += '정식 검수·확인·HTML에는 반영되지 않습니다. 가져온 뒤 별도로 검수·정식 저장해야 합니다.</p>'
    content += '<div class="compare-grid"><section class="card" data-restore-source><h2>가져올 이전 입력</h2><p>'+_stamp(source)+'</p>'
    action = source['payload']['action']
    if action['kind']=='block':
        from .school_review import BLOCK_LABELS
        content += '<p>'+esc(action['block_id'])+' · '+esc(BLOCK_LABELS[action['block_kind']])+'</p><pre>'+esc(action['text'])+'</pre>'
    else:
        from .school_review import RELATION_LABELS
        edge=action['relation']
        content += '<p>'+esc(RELATION_LABELS[edge['kind']])+'</p><p>'+esc(edge['source'] or '(미입력)')+' → '+esc(edge['target'] or '(미입력)')+'</p>'
        content += '<p>미완성 관계도 개인 입력으로만 가져옵니다. 관계가 추가·제거되지는 않습니다.</p>'
    content += '<h3>개인 임시 사유</h3><pre>'+esc(source['payload']['comment'])+'</pre></section>'
    content += '<section class="card"><h2>현재 최신 개인 입력</h2><p>'+_stamp(head)+'</p><p>이 입력도 이전 버전으로 보존됩니다.</p>'
    content += '<a href="'+esc(diff_link(source['id'],head['id']))+'">이전 입력과 최신 개인 입력 비교</a></section></div>'
    if data['can_restore']:
        fields={'source_id':source['id'],'expected_draft':head['id'], 'base_hash':source['base_hash'], 'expected_review':source['base_review']}
        content += '<form action="/structure-draft/restore" method="post" data-draft-restore-form>'+handler._csrf_field()
        content += ''.join(_hidden(k,v) for k,v in fields.items())
        content += '<label><input type="checkbox" name="acknowledge" value="yes" required> 이전 입력으로 새 개인 임시 버전만 만들며 정식 검수에 반영되지 않음을 확인했습니다.</label>'
        content += '<button>새 임시 버전으로 가져오기</button></form>'
    else:
        content += '<section class="card"><h2>지금은 가져올 수 없습니다</h2><ul>'
        for reason in data['restore_reasons']:
            content += '<li data-restore-reason="'+reason+'">'+RESTORE_REASONS[reason]+'</li>'
        content += '</ul></section>'
    content += '<p>화면을 연 뒤 다른 저장·검수·변환·권한 변경이 있으면 가져오기를 중단합니다. 다시 비교하고 확인해야 합니다.</p>'
    content += '<nav><a href="'+esc(current_link(source['id']))+'">현재 검수본과 비교</a> · '
    content += '<a data-draft-history href="'+esc(history_link(head['id']))+'">임시 입력 이력</a></nav></section>'
    return handler._send(handler._shell(content))


def submit(handler, form):
    fields={'csrf','source_id','expected_draft','base_hash','expected_review','acknowledge'}
    if set(form)!=fields or any(len(v)!=1 for v in form.values()) or form['acknowledge'][0]!='yes':
        raise ValueError('Explicit private input restore confirmation required')
    ident=restore.restore(handler.store,handler._principal(),form['source_id'][0],
        expected_draft=form['expected_draft'][0],base_hash=form['base_hash'][0],expected_review=form['expected_review'][0])
    return handler._redirect(school_draft_web.link(ident))
