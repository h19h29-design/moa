"""Escaped, JavaScript-independent, read-only personal draft comparison."""
from urllib.parse import urlencode

from . import school_draft_compare as compare, school_draft_web, school_review as review
from .school_compare_web import STYLE, text_pair
from .school_saved_queue_web import query
from .web import esc

REASONS={
    'newer_input':'같은 항목에 더 최신 임시 입력이 있습니다. 이 저장본을 덮어쓰지 않습니다.',
    'read_only':'현재 계정은 조회 권한만 있어 입력을 복구해 저장할 수 없습니다.',
    'receiving_changed':'새 정정 수신본이 있습니다. 구역 ID를 새 수신본과 자동으로 맞추지 않습니다.',
    'conversion_changed':'새 변환 버전이 있습니다. 구역 ID를 새 변환과 자동으로 맞추지 않습니다.',
    'conversion_pending':'재변환이 진행 중입니다. 기존 저장 입력을 보존합니다.',
    'conversion_failed':'재변환이 실패했습니다. 기존 저장 입력을 보존합니다.',
    'review_changed':'정식 검수가 변경됐습니다. 이전 기준의 입력을 자동 적용·병합하지 않습니다.',
    'structure_changed':'저장 당시 자동 분석 기준과 달라 항목을 안전하게 비교할 수 없습니다.',
    'structure_unavailable':'저장 당시 구조를 조회할 수 없어 항목을 비교하지 않습니다.',
    'migration_required':'명시적 검수 마이그레이션이 필요합니다. 조회로 DB를 변경하지 않습니다.',
    'item_missing':'대상 구역·관계가 없어 입력을 자동 복구하지 않습니다.',
}


def link(ident): return '/structure-draft/compare?'+urlencode({'id':ident})


def render(handler, raw):
    from .school_draft_history_web import link as history_link
    q=query(raw,{'id'})
    data=compare.compare(handler.store,handler._principal(),q.get('id',[''])[0])
    row=data['draft']; action=row['payload']['action']; current=data['current']
    content=STYLE+'<section class="comparison" id="draft-comparison"><h1>임시 입력과 현재 검수본 비교</h1>'
    content+='<p>학교·담당자별 비공개 · 읽기 전용 · '+esc(row['title'])+'</p>'
    content+='<p class="compare-note">저장된 개인 입력은 정식 검수·확인·HTML 반영이 아닙니다. '
    content+='같은 수신본·변환 버전에서 조회한 검수본과만 대조합니다. 자동 병합·적용·재저장은 하지 않습니다.</p>'
    content+='<p>임시 #'+str(row['sequence'])+' · '+esc(row['created_at'])+' · 사유와 미완성 입력을 그대로 보존합니다.</p>'
    content+='<details><summary>저장 기준·비교 시점</summary><p>수신본 '+esc(row['notice_version'])+'</p>'
    content+='<p>변환 '+esc(row['conversion_revision'])+'</p><p>저장 당시 검수 '+esc(row['base_review'] or '자동 분석본')+'</p>'
    content+='<p>비교 시점 검수 '+esc((current['review_id'] or '자동 분석본') if current['available'] else '조회할 수 없음')+' · '+esc(current['created_at'])+'</p>'
    content+='<p>저장 기준 SHA-256 '+esc(row['base_hash'])+'</p></details>'
    if data['reasons']:
        content+='<section class="card"><h2>복구 전 확인이 필요합니다</h2><ul>'
        for reason in data['reasons']: content+='<li data-draft-reason="'+reason+'">'+REASONS[reason]+'</li>'
        content+='</ul></section>'
    else: content+='<p role="status">현재 기준과 일치하는 최신 개인 입력입니다. 복구 후에도 정식 저장 전 확인이 필요합니다.</p>'
    content+='<div class="compare-grid"><section class="card" data-draft-compare-current><h2>현재 검수본 · 저장 당시 수신/변환</h2>'
    saved='<section class="card" data-draft-compare-saved><h2>저장한 개인 임시 입력 · 미반영</h2>'
    if not data['comparable']:
        content+='<p>안전하게 대응되는 항목이 없어 내용을 비교하지 않습니다.</p>'
    if action['kind']=='block':
        saved+='<p>구역 '+esc(action['block_id'])+'</p><p>종류: '+esc(review.BLOCK_LABELS[action['block_kind']])+'</p>'
        old,new=('',esc(action['text']))
        if data['comparable']:
            fields=data['fields']; old,new=text_pair(fields['text']['current'],fields['text']['saved'])
            content+='<p>구역 '+esc(action['block_id'])+'</p><p>종류: '+esc(review.BLOCK_LABELS[fields['kind']['current']])+'</p>'
            content+='<pre>'+old+'</pre>'
            content+='<p>'+('종류·텍스트가 같습니다. 사유와 기준 변경 여부는 별도로 확인하세요.' if all(v['current']==v['saved'] for v in fields.values()) else '종류 또는 텍스트가 다릅니다.')+'</p>'
        saved+='<pre>'+new+'</pre>'
    else:
        edge=action['relation']; relation=data['relation']
        saved+='<p>'+('관계 추가 입력' if action['kind']=='relation_add' else '관계 제거 사유 입력')+'</p>'
        saved+='<p>'+esc(review.RELATION_LABELS[edge['kind']])+'</p><p>'+esc(edge['source'] or '(미입력)')+' → '+esc(edge['target'] or '(미입력)')+'</p>'
        if relation is not None:
            content+='<p>같은 종류·출발·대상 관계: '+str(relation['current_count'])+'건</p>'
            content+='<p>출발 ID: '+('존재' if relation['source_exists'] else '미입력 또는 없음')+' · 대상 ID: '+('존재' if relation['target_exists'] else '미입력 또는 없음')+'</p>'
            content+='<p>관계가 이미 있거나 없어도 임시 입력만으로 추가·제거하지 않습니다.</p>'
    saved+='<h3>임시 저장 사유 · 정식 검수 사유 아님</h3><pre>'+esc(row['payload']['comment'])+'</pre></section>'
    content+='</section>'+saved+'</div><p>강조는 다른 가운데 범위를 표시하며 최소 단어 단위 차이나 적용 결과가 아닙니다.</p><nav aria-label="임시 입력 비교 이동">'
    if data['recoverable']: content+='<a data-draft-recover href="'+esc(school_draft_web.link(row['id']))+'">이 입력 복구·검수 계속하기</a> · '
    if data['head']['id']!=row['id']: content+='<a href="'+esc(link(data['head']['id']))+'">더 최신 임시 입력 비교</a> · '
    if data['comparable']:
        params={'id':row['notice_version'],'revision':row['conversion_revision']}
        route='/structure'
        if current['review_id']: route='/structure-review'; params['event']=current['review_id']
        content+='<a data-current-snapshot href="'+esc(route+'?'+urlencode(params))+'">비교한 검수 시점 보기</a> · '
    content+='<a data-latest-notice href="'+esc('/notice?'+urlencode({'id':data['latest_version']}))+'">최신 수신본 확인</a> · '
    content+='<a data-draft-history href="'+esc(history_link(row['id']))+'">임시 입력 버전 이력</a> · '
    content+='<a href="/structure-drafts">내 임시 저장 입력</a></nav>'
    content+='<p>화면은 조회 시점 기준입니다. 복구·정식 저장 시 최신 권한과 기준을 다시 검사합니다. '
    content+='새 수신본·변환으로 텍스트를 옮기려면 최신 원본을 보고 담당자가 직접 다시 검수해야 합니다.</p></section>'
    return handler._send(handler._shell(content))
