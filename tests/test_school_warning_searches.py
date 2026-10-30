"""Durable warning conditions; no document, review or receipt rewrites."""
import concurrent.futures
import hashlib
import sqlite3
import threading
from urllib.parse import parse_qs, urlsplit

import pytest
from bs4 import BeautifulSoup

from moa import school_warning_searches as saved, school_warning_searches_web as web
from moa import school_warning_search as search, school_warning_search_web as live
from moa import school_service as svc, school_review as review, school_review_export as export
from moa import school_confirmed_export as batch, school_confirmed_export_saved as lists
from moa import school_confirmed_export_downloads as receipts
from moa.core import Store
from test_school_service import schools
from test_school_warning_guidance import warning_notice
from test_school_warning_search import many_warning_notice
from test_school_review_check import originals
from test_web_school import school_web, client, fetch, login

IDENT='w'*24


def page(raw): return BeautifulSoup(raw,'html.parser')
def inputs(form): return {i['name']:i.get('value','') for i in form.select('input[name]')}
def args(pin,**filters): return dict(name='내 경고 조건',notice=pin['id'],revision=pin['revision'],**filters)


def old_rows(s):
    tables=[r[0] for r in s.db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        if r[0] not in ('school_warning_searches','school_warning_search_events','school_warning_search_names','school_service_migrations')]
    return {n:[tuple(r) for r in s.db.execute('SELECT * FROM "'+n+'"')] for n in tables}


def drop_v11(s):
    s.db.executescript('DROP TABLE IF EXISTS school_warning_search_names; DROP TABLE IF EXISTS school_warning_search_events; DROP TABLE school_warning_searches; DELETE FROM school_service_migrations WHERE version>=11;')


def test_v10_upgrade_is_additive_backed_up_repeatable_and_preserves_existing_history(schools):
    _,s,p,_=schools; pin=warning_notice(s,p)
    pins=batch.prepare(s,p,[pin])['pins']; lists.create(s,p,'l'*24,pins,name='보존 묶음')
    receipts.record(s,p,'l'*24,batch.bundle(s,p,pins))
    drop_v11(s); before=list(s.db.iterdump()); old=old_rows(s); files=originals(s)
    version=s.db.execute('PRAGMA user_version').fetchone()[0]
    backup=svc.migrate(s)
    with sqlite3.connect(backup) as db:
        assert list(db.iterdump())==before
        assert db.execute('SELECT max(version) FROM school_service_migrations').fetchone()[0]==10
    assert old_rows(s)==old and originals(s)==files and saved.available(s)
    assert s.db.execute('PRAGMA user_version').fetchone()[0]==version
    assert s.db.execute('SELECT max(version) FROM school_service_migrations').fetchone()[0]==13
    after=list(s.db.iterdump()); assert svc.migrate(s) is None and list(s.db.iterdump())==after
    assert s.db.execute('PRAGMA quick_check').fetchone()[0]=='ok'
    assert not s.db.execute('PRAGMA foreign_key_check').fetchall()


def test_failed_v11_upgrade_rolls_back_without_losing_old_data(schools,monkeypatch):
    _,s,p,_=schools; warning_notice(s,p); drop_v11(s); before=list(s.db.iterdump())
    monkeypatch.setattr(saved,'SCHEMA_V11',saved.SCHEMA_V11+' INVALID SYNTHETIC DDL;')
    with pytest.raises(sqlite3.Error): svc.migrate(s)
    assert not saved.available(s) and list(s.db.iterdump())==before


def test_saved_filters_survive_store_reopen_and_stay_on_old_conversion(schools):
    _,s,p,_=schools; pin=warning_notice(s,p)
    f=dict(q=' 누락신청서 ',file='',code='code:unresolved_attachment')
    old,files=old_rows(s),originals(s)
    saved.create(s,p,IDENT,**args(pin,**f)); row=saved.get(s,p,IDENT)
    assert row['filters']=={**f,'q':'누락신청서'} and row['title']=='분석 경고 합성 통신문'
    assert row['file_label']=='전체' and '첨부' in row['code_label']
    assert old_rows(s)==old and originals(s)==files
    with Store(s.root) as fresh:
        assert saved.get(fresh,p,IDENT)==row
        assert saved.resolve(fresh,p,IDENT)==row
    svc.request_conversion(s,p,pin['id']); assert svc.process_conversions(s)==['done']
    assert svc.structure_for(s,p,pin['id'])['conversion_revision']!=pin['revision']
    assert saved.resolve(s,p,IDENT)==row
    assert search.listing(s,p,pin['id'],row['conversion_revision'],**row['filters'])['total']==1


def test_retry_is_idempotent_changed_form_conflicts_and_new_id_does_not_overwrite(schools):
    _,s,p,_=schools; pin=warning_notice(s,p); selected=args(pin,q=' 각주 ')
    assert saved.create(s,p,IDENT,**selected)==IDENT
    before=list(s.db.iterdump())
    assert saved.create(s,p,IDENT,**{**selected,'q':'각주'})==IDENT
    for changed in ({'name':'다른 이름'},{'q':'다른 검색'},{'code':'code:unresolved_attachment'},{'file':'file:body'}):
        # A genuinely different valid file filter must conflict; unknown files are rejected first.
        if 'file' in changed: changed['file']='file:'+svc.structure_for(s,p,pin['id'])['files'][0]['id']
        with pytest.raises(review.Conflict): saved.create(s,p,IDENT,**{**selected,**changed})
    assert list(s.db.iterdump())==before
    saved.create(s,p,'x'*24,**args(pin,q='누락신청서'))
    assert saved.listing(s,p)['total']==2


def test_concurrent_identical_retry_inserts_one_row(schools):
    _,s,p,_=schools; pin=warning_notice(s,p); gate=threading.Barrier(2)
    def submit():
        with Store(s.root) as own:
            gate.wait(); return saved.create(own,p,IDENT,**args(pin))
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(lambda _:submit(),range(2)))==[IDENT,IDENT]
    assert saved.listing(s,p)['total']==1


@pytest.mark.parametrize('bad', [{'name':''},{'name':'x'*121},{'name':'x\n'},{'q':'x'*121},{'q':'\x00'},
    {'q':'\ud800'},{'file':'file:missing'},{'file':'x'*513},{'code':'code:missing'},
    {'revision':''},{'notice':''},{'ident':'short'},{'ident':'/'*24}])
def test_invalid_conditions_do_not_write(schools,bad):
    _,s,p,_=schools; pin=warning_notice(s,p); before=list(s.db.iterdump())
    with pytest.raises((ValueError,UnicodeError)):
        saved.create(s,p,**{'ident':IDENT,**args(pin),**bad})
    assert list(s.db.iterdump())==before


def test_owner_scope_membership_roles_and_database_scope_trigger(schools):
    _,s,p,other=schools; pin=warning_notice(s,p); foreign=warning_notice(s,other)
    svc.grant_member(s,p.school,'second','uploader'); second=svc.Principal(p.school,'second')
    saved.create(s,p,IDENT,**args(pin))
    for who in (other,second):
        assert saved.listing(s,who)['total']==0
        with pytest.raises(LookupError): saved.get(s,who,IDENT)
        with pytest.raises(LookupError): saved.create(s,who,IDENT,**args(pin))
    for pair in ((foreign['id'],foreign['revision']),(pin['id'],foreign['revision'])):
        with pytest.raises(LookupError): saved.create(s,p,'f'*24,**args(dict(id=pair[0],revision=pair[1])))
    row=s.db.execute('SELECT * FROM school_warning_searches WHERE id=?',(IDENT,)).fetchone()
    values=list(row); values[0]='d'*24; values[5]=foreign['revision']
    with pytest.raises(sqlite3.IntegrityError): s.db.execute('INSERT INTO school_warning_searches VALUES('+','.join('?'*13)+')',values)
    s.db.rollback()
    svc.grant_member(s,p.school,p.subject,'reader')
    assert saved.resolve(s,p,IDENT)['name']=='내 경고 조건'
    with pytest.raises(PermissionError): saved.create(s,p,'r'*24,**args(pin))
    svc.grant_member(s,p.school,p.subject,'reader',active=False)
    for action in (saved.get,saved.resolve):
        with pytest.raises(PermissionError): action(s,p,IDENT)
    with pytest.raises(PermissionError): saved.listing(s,p)


@pytest.mark.parametrize('sql',["UPDATE school_warning_searches SET q='new'",'DELETE FROM school_warning_searches'])
def test_saved_rows_cannot_be_rewritten_or_deleted(schools,sql):
    _,s,p,_=schools; pin=warning_notice(s,p); saved.create(s,p,IDENT,**args(pin)); before=list(s.db.iterdump())
    with pytest.raises(sqlite3.IntegrityError): s.db.execute(sql)
    s.db.rollback(); assert list(s.db.iterdump())==before


def test_directory_paging_is_owner_only_metadata_read_and_never_migrates(schools,monkeypatch):
    _,s,p,_=schools; pin=warning_notice(s,p)
    monkeypatch.setattr(svc,'_now',lambda:'2026-10-30T12:00:00+09:00')
    for n in range(23): saved.create(s,p,f'{n:024d}',**args(pin))
    def forbidden(*a,**kw): raise AssertionError('Directory must not evaluate documents or change history')
    monkeypatch.setattr(search,'listing',forbidden); monkeypatch.setattr(svc,'migrate',forbidden)
    before=list(s.db.iterdump()); s.db.execute('PRAGMA query_only=ON')
    first=saved.listing(s,p); last=saved.listing(s,p,page=100)
    assert first['total']==23 and len(first['rows'])==20 and len(last['rows'])==3
    assert [r['id'] for r in first['rows']+last['rows']]==[f'{n:024d}' for n in range(22,-1,-1)]
    assert saved.get(s,p,f'{0:024d}')['notice_version']==pin['id']
    assert list(s.db.iterdump())==before
    s.db.execute('PRAGMA query_only=OFF'); drop_v11(s); before=list(s.db.iterdump())
    assert saved.listing(s,p)['migration_required']
    with pytest.raises(review.Conflict): saved.get(s,p,IDENT)
    assert list(s.db.iterdump())==before


def test_http_save_relogin_load_old_version_and_preserve_originals_exports_and_receipts(school_web):
    _,s,base=school_web; p=svc.Principal('A','a'); a=client(); csrf=login(a,base)
    pin=warning_notice(s,p); pins=batch.prepare(s,p,[pin])['pins']; list_id='l'*24
    lists.create(s,p,list_id,pins,name='보존 묶음'); archive=batch.bundle(s,p,pins); receipts.record(s,p,list_id,archive)
    csv=receipts.csv_report(s,p,list_id)
    snap=export.snapshot(s,p,pin['id'],revision_id=pin['revision'],event_id=pin['event']); html=export.render_html(snap)
    url=live.link(pin['id'],pin['revision'],q='누락신청서',code='code:unresolved_attachment')
    fields=inputs(page(fetch(a,base,url)[1]).select_one('[data-warning-search-save]')); fields['name']='<b>내 조건 "A"</b>'
    assert fields['q']=='누락신청서' and 'page' not in fields
    before,files=old_rows(s),originals(s)
    status,raw,headers=fetch(a,base,web.PATH+'/save',fields=fields)
    assert status==200 and 'private, no-store' in headers['Cache-Control']
    card=page(raw).select_one('[data-warning-search-card]'); assert card.h2.text==fields['name'] and not card.select('b')
    load=card.select_one('[data-warning-search-open]')['href']
    assert fetch(a,base,web.PATH+'/save',fields=fields)[0]==200 and saved.listing(s,p)['total']==1
    assert old_rows(s)==before and originals(s)==files
    svc.request_conversion(s,p,pin['id']); assert svc.process_conversions(s)==['done']
    assert fetch(a,base,'/logout',fields={'csrf':csrf})[0]==200
    fresh=client(); login(fresh,base); unchanged=list(s.db.iterdump()); files=originals(s)
    s.db.execute('PRAGMA query_only=ON')
    try:
        assert page(fetch(fresh,base,web.PATH)[1]).select_one('[data-warning-search-open]')['href']==load
        status,raw,_=fetch(fresh,base,load); loaded=page(raw); assert status==200
        assert loaded.select_one('[data-warning-revision]').text==pin['revision']
        assert loaded.select_one('[name=q]')['value']=='누락신청서' and len(loaded.select('.warning-card'))==1
        assert loaded.select_one('select[name=code] option[selected]')['value']=='code:unresolved_attachment'
        assert list(s.db.iterdump())==unchanged and originals(s)==files
        assert export.render_html(snap)==html and receipts.csv_report(s,p,list_id)==csv
        with pytest.raises(review.Conflict): batch.bundle(s,p,pins)
        assert receipts.listing(s,p,list_id)['rows'][0]['zip_sha256']==hashlib.sha256(archive).hexdigest()
    finally: s.db.execute('PRAGMA query_only=OFF')


def test_http_saving_last_page_keeps_applied_filters_and_reloads_first_page(school_web):
    _,s,base=school_web; p=svc.Principal('A','a'); a=client(); login(a,base)
    pin=many_warning_notice(s,p); analysis=svc.structure_for(s,p,pin['id'],revision_id=pin['revision'])
    filters=dict(q='누락양식',file='file:'+analysis['files'][0]['id'],code='code:unresolved_attachment')
    raw=fetch(a,base,live.link(pin['id'],pin['revision'],**filters,page=3))[1]
    assert len(page(raw).select('.warning-card'))==5
    form=inputs(page(raw).select_one('[data-warning-search-save]')); form['name']='끝 페이지에서 저장'
    assert {k:form[k] for k in filters}==filters and 'page' not in form
    raw=fetch(a,base,web.PATH+'/save',fields=form)[1]
    link=page(raw).select_one('[data-warning-search-open]')['href']
    loaded=page(fetch(a,base,link)[1])
    assert len(loaded.select('.warning-card'))==20 and '1 / 3페이지' in loaded.text
    assert loaded.select_one('[name=q]')['value']==filters['q']
    assert loaded.select_one('select[name=file] option[selected]')['value']==filters['file']
    assert loaded.select_one('select[name=code] option[selected]')['value']==filters['code']


def test_http_csrf_owner_scope_strict_forms_and_missing_schema(school_web):
    _,s,base=school_web; p=svc.Principal('A','a'); a,b,r=client(),client(),client()
    login(a,base); login(b,base,'b','synthetic-b-only'); cr=login(r,base,'reader','synthetic-reader-only')
    pin=warning_notice(s,p); url=live.link(pin['id'],pin['revision'])
    fields=inputs(page(fetch(a,base,url)[1]).select_one('[data-warning-search-save]')); fields['name']='내 조건'
    post=web.PATH+'/save'; assert fetch(a,base,post,fields=fields)[0]==200
    load=web.PATH+'/open?id='+fields['id']; before=list(s.db.iterdump())
    for path in (web.PATH,load): assert fetch(client(),base,path)[0]==401
    for who in (b,r):
        assert fetch(who,base,load)[0]==404
        assert not page(fetch(who,base,web.PATH)[1]).select('[data-warning-search-card]')
    assert not page(fetch(r,base,url)[1]).select('[data-warning-search-save]')
    assert fetch(a,base,post,fields={**fields,'csrf':'wrong'})[0]==403
    assert fetch(a,base,post,fields=fields,headers={'Origin':'https://evil.invalid'})[0]==403
    assert fetch(r,base,post,fields={**fields,'csrf':cr,'id':'r'*24})[0]==403
    for extra in ({'school':'B'},{'subject':'b'},{'page':'2'},{'file_label':'forged'},{'redirect':'https://evil.invalid'}):
        assert fetch(a,base,post,fields={**fields,**extra})[0]==400
    assert fetch(a,base,post,fields=list(fields.items())+[('q','duplicate')])[0]==400
    assert fetch(a,base,post,fields={k:v for k,v in fields.items() if k!='code'})[0]==400
    assert fetch(a,base,post+'?extra=x',fields=fields)[0]==400
    assert fetch(a,base,post,fields={**fields,'name':'x'*9000})[0]==413
    assert fetch(a,base,post)[0]==404 and fetch(a,base,load,fields={'csrf':fields['csrf']})[0]==404
    assert fetch(a,base,post,fields={**fields,'name':'changed'})[0]==409
    assert list(s.db.iterdump())==before
    drop_v11(s); before=list(s.db.iterdump())
    assert page(fetch(a,base,web.PATH)[1]).select_one('[data-warning-search-required]')
    assert page(fetch(a,base,url)[1]).select_one('[data-warning-search-required]')
    assert fetch(a,base,post,fields=fields)[0]==409 and fetch(a,base,load)[0]==409
    assert list(s.db.iterdump())==before and not saved.available(s)


@pytest.mark.parametrize('suffix',['?page=0','?page=1&page=2','?school=A','?unexpected=x','?page=1000001',
    '/open','/open?id=short','/open?id='+'w'*24+'&id='+'x'*24,'/open?id='+'w'*24+'&subject=a','/open?id=%FF'])
def test_http_invalid_directory_or_reload_query_is_read_only(school_web,suffix):
    _,s,base=school_web; a=client(); login(a,base); before=list(s.db.iterdump())
    assert fetch(a,base,web.PATH+suffix)[0]==400 and list(s.db.iterdump())==before
