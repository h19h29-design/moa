"""Saved conditions survive relogin; all receipt/document history stays intact."""
import concurrent.futures
import sqlite3
import threading
from urllib.parse import parse_qs, urlsplit

import pytest

from moa import school_receipt_searches as saved, school_receipt_searches_web as web
from moa import school_receipt_overview as overview, school_receipt_overview_web as live
from moa import school_service as svc, school_review as review
from moa import school_confirmed_export as batch, school_confirmed_export_downloads as downloads
from moa import school_confirmed_export_management as management
from moa.core import Store
from test_school_service import schools
from test_web_school import school_web, client, fetch, login
from test_school_confirmed_export_downloads import frozen
from test_school_confirmed_export_saved import IDENT, page, inputs
from test_school_confirmed_export_receipt_report import dated, rows
from test_school_receipt_overview import fixture
from test_school_review_check import originals

SEARCH = 's'*24


def existing_rows(s):
    tables = [r[0] for r in s.db.execute("SELECT name FROM sqlite_master WHERE type='table'")
              if r[0] not in ('school_receipt_searches','school_service_migrations')]
    return {name: [tuple(r) for r in s.db.execute('SELECT * FROM "'+name+'"')] for name in tables}


def drop_v10(s):
    s.db.executescript('DROP TABLE school_receipt_searches; DELETE FROM school_service_migrations WHERE version=10;')


def test_v9_upgrade_backs_up_all_history_and_never_changes_existing_rows(schools):
    _,s,p,_ = schools
    frozen(s,p); downloads.record(s,p,IDENT,b'synthetic original receipt')
    management.change(s,p,IDENT,action='archive',expected=0)
    drop_v10(s)
    dump, old, files = list(s.db.iterdump()), existing_rows(s), originals(s)
    version = s.db.execute('PRAGMA user_version').fetchone()[0]
    backup = svc.migrate(s)
    with sqlite3.connect(backup) as before:
        assert list(before.iterdump()) == dump
        assert before.execute('SELECT max(version) FROM school_service_migrations').fetchone()[0] == 9
    assert saved.available(s) and existing_rows(s)==old and originals(s)==files
    assert s.db.execute('PRAGMA user_version').fetchone()[0] == version
    assert [r[0] for r in s.db.execute('SELECT version FROM school_service_migrations ORDER BY version')] == list(range(1,11))
    after = list(s.db.iterdump())
    assert svc.migrate(s) is None and list(s.db.iterdump())==after
    assert s.db.execute('PRAGMA quick_check').fetchone()[0]=='ok'
    assert not s.db.execute('PRAGMA foreign_key_check').fetchall()


def test_failed_migration_rolls_back_all_v10_schema(schools,monkeypatch):
    _,s,_,_ = schools; drop_v10(s); before=list(s.db.iterdump())
    monkeypatch.setattr(saved,'SCHEMA_V10',saved.SCHEMA_V10+'\n INVALID SYNTHETIC DDL;')
    with pytest.raises(sqlite3.Error): svc.migrate(s)
    assert not saved.available(s) and list(s.db.iterdump())==before


def test_saved_conditions_are_durable_metadata_not_a_frozen_result_set(schools,monkeypatch):
    _,s,p,_ = schools; fixture(s,p,monkeypatch)
    f=dict(q='안내',after='2026-10-29',before='2026-10-29',state='archived')
    before, files = existing_rows(s), originals(s)
    assert saved.create(s,p,SEARCH,name='  10월 보관 안내  ',**f)==SEARCH
    row=saved.get(s,p,SEARCH)
    assert row['name']=='10월 보관 안내' and row['filters']==f
    assert set(row)=={'id','name','school','subject','created_at','filters'}
    assert existing_rows(s)==before and originals(s)==files
    with Store(s.root) as reopened:
        assert saved.get(reopened,p,SEARCH)==row
        assert len(rows(overview.csv_report(reopened,p,**row['filters'])))==2
    management.change(s,p,'b'*24,action='restore',expected=1)
    assert len(rows(overview.csv_report(s,p,**saved.get(s,p,SEARCH)['filters'])))==1
    management.change(s,p,'b'*24,action='archive',expected=2)
    dated(s,p,'b'*24,monkeypatch,['2026-10-29T23:59:59+09:00'])
    assert len(rows(overview.csv_report(s,p,**saved.get(s,p,SEARCH)['filters'])))==3
    assert saved.get(s,p,SEARCH)==row and originals(s)==files


def test_duplicate_submission_is_idempotent_and_changed_conditions_conflict(schools):
    _,s,p,_ = schools
    saved.create(s,p,SEARCH,name='내 조건',q='  안내  ')
    before=list(s.db.iterdump())
    assert saved.create(s,p,SEARCH,name='내 조건',q='안내')==SEARCH
    for change in ({'name':'다른 이름'},{'q':'다른 검색'},{'after':'2026-10-29'},{'state':'active'}):
        with pytest.raises(review.Conflict): saved.create(s,p,SEARCH,**{'name':'내 조건','q':'안내',**change})
    assert list(s.db.iterdump())==before
    # A deliberately new form may save another named preset; it never overwrites.
    saved.create(s,p,'t'*24,name='내 조건',q='다른 검색')
    assert saved.listing(s,p)['total']==2


def test_concurrent_retry_commits_one_search(schools):
    _,s,p,_ = schools; barrier=threading.Barrier(2)
    def submit():
        with Store(s.root) as own:
            barrier.wait()
            return saved.create(own,p,SEARCH,name='동시 저장',q='안내')
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:submit(), range(2)))
    assert results==[SEARCH,SEARCH] and saved.listing(s,p)['total']==1


@pytest.mark.parametrize('statement', [
    "UPDATE school_receipt_searches SET q='different'", 'DELETE FROM school_receipt_searches'])
def test_saved_searches_cannot_be_rewritten_or_deleted(schools,statement):
    _,s,p,_ = schools; saved.create(s,p,SEARCH,name='보존'); before=list(s.db.iterdump())
    with pytest.raises(sqlite3.IntegrityError): s.db.execute(statement)
    s.db.rollback()
    assert list(s.db.iterdump())==before


@pytest.mark.parametrize('bad', [
    {'name':''},{'name':' '*3},{'name':'x'*121},{'name':'x\n'}, {'q':'x'*121}, {'q':'x\x00'},
    {'after':'2026-02-30'}, {'before':'not-a-date'}, {'after':'2026-10-30','before':'2026-10-29'},
    {'state':'deleted'}, {'ident':'x'}, {'ident':'/'*24}])
def test_invalid_conditions_never_write(schools,bad):
    _,s,p,_ = schools; before=list(s.db.iterdump())
    with pytest.raises(ValueError): saved.create(s,p,**{'ident':SEARCH,'name':'조건',**bad})
    assert list(s.db.iterdump())==before


def test_owner_school_membership_and_reader_access(schools):
    _,s,p,other = schools
    svc.grant_member(s,p.school,'second','uploader'); second=svc.Principal(p.school,'second')
    saved.create(s,p,SEARCH,name='private')
    for who in (other,second):
        assert saved.listing(s,who)['total']==0
        with pytest.raises(LookupError): saved.get(s,who,SEARCH)
        with pytest.raises(LookupError): saved.create(s,who,SEARCH,name='private')
    svc.grant_member(s,p.school,p.subject,'reader')
    assert saved.get(s,p,SEARCH)['name']=='private'
    with pytest.raises(PermissionError): saved.create(s,p,'t'*24,name='forbidden')
    svc.grant_member(s,p.school,p.subject,'uploader',active=False)
    with pytest.raises(PermissionError): saved.listing(s,p)
    with pytest.raises(PermissionError): saved.get(s,p,SEARCH)
    with pytest.raises(PermissionError): saved.create(s,p,'t'*24,name='forbidden')
    svc.grant_member(s,p.school,p.subject,'uploader')
    svc.configure_school(s,p.school,'학교',enabled=False)
    with pytest.raises(PermissionError): saved.get(s,p,SEARCH)


def test_metadata_only_reads_paginate_in_one_snapshot_and_do_not_migrate(schools,monkeypatch):
    _,s,p,other = schools
    monkeypatch.setattr(svc,'_now',lambda:'2026-10-29T12:00:00+09:00')
    for n in range(23): saved.create(s,p,f'{n:024d}',name='조건 '+str(n))
    saved.create(s,other,'o'*24,name='다른 학교')
    def forbidden(*args,**kwargs): raise AssertionError('Search metadata must not touch documents or results')
    for module,names in ((batch,('prepare','bundle')),(overview,('listing','csv_report')),(downloads,('record',))):
        for name in names: monkeypatch.setattr(module,name,forbidden)
    before=list(s.db.iterdump()); s.db.execute('PRAGMA query_only=ON')
    first=saved.listing(s,p); last=saved.listing(s,p,page=100)
    assert first['total']==23 and first['pages']==2 and len(first['rows'])==20
    assert [r['id'] for r in first['rows']+last['rows']]==[f'{n:024d}' for n in range(22,-1,-1)]
    assert saved.get(s,p,f'{0:024d}')['name']=='조건 0'
    assert list(s.db.iterdump())==before
    s.db.execute('PRAGMA query_only=OFF'); drop_v10(s); before=list(s.db.iterdump())
    assert saved.listing(s,p)['migration_required']
    with pytest.raises(review.Conflict): saved.get(s,p,SEARCH)
    with pytest.raises(review.Conflict): saved.create(s,p,SEARCH,name='missing')
    assert list(s.db.iterdump())==before and not saved.available(s)


def test_http_save_relogin_load_and_whole_csv_preserve_history(school_web,monkeypatch):
    _,s,base=school_web; p=svc.Principal('A','a'); a=client(); csrf=login(a,base)
    pins=frozen(s,p,name='=합성 안내 %_ "A",')
    frozen(s,p,ident='b'*24,name='합성 안내 B',pins=pins)
    for ident,count in ((IDENT,22),('b'*24,2)):
        dated(s,p,ident,monkeypatch,['2026-10-29T12:00:00+09:00']*count)
    management.change(s,p,'b'*24,action='archive',expected=0)
    f=dict(q='안내',after='2026-10-29',before='2026-10-29',state='all')
    screen=page(fetch(a,base,live.link(**f,page=2))[1])
    fields=inputs(screen.select_one('[data-receipt-search-save]')); fields['name']='<b>10월 "안내"</b>'
    assert fields['q']=='안내' and 'page' not in fields
    old,files=existing_rows(s),originals(s)
    status,raw,headers=fetch(a,base,web.PATH+'/save',fields=fields)
    assert status==200 and '<b>10월' not in raw.decode()
    card=page(raw).select_one('[data-receipt-search-card]')
    assert card.h2.text==fields['name']
    load=card.select_one('[data-receipt-search-open]')['href']
    csv=card.select_one('[data-receipt-search-csv]')['href']
    assert parse_qs(urlsplit(csv).query)=={'q':['안내'],'from':['2026-10-29'],'to':['2026-10-29']}
    assert 'private, no-store' in headers['Cache-Control']
    assert fetch(a,base,web.PATH+'/save',fields=fields)[0]==200
    assert saved.listing(s,p)['total']==1
    assert fetch(a,base,'/logout',fields={'csrf':csrf})[0]==200
    fresh=client(); login(fresh,base); before=list(s.db.iterdump())
    directory=page(fetch(fresh,base,web.PATH)[1]); assert directory.select_one('[data-receipt-search-open]')['href']==load
    status,raw,_=fetch(fresh,base,load); loaded=page(raw)
    assert status==200 and loaded.select_one('[name=q]')['value']=='안내'
    assert loaded.select_one('[name=from]')['value']=='2026-10-29'
    assert len(loaded.select('[data-receipt-overview-card]'))==20
    endpoint=loaded.select_one('[data-receipt-overview-csv]')['href']
    status,raw,headers=fetch(fresh,base,endpoint)
    assert status==200 and raw==fetch(fresh,base,csv)[1]
    report=rows(raw); assert len(report)==25 and len(report[0])==8
    assert {r[0] for r in report[1:]}=={IDENT,'b'*24}
    assert all(r[1].startswith("'=") for r in report[1:] if r[0]==IDENT)
    assert headers['Content-Type']=='text/csv; charset=utf-8' and 'attachment' in headers['Content-Disposition']
    assert headers['X-Content-Type-Options']=='nosniff' and 'private, no-store' in headers['Cache-Control']
    assert list(s.db.iterdump())==before and existing_rows(s)==old and originals(s)==files


def test_http_permissions_csrf_ambiguity_and_foreign_ids(school_web):
    _,s,base=school_web; a,b,r=client(),client(),client()
    csrf=login(a,base); login(b,base,'b','synthetic-b-only'); cr=login(r,base,'reader','synthetic-reader-only')
    fields=inputs(page(fetch(a,base,live.PATH)[1]).select_one('[data-receipt-search-save]'))
    fields['name']='private'; post=web.PATH+'/save'
    assert fetch(a,base,post,fields=fields)[0]==200
    load=web.PATH+'/open?id='+fields['id']; before=list(s.db.iterdump())
    for path in (web.PATH,load): assert fetch(client(),base,path)[0]==401
    for who in (b,r):
        assert fetch(who,base,load)[0]==404
        assert not page(fetch(who,base,web.PATH)[1]).select('[data-receipt-search-card]')
    assert not page(fetch(r,base,live.PATH)[1]).select('[data-receipt-search-save]')
    assert fetch(client(),base,post,fields=fields)[0]==401
    assert fetch(r,base,post,fields={**fields,'csrf':cr,'id':'r'*24})[0]==403
    assert fetch(a,base,post,fields={**fields,'csrf':'wrong'})[0]==403
    assert fetch(a,base,post,fields=fields,headers={'Origin':'https://evil.invalid'})[0]==403
    for extra in ({'school':'B'},{'subject':'b'},{'page':'2'},{'redirect':'https://evil.invalid'}):
        assert fetch(a,base,post,fields={**fields,**extra})[0]==400
    assert fetch(a,base,post,fields=list(fields.items())+[('q','extra')])[0]==400
    assert fetch(a,base,post,fields={k:v for k,v in fields.items() if k!='state'})[0]==400
    assert fetch(a,base,post+'?q=extra',fields=fields)[0]==400
    assert fetch(a,base,post,fields={**fields,'name':'x'*9000})[0]==413
    assert fetch(a,base,post)[0]==404
    assert fetch(a,base,load,fields={'csrf':csrf})[0]==404
    assert list(s.db.iterdump())==before
    svc.grant_member(s,'A','a','reader')
    assert fetch(a,base,load)[0]==200
    assert not page(fetch(a,base,live.PATH)[1]).select('[data-receipt-search-save]')
    svc.grant_member(s,'A','a','reader',active=False)
    assert fetch(a,base,load)[0]==401 and fetch(a,base,post,fields=fields)[0]==401


@pytest.mark.parametrize('endpoint', [
    '?page=0','?page=1&page=2','?school=A','?q=x','?page=1000001',
    '/open','/open?id=short','/open?id='+'s'*24+'&id='+'t'*24,
    '/open?id='+'s'*24+'&subject=a','/open?id=%FF'])
def test_http_rejects_invalid_directory_or_load_query(school_web,endpoint):
    _,s,base=school_web; a=client(); login(a,base); before=list(s.db.iterdump())
    assert fetch(a,base,web.PATH+endpoint)[0]==400
    assert list(s.db.iterdump())==before


def test_http_missing_schema_conflicts_and_csv_limit_keep_conditions(school_web,monkeypatch):
    _,s,base=school_web; p=svc.Principal('A','a'); a=client(); login(a,base)
    fixture(s,p,monkeypatch)
    f=dict(q='안내',after='2026-10-29',before='2026-10-29')
    fields=inputs(page(fetch(a,base,live.link(**f))[1]).select_one('[data-receipt-search-save]')); fields['name']='안내'
    assert fetch(a,base,web.PATH+'/save',fields=fields)[0]==200
    assert fetch(a,base,web.PATH+'/save',fields={**fields,'name':'different'})[0]==409
    card=page(fetch(a,base,web.PATH)[1]).select_one('[data-receipt-search-card]')
    monkeypatch.setattr(downloads,'MAX_EXPORT_ROWS',1)
    before=list(s.db.iterdump())
    status,raw,headers=fetch(a,base,card.select_one('[data-receipt-search-csv]')['href'])
    assert status==400 and 'Content-Disposition' not in headers
    assert page(raw).find('a',string='영수증 검색 결과로 돌아가기')['href']==live.link(**f)
    assert list(s.db.iterdump())==before
    drop_v10(s); before=list(s.db.iterdump())
    assert page(fetch(a,base,web.PATH)[1]).select_one('[data-receipt-search-required]')
    assert page(fetch(a,base,live.PATH)[1]).select_one('[data-receipt-search-required]')
    assert fetch(a,base,web.PATH+'/save',fields=fields)[0]==409
    assert fetch(a,base,web.PATH+'/open?id='+fields['id'])[0]==409
    assert len(rows(fetch(a,base,live.link(q='absent',csv=True))[1]))==1
    assert list(s.db.iterdump())==before and not saved.available(s)
