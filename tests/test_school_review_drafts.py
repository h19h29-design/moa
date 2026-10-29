"""Private input recovery, isolated SQLite and real school HTTP, no models."""
from urllib.parse import parse_qs, urlsplit
import concurrent.futures
import json
import sqlite3

from bs4 import BeautifulSoup
import pytest

from moa import school_service as svc, school_review as review, school_review_drafts as drafts
from moa.core import Store, digest
from test_school_service import schools
from test_school_structure import converted
from test_school_structure_review import save as formal_save
from test_web_school import school_web, client, fetch, login


def page(a, base, path):
    status, body, headers = fetch(a,base,path)
    assert status==200, body.decode()
    return BeautifulSoup(body,'html.parser'), headers


def form(doc, action='block'):
    return next(f for f in doc.select('form[action="/structure-review"]')
                if f.select_one('[name=action]')['value']==action)


def inputs(f):
    result = {e['name']:e.get('value','') for e in f.select('input[name]') if e.get('type')!='checkbox'}
    result.update({e['name']:e.get_text() for e in f.select('textarea[name]')})
    for e in f.select('select[name]'):
        option=e.select_one('option[selected]') or e.select_one('option')
        result[e['name']]=option['value']
    return result


def content_without_drafts(s):
    return [line for line in s.db.iterdump() if not line.startswith('INSERT INTO "school_review_drafts"')]


def draft_save(s, p, ident, *, action=None, view=None, expected='', comment='작성 중'):
    v=view or review.view(s,p,ident)
    action=action or {'kind':'block','block_id':v['analysis']['blocks'][0]['id'],'block_kind':'body','text':'임시 입력'}
    return drafts.save(s,p,ident,revision_id=v['original']['conversion_revision'],base_hash=v['base_hash'],
        expected_review=v['latest']['id'] if v['latest'] else '',expected_draft=expected,action=action,comment=comment)


def http_draft(a,base,s,*,kind='block',changes=None,key='trip'):
    receipt,_=converted(svc,s,svc.Principal('A','a'),key=key)
    doc,_=page(a,base,'/structure-review?id='+receipt.version_id)
    values=inputs(form(doc,kind)); values.update(changes or {})
    status,body,_=fetch(a,base,'/structure-draft/save',fields=values)
    assert status==200,body.decode()
    ident=s.db.execute('SELECT id FROM school_review_drafts ORDER BY rowid DESC LIMIT 1').fetchone()[0]
    return receipt,values,ident


def test_partial_input_recovers_after_new_login_without_creating_a_review(school_web):
    _,s,base=school_web
    a=client(); csrf=login(a,base); principal=svc.Principal('A','a')
    receipt,_=converted(svc,s,principal)
    doc,_=page(a,base,'/structure-review?id='+receipt.version_id)
    f=form(doc)
    assert f.select_one('button[formaction="/structure-draft/save"]'), 'Input draft save control is missing'
    values=inputs(f); values.update(text='작성 중 <script>draft-only</script>\n아직 미완성',comment='')
    before=content_without_drafts(s)
    status,body,_=fetch(a,base,'/structure-draft/save',fields=values)
    assert status==200,body.decode()
    assert content_without_drafts(s)==before
    assert review.view(s,principal,receipt.version_id)['status']=='unreviewed'
    assert s.db.execute('SELECT count(*) FROM school_structure_reviews').fetchone()[0]==0
    assert fetch(a,base,'/logout',fields={'csrf':csrf})[0]==200
    fresh=client(); login(fresh,base)
    directory,_=page(fresh,base,'/structure-drafts')
    resume=directory.select_one('[data-draft-resume]')['href']
    snapshot=list(s.db.iterdump())
    recovered,headers=page(fresh,base,resume)
    assert list(s.db.iterdump())==snapshot
    assert recovered.select_one('#draft-restored')
    restored=inputs(form(recovered))
    assert restored['text']==values['text'] and restored['comment']==''
    assert '<script>draft-only</script>' not in str(recovered)
    assert 'no-store' in headers['Cache-Control']
    restored['comment']='복구 후 정식 검수 저장'
    assert fetch(fresh,base,'/structure-review',fields=restored)[0]==200
    assert review.view(s,principal,receipt.version_id)['status']=='draft'
    assert s.db.execute('SELECT count(*) FROM school_structure_reviews').fetchone()[0]==1


def test_append_only_cas_and_concurrent_tabs_preserve_every_saved_input(schools):
    m,s,a,_=schools; receipt,_=converted(m,s,a)
    first=draft_save(s,a,receipt.version_id)
    before=content_without_drafts(s)
    def attempt(n):
        with Store(s.root) as other:
            try: return draft_save(other,a,receipt.version_id,expected=first,comment='임시 '+str(n))
            except review.Conflict: return 'conflict'
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(attempt,range(2)))
    assert results.count('conflict')==1
    second=next(x for x in results if x!='conflict')
    row=drafts.get(s,a,second)
    assert row['previous_id']==first and row['sequence']==2
    assert drafts.get(s,a,first)['payload']['comment']=='작성 중'
    assert drafts.listing(s,a)['rows'][0]['id']==second
    assert content_without_drafts(s)==before
    with pytest.raises(review.Conflict): draft_save(s,a,receipt.version_id,expected=first)
    for sql in ("UPDATE school_review_drafts SET payload='{}'",'DELETE FROM school_review_drafts'):
        with pytest.raises(sqlite3.IntegrityError): s.db.execute(sql)
        s.db.rollback()
    assert s.db.execute('SELECT count(*) FROM school_review_drafts').fetchone()[0]==2


def test_draft_owner_and_school_are_both_required_not_just_edit_permission(school_web):
    _,s,base=school_web; a,b,reader,anon=client(),client(),client(),client()
    login(a,base); csrf_b=login(b,base,'b','synthetic-b-only')
    csrf_r=login(reader,base,'reader','synthetic-reader-only')
    receipt,values,ident=http_draft(a,base,s,changes={'text':'소유자만 보는 임시 비공개 입력'})
    for who in (b,reader):
        assert fetch(who,base,'/structure-draft?id='+ident)[0]==404
        assert '소유자만 보는'.encode() not in fetch(who,base,'/structure-drafts')[1]
    # A same-school writer is not allowed to read another staff member's input.
    svc.grant_member(s,'A','reader','manager')
    assert fetch(reader,base,'/structure-draft?id='+ident)[0]==404
    assert fetch(b,base,'/structure-draft/save',fields={**values,'csrf':csrf_b})[0]==404
    assert fetch(anon,base,'/structure-draft?id='+ident)[0]==401
    svc.grant_member(s,'A','a','reader')
    doc,_=page(a,base,'/structure-draft?id='+ident)
    assert doc.select_one('#draft-readonly') and not doc.select_one('form[action="/structure-review"]')
    assert fetch(a,base,'/structure-draft/save',fields=values)[0]==403
    svc.grant_member(s,'A','a','reader',active=False)
    assert fetch(a,base,'/structure-draft?id='+ident)[0]==401
    assert s.db.execute('SELECT count(*) FROM school_structure_reviews').fetchone()[0]==0


@pytest.mark.parametrize('changes',[
    {'school':'B'},{'subject':'admin'},{'action':'confirm'},{'action':'reopen'},
    {'action':'restore'},{'acknowledge_warnings':'yes'},{'acknowledge_restore':'yes'},
    {'source_event':'foreign'},{'text':'bad\0text'},{'text':'x'*20001},{'comment':'x'*2001},
    {'block_id':'foreign/b1'},{'block_kind':'script'},{'expected_draft':'bad'},
],ids=['school','subject','confirm','reopen','restore','approve','restore-ack','event','nul','large-text',
        'large-comment','foreign-block','bad-kind','bad-id'])
def test_malformed_or_authority_injected_input_never_writes(school_web,changes):
    _,s,base=school_web; a=client(); login(a,base)
    receipt,_=converted(svc,s,svc.Principal('A','a'))
    doc,_=page(a,base,'/structure-review?id='+receipt.version_id)
    values=inputs(form(doc)); before=list(s.db.iterdump())
    assert fetch(a,base,'/structure-draft/save',fields={**values,**changes})[0]==400
    assert list(s.db.iterdump())==before


def test_csrf_origin_duplicates_strict_encoding_and_bounded_request(school_web):
    from urllib.parse import urlencode
    _,s,base=school_web; a=client(); login(a,base)
    receipt,_=converted(svc,s,svc.Principal('A','a'))
    doc,_=page(a,base,'/structure-review?id='+receipt.version_id); values=inputs(form(doc))
    before=list(s.db.iterdump())
    assert fetch(a,base,'/structure-draft/save',fields={**values,'csrf':'wrong'})[0]==403
    assert fetch(a,base,'/structure-draft/save',fields=values,headers={'Origin':'https://evil.example.test'})[0]==403
    raw=urlencode(values).encode()
    assert fetch(a,base,'/structure-draft/save',data=raw+b'&text=duplicate')[0]==400
    assert fetch(a,base,'/structure-draft/save',data=raw+b'&text=%FF')[0]==400
    assert fetch(a,base,'/structure-draft/save',data=b'x'*300001)[0]==413
    assert list(s.db.iterdump())==before


@pytest.mark.parametrize('kind,changes',[
    ('block',{'text':'','comment':''}),
    ('relation_add',{'relation_source':'','relation_target':'','comment':''}),
    ('relation_remove',{'comment':'아직 작성 중인 관계 제거 이유'}),
])
def test_unfinished_each_form_recovers_without_formal_changes(school_web,kind,changes):
    _,s,base=school_web; a=client(); login(a,base)
    receipt,values,ident=http_draft(a,base,s,kind=kind,changes=changes)
    original=review.view(s,svc.Principal('A','a'),receipt.version_id)
    doc,_=page(a,base,'/structure-draft?id='+ident)
    restored=inputs(form(doc,kind))
    assert all(restored[k]==val for k,val in changes.items())
    assert not doc.select('input[type=checkbox][checked]')
    assert not form(doc,'confirm').select_one('[data-draft-save]')
    assert review.view(s,svc.Principal('A','a'),receipt.version_id)==original
    if kind!='relation_remove':
        assert fetch(a,base,'/structure-review',fields=restored)[0]==400
        assert not s.db.execute('SELECT 1 FROM school_structure_reviews').fetchone()
    else:
        assert fetch(a,base,'/structure-review',fields=restored)[0]==200
        assert s.db.execute('SELECT count(*) FROM school_structure_reviews').fetchone()[0]==1


@pytest.mark.parametrize('change',['review','confirm','conversion','correction','failed','new-input'])
def test_changed_bases_keep_old_input_read_only_and_never_retarget(school_web,change):
    _,s,base=school_web; a=client(); login(a,base); p=svc.Principal('A','a')
    receipt,values,ident=http_draft(a,base,s,changes={'text':'보존할 임시 입력 draft-preserved'})
    old=drafts.get(s,p,ident)
    if change in ('review','confirm'):
        action={'kind':'confirm','acknowledge_warnings':True} if change=='confirm' else {
            'kind':'block','block_id':values['block_id'],'block_kind':values['block_kind'],'text':'다른 정식 수정'}
        formal_save(review,s,p,receipt.version_id,action)
    elif change in ('conversion','failed'):
        svc.request_conversion(s,p,receipt.version_id)
        if change=='conversion': assert svc.process_conversions(s)==['done']
        else:
            version=svc.get_version(s,p,receipt.version_id)
            s.db.execute("UPDATE mobile_notices SET state='conversion_failed' WHERE id=?",(version['mobile_id'],)); s.db.commit()
    elif change=='correction': converted(svc,s,p,price='15000')
    else: draft_save(s,p,receipt.version_id,expected=ident,comment='new input')
    snapshot=list(s.db.iterdump()); doc,_=page(a,base,'/structure-draft?id='+ident)
    assert doc.select_one('#draft-readonly')
    assert not doc.select_one('form[action="/structure-review"]')
    assert 'draft-preserved' in doc.get_text()
    assert drafts.get(s,p,ident)==old and list(s.db.iterdump())==snapshot
    assert fetch(a,base,'/structure-draft/save',fields=values)[0]==409


def test_review_changing_between_recovery_lookup_and_render_cannot_rebase_input(school_web,monkeypatch):
    from moa import school_review_web
    _,s,base=school_web; a=client(); login(a,base); p=svc.Principal('A','a')
    receipt,_,ident=http_draft(a,base,s)
    original=school_review_web.render
    def concurrent_change(*args,**kwargs):
        formal_save(review,s,p,receipt.version_id,{'kind':'confirm','acknowledge_warnings':True})
        return original(*args,**kwargs)
    monkeypatch.setattr(school_review_web,'render',concurrent_change)
    doc,_=page(a,base,'/structure-draft?id='+ident)
    assert doc.select_one('#draft-readonly'), 'Stale input must never be rebased onto a newer formal review'
    assert not doc.select_one('form[action="/structure-review"]')


def test_directory_paginates_latest_heads_without_loading_payload_and_restart(schools):
    m,s,a,b=schools
    for n in range(22):
        receipt,_=converted(m,s,a,key='notice-'+str(n))
        first=draft_save(s,a,receipt.version_id)
        draft_save(s,a,receipt.version_id,expected=first,comment='superseding input')
    rows=drafts.listing(s,a)['rows']+drafts.listing(s,a,page=2)['rows']
    assert len(rows)==22 and all(r['sequence']==2 and 'payload' not in r for r in rows)
    assert drafts.listing(s,a)['pages']==2
    assert drafts.listing(s,a,page=100)['page']==2
    assert not drafts.listing(s,b)['rows']
    with Store(s.root) as reopened:
        assert drafts.get(reopened,a,rows[0]['id'])['payload']['comment']=='superseding input'
    for invalid in ('1',True,0,1_000_001):
        with pytest.raises(ValueError): drafts.listing(s,a,page=invalid)


def test_saved_queue_context_is_checked_and_recovery_does_not_move_checkpoint(school_web):
    from test_school_saved_queue import start, pair, save_list
    _,s,base=school_web; a=client(); login(a,base); pair(s)
    doc,_=save_list(a,base,start(a,base)[0]); values=inputs(form(doc))
    cursor=[tuple(r) for r in s.db.execute('SELECT * FROM school_review_lists')]
    values.update(text='목록에서 작성 중',comment='')
    for edits in ({'saved_item':'2'},{'queue_id':'x'*24,'queue_item':'1'},{'revision':'f'*32}):
        assert fetch(a,base,'/structure-draft/save',fields={**values,**edits})[0]==400
    status,body,_=fetch(a,base,'/structure-draft/save',fields=values)
    assert status==200
    doc=BeautifulSoup(body,'html.parser'); restored=inputs(form(doc))
    assert restored['saved_id']==values['saved_id'] and restored['saved_item']=='1'
    assert restored['text']==values['text'] and doc.select_one('#review-queue')
    assert [tuple(r) for r in s.db.execute('SELECT * FROM school_review_lists')]==cursor
    ident=s.db.execute('SELECT id FROM school_review_drafts').fetchone()[0]
    assert fetch(a,base,'/structure-draft?id='+ident+'&saved_id='+values['saved_id']+'&saved_item=2')[0]==400


def test_missing_v4_schema_is_never_installed_by_read_or_save_then_explicit_upgrade_preserves_rows(school_web):
    from moa import web_school
    _,s,base=school_web; a=client(); login(a,base); p=svc.Principal('A','a')
    receipt,_=converted(svc,s,p)
    formal_save(review,s,p,receipt.version_id,{'kind':'confirm','acknowledge_warnings':True})
    s.db.executescript('DROP TABLE IF EXISTS school_warning_search_events; DROP TABLE IF EXISTS school_warning_searches; DROP TABLE IF EXISTS school_receipt_searches; DROP TABLE IF EXISTS school_confirmed_export_downloads; DROP TABLE IF EXISTS school_confirmed_export_list_events; DROP TABLE school_confirmed_export_items; DROP TABLE school_confirmed_export_lists; DROP TABLE school_review_draft_handoffs; DROP TABLE school_review_draft_restores; DROP TABLE school_review_drafts; DELETE FROM school_service_migrations WHERE version>=4;')
    before=list(s.db.iterdump()); version=s.db.execute('PRAGMA user_version').fetchone()[0]
    web_school.make_handler(s,{})
    doc,_=page(a,base,'/structure-review?id='+receipt.version_id)
    assert not doc.select('[data-draft-save]') and '명시적 마이그레이션' in doc.get_text()
    assert '명시적 마이그레이션' in page(a,base,'/structure-drafts')[0].get_text()
    values=inputs(form(doc)); values['expected_draft']=''
    assert fetch(a,base,'/structure-draft/save',fields=values)[0]==409
    assert list(s.db.iterdump())==before
    objects={str(p):(digest(p.read_bytes()),p.stat().st_mtime_ns)
        for folder in ('objects','extracted') for p in (s.root/folder).rglob('*') if p.is_file()}
    backup=svc.migrate(s)
    with sqlite3.connect(backup) as old:
        assert old.execute('SELECT max(version) FROM school_service_migrations').fetchone()[0]==3
        assert list(old.iterdump())==before
    assert set(before)<=set(s.db.iterdump())
    assert svc.migrate(s) is None
    assert s.db.execute('PRAGMA user_version').fetchone()[0]==version
    assert s.db.execute('PRAGMA foreign_key_check').fetchall()==[]
    assert s.db.execute('PRAGMA quick_check').fetchone()[0]=='ok'
    assert {str(p):(digest(p.read_bytes()),p.stat().st_mtime_ns)
        for folder in ('objects','extracted') for p in (s.root/folder).rglob('*') if p.is_file()}==objects
    assert fetch(a,base,'/structure-draft/save',fields=values)[0]==200
    assert review.view(s,p,receipt.version_id)['status']=='confirmed'
    s.db.execute("INSERT INTO school_service_migrations VALUES(999,'unknown')"); s.db.commit()
    with pytest.raises(RuntimeError): svc.migrate(s)


def test_fresh_server_handler_reauthenticates_and_recovers_from_persistent_db(school_web):
    import threading
    from http.server import ThreadingHTTPServer
    web,s,base=school_web; a=client(); login(a,base)
    _,_,ident=http_draft(a,base,s)
    with Store(s.root,thread_safe=True) as reopened:
        users={'a':dict(school='A',subject='a',**web.password_record('synthetic-a-only'))}
        server=ThreadingHTTPServer(('127.0.0.1',0),web.make_handler(reopened,users))
        thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        restarted='http://127.0.0.1:'+str(server.server_port)
        try:
            assert fetch(a,restarted,'/structure-draft?id='+ident)[0]==401
            new=client(); login(new,restarted)
            assert page(new,restarted,'/structure-draft?id='+ident)[0].select_one('#draft-restored')
        finally:
            server.shutdown(); thread.join(); server.server_close()


def test_later_block_page_and_multiple_targets_restore_only_the_selected_input(school_web):
    _,s,base=school_web; a=client(); login(a,base); p=svc.Principal('A','a')
    receipt=svc.upload(s,p,svc.IncomingPost('many','<img src=x onerror=1>\n합성 긴 문서',
        ''.join('<p>구역 '+str(i)+'</p>' for i in range(25))))
    svc.request_conversion(s,p,receipt.version_id); assert svc.process_conversions(s)==['done']
    v=review.view(s,p,receipt.version_id)
    first=draft_save(s,p,receipt.version_id,action={
        'kind':'block','block_id':v['analysis']['blocks'][0]['id'],'block_kind':'body','text':'first-draft-only'})
    last=draft_save(s,p,receipt.version_id,action={
        'kind':'block','block_id':v['analysis']['blocks'][24]['id'],'block_kind':'heading','text':'last-draft-only'})
    doc,_=page(a,base,'/structure-draft?id='+last)
    assert inputs(form(doc))['block_id']==v['analysis']['blocks'][20]['id']
    selected=next(f for f in doc.select('form[action="/structure-review"]')
        if f.select_one('[name=block_id][value="'+v['analysis']['blocks'][24]['id']+'"]'))
    assert inputs(selected)['text']=='last-draft-only'
    assert inputs(form(page(a,base,'/structure-draft?id='+first)[0]))['text']=='first-draft-only'
    directory,_=page(a,base,'/structure-drafts')
    assert not directory.select('img') and len(directory.select('[data-draft-resume]'))==2
    assert 'first-draft-only' not in directory.get_text() and 'last-draft-only' not in directory.get_text()
    # If original analysis becomes unavailable, saved input remains readable,
    # rather than disappearing or being pointed at a different conversion.
    s.db.execute("UPDATE mobile_revisions SET payload='{}' WHERE id=?",(v['original']['conversion_revision'],)); s.db.commit()
    assert page(a,base,'/structure-draft?id='+last)[0].select_one('#draft-readonly')


@pytest.mark.parametrize('query',['id=bad','id='+('a'*32)+'&id='+('a'*32),'id='+('a'*32)+'&subject=a',
    'id=%FF','page=0','page=2&page=3','page='+('9'*4100)])
def test_draft_get_rejects_ambiguous_or_malformed_query_without_writes(school_web,query):
    _,s,base=school_web; a=client(); login(a,base); before=list(s.db.iterdump())
    endpoint='/structure-drafts?' if query.startswith('page') else '/structure-draft?'
    assert fetch(a,base,endpoint+query)[0]==400
    assert list(s.db.iterdump())==before
