"""Persistent private review lists over isolated SQLite and real HTTP only."""
import sqlite3
import threading
from http.server import ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit, urlencode

from bs4 import BeautifulSoup
import pytest

from moa import school_saved_queue as saved, school_service as svc, school_review as review
from moa.core import Store, digest
from test_school_structure import converted
from test_school_structure_review import save

from test_school_review_queue import school_web, client, fetch, login, start, fields, pair, page


def form_fields(doc, action):
    form = doc.select_one('form[action="'+action+'"]')
    assert form is not None, 'Persistent review-list control is missing: '+action
    return {e['name']:e.get('value','') for e in form.select('input[name]')}


def save_list(opener, base, doc, name='합성 이어서 검수'):
    values = form_fields(doc, '/review-queue/save')
    values['name'] = name
    status, body, headers = fetch(opener, base, '/review-queue/save', fields=values)
    assert status == 200, body.decode()
    return BeautifulSoup(body, 'html.parser'), headers


def move(opener, base, doc, target):
    values = form_fields(doc, '/review-saved/move')
    values['target'] = str(target)
    return fetch(opener, base, '/review-saved/move', fields=values)


def test_saved_order_and_position_survive_logout_and_a_fresh_login(school_web):
    _, s, base = school_web
    a = client(); csrf = login(a, base); pair(s)
    ephemeral, _ = start(a, base, status='unreviewed')
    first = fields(ephemeral, 'confirm')['id']
    doc, _ = save_list(a, base, ephemeral)
    assert '저장한 목록' in doc.select_one('#review-queue').get_text()
    assert fields(doc, 'confirm')['id'] == first
    status, body, _ = fetch(a, base, '/structure-review', fields=fields(doc, 'confirm'))
    assert status == 200
    doc = BeautifulSoup(body, 'html.parser')
    status, body, _ = move(a, base, doc, 2)
    assert status == 200
    second = BeautifulSoup(body, 'html.parser')
    second_id = fields(second, 'confirm')['id']
    assert second_id != first and '2 / 2' in second.select_one('#review-queue').get_text()
    saved_url = second.select_one('[data-queue=current]')['href']
    ident = parse_qs(urlsplit(saved_url).query)['id'][0]
    assert fetch(a, base, '/logout', fields={'csrf':csrf})[0] == 200
    fresh = client(); login(fresh, base)
    directory, headers = page(fresh, base, '/review-saved')
    assert '합성 이어서 검수' in directory.get_text()
    resume = directory.select_one('[data-saved-resume]')['href']
    assert ident in resume
    resumed, _ = page(fresh, base, resume)
    assert fields(resumed, 'confirm')['id'] == second_id
    assert '2 / 2' in resumed.select_one('#review-queue').get_text()
    # Confirmation removed the first row from the live filter, not the frozen list.
    status, body, _ = move(fresh, base, resumed, 1)
    assert status == 200
    assert fields(BeautifulSoup(body,'html.parser'),'reopen')['id'] == first
    assert 'no-store' in headers['Cache-Control']


def test_separate_store_and_restarted_handler_resume_from_disk(school_web):
    web, s, base = school_web
    a = client(); login(a,base); pair(s)
    doc,_ = save_list(a,base,start(a,base)[0])
    status,body,_ = move(a,base,doc,2); assert status==200
    doc = BeautifulSoup(body,'html.parser')
    current = fields(doc,'confirm')['id']
    # A new SQLite connection and handler have neither the login nor queue map.
    with Store(s.root,thread_safe=True) as reopened:
        users = {'a':dict(school='A',subject='a',**web.password_record('synthetic-a-only'))}
        server = ThreadingHTTPServer(('127.0.0.1',0),web.make_handler(reopened,users))
        thread = threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        try:
            fresh_base = 'http://127.0.0.1:'+str(server.server_port)
            fresh = client(); login(fresh,fresh_base)
            directory,_ = page(fresh,fresh_base,'/review-saved')
            resumed,_ = page(fresh,fresh_base,directory.select_one('[data-saved-resume]')['href'])
            assert fields(resumed,'confirm')['id']==current
            assert '2 / 2' in resumed.select_one('#review-queue').get_text()
        finally:
            server.shutdown(); thread.join(); server.server_close()


def test_saved_lists_are_owner_private_and_live_membership_is_required(school_web):
    _,s,base = school_web
    a,b,reader,same = client(),client(),client(),client()
    login(a,base); csrf_b=login(b,base,'b','synthetic-b-only')
    csrf_r=login(reader,base,'reader','synthetic-reader-only'); login(same,base)
    pair(s); doc,_=save_list(a,base,start(a,base)[0])
    path=doc.select_one('[data-queue=current]')['href']
    update=form_fields(doc,'/review-saved/move'); update['target']='2'
    assert fetch(client(),base,path)[0]==401
    assert fetch(same,base,path)[0]==200
    before=list(s.db.iterdump())
    for other,csrf in ((b,csrf_b),(reader,csrf_r)):
        assert fetch(other,base,path)[0]==404
        assert not page(other,base,'/review-saved')[0].select('[data-saved-resume]')
        assert fetch(other,base,'/review-saved/move',fields={**update,'csrf':csrf})[0] in (403,404)
    read_queue,_=start(reader,base)
    assert read_queue.select_one('form[action="/review-queue/save"]') is None
    assert fetch(reader,base,'/review-queue/save',fields={
        'csrf':csrf_r,'queue_id':parse_qs(urlsplit(read_queue.select_one('[data-queue=current]')['href']).query)['queue'][0],
        'queue_item':'1','name':'reader cannot save'})[0]==403
    assert list(s.db.iterdump())==before
    svc.grant_member(s,'A','a','reader')
    demoted,_=page(a,base,path)
    assert not demoted.select('form[action="/structure-review"],form[action="/review-saved/move"]')
    assert fetch(a,base,'/review-saved/move',fields=update)[0]==403
    svc.grant_member(s,'A','a','uploader',active=False)
    assert fetch(a,base,path)[0]==fetch(a,base,'/review-saved/move',fields=update)[0]==401


def test_gets_never_move_checkpoint_and_old_tabs_cannot_reset_it(school_web):
    _,s,base=school_web
    a=client(); login(a,base); pair(s)
    ephemeral,_=start(a,base); original_save=form_fields(ephemeral,'/review-queue/save')
    doc,_=save_list(a,base,ephemeral)
    path=doc.select_one('[data-queue=current]')['href']
    before=list(s.db.iterdump())
    for url in (path,path+'&block_page=1',path.replace('item=1','item=2'),'/review-saved','/review-saved/lists?page=1'):
        assert fetch(a,base,url)[0]==200
    assert list(s.db.iterdump())==before
    assert move(a,base,doc,2)[0]==200
    after=list(s.db.iterdump())
    # Same move from a stale tab/replayed POST is rejected, not last-writer-wins.
    assert move(a,base,doc,1)[0]==409
    assert list(s.db.iterdump())==after
    assert fetch(a,base,'/review-queue/save',fields={**original_save,'name':'do not rename or reset'})[0]==200
    assert list(s.db.iterdump())==after
    row=s.db.execute('SELECT * FROM school_review_lists').fetchone()
    assert row['cursor']==2 and row['version']==2 and row['name']=='합성 이어서 검수'


@pytest.mark.parametrize('query',[
    'school=B','id=bad','id='+('x'*24)+'&id='+('x'*24),
    'id='+('x'*24)+'&item=0','id='+('x'*24)+'&item=%FF','page=0',
    'id='+('x'*24)+'&subject=a','id='+('x'*4100),
])
def test_saved_get_rejects_malformed_scope_and_query_without_writes(school_web,query):
    _,s,base=school_web
    a=client(); login(a,base)
    before=list(s.db.iterdump())
    assert fetch(a,base,'/review-saved?'+query)[0]==400
    assert list(s.db.iterdump())==before


def test_saved_posts_reject_csrf_origin_duplicates_and_document_context_forgery(school_web):
    _,s,base=school_web
    a=client(); login(a,base); pair(s)
    ephemeral,_=start(a,base); values=form_fields(ephemeral,'/review-queue/save')
    doc,_=save_list(a,base,ephemeral); form=fields(doc,'confirm')
    update=form_fields(doc,'/review-saved/move'); update['target']='2'
    before=list(s.db.iterdump())
    for edits,code in (({'csrf':'wrong'},403),({'target':'21'},400),({'school':'B'},400),
                       ({'target':'0'},400),({'expected_version':'-1'},400),({'expected_version':'9'*20},400)):
        assert fetch(a,base,'/review-saved/move',fields={**update,**edits})[0]==code
    assert fetch(a,base,'/review-saved/move',fields=update,headers={'Origin':'https://foreign.example.test'})[0]==403
    assert fetch(a,base,'/review-saved/move',data=(urlencode(update)+'&target=1').encode())[0]==400
    assert fetch(a,base,'/review-queue/save',fields={**values,'name':''})[0]==400
    assert fetch(a,base,'/review-queue/save',fields={**values,'name':'x'*121})[0]==400
    assert fetch(a,base,'/review-queue/save',fields={**values,'name':'bad\x00name'})[0]==400
    assert fetch(a,base,'/review-queue/save',data=(urlencode(values)+'&name=duplicate').encode())[0]==400
    for edits,code in (({'saved_item':'2'},400),({'saved_id':'x'*24},404),({'saved_item':'0'},400),
                       ({'queue_id':'x'*24,'queue_item':'1'},400),({'revision':'0'*32},400)):
        assert fetch(a,base,'/structure-review',fields={**form,**edits})[0]==code
    assert list(s.db.iterdump())==before
    assert fetch(a,base,'/structure-review',fields=form)[0]==200
    after=list(s.db.iterdump())
    assert fetch(a,base,'/structure-review',fields=form)[0]==409
    assert list(s.db.iterdump())==after


@pytest.mark.parametrize('change',['receipt','conversion','failed','broken'])
def test_pinned_saved_items_stay_old_readonly_or_recoverable(change,school_web):
    _,s,base=school_web
    a=client(); login(a,base); principal=svc.Principal('A','a')
    pair(s); doc,_=save_list(a,base,start(a,base)[0])
    form=fields(doc,'confirm'); ident=form['id']; revision=form['revision']
    path=doc.select_one('[data-queue=current]')['href']
    version=svc.get_version(s,principal,ident)
    if change=='receipt':
        svc.upload(s,principal,svc.IncomingPost(version['post_key'],'합성 정정','<p>새 수신본</p>'))
    elif change=='conversion':
        svc.request_conversion(s,principal,ident); assert svc.process_conversions(s)==['done']
    elif change=='failed':
        s.db.execute("UPDATE mobile_notices SET state='conversion_failed' WHERE id=?",(version['mobile_id'],)); s.db.commit()
    else:
        s.db.execute("UPDATE mobile_revisions SET payload='{}' WHERE id=?",(revision,)); s.db.commit()
    before=list(s.db.iterdump())
    status,body,_=fetch(a,base,path)
    expected=404 if change=='broken' else 200
    assert status==expected
    opened=BeautifulSoup(body,'html.parser')
    assert not opened.select('form[action="/structure-review"]')
    assert opened.select_one('form[action="/review-saved/move"]')
    if change!='broken': assert revision in opened.get_text() and '읽기 전용' in opened.get_text()
    assert fetch(a,base,'/structure-review',fields=form)[0] in (409,404)
    assert list(s.db.iterdump())==before
    assert move(a,base,opened,2)[0]==200
    assert s.db.execute('SELECT cursor FROM school_review_lists').fetchone()[0]==2


def test_saved_list_pagination_names_xss_and_twenty_item_boundary(school_web):
    _,s,base=school_web
    a=client(); login(a,base); principal,_=pair(s)
    first=None
    for i in range(21):
        doc,_=start(a,base)
        doc,_=save_list(a,base,doc,name='<img src=x onerror=alert(1)> 합성 '+str(i))
        if first is None: first=doc.select_one('[data-queue=current]')['href']
    listing,headers=page(a,base,'/review-saved')
    assert len(listing.select('article'))==20
    assert '<img' in listing.get_text() and not listing.select('img, [onerror]')
    assert "script-src 'self'" in headers['Content-Security-Policy']
    second,_=page(a,base,listing.select_one('a[rel=next]')['href'])
    assert len(second.select('article'))==1 and second.select_one('a[rel=prev]')
    assert fetch(a,base,first)[0]==200  # The 3-memory-list limit does not evict saved lists.
    for i in range(19): converted(svc,s,principal,key='more-'+str(i))
    doc,_=save_list(a,base,start(a,base)[0]); ident=parse_qs(urlsplit(doc.select_one('[data-queue=current]')['href']).query)['id'][0]
    assert saved.get(s,principal,ident)['item_count']==20
    assert move(a,base,doc,20)[0]==200
    assert saved.get(s,principal,ident)['cursor']==20


def test_explicit_v3_migration_keeps_v2_rows_and_consistent_backup(tmp_path):
    with Store(tmp_path) as s:
        s.db.executescript(svc.SCHEMA+review.SCHEMA_V2)
        s.db.executemany('INSERT INTO school_service_migrations VALUES(?,?)',[(1,'synthetic'),(2,'synthetic')]); s.db.commit()
        svc.configure_school(s,'A','합성학교'); svc.grant_member(s,'A','a','uploader')
        principal=svc.Principal('A','a'); receipt,_=converted(svc,s,principal)
        save(review,s,principal,receipt.version_id,{'kind':'confirm','acknowledge_warnings':True})
        before=[tuple(r) for r in s.db.execute('SELECT * FROM school_structure_reviews')]
        originals={str(p):(digest(p.read_bytes()),p.stat().st_mtime_ns) for folder in ('objects','extracted')
            for p in (s.root/folder).rglob('*') if p.is_file()}
        assert not saved.available(s)
        assert saved.listing(s,principal)['migration_required'] is True
        backup=svc.migrate(s)
        assert backup.parent==tmp_path/'db/backups'
        with sqlite3.connect(backup) as old:
            assert old.execute('SELECT max(version) FROM school_service_migrations').fetchone()[0]==2
            assert old.execute('SELECT * FROM school_structure_reviews').fetchall()==before
            assert not old.execute("SELECT 1 FROM sqlite_master WHERE name='school_review_lists'").fetchone()
        assert [tuple(r) for r in s.db.execute('SELECT * FROM school_structure_reviews')]==before
        assert svc.migrate(s) is None and saved.available(s)
        assert s.db.execute('PRAGMA foreign_key_check').fetchall()==[]
        assert s.db.execute('PRAGMA quick_check').fetchone()[0]=='ok'
        assert s.db.execute('PRAGMA user_version').fetchone()[0]==5
        assert {str(p):(digest(p.read_bytes()),p.stat().st_mtime_ns) for folder in ('objects','extracted')
            for p in (s.root/folder).rglob('*') if p.is_file()}==originals


def test_old_v2_http_get_and_save_do_not_install_schema(school_web):
    _,s,base=school_web
    a=client(); login(a,base); pair(s)
    # Synthetic fixture only: simulate a pre-v3 DB before creating any saved list.
    s.db.executescript('DROP TABLE school_review_list_items; DROP TABLE school_review_lists; DELETE FROM school_service_migrations WHERE version=3;')
    before=list(s.db.iterdump()); doc,_=start(a,base)
    assert '명시적 마이그레이션' in doc.get_text()
    assert not doc.select_one('form[action="/review-queue/save"]')
    listing,_=page(a,base,'/review-saved'); assert '명시적 마이그레이션' in listing.get_text()
    query=parse_qs(urlsplit(doc.select_one('[data-queue=current]')['href']).query)
    csrf=doc.select_one('input[name=csrf]')['value']
    assert fetch(a,base,'/review-queue/save',fields={
        'csrf':csrf,'queue_id':query['queue'][0],'queue_item':'1','name':'not installed'})[0]==409
    assert list(s.db.iterdump())==before


def test_list_scope_constraints_and_no_original_or_audit_mutations(school_web):
    _,s,base=school_web
    a=client(); login(a,base); principal,_=pair(s)
    receipt=svc.list_notices(s,principal)[0]['latest_version']
    save(review,s,principal,receipt,{'kind':'confirm','acknowledge_warnings':True})
    originals={str(p):(digest(p.read_bytes()),p.stat().st_mtime_ns) for folder in ('objects','extracted')
        for p in (s.root/folder).rglob('*') if p.is_file()}
    conversions=[tuple(r) for r in s.db.execute('SELECT * FROM mobile_revisions ORDER BY id')]
    history=[tuple(r) for r in s.db.execute('SELECT * FROM school_structure_reviews')]
    other_db=lambda:[line for line in s.db.iterdump() if not line.startswith(('INSERT INTO "school_review_lists"','INSERT INTO "school_review_list_items"'))]
    before=other_db()
    doc,_=save_list(a,base,start(a,base)[0]); assert move(a,base,doc,2)[0]==200
    assert other_db()==before
    assert [tuple(r) for r in s.db.execute('SELECT * FROM mobile_revisions ORDER BY id')]==conversions
    assert [tuple(r) for r in s.db.execute('SELECT * FROM school_structure_reviews')]==history
    assert {str(p):(digest(p.read_bytes()),p.stat().st_mtime_ns) for folder in ('objects','extracted')
        for p in (s.root/folder).rglob('*') if p.is_file()}==originals
    assert not s.db.execute('SELECT 1 FROM mobile_reviews').fetchone()
    assert not s.db.execute("SELECT 1 FROM jobs WHERE status IN ('pending','running')").fetchone()
    frozen=list(s.db.iterdump())
    for sql in ("UPDATE school_review_list_items SET position=20", "DELETE FROM school_review_list_items",
                "UPDATE school_review_lists SET subject='reader'", "DELETE FROM school_review_lists"):
        with pytest.raises(sqlite3.IntegrityError): s.db.execute(sql)
        s.db.rollback(); assert list(s.db.iterdump())==frozen


def test_service_rejects_cross_school_item_without_partial_list(school_web):
    _,s,base=school_web
    principal,ids=pair(s)
    b=svc.Principal('B','b'); foreign,ver=converted(svc,s,b)
    snapshot={'school':'A','subject':'a','query':{'q':'','status':'','page':1},
        'items':[{'id':foreign.version_id,'revision':ver['revision'],'title':'합성 외부학교'}]}
    before=list(s.db.iterdump())
    with pytest.raises(LookupError): saved.create(s,principal,'x'*24,snapshot,name='합성',cursor=1)
    assert list(s.db.iterdump())==before


def test_concurrent_checkpoints_from_two_connections_have_one_winner(school_web):
    _,s,base=school_web
    a=client(); login(a,base); principal,_=pair(s)
    doc,_=save_list(a,base,start(a,base)[0])
    ident=parse_qs(urlsplit(doc.select_one('[data-queue=current]')['href']).query)['id'][0]
    with Store(s.root,thread_safe=True) as second:
        barrier=threading.Barrier(2); results=[]
        def checkpoint(store,target):
            barrier.wait(timeout=5)
            try:
                saved.move(store,principal,ident,target=target,expected_version=1)
                results.append('saved')
            except review.Conflict: results.append('conflict')
        threads=[threading.Thread(target=checkpoint,args=(store,target)) for store,target in ((s,1),(second,2))]
        for thread in threads: thread.start()
        for thread in threads: thread.join(timeout=10); assert not thread.is_alive()
        assert sorted(results)==['conflict','saved']
        assert saved.get(s,principal,ident)['version']==2
