"""Private saved condition search, reversible display state and preserved history."""
import concurrent.futures
import sqlite3
import threading
import unicodedata
from urllib.parse import parse_qs, urlsplit

import pytest

from moa import school_warning_search_management as manage, school_warning_search_management_web as ui
from moa import school_warning_searches as saved, school_warning_search as search
from moa import school_service as svc, school_review as review, school_review_export as export
from moa import school_confirmed_export as batch, school_confirmed_export_saved as lists
from moa import school_confirmed_export_downloads as receipts
from moa.core import Store
from test_school_service import schools
from test_school_warning_guidance import warning_notice
from test_school_warning_searches import args, page, inputs
from test_school_review_check import originals
from test_web_school import school_web, client, fetch, login

IDENT='m'*24


def seed(s,p,*,name='CAFÉ Straße A_%'):
    pin=warning_notice(s,p)
    saved.create(s,p,IDENT,name=name,notice=pin['id'],revision=pin['revision'],q='누락신청서',code='code:unresolved_attachment')
    return pin


def protected(s):
    tables=[r[0] for r in s.db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        if r[0] not in ('school_warning_search_names','school_warning_search_events','school_service_migrations')]
    return {n:[tuple(r) for r in s.db.execute('SELECT * FROM "'+n+'"')] for n in tables}


def drop_v12(s):
    s.db.executescript('DROP TABLE IF EXISTS school_warning_search_names; DROP TABLE school_warning_search_events; DELETE FROM school_service_migrations WHERE version>=12;')


def test_v11_migration_backs_up_all_existing_data_and_preserves_receipts(schools):
    _,s,p,_=schools; pin=seed(s,p); pins=batch.prepare(s,p,[pin])['pins']
    lists.create(s,p,'l'*24,pins,name='보존 묶음'); receipts.record(s,p,'l'*24,batch.bundle(s,p,pins))
    drop_v12(s); dump=list(s.db.iterdump()); before,files=protected(s),originals(s)
    core=s.db.execute('PRAGMA user_version').fetchone()[0]; backup=svc.migrate(s)
    with sqlite3.connect(backup) as db:
        assert list(db.iterdump())==dump and db.execute('SELECT max(version) FROM school_service_migrations').fetchone()[0]==11
    assert protected(s)==before and originals(s)==files
    assert s.db.execute('PRAGMA user_version').fetchone()[0]==core
    assert s.db.execute('SELECT max(version) FROM school_service_migrations').fetchone()[0]==13
    dump=list(s.db.iterdump()); assert svc.migrate(s) is None and list(s.db.iterdump())==dump
    assert s.db.execute('PRAGMA quick_check').fetchone()[0]=='ok' and not s.db.execute('PRAGMA foreign_key_check').fetchall()


def test_v12_failure_rolls_back_all_ddl_and_rows(schools,monkeypatch):
    _,s,p,_=schools; seed(s,p); drop_v12(s); before=list(s.db.iterdump())
    monkeypatch.setattr(manage,'SCHEMA_V12',manage.SCHEMA_V12+' INVALID SYNTHETIC DDL;')
    with pytest.raises(sqlite3.Error): svc.migrate(s)
    assert not manage.available(s) and list(s.db.iterdump())==before


@pytest.mark.parametrize('q,total',[('café',1),(unicodedata.normalize('NFD','CAFÉ'),1),('STRASSE',1),('a_%',1),
    ('%',1),('_',1),('분석 경고 합성',1),('누락신청서',1),('첨부',1),('unresolved_attachment',1),("%' OR 1=1 --",0),('없음',0)])
def test_searches_names_titles_conditions_and_labels_as_literal_unicode(schools,q,total):
    _,s,p,_=schools; seed(s,p); before=list(s.db.iterdump()); s.db.execute('PRAGMA query_only=ON')
    result=manage.listing(s,p,q=q)
    assert result['total']==total and result['filters']['q']==unicodedata.normalize('NFC',q)
    assert list(s.db.iterdump())==before


@pytest.mark.parametrize('bad',[dict(q='x'*121),dict(q='\n'),dict(q='\x00'),dict(q='\ud800'),dict(q=None),
    dict(state='unknown'),dict(page=0),dict(page=True),dict(page=1_000_001)])
def test_invalid_search_rejects_without_database_changes(schools,bad):
    _,s,p,_=schools; before=list(s.db.iterdump())
    with pytest.raises((ValueError,UnicodeError)): manage.listing(s,p,**bad)
    assert list(s.db.iterdump())==before


def test_metadata_paging_and_state_intersections_never_read_documents(schools,monkeypatch):
    _,s,p,_=schools; pin=seed(s,p,name='비검색 항목')
    monkeypatch.setattr(svc,'_now',lambda:'2026-10-30T12:00:00+09:00')
    for n in range(23): saved.create(s,p,f'{n:024d}',**{**args(pin),'name':f'정리 항목 {n}'})
    def forbidden(*a,**kw): raise AssertionError('Metadata must not evaluate or rewrite documents')
    monkeypatch.setattr(search,'listing',forbidden);monkeypatch.setattr(svc,'structure_for',forbidden)
    monkeypatch.setattr(svc,'migrate',forbidden)
    before,files=protected(s),originals(s)
    for n in range(0,23,2): manage.change(s,p,f'{n:024d}',action='archive',expected=0)
    s.db.execute('PRAGMA query_only=ON'); dump=list(s.db.iterdump())
    first=manage.listing(s,p,q='정리',state='all'); last=manage.listing(s,p,q='정리',state='all',page=100)
    assert first['total']==23 and len(first['rows'])==20 and len(last['rows'])==3
    assert [r['id'] for r in first['rows']+last['rows']]==[f'{n:024d}' for n in range(22,-1,-1)]
    assert manage.listing(s,p,q='정리',state='active')['total']==11
    assert manage.listing(s,p,q='정리',state='archived')['total']==12
    assert manage.get(s,p,'0'*24)['state']=='archived'
    assert list(s.db.iterdump())==dump and protected(s)==before and originals(s)==files


def test_archive_restore_reopen_conflicts_and_all_existing_exports_are_preserved(schools):
    _,s,p,_=schools; pin=seed(s,p); pins=batch.prepare(s,p,[pin])['pins']; list_id='l'*24
    lists.create(s,p,list_id,pins,name='기존 묶음'); archive=batch.bundle(s,p,pins); receipts.record(s,p,list_id,archive)
    csv=receipts.csv_report(s,p,list_id); snap=export.snapshot(s,p,pin['id'],revision_id=pin['revision'],event_id=pin['event'])
    html=export.render_html(snap); before,files,row=protected(s),originals(s),saved.get(s,p,IDENT)
    assert manage.change(s,p,IDENT,action='archive',expected=0)==1
    assert not saved.listing(s,p)['rows'] and manage.listing(s,p,state='archived')['total']==1
    assert saved.resolve(s,p,IDENT)==row
    with Store(s.root) as fresh:
        assert manage.get(fresh,p,IDENT)['state']=='archived'
        assert manage.change(fresh,p,IDENT,action='restore',expected=1)==2
    for action,expected in (('archive',0),('restore',1),('restore',2)):
        with pytest.raises(review.Conflict): manage.change(s,p,IDENT,action=action,expected=expected)
    assert manage.get(s,p,IDENT)['version']==2 and saved.get(s,p,IDENT)==row
    assert protected(s)==before and originals(s)==files
    assert batch.bundle(s,p,pins)==archive and export.render_html(snap)==html and receipts.csv_report(s,p,list_id)==csv


def test_stale_document_pins_do_not_block_management_or_retarget_saved_conditions(schools):
    _,s,p,_=schools; pin=seed(s,p); old=saved.get(s,p,IDENT)
    svc.request_conversion(s,p,pin['id']); assert svc.process_conversions(s)==['done']
    manage.change(s,p,IDENT,action='archive',expected=0);manage.change(s,p,IDENT,action='restore',expected=1)
    assert saved.resolve(s,p,IDENT)==old


def test_history_pagination_is_immutable_and_database_rejects_bad_owners_transitions(schools):
    _,s,p,other=schools; seed(s,p)
    for values in ((IDENT,other.school,other.subject,1,'archive','synthetic'),
        (IDENT,p.school,p.subject,1,'restore','synthetic'),(IDENT,p.school,p.subject,2,'archive','synthetic')):
        with pytest.raises(sqlite3.IntegrityError): s.db.execute('INSERT INTO school_warning_search_events VALUES(?,?,?,?,?,?)',values)
        s.db.rollback()
    for n in range(45): manage.change(s,p,IDENT,action='archive' if n%2==0 else 'restore',expected=n)
    first,last=manage.get(s,p,IDENT),manage.get(s,p,IDENT,page=100)
    assert first['total']==45 and len(first['events'])==20 and last['page']==3 and len(last['events'])==5
    assert [e['version'] for e in first['events']]==list(range(45,25,-1))
    before=list(s.db.iterdump())
    for sql in ("UPDATE school_warning_search_events SET action='restore'",'DELETE FROM school_warning_search_events'):
        with pytest.raises(sqlite3.IntegrityError): s.db.execute(sql)
        s.db.rollback()
    assert list(s.db.iterdump())==before


def test_owner_membership_write_role_and_one_winner_for_concurrent_archive(schools):
    _,s,p,other=schools; seed(s,p);svc.grant_member(s,p.school,'second','uploader'); second=svc.Principal(p.school,'second')
    before=list(s.db.iterdump())
    for who in (other,second):
        assert manage.listing(s,who,state='all')['total']==0
        with pytest.raises(LookupError): manage.get(s,who,IDENT)
        with pytest.raises(LookupError): manage.change(s,who,IDENT,action='archive',expected=0)
    assert list(s.db.iterdump())==before
    gate=threading.Barrier(2)
    def send():
        with Store(s.root) as own:
            gate.wait()
            try: return manage.change(own,p,IDENT,action='archive',expected=0)
            except review.Conflict: return 'conflict'
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _:send(),range(2)),key=str)==[1,'conflict']
    svc.grant_member(s,p.school,p.subject,'reader')
    assert manage.get(s,p,IDENT)['state']=='archived'
    with pytest.raises(PermissionError): manage.change(s,p,IDENT,action='restore',expected=1)
    svc.grant_member(s,p.school,p.subject,'reader',active=False)
    with pytest.raises(PermissionError): manage.get(s,p,IDENT)
    with pytest.raises(PermissionError): manage.listing(s,p)


def test_http_filtered_paging_archive_restore_history_relogin_and_preserved_context(school_web):
    _,s,base=school_web;p=svc.Principal('A','a'); a=client();csrf=login(a,base);pin=seed(s,p)
    for n in range(23): saved.create(s,p,f'{n:024d}',**{**args(pin),'name':f'정리 <{n}>'})
    url=ui.directory_link(q='정리',state='all',page=2); doc=page(fetch(a,base,url)[1])
    assert len(doc.select('[data-warning-search-card]'))==3 and not doc.select('[data-warning-search-card] h2 b')
    href=doc.select_one('[data-warning-management-open]')['href'];current=page(fetch(a,base,href)[1])
    fields=inputs(current.select_one('[data-warning-management]'));before,files=protected(s),originals(s)
    assert fields['q']=='정리' and fields['state']=='all' and fields['list_page']=='2'
    status,raw,_=fetch(a,base,ui.PATH+'/archive',fields=fields); assert status==200
    changed=page(raw);assert '보관함' in changed.select_one('[data-warning-management-state]').text
    assert len(changed.select('[data-warning-management-history] li'))==1
    assert parse_qs(urlsplit(changed.select_one('[data-warning-management-back]')['href']).query)=={'q':['정리'],'state':['all'],'page':['2']}
    assert fetch(a,base,ui.PATH+'/archive',fields=fields)[0]==409
    restore=inputs(changed.select_one('[data-warning-management]'))
    assert fetch(a,base,'/logout',fields={'csrf':csrf})[0]==200
    fresh=client();login(fresh,base);doc=page(fetch(fresh,base,href)[1]);restore=inputs(doc.select_one('[data-warning-management]'))
    assert fetch(fresh,base,ui.PATH+'/restore',fields=restore)[0]==200
    assert manage.get(s,p,fields['id'])['version']==2 and protected(s)==before and originals(s)==files


def test_http_csrf_scope_stale_form_strict_input_and_read_only_permission(school_web):
    _,s,base=school_web;p=svc.Principal('A','a');a,b=client(),client();login(a,base);cb=login(b,base,'b','synthetic-b-only');seed(s,p)
    route=ui.link(IDENT);doc=page(fetch(a,base,route)[1]);fields=inputs(doc.select_one('[data-warning-management]'))
    before=list(s.db.iterdump());post=ui.PATH+'/archive'
    assert fetch(client(),base,route)[0]==401 and fetch(b,base,route)[0]==404
    assert fetch(b,base,post,fields={**fields,'csrf':cb})[0]==404
    assert fetch(a,base,post,fields={**fields,'csrf':'wrong'})[0]==403
    assert fetch(a,base,post,fields=fields,headers={'Origin':'https://evil.invalid'})[0]==403
    for extra in ({'acknowledge':'no'},{'expected':'-1'},{'expected':'00'},{'expected':'2147483647'},
        {'expected':'10000000000'},{'list_page':'0'},{'state':'unknown'},{'q':'\n'},{'school':'B'},{'subject':'b'},{'redirect':'https://evil.invalid'}):
        assert fetch(a,base,post,fields={**fields,**extra})[0]==400
    assert fetch(a,base,post,fields={k:v for k,v in fields.items() if k!='acknowledge'})[0]==400
    assert fetch(a,base,post,fields=list(fields.items())+[('id',IDENT)])[0]==400
    assert fetch(a,base,post+'?unexpected=1',fields=fields)[0]==400
    assert fetch(a,base,post,fields={**fields,'q':'x'*5000})[0]==413
    assert fetch(a,base,post)[0]==404 and fetch(a,base,route,fields=fields)[0]==404
    assert list(s.db.iterdump())==before
    svc.grant_member(s,'A','a','reader');before=list(s.db.iterdump())
    assert not page(fetch(a,base,route)[1]).select('[data-warning-management]')
    assert fetch(a,base,post,fields=fields)[0]==403 and list(s.db.iterdump())==before


def test_v11_search_load_and_management_read_never_install_missing_schema(school_web,monkeypatch):
    _,s,base=school_web;p=svc.Principal('A','a');a=client();login(a,base);seed(s,p)
    fields=inputs(page(fetch(a,base,ui.link(IDENT))[1]).select_one('[data-warning-management]'))
    drop_v12(s);before=list(s.db.iterdump())
    monkeypatch.setattr(svc,'migrate',lambda *_:pytest.fail('HTTP must not migrate'))
    s.db.execute('PRAGMA query_only=ON')
    assert page(fetch(a,base,ui.directory_link(q='CAFÉ'))[1]).select_one('[data-warning-management-required]')
    assert page(fetch(a,base,ui.link(IDENT))[1]).select_one('[data-warning-management-required]')
    assert manage.listing(s,p,state='archived')['total']==0 and saved.resolve(s,p,IDENT)
    s.db.execute('PRAGMA query_only=OFF')
    assert fetch(a,base,ui.PATH+'/archive',fields=fields)[0]==409
    assert list(s.db.iterdump())==before and not manage.available(s)


@pytest.mark.parametrize('suffix',['?q=x&q=y','?q=%FF','?state=unknown','?page=0','?q='+'x'*121,'?owner=a',
    '/manage','/manage?id=short','/manage?id='+'m'*24+'&page=0','/manage?id='+'m'*24+'&list_page=0',
    '/manage?id='+'m'*24+'&state=unknown','/manage?id='+'m'*24+'&id='+'m'*24])
def test_http_malformed_search_management_queries_are_nonmutating(school_web,suffix):
    _,s,base=school_web;a=client();login(a,base);before=list(s.db.iterdump())
    assert fetch(a,base,ui.PATH+suffix)[0]==400 and list(s.db.iterdump())==before
