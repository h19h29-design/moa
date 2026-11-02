"""Export every matching private receipt once, without touching any history."""
import hashlib
from urllib.parse import parse_qs, urlsplit

import pytest

from moa import school_receipt_overview as overview, school_receipt_overview_web as web
from moa import school_confirmed_export as batch, school_confirmed_export_saved as saved
from moa import school_confirmed_export_downloads as downloads
from moa import school_confirmed_export_management as management, school_review as review, school_service as svc
from moa.core import Store
from test_school_service import schools
from test_web_school import school_web, client, fetch, login
from test_school_confirmed_export_downloads import frozen
from test_school_confirmed_export_receipt_report import dated, rows
from test_school_confirmed_export_saved import IDENT, page, http_saved
from test_school_confirmed_export import confirmed
from test_school_receipt_overview import fixture
from test_school_structure_review import save
from test_school_review_check import originals


def test_all_pages_and_batches_keep_exact_metadata_order_and_originals(schools, monkeypatch):
    _, s, p, _ = schools
    item = confirmed(s,p)
    pins = frozen(s,p,name='합성 "가을", 안내',pins=batch.prepare(s,p,[item])['pins'])
    frozen(s,p,ident='b'*24,name='다음 묶음',pins=pins)
    frozen(s,p,ident='c'*24,name='영수증 없음',pins=pins)
    for ident,count in ((IDENT,21),('b'*24,2)):
        dated(s,p,ident,monkeypatch,['2026-10-29T12:00:00+09:00']*count)
    management.change(s,p,'b'*24,action='archive',expected=0)
    save(review,s,p,item['id'],{'kind':'reopen'})
    before,files,changes = list(s.db.iterdump()),originals(s),s.db.total_changes
    def forbidden(*args,**kwargs): raise AssertionError('CSV must not inspect documents or record a download')
    for module,names in ((batch,('prepare','bundle')), (saved,('get',)), (downloads,('record','csv_report')),
                         (overview,('listing',)), (management,('get',))):
        for name in names: monkeypatch.setattr(module,name,forbidden)
    s.db.execute('PRAGMA query_only=ON')
    raw = overview.csv_report(s,p); data = rows(raw)
    assert len(data)==24 and len(data[0])==8 and data[0][-1]=='묶음 현재 상태'
    assert [x[:3] for x in data[1:]] == [[IDENT,'합성 "가을", 안내',str(v)] for v in range(21,0,-1)]+[
        ['b'*24,'다음 묶음',str(v)] for v in (2,1)]
    for x in data[1:]:
        assert x[3:7]==['2026-10-29T12:00:00+09:00','1','18',hashlib.sha256(b'synthetic-delivery').hexdigest()]
        assert x[7]==('보관함' if x[0]=='b'*24 else '사용 중')
    assert raw.count(b'\r\n')==24 and b'""' in raw
    assert list(s.db.iterdump())==before and originals(s)==files and s.db.total_changes==changes
    with Store(s.root) as reopened: assert overview.csv_report(reopened,p)==raw


def test_combined_filters_match_screen_inclusive_kst_and_current_state(schools,monkeypatch):
    _,s,p,_ = schools
    fixture(s,p,monkeypatch)
    filters = dict(q='안내',after='2026-10-29',before='2026-10-29')
    assert [(x[0],x[2]) for x in rows(overview.csv_report(s,p,**filters))[1:]]==[('b'*24,'1'),(IDENT,'2')]
    one = rows(overview.csv_report(s,p,**filters,state='archived'))
    assert len(one)==2 and one[1][0]=='b'*24 and one[1][-1]=='보관함'
    active = rows(overview.csv_report(s,p,**filters,state='active'))
    assert len(active)==2 and active[1][0]==IDENT
    for f in (filters,dict(before='2026-10-28'),dict(after='2026-10-30'),dict(q='봄'),dict(q='absent')):
        data = overview.listing(s,p,**f)
        report = rows(overview.csv_report(s,p,**f))[1:]
        assert [(x[0],int(x[2])) for x in report]==[(x['list_id'],x['version']) for x in data['rows']]
        assert len(report)==data['total'] and sum(int(x[5]) for x in report)==data['zip_bytes_total']
    management.change(s,p,'b'*24,action='restore',expected=1)
    assert len(rows(overview.csv_report(s,p,state='archived')))==1
    assert all(x[-1]=='사용 중' for x in rows(overview.csv_report(s,p))[1:])


@pytest.mark.parametrize('name',['=1+1','+SUM(1,2)','-1+1','@SUM(1,2)'])
def test_formula_and_csv_escaping_preserve_stored_name_and_literal_search(schools,name):
    _,s,p,_ = schools
    ident='-'+'x'*23
    frozen(s,p,ident=ident,name=name)
    downloads.record(s,p,ident,b'synthetic')
    before = list(s.db.iterdump())
    assert rows(overview.csv_report(s,p,q=name))[1][:2]==["'"+ident,"'"+name]
    assert list(s.db.iterdump())==before


def test_literal_wildcards_and_empty_result(schools):
    _,s,p,_ = schools
    pins=frozen(s,p,name='100%_\\ ALPHA')
    frozen(s,p,ident='b'*24,name='different',pins=pins)
    for ident in (IDENT,'b'*24): downloads.record(s,p,ident,b'synthetic')
    for q in ('%','_','\\','alpha'):
        data=rows(overview.csv_report(s,p,q=q))
        assert len(data)==2 and data[1][0]==IDENT
    before=list(s.db.iterdump())
    assert len(rows(overview.csv_report(s,p,q="' OR 1=1 --")))==1
    assert len(rows(overview.csv_report(s,p,after='9999-12-31')))==1
    assert list(s.db.iterdump())==before


def test_real_5000_row_limit_is_global_and_overflow_never_returns_partial_csv(schools):
    _,s,p,_ = schools
    assert downloads.MAX_EXPORT_ROWS==5000
    pins=frozen(s,p,name='active-batch')
    frozen(s,p,ident='b'*24,name='archived-batch',pins=pins)
    management.change(s,p,'b'*24,action='archive',expected=0)
    s.db.executemany('INSERT INTO school_confirmed_export_downloads VALUES(?,?,?,?,?,?,?,?)',
        [(ident,p.school,p.subject,v,1,'a'*64,1,'2026-10-29T00:00:00+09:00')
         for ident in (IDENT,'b'*24) for v in range(1,2501)])
    s.db.commit()
    assert len(rows(overview.csv_report(s,p)))==5001
    s.db.execute('INSERT INTO school_confirmed_export_downloads VALUES(?,?,?,?,?,?,?,?)',
        (IDENT,p.school,p.subject,2501,1,'b'*64,2,'2026-10-30T00:00:00+09:00'))
    s.db.commit(); before=list(s.db.iterdump())
    with pytest.raises(downloads.ExportTooLarge): overview.csv_report(s,p)
    assert len(rows(overview.csv_report(s,p,after='2026-10-30')))==2
    assert len(rows(overview.csv_report(s,p,q='active-batch')))==2502
    assert len(rows(overview.csv_report(s,p,state='archived')))==2501
    assert list(s.db.iterdump())==before


def test_csv_snapshot_keeps_rows_and_archive_state_at_one_time(schools):
    _,s,p,_ = schools
    frozen(s,p); downloads.record(s,p,IDENT,b'one')
    changed=[]
    with Store(s.root) as other:
        def append(sql):
            if sql.startswith('WITH visible AS') and not changed:
                changed.append(downloads.record(other,p,IDENT,b'later'))
                management.change(other,p,IDENT,action='archive',expected=0)
        s.db.set_trace_callback(append)
        try: data=rows(overview.csv_report(s,p,state='active'))
        finally: s.db.set_trace_callback(None)
    assert changed==[2] and len(data)==2 and data[1][2]=='1' and data[1][-1]=='사용 중'
    assert len(rows(overview.csv_report(s,p,state='active')))==1
    assert [x[2] for x in rows(overview.csv_report(s,p,state='archived'))[1:]]==['2','1']


def test_current_owner_school_and_membership_are_checked(schools):
    _,s,p,other = schools
    svc.grant_member(s,p.school,'second','uploader'); second=svc.Principal(p.school,'second')
    for who,ident in ((p,IDENT),(other,'b'*24),(second,'c'*24)):
        frozen(s,who,ident=ident,name=ident); downloads.record(s,who,ident,b'synthetic')
    for who,ident in ((p,IDENT),(other,'b'*24),(second,'c'*24)):
        assert [x[0] for x in rows(overview.csv_report(s,who))[1:]]==[ident]
    svc.grant_member(s,p.school,p.subject,'reader')
    assert len(rows(overview.csv_report(s,p)))==2
    svc.grant_member(s,p.school,p.subject,'reader',active=False)
    with pytest.raises(PermissionError): overview.csv_report(s,p)
    svc.grant_member(s,p.school,p.subject,'reader')
    svc.configure_school(s,p.school,'합성학교',enabled=False)
    with pytest.raises(PermissionError): overview.csv_report(s,p)


@pytest.mark.parametrize('args',[dict(q=None),dict(q='x'*121),dict(q='bad\nname'),dict(after='2026-02-29'),
    dict(before=None),dict(after='２０２６-10-29'),dict(after='2026-10-30',before='2026-10-29'),dict(state='unknown')])
def test_invalid_filters_never_change_data(schools,args):
    _,s,p,_=schools; before=list(s.db.iterdump())
    with pytest.raises(ValueError): overview.csv_report(s,p,**args)
    assert list(s.db.iterdump())==before


@pytest.mark.parametrize('table',['school_confirmed_export_items','school_confirmed_export_list_events','school_confirmed_export_downloads'])
def test_missing_schema_is_an_explicit_conflict_without_installing(schools,table):
    _,s,p,_=schools
    s.db.execute('DROP TABLE '+table); s.db.commit(); before=list(s.db.iterdump())
    with pytest.raises(review.Conflict): overview.csv_report(s,p)
    assert list(s.db.iterdump())==before


def test_http_csv_link_on_second_page_exports_all_matching_batches(school_web,monkeypatch):
    _,s,base=school_web; p=svc.Principal('A','a'); a=client(); login(a,base)
    pins=frozen(s,p,name='=합성 안내 "가을", %_')
    frozen(s,p,ident='b'*24,name='합성 안내 %_',pins=pins)
    for ident in (IDENT,'b'*24): dated(s,p,ident,monkeypatch,['2026-10-29T00:00:00+09:00']*11)
    f=dict(q='%_',after='2026-10-29',before='2026-10-29',state='active')
    before,files=list(s.db.iterdump()),originals(s)
    status,raw,_=fetch(a,base,web.link(**f,page=2))
    doc=page(raw); assert status==200 and len(doc.select('[data-receipt-overview-card]'))==2
    href=doc.select_one('[data-receipt-overview-csv]')['href']
    assert urlsplit(href).path==web.PATH+'.csv'
    assert parse_qs(urlsplit(href).query)=={'q':['%_'],'from':['2026-10-29'],'to':['2026-10-29'],'state':['active']}
    assert '모든 묶음·모든 페이지' in doc.get_text() and '5,000' in doc.get_text()
    status,raw,headers=fetch(a,base,href); report=rows(raw)
    assert status==200 and len(report)==23 and {x[0] for x in report[1:]}=={IDENT,'b'*24}
    assert {x[1] for x in report[1:] if x[0]==IDENT}=={'\'=합성 안내 "가을", %_'}
    assert headers['Content-Type']=='text/csv; charset=utf-8'
    assert 'attachment' in headers['Content-Disposition'] and 'confirmed-export-receipts-overview.csv' in headers['Content-Disposition']
    assert int(headers['Content-Length'])==len(raw) and 'private, no-store' in headers['Cache-Control']
    assert headers['X-Content-Type-Options']=='nosniff' and 'frame-ancestors' in headers['Content-Security-Policy']
    assert not headers.get('Access-Control-Allow-Origin')
    assert list(s.db.iterdump())==before and originals(s)==files


def test_http_csv_relogin_stale_versions_privacy_and_get_only(school_web):
    _,s,base=school_web; p=svc.Principal('A','a'); a,b,reader=client(),client(),client()
    csrf=login(a,base); login(b,base,'b','synthetic-b-only'); login(reader,base,'reader','synthetic-reader-only')
    item=confirmed(s,p); _,download,_,_=http_saved(a,base,csrf,[item])
    downloads.record(s,p,download['id'],b'synthetic')
    management.change(s,p,download['id'],action='archive',expected=0)
    save(review,s,p,item['id'],{'kind':'reopen'})
    endpoint=web.link(state='archived',csv=True)
    report=fetch(a,base,endpoint)[1]
    assert len(rows(report))==2 and rows(report)[1][-1]=='보관함'
    assert fetch(a,base,'/logout',fields={'csrf':csrf})[0]==200
    fresh=client(); fresh_csrf=login(fresh,base); before,files=list(s.db.iterdump()),originals(s)
    assert fetch(fresh,base,endpoint)[1]==report
    assert fetch(fresh,base,'/confirmed-export/saved/preview?id='+download['id'])[0]==409
    assert fetch(client(),base,endpoint)[0]==401
    for who in (b,reader): assert len(rows(fetch(who,base,endpoint)[1]))==1
    assert fetch(fresh,base,endpoint,fields={'csrf':fresh_csrf})[0]==404
    assert list(s.db.iterdump())==before and originals(s)==files
    svc.grant_member(s,p.school,p.subject,'reader')
    assert fetch(fresh,base,endpoint)[1]==report
    svc.grant_member(s,p.school,p.subject,'reader',active=False)
    assert fetch(fresh,base,endpoint)[0]==401
    svc.grant_member(s,p.school,p.subject,'reader')
    svc.configure_school(s,p.school,'합성학교',enabled=False)
    assert fetch(fresh,base,endpoint)[0]==401


@pytest.mark.parametrize('query',['page=1','id='+IDENT,'school=B','subject=a','q=a&q=b','state=all&state=active',
    'from=2026-02-30','to=%FF','from=2026-10-30&to=2026-10-29','state=deleted','q='+('x'*121),'q='+('x'*4097)])
def test_http_csv_rejects_unknown_duplicate_malformed_or_overlong_queries(school_web,query):
    _,s,base=school_web; a=client(); login(a,base); before=list(s.db.iterdump())
    status,_,headers=fetch(a,base,web.PATH+'.csv?'+query)
    assert status==400 and 'Content-Disposition' not in headers
    assert list(s.db.iterdump())==before


def test_http_csv_empty_limit_error_and_missing_schema_preserve_filters(school_web,monkeypatch):
    _,s,base=school_web; p=svc.Principal('A','a'); a=client(); login(a,base)
    fixture(s,p,monkeypatch)
    filters=dict(q='안내',after='2026-10-29',before='2026-10-29',state='all')
    endpoint=web.link(**filters,csv=True); before=list(s.db.iterdump())
    assert len(rows(fetch(a,base,web.link(q='missing',csv=True))[1]))==1
    monkeypatch.setattr(downloads,'MAX_EXPORT_ROWS',1)
    status,raw,headers=fetch(a,base,endpoint)
    assert status==400 and '일부만 내보내지 않습니다' in page(raw).get_text() and 'Content-Disposition' not in headers
    assert page(raw).find('a',string='영수증 검색 결과로 돌아가기')['href']==web.link(**filters)
    assert list(s.db.iterdump())==before
    assert len(rows(fetch(a,base,web.link(**{**filters,'state':'archived'},csv=True))[1]))==2
    s.db.execute('DROP TABLE school_confirmed_export_list_events'); s.db.commit(); before=list(s.db.iterdump())
    status,raw,headers=fetch(a,base,endpoint)
    assert status==409 and '명시적 마이그레이션' in page(raw).get_text() and 'Content-Disposition' not in headers
    assert page(raw).find('a',string='영수증 검색 결과로 돌아가기')['href']==web.link(**filters)
    assert not page(fetch(a,base,web.link(**filters))[1]).select_one('[data-receipt-overview-csv]')
    assert list(s.db.iterdump())==before
