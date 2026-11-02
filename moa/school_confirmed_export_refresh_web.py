"""Private saved/current comparison; preparing never rewrites the saved list."""
from urllib.parse import urlencode

from . import school_confirmed_export_refresh as refresh, school_confirmed_export_web as web
from . import school_service as svc
from .school_saved_queue_web import query
from .school_review_web import _hidden
from .school_review_check_web import link as check_link
from .web import esc


def link(ident):
    return '/confirmed-export/saved/status?'+urlencode({'id':ident})


def render(handler, ident, *, changed_again=False):
    data=refresh.inspect(handler.store,handler._principal(),ident);row=data['saved']
    content=web.STYLE+'<section class="comparison" id="saved-export-status"><h1>저장한 묶음 상태·변경 확인</h1>'
    content+='<h2 data-export-saved-name>'+esc(row['name'])+'</h2><p>목록 저장 '+esc(row['created_at'])+' · '+str(row['item_count'])+'건 · 내 계정 전용</p>'
    if changed_again:
        content+='<p role="alert" data-export-refresh-conflict>화면 확인 뒤 최신 상태가 바뀌었거나 준비할 수 없습니다. 아래 상태를 다시 대조하세요.</p>'
    content+='<p class="compare-note">원래 저장 목록·원본·검수 이력은 그대로 보존합니다. 최신 확인본으로 준비하면 별도 목록이 됩니다. '
    content+='이 화면은 버전과 원본 목록을 대조합니다. 원본 파일 내용의 SHA 검사는 ZIP 다운로드 때 다시 실행합니다.</p>'
    for i,item in enumerate(data['items'],1):
        pin=item['saved'];current=item['current']
        content+='<article class="card" data-export-status-item><h2>'+str(i)+'. '+esc(pin['title'])+'</h2>'
        content+='<p><a href="'+esc(check_link(pin['id'],pin['revision'],pin['event']))+'">저장 당시 확인 기록·HTML</a></p>'
        if item['reasons']:
            content+='<ul>'+''.join('<li data-export-change="'+code+'">'+esc(refresh.REASONS[code])+'</li>' for code in item['reasons'])+'</ul>'
        else: content+='<p data-export-version-matches>저장 당시 수신·변환·확인 버전과 일치합니다.</p>'
        if current:
            content+='<p data-export-current>현재 제목: '+esc(current['title'])+' · 수신 #'+str(current['sequence'])+'</p>'
            event=current.get('review')
            if event:
                content+='<p>현재 검수 #'+str(event['sequence'])+' · '+esc(event['created_at'])+'</p>'
                content+='<a href="'+esc(check_link(current['id'],current['revision'],event['id']))+'">현재 검수 기록·HTML 대조</a> · '
            content+='<a href="'+esc('/structure-review?'+urlencode({'id':current['id'],'revision':current['revision'] or ''}))+'">현재 문서 검수 열기</a>'
        content+='</article>'
    member=svc.require_member(handler.store,handler._principal())
    if data['can_prepare'] and member['role'] in svc.WRITE_ROLES:
        content+='<form method="post" action="/confirmed-export/saved/prepare-current" data-export-refresh>'+handler._csrf_field()
        content+=_hidden('id',ident)+_hidden('expected',data['expected'])
        content+='<label><input type="checkbox" name="acknowledge" value="yes" required> 문서별 변경과 현재 확인 기록을 대조했으며 최신 확인본으로 새 묶음을 준비합니다.</label>'
        content+='<button>최신 확인본으로 새 묶음 준비</button></form><p>준비 결과에서 포함 문서·경고를 다시 확인하고 새 이름으로 목록을 저장하세요.</p>'
    elif not data['can_prepare']:
        content+='<p data-export-refresh-blocked>'+('최신 원본 합계가 묶음 한도를 넘습니다. 문서를 나누어 다시 선택하세요.' if data['limit'] else
            '모든 문서의 최신 변환·확인·근거가 준비되어야 새 묶음을 만들 수 있습니다. 확인 미완료 문서를 제외하거나 이전 확인본으로 대체하지 않습니다.')+'</p>'
    else: content+='<p>최신본을 새 목록으로 준비하려면 담당자 쓰기 권한이 필요합니다.</p>'
    from .school_confirmed_export_saved_web import link as saved_link
    content+='<p><a href="'+esc(saved_link(ident))+'">저장 당시 묶음 열기</a> · <a href="'+esc(link(ident))+'">상태 다시 확인</a> · <a href="/confirmed-export/saved">저장 목록</a></p></section>'
    return handler._send(handler._shell(content),409 if changed_again else 200)


def dispatch(handler, *, raw=None, form=None):
    if form is None:
        q=query(raw,{'id'})
        if set(q)!={'id'}: raise ValueError('Saved export list required')
        return render(handler,q['id'][0])
    if (set(form)!={'csrf','id','expected','acknowledge'} or any(len(v)!=1 for v in form.values()) or
        form['acknowledge'][0]!='yes'): raise ValueError('Explicit fresh preparation acknowledgement required')
    ident=form['id'][0]
    from .school_review import Conflict
    try: result=refresh.prepare(handler.store,handler._principal(),ident,expected=form['expected'][0])
    except Conflict: return render(handler,ident,changed_again=True)
    row=result['saved']
    token=web.remember(handler,result['prepared'],source={'id':ident,'name':row['name']})
    return handler._redirect('/confirmed-export/preview?'+urlencode({'bundle':token}))
