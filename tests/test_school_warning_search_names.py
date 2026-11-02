"""Display names never overwrite frozen conditions, state, reviews or receipts."""
import concurrent.futures
import sqlite3
import threading
import unicodedata
from urllib.parse import parse_qs, urlsplit
import pytest
from moa import school_warning_search_names as names, school_warning_search_names_web as ui
from moa import school_warning_search_management as manage, school_warning_searches as saved
from moa import school_service as svc, school_review as review, school_warning_search as search
from moa import school_confirmed_export as batch, school_confirmed_export_saved as lists
from moa import school_confirmed_export_downloads as receipts, school_review_export as export
from moa.core import Store
from test_school_service import schools
from test_school_warning_search_management import seed, IDENT
from test_school_warning_searches import args, page, inputs
from test_school_review_check import originals
from test_web_school import school_web, client, fetch, login


def protected(s):
    tables=[r[0] for r in s.db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        if r[0] not in ('school_warning_search_names','school_service_migrations')]
    return {n:[tuple(r) for r in s.db.execute('SELECT * FROM "'+n+'"')] for n in tables}


def drop_v13(s):
    s.db.executescript('DROP TABLE school_warning_search_names; DELETE FROM school_service_migrations WHERE version=13;')


def test_v12_upgrade_backs_up_archive_history_and_all_existing_rows(schools):
    _,s,p,_=schools;seed(s,p);manage.change(s,p,IDENT,action='archive',expected=0);drop_v13(s)
    before,files,dump=protected(s),originals(s),list(s.db.iterdump());core=s.db.execute('PRAGMA user_version').fetchone()[0]
    backup=svc.migrate(s)
    with sqlite3.connect(backup) as old:assert list(old.iterdump())==dump
    assert protected(s)==before and originals(s)==files and manage.get(s,p,IDENT)['state']=='archived'
    assert s.db.execute('PRAGMA user_version').fetchone()[0]==core
    assert s.db.execute('SELECT max(version) FROM school_service_migrations').fetchone()[0]==13
    after=list(s.db.iterdump());assert svc.migrate(s) is None and list(s.db.iterdump())==after
    assert s.db.execute('PRAGMA quick_check').fetchone()[0]=='ok' and not s.db.execute('PRAGMA foreign_key_check').fetchall()


def test_v13_ddl_failure_rolls_back(schools,monkeypatch):
    _,s,p,_=schools;seed(s,p);drop_v13(s);before=list(s.db.iterdump())
    monkeypatch.setattr(names,'SCHEMA_V13',names.SCHEMA_V13+' INVALID SYNTHETIC DDL;')
    with pytest.raises(sqlite3.Error):svc.migrate(s)
    assert not names.available(s) and list(s.db.iterdump())==before


def test_rename_archived_condition_reopen_and_preserve_all_exports_and_pins(schools):
    _,s,p,_=schools;pin=seed(s,p);pins=batch.prepare(s,p,[pin])['pins'];ident='l'*24
    lists.create(s,p,ident,pins,name='영수증 보존');archive=batch.bundle(s,p,pins);receipts.record(s,p,ident,archive)
    html=export.render_html(export.snapshot(s,p,pin['id'],revision_id=pin['revision'],event_id=pin['event']))
    csv=receipts.csv_report(s,p,ident);manage.change(s,p,IDENT,action='archive',expected=0)
    before,files,condition=protected(s),originals(s),saved.resolve(s,p,IDENT)
    assert names.rename(s,p,IDENT,name='  새 첨부 확인  ',expected=0,expected_state=1)==1
    with Store(s.root) as fresh:
        row=names.get(fresh,p,IDENT);assert row['name']=='새 첨부 확인' and row['state']=='archived'
        assert row['original_name']==condition['name'] and row['name_version']==1
    assert protected(s)==before and originals(s)==files and saved.resolve(s,p,IDENT)==condition
    assert batch.bundle(s,p,pins)==archive and receipts.csv_report(s,p,ident)==csv
    assert export.render_html(export.snapshot(s,p,pin['id'],revision_id=pin['revision'],event_id=pin['event']))==html


def test_metadata_searches_both_current_and_original_names_without_reading_documents(schools,monkeypatch):
    _,s,p,_=schools;seed(s,p,name='처음 조건')
    names.rename(s,p,IDENT,name=unicodedata.normalize('NFD','CAFÉ Straße A_%'),expected=0,expected_state=0)
    def forbidden(*a,**kw):pytest.fail('Metadata must not read or evaluate documents')
    monkeypatch.setattr(search,'listing',forbidden);monkeypatch.setattr(svc,'structure_for',forbidden)
    before=list(s.db.iterdump());s.db.execute('PRAGMA query_only=ON')
    for q in ('처음 조건','café','STRASSE','A_%','%'):
        result=manage.listing(s,p,q=q);assert result['total']==1 and result['rows'][0]['name']=='CAFÉ Straße A_%'
        assert result['rows'][0]['original_name']=='처음 조건' and result['rows'][0]['name_version']==1
    assert not manage.listing(s,p,q="%' OR 1=1 --")['rows']
    assert names.get(s,p,IDENT)['name']=='CAFÉ Straße A_%' and list(s.db.iterdump())==before


@pytest.mark.parametrize('bad',['','   ','x'*121,'\n','x\x00','x\x7f','\ud800',None])
def test_invalid_names_never_write(schools,bad):
    _,s,p,_=schools;seed(s,p);before=list(s.db.iterdump())
    with pytest.raises((ValueError,UnicodeError)):names.rename(s,p,IDENT,name=bad,expected=0,expected_state=0)
    assert list(s.db.iterdump())==before


@pytest.mark.parametrize('bad',[dict(expected=True),dict(expected=-1),dict(expected=2147483647),dict(expected_state=True),dict(expected_state=-1)])
def test_invalid_versions_never_write(schools,bad):
    _,s,p,_=schools;seed(s,p);before=list(s.db.iterdump())
    with pytest.raises(ValueError):names.rename(s,p,IDENT,name='변경 이름',**(dict(expected=0,expected_state=0)|bad))
    assert list(s.db.iterdump())==before


def test_old_and_duplicate_forms_same_name_and_archive_change_are_blocked(schools):
    _,s,p,_=schools;seed(s,p);original=saved.get(s,p,IDENT)['name']
    with pytest.raises(review.Conflict):names.rename(s,p,IDENT,name=original,expected=0,expected_state=0)
    names.rename(s,p,IDENT,name='두 번째',expected=0,expected_state=0)
    with pytest.raises(review.Conflict):names.rename(s,p,IDENT,name='두 번째',expected=0,expected_state=0)
    manage.change(s,p,IDENT,action='archive',expected=0)
    with pytest.raises(review.Conflict):names.rename(s,p,IDENT,name='세 번째',expected=1,expected_state=0)
    names.rename(s,p,IDENT,name=original,expected=1,expected_state=1)
    with pytest.raises(review.Conflict):names.rename(s,p,IDENT,name='오래된 이름',expected=0,expected_state=1)
    assert [e['name'] for e in names.get(s,p,IDENT)['events']]==[original,'두 번째']


def test_owner_permissions_concurrent_one_winner_and_stale_document(schools):
    _,s,p,other=schools;pin=seed(s,p);svc.grant_member(s,p.school,'second','uploader');second=svc.Principal(p.school,'second')
    for who in (other,second):
        with pytest.raises(LookupError):names.get(s,who,IDENT)
        with pytest.raises(LookupError):names.rename(s,who,IDENT,name='타인',expected=0,expected_state=0)
    svc.request_conversion(s,p,pin['id']);assert svc.process_conversions(s)==['done']
    barrier=threading.Barrier(2)
    def send(value):
        with Store(s.root) as fresh:
            barrier.wait()
            try:return names.rename(fresh,p,IDENT,name=value,expected=0,expected_state=0)
            except review.Conflict:return 'conflict'
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(send,['변경 A','변경 B']),key=str)==[1,'conflict']
    assert saved.resolve(s,p,IDENT)['conversion_revision']==pin['revision']
    svc.grant_member(s,p.school,p.subject,'reader');assert names.get(s,p,IDENT)
    with pytest.raises(PermissionError):names.rename(s,p,IDENT,name='읽기 전용',expected=1,expected_state=0)
    svc.grant_member(s,p.school,p.subject,'reader',active=False)
    with pytest.raises(PermissionError):names.get(s,p,IDENT)


def test_history_pages_and_sql_immutability_owner_version_noop_constraints(schools):
    _,s,p,other=schools;seed(s,p);original=saved.get(s,p,IDENT)['name']
    for values in ((IDENT,other.school,other.subject,1,'타인','now'),(IDENT,p.school,p.subject,2,'건너뜀','now'),(IDENT,p.school,p.subject,1,original,'now')):
        with pytest.raises(sqlite3.IntegrityError):s.db.execute('INSERT INTO school_warning_search_names VALUES(?,?,?,?,?,?)',values)
        s.db.rollback()
    for n in range(45):names.rename(s,p,IDENT,name=f'순서 {n}',expected=n,expected_state=0)
    first,last=names.get(s,p,IDENT),names.get(s,p,IDENT,page=100)
    assert first['total']==45 and len(first['events'])==20 and last['page']==3 and len(last['events'])==5
    assert [e['version'] for e in first['events']]==list(range(45,25,-1))
    before=list(s.db.iterdump())
    for sql in ('UPDATE school_warning_search_names SET name="변경"','DELETE FROM school_warning_search_names'):
        with pytest.raises(sqlite3.IntegrityError):s.db.execute(sql)
        s.db.rollback()
    assert list(s.db.iterdump())==before


def test_http_rename_escaped_names_relogin_and_search_context(school_web):
    _,s,base=school_web;p=svc.Principal('A','a');a=client();csrf=login(a,base);pin=seed(s,p)
    for n in range(23):saved.create(s,p,f'{n:024d}',**{**args(pin),'name':f'정리 {n}'})
    doc=page(fetch(a,base,ui.directory_link(q='정리',state='all',page=2))[1]);href=doc.select_one('[data-warning-name-open]')['href']
    doc=page(fetch(a,base,href)[1]);fields=inputs(doc.select_one('[data-warning-name-form]'));fields['name']='<b>새 이름</b>'
    before,files=protected(s),originals(s)
    status,raw,_=fetch(a,base,ui.PATH+'/rename',fields=fields);assert status==200
    doc=page(raw);assert doc.select_one('[data-warning-current-name]').text=='<b>새 이름</b>' and not doc.select('[data-warning-current-name] b')
    assert len(doc.select('[data-warning-name-history] li'))==1 and fetch(a,base,ui.PATH+'/rename',fields=fields)[0]==409
    assert parse_qs(urlsplit(doc.select_one('[data-warning-name-back]')['href']).query)=={'q':['정리'],'state':['all'],'page':['2']}
    fetch(a,base,'/logout',fields={'csrf':csrf});fresh=client();login(fresh,base)
    assert page(fetch(fresh,base,href)[1]).select_one('[data-warning-current-name]').text=='<b>새 이름</b>'
    assert protected(s)==before and originals(s)==files


def test_http_csrf_owner_role_strict_requests_and_missing_schema_are_nonmutating(school_web,monkeypatch):
    _,s,base=school_web;p=svc.Principal('A','a');a,b=client(),client();login(a,base);cb=login(b,base,'b','synthetic-b-only');seed(s,p)
    route=ui.link(IDENT);fields=inputs(page(fetch(a,base,route)[1]).select_one('[data-warning-name-form]'));fields['name']='새 이름'
    before=list(s.db.iterdump());post=ui.PATH+'/rename'
    assert fetch(client(),base,route)[0]==401 and fetch(b,base,route)[0]==404
    assert fetch(b,base,post,fields={**fields,'csrf':cb})[0]==404
    assert fetch(a,base,post,fields={**fields,'csrf':'wrong'})[0]==403
    assert fetch(a,base,post,fields=fields,headers={'Origin':'https://evil.invalid'})[0]==403
    for bad in ({'school':'B'},{'subject':'b'},{'acknowledge':'no'},{'expected':'00'},{'expected_state':'-1'},{'expected':'2147483647'},{'q':'\n'},{'list_page':'0'},{'name':'x'*121}):
        assert fetch(a,base,post,fields={**fields,**bad})[0]==400
    assert fetch(a,base,post,fields=list(fields.items())+[('name','중복')])[0]==400
    assert fetch(a,base,post,fields={k:v for k,v in fields.items() if k!='acknowledge'})[0]==400
    assert fetch(a,base,post+'?x=1',fields=fields)[0]==400
    assert fetch(a,base,post,fields={**fields,'name':'x'*5000})[0]==413
    assert fetch(a,base,post)[0]==404 and fetch(a,base,route,fields=fields)[0]==404
    assert list(s.db.iterdump())==before
    svc.grant_member(s,'A','a','reader');before=list(s.db.iterdump())
    assert not page(fetch(a,base,route)[1]).select('[data-warning-name-form]')
    assert fetch(a,base,post,fields=fields)[0]==403 and list(s.db.iterdump())==before
    svc.grant_member(s,'A','a','uploader');drop_v13(s);before=list(s.db.iterdump())
    monkeypatch.setattr(svc,'migrate',lambda *_:pytest.fail('No automatic HTTP migration'))
    assert page(fetch(a,base,route)[1]).select_one('[data-warning-name-required]')
    assert fetch(a,base,post,fields=fields)[0]==409 and list(s.db.iterdump())==before
    assert saved.resolve(s,p,IDENT) and manage.listing(s,p)['total']==1


@pytest.mark.parametrize('suffix',['?id=short','?id='+'m'*24+'&page=0','?id='+'m'*24+'&q=%FF','?id='+'m'*24+'&id='+'m'*24,'?id='+'m'*24+'&state=unknown'])
def test_bad_history_queries_are_nonmutating(school_web,suffix):
    _,s,base=school_web;a=client();login(a,base);before=list(s.db.iterdump())
    assert fetch(a,base,ui.PATH+'/name'+suffix)[0]==400 and list(s.db.iterdump())==before
