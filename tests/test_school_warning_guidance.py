"""Human explanations must not reinterpret evidence, change exports or cross schools."""
import copy
import json
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from bs4 import BeautifulSoup

from moa import school_warning_web as ui, school_service as svc, school_review as review
from moa import school_review_export as export, school_confirmed_export as batch
from moa import school_confirmed_export_saved as saved, school_confirmed_export_downloads as receipts
from test_school_formats import sample
from test_school_review_check import originals
from test_school_structure_review import save
from test_web_school import school_web, client, fetch, login


def fixture_analysis():
    return dict(conversion_revision='old-conversion', files=[
        {'id':'a','filename':'동의서.pdf'}, {'id':'b','filename':'동의서.pdf'}], blocks=[
        {'id':'a/first','file_id':'a','locator':{'format':'pdf','page':1,'line':0}},
        {'id':'a/second','file_id':'a','locator':{'format':'pdf','page':2,'line':3}},
        {'id':'b/first','file_id':'b','locator':{'format':'hwpx','part':'Contents/section4.xml','path':'/p[99]'}}],
        warnings=[])


@pytest.mark.parametrize('code, words', [
    ('unreadable_file', '본문·표·첨부'),
    ('native_zones_unavailable', '머리말·바닥글·각주'),
    ('unsupported_structure', '원본 파일을 열어'),
    ('structure_parser_failed', '분석 실패'),
    ('notice_structure_limit', '분석 범위'),
    ('invalid_table_header_flag', '각 열의 제목'),
    ('noncontiguous_table_header_ignored', '제목 행과 본문 행'),
    ('table_geometry_unconfirmed', '합쳐진 셀'),
    ('missing_header_part', '학교명·발신 정보'),
    ('external_or_unsafe_part_ignored', '외부 주소를 열 필요는 없습니다'),
    ('unresolved_footnote', '각주 표시와 해당 설명'),
    ('unlinked_footnote', '각주를 사용하는 본문'),
    ('unresolved_attachment', '누락·파일명 차이'),
    ('ambiguous_attachment', '파일 번호와 내용'),
])
def test_warning_has_korean_explanation_action_and_preserved_code(code, words):
    a=fixture_analysis(); a['warnings']=[{'code':code,'file_id':'a'}]; before=copy.deepcopy(a)
    item=ui.describe(a,'notice')[0]
    assert words in item['action'] and item['explanation'] and code not in item['title']
    assert item['evidence']==a['warnings'][0]
    page=BeautifulSoup(ui.render(a,'notice'),'html.parser')
    assert code in page.details.get_text() and code not in page.h3.get_text()
    assert '확인할 내용' in page.get_text() and a==before


def test_internal_footnote_identifier_is_evidence_not_a_visible_name():
    a=fixture_analysis(); a['warnings']=[{'code':'unresolved_footnote','file_id':'a',
        'block_id':'a/first','reference':'footnotes:123'}]
    page=BeautifulSoup(ui.render(a,'notice'),'html.parser')
    details=page.details.extract()
    assert 'footnotes:123' in details.get_text() and 'footnotes:123' not in page.get_text()
    a['warnings'][0]['code']='unresolved_attachment'
    assert ui.describe(a,'notice')[0]['reference']=='footnotes:123'


def test_pdf_page_extracted_line_and_per_file_block_ordinals_are_explicit_and_pinned():
    a=fixture_analysis(); a['warnings']=[{'code':'unresolved_attachment','file_id':'a','block_id':'a/second','reference':'신청서'}]
    item=ui.describe(a,'notice')[0]
    assert item['location']=='파일 1 · 동의서.pdf · PDF 2쪽 · 추출 텍스트 4번째 줄 · 자동 분석 구역 2'
    path=urlsplit(item['href'])
    assert path.path=='/structure' and parse_qs(path.query)=={'id':['notice'],'revision':['old-conversion']}
    assert path.fragment==ui.anchor('block','a/second')
    a['warnings'][0].update(file_id='b',block_id='b/first')
    item=ui.describe(a,'notice')[0]
    assert item['location']=='파일 2 · 동의서.pdf · 자동 분석 구역 1'
    assert '쪽' not in item['location'] and '100' not in item['location']
    assert '원본의 쪽·문단 번호가 아닙니다' in ui.render(a,'notice')


@pytest.mark.parametrize('locator', [
    {'format':'docx','part':'word/header1.xml','path':'/p[3]','page':4},
    {'format':'hwp','source_block':10}, {'format':'html','path':'/p[99]'},
    {'format':'pdf','page':True,'line':3}, {'format':'pdf','page':0,'line':0},
    {'format':'pdf','page':'2','line':0}, {},
])
def test_never_invents_physical_page_or_printed_line(locator):
    a=fixture_analysis(); a['blocks'][0]['locator']=locator
    a['warnings']=[{'code':'unlinked_footnote','file_id':'a','block_id':'a/first'}]
    assert ui.describe(a,'notice')[0]['location']=='파일 1 · 동의서.pdf · 자동 분석 구역 1'


@pytest.mark.parametrize('warning, expected', [
    ({'file_id':'a'}, '파일 1 · 동의서.pdf · 파일 전체 · 세부 위치 미확인'),
    ({'file_id':'a','block_id':'missing'}, '파일 1 · 동의서.pdf · 파일 전체 · 세부 위치 미확인'),
    ({'file_id':'a','block_id':'b/first'}, '파일 1 · 동의서.pdf · 파일 전체 · 세부 위치 미확인'),
    ({'file_id':'missing','block_id':'a/first'}, '문서 묶음 · 파일 위치 미확인'),
    ({}, '문서 묶음 · 파일 위치 미확인'),
])
def test_missing_or_mismatched_location_is_not_guessed(warning, expected):
    a=fixture_analysis(); a['warnings']=[{'code':'future_warning',**warning}]
    item=ui.describe(a,'notice')[0]
    assert item['location']==expected and '아직 등록되지 않았습니다' in item['explanation']
    assert 'block-' not in item['href'] and (bool(item['href'])==('파일 1' in expected))


def test_warning_text_and_evidence_cannot_inject_markup_or_external_links():
    a=fixture_analysis(); attack='<img src=x onerror="alert(1)"><script>bad()</script>'
    a['files'][0]['filename']=attack; a['files'][0]['id']=attack
    a['blocks'][0].update(id=attack,file_id=attack,locator={'format':'html','path':attack})
    a['warnings']=[dict(code=attack,file_id=attack,block_id=attack,reference='javascript:'+attack)]
    raw=ui.render(a,'notice&revision=wrong'); page=BeautifulSoup(raw,'html.parser')
    assert not page.select('img,script,iframe,[onerror]')
    assert attack in page.get_text() and len(page.select('a'))==2
    href=urlsplit(page.select_one('[data-warning-location]')['href'])
    assert not href.netloc and href.path=='/structure'
    assert parse_qs(href.query)=={'id':['notice&revision=wrong'],'revision':['old-conversion']}
    assert href.fragment==ui.anchor('block',attack) and '<' not in href.fragment


def warning_post():
    body=('<header>합성학교 안내</header><p>여행 안내 <sup><a role="doc-noteref" href="#missing">1</a></sup></p>'
          '<p>붙임 1. 동의서 1부.</p><p>첨부 2. 누락신청서 1부.</p>'
          '<aside role="doc-footnote" id="alone">참고: 미참가 시 비용 없음</aside>')
    return svc.IncomingPost('warning-guidance','분석 경고 합성 통신문',body,(
        svc.IncomingFile('동의서.html', b'<html><p>first consent</p></html>', 'attachment'),
        svc.IncomingFile('동의서.html', b'<html><p>second consent</p></html>', 'attachment'),
        svc.IncomingFile('참고자료.txt', b'Unread structured content', 'attachment'),
        svc.IncomingFile('안내.pdf', sample('pdf'), 'attachment'),
        svc.IncomingFile('안내.hwpx', sample('hwpx'), 'attachment')))


def warning_notice(s,p):
    receipt=svc.upload(s,p,warning_post()); svc.request_conversion(s,p,receipt.version_id)
    assert svc.process_conversions(s)==['done']
    v=review.view(s,p,receipt.version_id)
    # A correction is deliberately different from the original warning context.
    block=next(b for b in v['analysis']['blocks'] if b['kind']=='body')
    save(review,s,p,receipt.version_id,dict(kind='block',block_id=block['id'],block_kind='body',text='담당자 수정 본문'))
    event=save(review,s,p,receipt.version_id,dict(kind='confirm',acknowledge_warnings=True))
    return dict(id=receipt.version_id,revision=v['original']['conversion_revision'],event=event)


def urls(pin):
    return ['/structure?'+urlencode({'id':pin['id'],'revision':pin['revision']}),
        *[route+'?'+urlencode(pin) for route in ('/structure-review','/structure-confirm','/review-preview')],
        '/structure-compare?'+urlencode(dict(id=pin['id'],revision=pin['revision'],left='original',right=pin['event']))]


def test_all_staff_pages_link_real_warning_locations_without_changing_records_or_downloads(school_web):
    _,s,base=school_web; p=svc.Principal('A','a'); a=client(); login(a,base)
    pin=warning_notice(s,p); analysis=svc.structure_for(s,p,pin['id'])
    assert {w['code'] for w in analysis['warnings']} >= {
        'ambiguous_attachment','unresolved_attachment','unresolved_footnote','unlinked_footnote',
        'native_zones_unavailable','unreadable_file','unsupported_structure'}
    pins=batch.prepare(s,p,[pin])['pins']; list_id='7'*24
    saved.create(s,p,list_id,pins,name='합성 보존 묶음')
    archive=batch.bundle(s,p,pins); receipts.record(s,p,list_id,archive)
    report=receipts.csv_report(s,p,list_id)
    snapshot=export.snapshot(s,p,pin['id'],revision_id=pin['revision'],event_id=pin['event'])
    html=export.render_html(snapshot); zipped=export.bundle(s,snapshot)
    before=list(s.db.iterdump()); files=originals(s)
    json_before=fetch(a,base,urls(pin)[0]+'&format=json')[1]
    for url in urls(pin):
        status,raw,headers=fetch(a,base,url)
        assert status==200 and 'no-store' in headers['Cache-Control'], url
        page=BeautifulSoup(raw,'html.parser'); cards=page.select('.warning-card')
        assert len(cards)==len(analysis['warnings']), url
        for card in cards:
            assert card.h3 and card.details and '확인할 내용' in card.get_text()
            link=card.select_one('[data-warning-location]'); assert link
            target=urlsplit(link['href'])
            assert parse_qs(target.query)=={'id':[pin['id']],'revision':[pin['revision']]}
            dest=BeautifulSoup(fetch(a,base,link['href'])[1],'html.parser')
            assert dest.find(id=target.fragment), 'Warning points to no original evidence'
    assert fetch(a,base,urls(pin)[0]+'&format=json')[1]==json_before
    assert svc.structure_for(s,p,pin['id'])==analysis
    assert list(s.db.iterdump())==before and originals(s)==files
    assert export.render_html(snapshot)==html and export.bundle(s,snapshot)==zipped
    assert batch.bundle(s,p,pins)==archive and receipts.csv_report(s,p,list_id)==report


def test_warning_links_keep_old_conversion_and_all_views_enforce_school_scope(school_web):
    _,s,base=school_web; p=svc.Principal('A','a'); a,b,reader,anon=(client() for _ in range(4))
    login(a,base); login(b,base,'b','synthetic-b-only'); login(reader,base,'reader','synthetic-reader-only')
    pin=warning_notice(s,p)
    svc.request_conversion(s,p,pin['id']); assert svc.process_conversions(s)==['done']
    assert svc.structure_for(s,p,pin['id'])['conversion_revision']!=pin['revision']
    before=list(s.db.iterdump())
    for url in urls(pin):
        assert fetch(anon,base,url)[0]==401 and fetch(b,base,url)[0]==404
        for browser in (a,reader):
            status,raw,_=fetch(browser,base,url); assert status==200
            page=BeautifulSoup(raw,'html.parser')
            for link in page.select('[data-warning-location]'):
                assert parse_qs(urlsplit(link['href']).query)['revision']==[pin['revision']]
                assert fetch(b,base,link['href'])[0]==404
    assert list(s.db.iterdump())==before
    svc.grant_member(s,'A','a','uploader',active=False)
    assert all(fetch(a,base,url)[0]==401 for url in urls(pin))


def test_no_warning_is_not_an_approval():
    text=BeautifulSoup(ui.render(fixture_analysis(),'notice'),'html.parser').get_text()
    assert '기록된 자동 분석 경고가 없습니다' in text and '원본과의 대조는 필요합니다' in text
