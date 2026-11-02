"""Private input history and snapshot comparisons, without recovery or writes."""
from urllib.parse import urlencode

from . import school_draft_history as history, school_review as review
from .school_draft_compare_web import link as current_link
from .school_compare_web import STYLE, text_pair
from .school_saved_queue_web import query, number
from .web import esc

LABELS = {'block':'구역 입력', 'relation_add':'관계 추가 입력', 'relation_remove':'관계 제거 사유'}


def link(ident, page=1):
    return '/structure-draft/history?'+urlencode({'id':ident, 'page':page})


def diff_link(before, after):
    return '/structure-draft/diff?'+urlencode({'before':before, 'after':after})


def _stamp(row):
    return '임시 #'+str(row['sequence'])+' · '+esc(row['created_at'])


def directory(handler, raw):
    from .school_draft_restore_web import link as restore_link
    q = query(raw, {'id', 'page'})
    data = history.history(handler.store, handler._principal(), q.get('id', [''])[0],
        page=number(q.get('page', ['1'])[0], 1_000_000))
    anchor = data['anchor']; rows = data['rows']
    content = STYLE+'<section class="comparison" id="draft-history"><h1>임시 입력 버전 이력</h1>'
    content += '<p>학교·담당자별 비공개 · '+esc(anchor['title'])+' · '+LABELS[anchor['action_kind']]+'</p>'
    content += '<p class="compare-note">같은 수신본·변환·항목의 개인 입력만 표시합니다. 정식 검수 이력이 아닙니다. '
    content += '이전 입력은 읽기 전용으로 보존하며, 선택하거나 비교해도 복구·정식 저장하지 않습니다.</p>'
    content += '<p>목록 기준: '+_stamp(anchor)+'까지 · 총 '+str(data['total'])+'개. 새 자동저장으로 목록 순서가 바뀌지 않습니다.</p>'
    if data['head']['id'] != anchor['id']:
        content += '<p><a data-history-latest href="'+esc(link(data['head']['id']))+'">최신 임시 입력까지 이력 보기</a></p>'
    content += '<form method="get" action="/structure-draft/diff"><h2>두 임시 버전 비교</h2>'
    content += '<p>현재 페이지에서 기준과 대상을 선택하세요. 기준 번호는 대상 번호보다 작거나 같아야 합니다.</p><div class="compare-grid">'
    for field, label, selected in (('before','기준 임시 버전',rows[-1]['id']), ('after','대상 임시 버전',rows[0]['id'])):
        content += '<label>'+label+'<select name="'+field+'">'
        for row in rows:
            content += '<option value="'+esc(row['id'])+'"'+(' selected' if row['id']==selected else '')+'>'+_stamp(row)+'</option>'
        content += '</select></label>'
    content += '</div><button>선택한 임시 버전 비교</button></form>'
    for row in rows:
        content += '<article class="card" data-draft-history-row><h2>'+_stamp(row)+'</h2>'
        content += '<p>'+('현재 최신 개인 입력' if row['id']==data['head']['id'] else '이전 개인 입력 · 읽기 전용')+'</p>'
        content += '<a data-draft-current href="'+esc(current_link(row['id']))+'">이 입력과 현재 검수본 비교</a>'
        if row['id'] != data['head']['id']:
            content += ' · <a data-draft-restore href="'+esc(restore_link(row['id']))+'">이전 입력 가져오기 전 확인</a>'
        if row['previous_id']:
            content += ' · <a data-draft-previous href="'+esc(diff_link(row['previous_id'],row['id']))+'">직전 임시 버전과 비교</a>'
        content += '</article>'
    content += '<nav aria-label="임시 입력 이력 페이지">'+str(data['page'])+' / '+str(data['pages'])+'페이지 '
    for page, label, rel in ((data['page']-1,'이전 페이지','prev'),(data['page']+1,'다음 페이지','next')):
        if 1<=page<=data['pages']:
            content += '<a rel="'+rel+'" href="'+esc(link(anchor['id'],page))+'">'+label+'</a> '
    content += '</nav><p><a href="/structure-drafts">내 임시 저장 입력</a></p></section>'
    return handler._send(handler._shell(content))


def render_pair(handler, raw):
    from .school_draft_restore_web import link as restore_link, origin
    q = query(raw, {'before','after'})
    data = history.pair(handler.store, handler._principal(), q.get('before',[''])[0], q.get('after',[''])[0])
    before, after = data['before'], data['after']
    content = STYLE+'<section class="comparison" id="draft-versions-comparison"><h1>임시 입력 버전 간 비교</h1>'
    content += '<p>학교·담당자별 비공개 · '+esc(after['title'])+' · '+LABELS[after['action_kind']]+'</p>'
    content += '<p class="compare-note">두 개인 임시 입력의 차이입니다. 현재 검수본이나 정식 반영 결과가 아닙니다. '
    content += '비교만으로 복구·병합·정식 저장하지 않습니다.</p>'
    if not data['same_base']:
        content += '<p data-draft-base-changed role="status">두 입력의 작성 기준이 다릅니다. 중간에 정식 검수 또는 구조 기준이 변경됐습니다. '
        content += '차이를 한 기준의 수정 결과로 해석하거나 자동 적용하지 않습니다.</p>'
    elif before['id']==after['id']:
        content += '<p role="status">같은 임시 버전을 선택했습니다.</p>'
    elif before['payload']==after['payload']:
        content += '<p role="status">입력 내용과 사유가 같습니다. 저장 시점은 별개로 보존합니다.</p>'
    content += '<details><summary>공통 수신·변환 범위</summary><p>수신본 '+esc(after['notice_version'])+'</p><p>변환 '+esc(after['conversion_revision'])+'</p></details>'
    fields = [('comment','임시 사유 · 정식 검수 사유 아님',before['payload']['comment'],after['payload']['comment'])]
    left, right = before['payload']['action'], after['payload']['action']
    if after['action_kind']=='block':
        fields = [('block_id','구역 ID',left['block_id'],right['block_id']),
            ('block_kind','구역 종류',review.BLOCK_LABELS[left['block_kind']],review.BLOCK_LABELS[right['block_kind']]),
            ('text','입력 텍스트',left['text'],right['text'])]+fields
    else:
        fields = [(k,label,left['relation'][k],right['relation'][k]) for k,label in
            (('kind','관계 종류'),('source','출발 ID'),('target','대상 ID'))]+fields
        fields[0] = ('kind','관계 종류',review.RELATION_LABELS[fields[0][2]],review.RELATION_LABELS[fields[0][3]])
        content += '<p>빈 ID는 미완성 입력입니다. 관계의 유효성·존재 여부를 뜻하지 않습니다.</p>'
    rendered = [(name,label,text_pair(old,new)) for name,label,old,new in fields]
    content += '<div class="compare-grid">'
    for index, row, label, marker in ((0,before,'기준 임시 입력','before'),(1,after,'대상 임시 입력','after')):
        content += '<section class="card" data-draft-'+marker+'><h2>'+label+'</h2><p>'+_stamp(row)+'</p>'
        for name, title, values in rendered:
            content += '<h3>'+title+'</h3><pre data-draft-field="'+name+'">'+values[index]+'</pre>'
        content += '<details><summary>작성 기준</summary><p>정식 검수 '+esc(row['base_review'] or '자동 분석본')+'</p><p>구조 SHA-256 '+esc(row['base_hash'])+'</p></details>'
        content += origin(row)
        content += '<p><a data-draft-current href="'+esc(current_link(row['id']))+'">이 입력과 현재 검수본 비교</a> · '
        content += '<a data-draft-restore href="'+esc(restore_link(row['id']))+'">이 입력 가져오기 전 확인</a></p></section>'
    content += '</div><p>강조는 다른 가운데 범위이며 최소 단어 단위 차이 또는 적용 결과가 아닙니다. 공백·개행을 보존합니다.</p>'
    content += '<nav><a data-draft-history href="'+esc(link(after['id']))+'">대상 시점까지 임시 입력 이력</a> · '
    content += '<a href="/structure-drafts">내 임시 저장 입력</a></nav></section>'
    return handler._send(handler._shell(content))
