"""Pinned warning filters, school isolation and preservation on synthetic data."""
import copy
import unicodedata
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from bs4 import BeautifulSoup

from moa import school_service as svc, school_warning_search as search
from moa import school_warning_search_web as ui, school_warning_web as guidance
from moa import school_review_export as export, school_confirmed_export as batch
from moa import school_confirmed_export_saved as saved, school_confirmed_export_downloads as receipts
from test_school_review_check import originals
from test_school_warning_guidance import fixture_analysis, warning_notice, urls
from test_web_school import school_web, client, fetch, login


def analysis():
    a = fixture_analysis()
    a['warnings'] = [dict(code='unresolved_attachment',file_id='a',block_id='a/second',
        reference='CAFÉ A_%'+str(i)) for i in range(43)]
    a['warnings'] += [dict(code='unresolved_footnote',file_id='b',block_id='b/first'),
        dict(code='notice_structure_limit'), dict(code='future_warning',file_id='missing'),
        dict(code='unresolved_attachment',block_id='b/first',reference='복구 위치')]
    return a


def test_filters_search_all_pages_and_preserve_order_duplicates_and_original():
    a = analysis(); before = copy.deepcopy(a)
    first = search.select(a,'notice')
    assert (first['all_total'],first['total'],first['pages']) == (47,47,3)
    assert [r['number'] for r in first['rows']] == list(range(1,21))
    assert [r['number'] for r in search.select(a,'notice',page=3)['rows']] == list(range(41,48))
    result = search.select(a,'notice',q='a_%42',file='file:a',code='code:unresolved_attachment')
    assert result['total']==1 and result['rows'][0]['number']==43
    assert result['files']==first['files'] and result['codes']==first['codes']
    assert search.select(a,'notice',q='café',file='file:b')['total']==0
    assert [f['label'] for f in first['files'][:2]] == ['파일 1 · 동의서.pdf','파일 2 · 동의서.pdf']
    assert [f['count'] for f in first['files']] == [43,2,2]
    # Duplicates represent observations and must not be silently deduplicated.
    a['warnings'].append(copy.deepcopy(a['warnings'][0]))
    assert search.select(a,'notice',q='a_%0')['total']==2
    a['warnings'].pop(); assert a==before


@pytest.mark.parametrize('q,total', [('a_%',43),('%',43),('_',47),('CAFÉ',43),
    (unicodedata.normalize('NFD','café'),43),('PDF 2쪽',43),('각주',1),('복구 위치',1),('없음',0)])
def test_literal_unicode_casefold_search_of_friendly_text_and_evidence(q,total):
    # '_' occurs in every raw code. It is a literal, not a SQL wildcard.
    assert search.select(analysis(),'notice',q=q)['total']==total


def test_missing_file_unknown_code_and_mismatched_blocks_remain_visible():
    a=analysis()
    assert search.select(a,'notice',file='unlocated')['total']==2
    assert search.select(a,'notice',code='code:future_warning')['total']==1
    assert search.select(a,'notice',file='file:b')['total']==2  # Block-only source resolves within this revision.
    a['warnings']=[dict(code='unresolved_attachment',file_id='a',block_id='b/first')]
    item=guidance.describe(a,'notice')[0]
    assert item['file_id']=='a' and item['href'].endswith(guidance.anchor('file','a'))
    assert search.select(a,'notice',file='file:b')['total']==0


@pytest.mark.parametrize('filters', [dict(q='x'*121),dict(q='\x00'),dict(q='\n'),dict(q='\ud800'),
    dict(q=5),dict(file='file:missing'),dict(code='code:no_such_warning'),dict(file=[]),dict(code=[]),
    dict(page=0),dict(page=True),dict(page=4),dict(page=1.5),dict(page=1_000_001)])
def test_reject_invalid_filters_and_out_of_range_page(filters):
    with pytest.raises((ValueError,UnicodeError)): search.select(analysis(),'notice',**filters)


def test_no_warning_and_no_match_are_distinct():
    a=fixture_analysis()
    result=search.select(a,'notice')
    assert result['total']==result['all_total']==0 and result['pages']==1
    assert result['files'][0]['count']==0 and result['codes']==[]
    with pytest.raises(ValueError): search.select(a,'notice',page=2)
    found=search.select(analysis(),'notice',q='unfindable')
    assert found['total']==0 and found['all_total']==47 and found['pages']==1


def test_real_staff_links_filters_and_downloads_are_read_only(school_web):
    _,s,base=school_web; p=svc.Principal('A','a'); browser=client(); login(browser,base)
    pin=warning_notice(s,p); original=svc.structure_for(s,p,pin['id'],revision_id=pin['revision'])
    pins=batch.prepare(s,p,[pin])['pins']; list_id='8'*24
    saved.create(s,p,list_id,pins,name='경고 조회 보존 묶음')
    archive=batch.bundle(s,p,pins); receipts.record(s,p,list_id,archive)
    csv=receipts.csv_report(s,p,list_id)
    snapshot=export.snapshot(s,p,pin['id'],revision_id=pin['revision'],event_id=pin['event'])
    html,zipped=export.render_html(snapshot),export.bundle(s,snapshot)
    before,files=list(s.db.iterdump()),originals(s)
    json_before=fetch(browser,base,urls(pin)[0]+'&format=json')[1]
    s.db.execute('PRAGMA query_only=ON')
    try:
        for route in urls(pin):
            status,raw,_=fetch(browser,base,route); assert status==200
            directory=BeautifulSoup(raw,'html.parser').select_one('[data-warning-directory]')
            assert directory and directory['href']==ui.link(pin['id'],pin['revision'])
        status,raw,headers=fetch(browser,base,ui.link(pin['id'],pin['revision']))
        assert status==200 and 'no-store' in headers['Cache-Control']
        page=BeautifulSoup(raw,'html.parser')
        assert page.h1.get_text()=='분석 경고 모아보기'
        assert len(page.select('.warning-card'))==len(original['warnings'])
        form=page.select_one('[data-warning-filters]')
        assert form['method']=='get' and not page.select('script[src]')
        assert form.select_one('[name=file]')['aria-label']=='파일'
        assert form.select_one('[name=code]')['aria-label']=='경고 유형'
        fields={i['name']:i['value'] for i in form.select('input[type=hidden]')}
        assert fields=={'id':pin['id'],'revision':pin['revision']}
        # Same file name, separate IDs and ordinals; all original location links resolve.
        options=page.select('select[name=file] option')
        assert len([o for o in options if '동의서.html' in o.get_text()])==2
        for anchor in page.select('[data-warning-location]'):
            dest=urlsplit(anchor['href']); assert parse_qs(dest.query)=={'id':[pin['id']],'revision':[pin['revision']]}
            target=BeautifulSoup(fetch(browser,base,anchor['href'])[1],'html.parser')
            assert target.find(id=dest.fragment)
        filtered=ui.link(pin['id'],pin['revision'],q='누락신청서',code='code:unresolved_attachment')
        status,raw,_=fetch(browser,base,filtered); assert status==200
        result=BeautifulSoup(raw,'html.parser'); assert len(result.select('.warning-card'))==1
        assert '검색 결과 1건' in result.get_text()
        assert result.select_one('[data-warning-reset]')['href']==ui.link(pin['id'],pin['revision'])
        raw=fetch(browser,base,ui.link(pin['id'],pin['revision'],q='unfindable'))[1]
        empty=BeautifulSoup(raw,'html.parser'); assert not empty.select('.warning-card')
        assert '원래 기록된 경고는 그대로' in empty.select_one('[data-warning-empty]').get_text()
        assert fetch(browser,base,urls(pin)[0]+'&format=json')[1]==json_before
        assert export.render_html(snapshot)==html and export.bundle(s,snapshot)==zipped
        assert batch.bundle(s,p,pins)==archive and receipts.csv_report(s,p,list_id)==csv
        assert svc.structure_for(s,p,pin['id'],revision_id=pin['revision'])==original
        assert list(s.db.iterdump())==before and originals(s)==files
    finally: s.db.execute('PRAGMA query_only=OFF')


def many_warning_notice(s,p):
    body='<html><header>합성학교</header>'+''.join('<p>붙임 '+str(i)+'. 누락양식'+str(i)+' 1부.</p>' for i in range(45))+'</html>'
    receipt=svc.upload(s,p,svc.IncomingPost('many-warning-search','경고 목록 시험',body,()))
    svc.request_conversion(s,p,receipt.version_id); assert svc.process_conversions(s)==['done']
    a=svc.structure_for(s,p,receipt.version_id)
    assert len(a['warnings'])>=45
    return {'id':receipt.version_id,'revision':a['conversion_revision']}


def test_real_pagination_retains_all_filters_and_old_revision_after_reconversion(school_web):
    _,s,base=school_web; p=svc.Principal('A','a'); browser=client(); login(browser,base)
    pin=many_warning_notice(s,p)
    svc.request_conversion(s,p,pin['id']); assert svc.process_conversions(s)==['done']
    assert svc.structure_for(s,p,pin['id'])['conversion_revision']!=pin['revision']
    filters=dict(q='누락양식',file='file:body',code='code:unresolved_attachment')
    # Use actual parser-assigned body file ID.
    a=svc.structure_for(s,p,pin['id'],revision_id=pin['revision'])
    filters['file']='file:'+a['files'][0]['id']
    for n,count in ((1,20),(2,20),(3,5)):
        status,raw,_=fetch(browser,base,ui.link(pin['id'],pin['revision'],**filters,page=n))
        assert status==200
        page=BeautifulSoup(raw,'html.parser'); assert len(page.select('.warning-card'))==count
        assert page.select_one('[data-warning-revision]').get_text()==pin['revision']
        for link in page.select('nav[aria-label="분석 경고 페이지"] a'):
            q=parse_qs(urlsplit(link['href']).query)
            assert {k:q[k][0] for k in ('id','revision','q','file','code')}=={**pin,**filters}
        assert all(parse_qs(urlsplit(x['href']).query)['revision']==[pin['revision']] for x in page.select('[data-warning-location]'))
    assert fetch(browser,base,ui.link(pin['id'],pin['revision'],**filters,page=4))[0]==400


def test_authorization_pinned_input_validation_and_no_post_mutation(school_web):
    _,s,base=school_web; p=svc.Principal('A','a'); a,b,r,anon=(client() for _ in range(4))
    csrf=login(a,base); login(b,base,'b','synthetic-b-only'); login(r,base,'reader','synthetic-reader-only')
    pin=warning_notice(s,p); url=ui.link(pin['id'],pin['revision'])
    assert fetch(anon,base,url)[0]==401 and fetch(b,base,url)[0]==404
    assert fetch(r,base,url)[0]==200
    for suffix in ('&extra=x','&q=a&q=b','&page=0','&page=1000001','&page=01','&q='+('x'*121),
                   '&file=file%3Amissing','&code=code%3Aunknown','&q=%00','&q=%0A','&q=%ED%A0%80'):
        assert fetch(a,base,url+suffix)[0]==400, suffix
    for bad in ('/structure-warnings','/structure-warnings?id='+pin['id'],
                '/structure-warnings?revision='+pin['revision'],url+'&revision='):
        assert fetch(a,base,bad)[0]==400
    assert fetch(a,base,ui.link(pin['id'],'unknown'))[0]==404
    other=many_warning_notice(s,p)
    assert fetch(a,base,ui.link(other['id'],pin['revision']))[0]==404
    # A route with only GET support cannot add review events or receipts via POST.
    before=list(s.db.iterdump())
    assert fetch(a,base,'/structure-warnings',fields={'csrf':csrf,**pin})[0]==404
    assert list(s.db.iterdump())==before
    svc.grant_member(s,'A','a','uploader',active=False)
    assert fetch(a,base,url)[0]==401


def test_html_escaping_for_filter_values_file_names_and_unknown_codes(school_web,monkeypatch):
    _,s,base=school_web; browser=client(); login(browser,base)
    a=fixture_analysis(); attack='<script>bad()</script><img src=x onerror=bad()>'
    a['files'][0]['filename']=attack
    a['warnings']=[dict(code=attack,file_id='a',reference=attack)]
    monkeypatch.setattr(svc,'structure_for',lambda *args,**kwargs: copy.deepcopy(a))
    status,raw,_=fetch(browser,base,ui.link('notice','old-conversion',q=attack,code='code:'+attack))
    assert status==200
    page=BeautifulSoup(raw,'html.parser')
    assert not page.select('script,img,[onerror]')
    assert page.select_one('[name=q]')['value']==attack
    assert page.select_one('select[name=code] option[selected]')['value']=='code:'+attack
    assert attack in page.select_one('select[name=file]').get_text()


def test_real_empty_analysis_has_no_false_approval_or_results(school_web):
    _,s,base=school_web; p=svc.Principal('A','a'); browser=client(); login(browser,base)
    receipt=svc.upload(s,p,svc.IncomingPost('empty-warning-search','경고 없는 합성 문서','<p>합성 안내입니다.</p>',()))
    svc.request_conversion(s,p,receipt.version_id); assert svc.process_conversions(s)==['done']
    a=svc.structure_for(s,p,receipt.version_id); assert a['warnings']==[]
    status,raw,_=fetch(browser,base,ui.link(receipt.version_id,a['conversion_revision']))
    assert status==200
    page=BeautifulSoup(raw,'html.parser')
    assert not page.select('.warning-card') and '전체 경고 0건 · 검색 결과 0건' in page.get_text()
    assert '원본과의 대조는 필요합니다' in page.select_one('[data-warning-empty]').get_text()
