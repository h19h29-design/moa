"""Authenticated, explicitly pinned preview/download routes; no writes."""
from urllib.parse import urlencode

from . import school_review_export as export
from .school_review_compare import link as compare_link
from .web import esc


def link(route, ident, revision, event):
    return route+'?'+urlencode({'id':ident,'revision':revision,'event':event})


def serve(handler, route, query):
    if (set(query)-{'id','revision','event','download'} or any(len(v)!=1 for v in query.values()) or
        query.get('download',['0'])[0] not in ('0','1')):
        raise ValueError('Invalid export query')
    field = lambda k: query.get(k,[''])[0]
    saved = export.snapshot(handler.store,handler._principal(),field('id'),
                            revision_id=field('revision'),event_id=field('event'))
    name = 'review-'+saved['review']['id']
    if route=='/review-bundle':
        return handler._send_bytes(export.bundle(handler.store,saved),name+'.zip','application/zip')
    if route=='/review-html':
        return handler._send_bytes(export.render_html(saved),name+'.html','text/html; charset=utf-8',
                                   inline=field('download')!='1')
    html_url = esc(link('/review-html',field('id'),field('revision'),field('event')))
    zip_url = esc(link('/review-bundle',field('id'),field('revision'),field('event')))
    review_url = esc(link('/structure-review',field('id'),field('revision'),field('event')))
    content = '<h1>검수 수정본 미리보기</h1>'
    content += ('<p>'+esc(export.STATUS[saved['review']['status']])+' · 비공개 · 검수 #'+str(saved['review']['sequence'])+'</p>'
        '<p>선택한 검수 시점의 스냅샷입니다. 원본·자동 변환 HTML·이전 검수 버전은 변경하지 않습니다. '
        '이 파일은 공개 승인이나 현재 최신 확인 상태를 뜻하지 않습니다.</p>'
        '<p><a href="'+review_url+'">검수 기록으로</a> · <a href="'+html_url+'">HTML 크게 보기</a> · '
        '<a href="'+html_url+'&amp;download=1">수정본 HTML 다운로드</a> · '
        '<a href="'+zip_url+'">수정본 HTML·원본 ZIP 다운로드</a></p>'
        '<iframe title="검수 수정본 HTML" sandbox style="width:100%;height:75vh;border:1px solid #ccd" src="'+html_url+'"></iframe>')
    content += '<p><a href="'+esc(compare_link(field('id'),field('revision'),'original',field('event')))+'">자동 분석본과 변경점 비교</a></p>'
    from .school_review_check_web import link as check_link
    content += '<p><a data-final-check href="'+esc(check_link(field('id'),field('revision'),field('event')))+'">확인 전 변경 요약·HTML 검토</a></p>'
    return handler._send(handler._shell(content))
