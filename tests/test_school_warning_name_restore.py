"""Selected historical names append new records and preserve every other artifact."""
import concurrent.futures
import threading
from urllib.parse import parse_qs, urlsplit
import pytest
from moa import school_warning_search_names as names, school_warning_search_names_web as history
from moa import school_warning_name_restore_web as ui, school_warning_search_management as manage
from moa import school_service as svc, school_review as review, school_warning_searches as saved
from moa import school_confirmed_export as batch, school_confirmed_export_saved as lists
from moa import school_confirmed_export_downloads as receipts, school_review_export as export
from moa.core import Store
from test_school_service import schools
from test_school_warning_search_management import seed, IDENT
from test_school_warning_search_names import protected, drop_v13
from test_school_warning_searches import page, inputs
from test_school_review_check import originals
from test_web_school import school_web, client, fetch, login


def renamed(s,p):
    pin=seed(s,p,name='처음 이름')
    names.rename(s,p,IDENT,name='<b>이전 & 이름</b>',expected=0,expected_state=0)
    names.rename(s,p,IDENT,name='현재 이름',expected=1,expected_state=0)
    return pin


def test_restore_previous_and_original_reopen_preserves_frozen_rows_history_and_exports(schools):
    _,s,p,_=schools;pin=renamed(s,p);pins=batch.prepare(s,p,[pin])['pins'];ident='l'*24
    lists.create(s,p,ident,pins,name='보존 묶음');raw=batch.bundle(s,p,pins);receipts.record(s,p,ident,raw)
    html=export.render_html(export.snapshot(s,p,pin['id'],revision_id=pin['revision'],event_id=pin['event']))
    csv=receipts.csv_report(s,p,ident);manage.change(s,p,IDENT,action='archive',expected=0)
    before,files,condition=protected(s),originals(s),saved.resolve(s,p,IDENT)
    events=[tuple(r) for r in s.db.execute('SELECT * FROM school_warning_search_names ORDER BY version')]
    assert names.restore(s,p,IDENT,source_version=1,expected=2,expected_state=1)==3
    assert names.get(s,p,IDENT)['name']=='<b>이전 & 이름</b>'
    assert names.restore(s,p,IDENT,source_version=0,expected=3,expected_state=1)==4
    with Store(s.root) as fresh:
        row=names.get(fresh,p,IDENT)
        assert row['name']=='처음 이름' and row['name_version']==4 and row['state']=='archived'
        assert [e['name'] for e in row['events']]==['처음 이름','<b>이전 & 이름</b>','현재 이름','<b>이전 & 이름</b>']
    assert [tuple(r) for r in s.db.execute('SELECT * FROM school_warning_search_names WHERE version<=2 ORDER BY version')]==events
    assert protected(s)==before and originals(s)==files and saved.resolve(s,p,IDENT)==condition
    assert batch.bundle(s,p,pins)==raw and receipts.csv_report(s,p,ident)==csv
    assert export.render_html(export.snapshot(s,p,pin['id'],revision_id=pin['revision'],event_id=pin['event']))==html
    assert s.db.execute('PRAGMA quick_check').fetchone()[0]=='ok' and not s.db.execute('PRAGMA foreign_key_check').fetchall()


def test_selection_is_read_only_and_does_not_read_documents_or_migrate(schools,monkeypatch):
    _,s,p,_=schools;renamed(s,p)
    def forbidden(*a,**kw):pytest.fail('Name restore selection must only read private metadata')
    monkeypatch.setattr(svc,'structure_for',forbidden);monkeypatch.setattr(svc,'migrate',forbidden)
    before=list(s.db.iterdump());s.db.execute('PRAGMA query_only=ON')
    assert names.selection(s,p,IDENT,source_version=0)['source_name']=='처음 이름'
    assert names.selection(s,p,IDENT,source_version=1)['source_name']=='<b>이전 & 이름</b>'
    assert list(s.db.iterdump())==before


@pytest.mark.parametrize('source',[True,-1,2147483648,1.0,'1',None])
def test_invalid_source_never_writes(schools,source):
    _,s,p,_=schools;renamed(s,p);before=list(s.db.iterdump())
    for action in (lambda:names.selection(s,p,IDENT,source_version=source),
                   lambda:names.restore(s,p,IDENT,source_version=source,expected=2,expected_state=0)):
        with pytest.raises(ValueError):action()
    assert list(s.db.iterdump())==before


def test_missing_source_noop_duplicate_and_aba_name_or_state_are_blocked(schools):
    _,s,p,_=schools;renamed(s,p);before=list(s.db.iterdump())
    with pytest.raises(LookupError):names.restore(s,p,IDENT,source_version=3,expected=2,expected_state=0)
    with pytest.raises(review.Conflict):names.restore(s,p,IDENT,source_version=2,expected=2,expected_state=0)
    assert list(s.db.iterdump())==before
    names.restore(s,p,IDENT,source_version=0,expected=2,expected_state=0)
    names.restore(s,p,IDENT,source_version=2,expected=3,expected_state=0)
    with pytest.raises(review.Conflict):names.restore(s,p,IDENT,source_version=0,expected=2,expected_state=0)
    manage.change(s,p,IDENT,action='archive',expected=0);manage.change(s,p,IDENT,action='restore',expected=1)
    with pytest.raises(review.Conflict):names.restore(s,p,IDENT,source_version=0,expected=4,expected_state=0)
    assert names.get(s,p,IDENT)['name_version']==4


def test_restore_owner_role_and_concurrent_rename_one_winner(schools):
    _,s,p,other=schools;renamed(s,p);svc.grant_member(s,p.school,'second','uploader');second=svc.Principal(p.school,'second')
    for who in (other,second):
        with pytest.raises(LookupError):names.selection(s,who,IDENT,source_version=0)
        with pytest.raises(LookupError):names.restore(s,who,IDENT,source_version=0,expected=2,expected_state=0)
    barrier=threading.Barrier(2)
    def send(which):
        with Store(s.root) as fresh:
            barrier.wait()
            try:
                if which:return names.restore(fresh,p,IDENT,source_version=0,expected=2,expected_state=0)
                return names.rename(fresh,p,IDENT,name='동시 이름',expected=2,expected_state=0)
            except review.Conflict:return 'conflict'
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(send,[0,1]),key=str)==[3,'conflict']
    svc.grant_member(s,p.school,p.subject,'reader');assert names.selection(s,p,IDENT,source_version=0)
    with pytest.raises(PermissionError):names.restore(s,p,IDENT,source_version=1,expected=3,expected_state=0)
    svc.grant_member(s,p.school,p.subject,'reader',active=False)
    with pytest.raises(PermissionError):names.selection(s,p,IDENT,source_version=0)


def test_http_confirmation_cancel_context_restore_relogin_and_same_name_disabled(school_web):
    _,s,base=school_web;p=svc.Principal('A','a');a=client();csrf=login(a,base);renamed(s,p)
    for n in range(2,22):names.rename(s,p,IDENT,name=f'이름 {n}',expected=n,expected_state=0)
    route=history.link(IDENT,page=2,q='처음',state='all',list_page=3)
    doc=page(fetch(a,base,route)[1]);href=doc.select_one('[data-warning-name-event="1"] a')['href']
    before=list(s.db.iterdump());status,raw,_=fetch(a,base,href);assert status==200 and list(s.db.iterdump())==before
    doc=page(raw);assert doc.select_one('[data-warning-name-restore-selected]').text=='<b>이전 & 이름</b>'
    assert not doc.select('[data-warning-name-restore-selected] b')
    assert parse_qs(urlsplit(doc.select_one('[data-warning-name-restore-cancel]')['href']).query)=={
        'id':[IDENT],'page':['2'],'q':['처음'],'state':['all'],'list_page':['3']}
    fields=inputs(doc.select_one('[data-warning-name-restore-form]'));assert 'name' not in fields
    status,raw,_=fetch(a,base,ui.ROUTE,fields=fields);assert status==200
    doc=page(raw);assert doc.select_one('[data-warning-current-name]').text=='<b>이전 & 이름</b>'
    assert doc.select_one('[data-warning-name-event="23"]') and fetch(a,base,ui.ROUTE,fields=fields)[0]==409
    assert parse_qs(urlsplit(doc.select_one('[data-warning-name-back]')['href']).query)=={'q':['처음'],'state':['all'],'page':['3']}
    doc=page(fetch(a,base,href)[1]);assert doc.select_one('[data-warning-name-restore-same]') and not doc.select('[data-warning-name-restore-form]')
    fetch(a,base,'/logout',fields={'csrf':csrf});fresh=client();login(fresh,base)
    assert page(fetch(fresh,base,history.link(IDENT))[1]).select_one('[data-warning-current-name]').text=='<b>이전 & 이름</b>'


def test_http_owner_csrf_strict_fields_and_missing_schema_do_not_write(school_web,monkeypatch):
    _,s,base=school_web;p=svc.Principal('A','a');a,b=client(),client();login(a,base);cb=login(b,base,'b','synthetic-b-only');renamed(s,p)
    route=ui.link(IDENT,source_version=0);doc=page(fetch(a,base,route)[1]);fields=inputs(doc.select_one('[data-warning-name-restore-form]'))
    before=list(s.db.iterdump());assert fetch(client(),base,route)[0]==401 and fetch(b,base,route)[0]==404
    assert fetch(b,base,ui.ROUTE,fields={**fields,'csrf':cb})[0]==404
    assert fetch(a,base,ui.ROUTE,fields={**fields,'csrf':'wrong'})[0]==403
    assert fetch(a,base,ui.ROUTE,fields=fields,headers={'Origin':'https://evil.invalid'})[0]==403
    for change in ({'name':'공격 이름'},{'school':'B'},{'subject':'b'},{'source_version':'00'},{'source_version':'2147483648'},
                   {'expected':'-1'},{'expected':'2147483647'},{'expected_state':'true'},{'history_page':'0'},
                   {'list_page':'0'},{'state':'unknown'},{'q':'\n'},{'acknowledge':'no'}):
        assert fetch(a,base,ui.ROUTE,fields={**fields,**change})[0]==400
    assert fetch(a,base,ui.ROUTE,fields=list(fields.items())+[('source_version','1')])[0]==400
    assert fetch(a,base,ui.ROUTE,fields={k:v for k,v in fields.items() if k!='acknowledge'})[0]==400
    assert fetch(a,base,ui.ROUTE+'?x=1',fields=fields)[0]==400
    assert fetch(a,base,ui.ROUTE,fields={**fields,'q':'x'*5000})[0]==413
    assert fetch(a,base,ui.ROUTE,fields={**fields,'source_version':'9'})[0]==404
    assert list(s.db.iterdump())==before
    svc.grant_member(s,'A','a','reader');before=list(s.db.iterdump())
    assert not page(fetch(a,base,route)[1]).select('[data-warning-name-restore-form]')
    assert not page(fetch(a,base,history.link(IDENT))[1]).select('[data-warning-name-restore-open],[data-warning-name-original-restore]')
    assert fetch(a,base,ui.ROUTE,fields=fields)[0]==403 and list(s.db.iterdump())==before
    svc.grant_member(s,'A','a','uploader');drop_v13(s);before=list(s.db.iterdump())
    monkeypatch.setattr(svc,'migrate',lambda *_:pytest.fail('HTTP must never migrate'))
    assert fetch(a,base,route)[0]==409 and fetch(a,base,ui.ROUTE,fields=fields)[0]==409
    assert list(s.db.iterdump())==before


def test_http_archive_change_blocks_old_confirmation_but_new_confirmation_preserves_archive(school_web):
    _,s,base=school_web;p=svc.Principal('A','a');a=client();login(a,base);renamed(s,p)
    route=ui.link(IDENT,source_version=0);fields=inputs(page(fetch(a,base,route)[1]).select_one('[data-warning-name-restore-form]'))
    manage.change(s,p,IDENT,action='archive',expected=0);before,files=protected(s),originals(s)
    status,raw,_=fetch(a,base,ui.ROUTE,fields=fields);assert status==409
    doc=page(raw);assert doc.select_one('[data-warning-name-restore-conflict]') and names.get(s,p,IDENT)['name_version']==2
    fields=inputs(doc.select_one('[data-warning-name-restore-form]'));assert fields['expected_state']=='1'
    assert fetch(a,base,ui.ROUTE,fields=fields)[0]==200 and names.get(s,p,IDENT)['state']=='archived'
    assert protected(s)==before and originals(s)==files


@pytest.mark.parametrize('query',['','?id=short&source_version=0','?id='+'m'*24+'&source_version=-1',
    '?id='+'m'*24+'&source_version=01','?id='+'m'*24+'&source_version=0&source_version=1',
    '?id='+'m'*24+'&source_version=0&history_page=0','?id='+'m'*24+'&source_version=0&q=%FF'])
def test_malformed_confirmation_queries_are_nonmutating(school_web,query):
    _,s,base=school_web;a=client();login(a,base);before=list(s.db.iterdump())
    assert fetch(a,base,ui.ROUTE+query)[0]==400 and list(s.db.iterdump())==before
