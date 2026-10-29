"""Immutable personal export lists, real HTTP resume and isolated migration."""
import concurrent.futures
import sqlite3
import threading
from http.server import ThreadingHTTPServer
from urllib.parse import urlencode

from bs4 import BeautifulSoup
import pytest

from moa import school_confirmed_export as batch, school_confirmed_export_saved as saved
from moa import school_review as review, school_review_export as export, school_service as svc
from moa.core import Store
from test_school_confirmed_export import confirmed, http_prepare, verify_zip, get_url
from test_school_service import schools
from test_school_structure import converted
from test_school_structure_review import save
from test_school_review_check import originals
from test_web_school import school_web, client, fetch, login


IDENT='saved_confirmed_export01'  # Exactly 24 URL-safe characters.


def url(ident): return '/confirmed-export/saved/preview?'+urlencode({'id':ident})


def page(raw): return BeautifulSoup(raw,'html.parser')


def inputs(form): return {e['name']:e.get('value','') for e in form.select('input[name]')}


def http_saved(a,base,csrf,items,name='합성 확인 완료본 묶음'):
    status,prepared,_,temporary=http_prepare(a,base,csrf,items);assert status==200
    fields=inputs(prepared.select_one('[data-export-save]'));fields['name']=name
    status,raw,headers=fetch(a,base,'/confirmed-export/save',fields=fields);assert status==200,raw.decode()
    doc=page(raw);download=inputs(doc.select_one('[data-export-download]'));download['acknowledge']='yes'
    return doc,download,temporary,fields


def other_rows(s):
    return [r for r in s.db.iterdump() if not r.startswith(('INSERT INTO "school_confirmed_export_lists"',
        'INSERT INTO "school_confirmed_export_items"'))]


def drop_v7(s):
    s.db.executescript('DROP TABLE IF EXISTS school_confirmed_export_list_events; DROP TABLE school_confirmed_export_items; DROP TABLE school_confirmed_export_lists;'
        ' DELETE FROM school_service_migrations WHERE version>=7;')


def test_frozen_order_titles_hashes_survive_a_new_connection_without_changing_existing_evidence(schools):
    _,s,p,_=schools;items=[confirmed(s,p,key='second'),confirmed(s,p)]
    pins=batch.prepare(s,p,items)['pins'];raw=batch.bundle(s,p,pins)
    previous=export.snapshot(s,p,items[0]['id'],revision_id=items[0]['revision'],event_id=items[0]['event'])
    old_html,old_zip=export.render_html(previous),export.bundle(s,previous)
    before,files=other_rows(s),originals(s)
    assert saved.create(s,p,IDENT,pins,name='  합성 묶음  ')==IDENT
    row=saved.get(s,p,IDENT)
    assert row['school']==p.school and row['subject']==p.subject and row['name']=='합성 묶음'
    assert row['pins']==pins and row['item_count']==2
    assert [r['id'] for r in row['items']]==[r['id'] for r in items]
    assert other_rows(s)==before and originals(s)==files
    snapshot=list(s.db.iterdump());changes=s.db.total_changes
    with Store(s.root) as reopened:
        assert saved.get(reopened,p,IDENT)==row
        assert batch.bundle(reopened,p,row['pins'])==raw
        assert saved.listing(reopened,p)['rows'][0]['id']==IDENT
    verify_zip(raw,s,p,items)
    assert list(s.db.iterdump())==snapshot and s.db.total_changes==changes and originals(s)==files
    assert export.render_html(previous)==old_html and export.bundle(s,previous)==old_zip
    assert s.db.execute('PRAGMA quick_check').fetchone()[0]=='ok'
    assert not s.db.execute('PRAGMA foreign_key_check').fetchall()


def test_exact_replay_and_concurrent_saves_create_one_list_without_renaming_or_reordering(schools):
    _,s,p,_=schools;items=[confirmed(s,p),confirmed(s,p,key='other')];pins=batch.prepare(s,p,items)['pins']
    def attempt(_):
        with Store(s.root) as other: return saved.create(other,p,IDENT,pins,name='한 번 저장')
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(attempt,range(2)))==[IDENT,IDENT]
    before=list(s.db.iterdump());changes=s.db.total_changes
    assert saved.create(s,p,IDENT,pins,name='한 번 저장')==IDENT
    for name,order in [('다른 이름',pins),('한 번 저장',list(reversed(pins)))]:
        with pytest.raises(review.Conflict):saved.create(s,p,IDENT,order,name=name)
    assert list(s.db.iterdump())==before and s.db.total_changes==changes
    assert s.db.execute('SELECT count(*) FROM school_confirmed_export_lists').fetchone()[0]==1


@pytest.mark.parametrize('table',['school_confirmed_export_lists','school_confirmed_export_items'])
@pytest.mark.parametrize('operation',['UPDATE','DELETE'])
def test_database_rejects_rewriting_or_deleting_saved_lists_and_pins(schools,table,operation):
    _,s,p,_=schools;pins=batch.prepare(s,p,[confirmed(s,p)])['pins'];saved.create(s,p,IDENT,pins,name='보존')
    before=list(s.db.iterdump())
    sql='UPDATE '+table+' SET '+('name=name' if table.endswith('lists') else 'title=title') if operation=='UPDATE' else 'DELETE FROM '+table
    with pytest.raises(sqlite3.IntegrityError):s.db.execute(sql)
    s.db.rollback();assert list(s.db.iterdump())==before


@pytest.mark.parametrize('damage',['foreign_school','wrong_review','draft_review','position','duplicate','hash'])
def test_database_enforces_scope_confirmation_position_uniqueness_and_hash(schools,damage):
    _,s,p,other=schools;item=confirmed(s,p);donor=confirmed(s,other)
    pins=batch.prepare(s,p,[item])['pins'];saved.create(s,p,IDENT,pins,name='범위')
    child=list(s.db.execute('SELECT * FROM school_confirmed_export_items').fetchone())
    if damage!='duplicate':
        parent=list(s.db.execute('SELECT * FROM school_confirmed_export_lists').fetchone());parent[0]='x'*24
        s.db.execute('INSERT INTO school_confirmed_export_lists VALUES(?,?,?,?,?,?)',parent);s.db.commit()
        child[0]=parent[0]
    if damage=='foreign_school':child[3:6]=[donor['id'],donor['revision'],donor['event']]
    elif damage=='wrong_review':child[5]=donor['event']
    elif damage=='draft_review':
        event=save(review,s,p,item['id'],{'kind':'reopen'});child[5]=event
    elif damage=='position':child[2]=2
    elif damage=='hash':child[6]='g'*64
    before=list(s.db.iterdump())
    with pytest.raises(sqlite3.IntegrityError):s.db.execute('INSERT INTO school_confirmed_export_items VALUES(?,?,?,?,?,?,?,?)',child)
    s.db.rollback();assert list(s.db.iterdump())==before


@pytest.mark.parametrize('name',['',' '*4,'x'*121,'bad\nname','bad\x00name','bad\x7fname',None,'\ud800'])
def test_bad_saved_names_never_write_rows(schools,name):
    _,s,p,_=schools;pins=batch.prepare(s,p,[confirmed(s,p)])['pins'];before=list(s.db.iterdump())
    with pytest.raises((ValueError,UnicodeError)):saved.create(s,p,IDENT,pins,name=name)
    assert list(s.db.iterdump())==before


@pytest.mark.parametrize('change',['reopen','reconfirm','edit','receipt','conversion','failed','processing','hash'])
def test_changed_version_blocks_saved_preview_and_download_but_keeps_the_saved_list(school_web,monkeypatch,change):
    _,s,base=school_web;p=svc.Principal('A','a');items=[confirmed(s,p),confirmed(s,p,key='two')]
    a=client();csrf=login(a,base);_,fields,_,_=http_saved(a,base,csrf,items)
    frozen=saved.get(s,p,fields['id']);item=items[1]
    if change in ('reopen','reconfirm','edit'):
        save(review,s,p,item['id'],{'kind':'reopen'})
        if change=='reconfirm':save(review,s,p,item['id'],{'kind':'confirm','acknowledge_warnings':True})
        elif change=='edit':
            block=next(b for b in review.view(s,p,item['id'])['analysis']['blocks'] if b['kind']=='body')
            save(review,s,p,item['id'],{'kind':'block','block_id':block['id'],'block_kind':'body','text':'새 합성 검수'})
    elif change=='receipt':converted(svc,s,p,key='two',price='16000')
    elif change=='conversion':svc.request_conversion(s,p,item['id']);assert svc.process_conversions(s)==['done']
    elif change in ('failed','processing'):
        s.db.execute('UPDATE mobile_notices SET state=? WHERE id=(SELECT mobile_id FROM school_service_versions WHERE id=?)',
            ('conversion_failed' if change=='failed' else 'processing',item['id']));s.db.commit()
    else:
        original=export.snapshot
        def different(*args,**kwargs):
            data=original(*args,**kwargs);data['analysis']['complete']=not data['analysis']['complete'];return data
        monkeypatch.setattr(export,'snapshot',different)
    before=list(s.db.iterdump());files=originals(s)
    for path,values in ((url(fields['id']),None),('/confirmed-export/saved/download',fields)):
        status,raw,headers=fetch(a,base,path,fields=values)
        assert status==409 and page(raw).select_one('#saved-export-blocked')
        assert not page(raw).select('[data-export-download]') and headers['Content-Type'].startswith('text/html')
    assert saved.get(s,p,fields['id'])==frozen
    assert page(fetch(a,base,'/confirmed-export/saved')[1]).select_one('[data-export-saved-resume]')
    assert list(s.db.iterdump())==before and originals(s)==files


@pytest.mark.parametrize('damage',['missing','changed','symlink'])
def test_original_damage_in_any_saved_document_returns_no_partial_zip(school_web,damage):
    _,s,base=school_web;p=svc.Principal('A','a');items=[confirmed(s,p),confirmed(s,p,key='two')]
    a=client();csrf=login(a,base);_,fields,_,_=http_saved(a,base,csrf,items)
    path=s.object_path(svc.get_version(s,p,items[1]['id'])['files'][0]['sha256']);raw=path.read_bytes()
    if damage=='changed':path.write_bytes(b'x'*len(raw))
    else:
        path.unlink()
        if damage=='symlink':
            other=s.root/'synthetic-original';other.write_bytes(raw);path.symlink_to(other)
    before=list(s.db.iterdump())
    status,body,headers=fetch(a,base,'/confirmed-export/saved/download',fields=fields)
    assert status==409 and not body.startswith(b'PK') and headers['Content-Type'].startswith('text/html')
    assert page(body).select_one('#saved-export-blocked') and list(s.db.iterdump())==before


def test_http_saved_bundle_survives_logout_and_expiring_preparations_with_fresh_acknowledgement(school_web):
    _,s,base=school_web;p=svc.Principal('A','a');items=[confirmed(s,p),confirmed(s,p,key='two')]
    a=client();csrf=login(a,base);before,files=other_rows(s),originals(s)
    old=export.snapshot(s,p,items[0]['id'],revision_id=items[0]['revision'],event_id=items[0]['event'])
    old_html,old_zip=export.render_html(old),export.bundle(s,old)
    doc,fields,temporary,save_fields=http_saved(a,base,csrf,items,name='<img src=x onerror=alert(1)> 합성 묶음')
    assert doc.select_one('[data-export-saved-name]').get_text()==save_fields['name'] and not doc.select('img,[onerror]')
    ack=doc.select_one('[name=acknowledge]');assert ack.has_attr('required') and not ack.has_attr('checked')
    status,raw,_=fetch(a,base,'/confirmed-export/saved/download',fields=fields);assert status==200
    verify_zip(raw,s,p,items);assert fetch(a,base,'/confirmed-export/download',fields=temporary)[1]==raw
    after=list(s.db.iterdump());assert fetch(a,base,'/confirmed-export/save',fields=save_fields)[0]==200
    assert list(s.db.iterdump())==after
    for _ in range(4):assert http_prepare(a,base,csrf,items)[0]==200
    assert fetch(a,base,get_url(temporary))[0]==404
    assert fetch(a,base,'/confirmed-export/saved/download',fields=fields)[1]==raw
    assert fetch(a,base,'/logout',fields={'csrf':csrf})[0]==200
    assert fetch(a,base,url(fields['id']))[0]==401
    fresh=client();fresh_csrf=login(fresh,base)
    assert page(fetch(fresh,base)[1]).select_one('[data-action=confirmed-export-saved]')
    status,listing,headers=fetch(fresh,base,'/confirmed-export/saved');assert status==200
    resume=page(listing).select_one('[data-export-saved-resume]')['href'];assert resume==url(fields['id'])
    resumed=page(fetch(fresh,base,resume)[1]);assert not resumed.select_one('[name=acknowledge]').has_attr('checked')
    current=inputs(resumed.select_one('[data-export-download]'))
    assert current['csrf']==fresh_csrf and fresh_csrf!=csrf
    assert fetch(fresh,base,'/confirmed-export/saved/download',fields={k:v for k,v in current.items() if k!='acknowledge'})[0]==400
    assert fetch(fresh,base,'/confirmed-export/saved/download',fields=fields)[0]==403
    assert fetch(fresh,base,'/confirmed-export/saved/download',fields={**current,'acknowledge':'yes'})[1]==raw
    assert 'no-store' in headers['Cache-Control'] and "script-src 'self'" in headers['Content-Security-Policy']
    assert list(s.db.iterdump())==after and other_rows(s)==before and originals(s)==files
    assert export.render_html(old)==old_html and export.bundle(s,old)==old_zip


def test_restart_without_any_sessions_can_resume_and_download_from_a_separate_store(school_web):
    web,s,base=school_web;p=svc.Principal('A','a');items=[confirmed(s,p)]
    a=client();csrf=login(a,base);_,fields,_,_=http_saved(a,base,csrf,items)
    expected=fetch(a,base,'/confirmed-export/saved/download',fields=fields)[1];before=list(s.db.iterdump())
    with Store(s.root,thread_safe=True) as reopened:
        users={'a':dict(school='A',subject='a',**web.password_record('synthetic-a-only'))}
        handler=web.make_handler(reopened,users);assert not handler.sessions
        server=ThreadingHTTPServer(('127.0.0.1',0),handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            address='http://127.0.0.1:'+str(server.server_port);fresh=client();login(fresh,address)
            listing=page(fetch(fresh,address,'/confirmed-export/saved')[1])
            doc=page(fetch(fresh,address,listing.select_one('[data-export-saved-resume]')['href'])[1])
            download=inputs(doc.select_one('[data-export-download]'));download['acknowledge']='yes'
            assert fetch(fresh,address,'/confirmed-export/saved/download',fields=download)[1]==expected
        finally:server.shutdown();thread.join();server.server_close()
    assert list(s.db.iterdump())==before


def test_lists_are_owner_private_and_readers_cannot_save_but_demoted_owners_can_still_download(school_web):
    _,s,base=school_web;p=svc.Principal('A','a');items=[confirmed(s,p)]
    a,b,reader,same=client(),client(),client(),client();csrf=login(a,base)
    csrf_b=login(b,base,'b','synthetic-b-only');csrf_r=login(reader,base,'reader','synthetic-reader-only');login(same,base)
    _,fields,_,_=http_saved(a,base,csrf,items);before=list(s.db.iterdump())
    for other,token in ((b,csrf_b),(reader,csrf_r)):
        assert fetch(other,base,url(fields['id']))[0]==404
        assert fetch(other,base,'/confirmed-export/saved/download',fields={**fields,'csrf':token})[0]==404
        assert not page(fetch(other,base,'/confirmed-export/saved')[1]).select('[data-export-saved-resume]')
    assert fetch(same,base,url(fields['id']))[0]==200
    status,prepared,_,temp=http_prepare(reader,base,csrf_r,items);assert status==200
    assert not prepared.select_one('[data-export-save]')
    assert fetch(reader,base,'/confirmed-export/save',fields={'csrf':csrf_r,'bundle':temp['bundle'],'name':'금지'})[0]==403
    assert list(s.db.iterdump())==before
    svc.grant_member(s,'A','a','reader')
    assert fetch(a,base,url(fields['id']))[0]==200
    assert fetch(a,base,'/confirmed-export/saved/download',fields=fields)[0]==200
    assert not http_prepare(a,base,csrf,items)[1].select_one('[data-export-save]')
    svc.grant_member(s,'A','a','uploader',active=False)
    assert fetch(a,base,url(fields['id']))[0]==401
    assert fetch(a,base,'/confirmed-export/saved/download',fields=fields)[0]==401


def test_same_school_other_writer_cannot_read_or_collide_with_the_saved_list(schools):
    _,s,p,_=schools;items=[confirmed(s,p)];pins=batch.prepare(s,p,items)['pins'];saved.create(s,p,IDENT,pins,name='개인')
    svc.grant_member(s,p.school,'second-staff','uploader');other=svc.Principal(p.school,'second-staff');before=list(s.db.iterdump())
    assert saved.listing(s,other)['rows']==[]
    with pytest.raises(LookupError):saved.get(s,other,IDENT)
    with pytest.raises(LookupError):saved.create(s,other,IDENT,pins,name='개인')
    assert list(s.db.iterdump())==before


@pytest.mark.parametrize('path,query',[
    ('/confirmed-export/saved','page=0'),('/confirmed-export/saved','page=1&page=2'),
    ('/confirmed-export/saved','school=A'),('/confirmed-export/saved','page=%FF'),
    ('/confirmed-export/saved','page='),('/confirmed-export/saved','page=1000001'),
    ('/confirmed-export/saved/preview',''),('/confirmed-export/saved/preview','id='),
    ('/confirmed-export/saved/preview','id=bad'),('/confirmed-export/saved/preview','id=%FF'),
    ('/confirmed-export/saved/preview','id='+IDENT+'&id='+IDENT),
    ('/confirmed-export/saved/preview','id='+IDENT+'&school=A'),
    ('/confirmed-export/saved/preview','id='+('x'*4097)),
])
def test_saved_queries_are_strict_bounded_and_read_only(school_web,path,query):
    _,s,base=school_web;a=client();login(a,base);before=list(s.db.iterdump())
    assert fetch(a,base,path+'?'+query)[0]==400 and list(s.db.iterdump())==before


@pytest.mark.parametrize('endpoint',['/confirmed-export/save','/confirmed-export/saved/download'])
@pytest.mark.parametrize('invalid',['extra','duplicate','blank_extra','blank_duplicate','missing','csrf','origin','utf8','oversize'])
def test_persistent_posts_reject_ambiguous_or_unauthorized_input_without_writes(school_web,endpoint,invalid):
    _,s,base=school_web;p=svc.Principal('A','a');a=client();csrf=login(a,base)
    _,download,_,create=http_saved(a,base,csrf,[confirmed(s,p)])
    values=list((create if endpoint.endswith('/save') else download).items());headers={};expected=400
    if invalid=='extra':values.append(('school','B'))
    elif invalid=='duplicate':values.append(values[-1])
    elif invalid=='blank_extra':values.append(('school',''))
    elif invalid=='blank_duplicate':values.append((values[-1][0],''))
    elif invalid=='missing':values.pop()
    elif invalid=='csrf':values=[(k,'wrong' if k=='csrf' else v) for k,v in values];expected=403
    elif invalid=='origin':headers['Origin']='https://untrusted.invalid';expected=403
    raw=urlencode(values).encode()
    if invalid=='utf8':raw+=b'&unexpected=%FF'
    elif invalid=='oversize':raw+=b'&unexpected='+b'x'*16384;expected=413
    before=list(s.db.iterdump())
    status,body,_=fetch(a,base,endpoint,data=raw,headers=headers)
    assert status==expected and not body.startswith(b'PK') and list(s.db.iterdump())==before


def test_listing_is_paginated_and_escapes_names_without_inspecting_or_changing_documents(school_web,monkeypatch):
    _,s,base=school_web;p=svc.Principal('A','a');other=svc.Principal('B','b');pins=batch.prepare(s,p,[confirmed(s,p)])['pins']
    for i in range(21):saved.create(s,p,f'{i:024d}',pins,name=f'<img src=x> 합성 {i}')
    def forbidden(*args,**kwargs):raise AssertionError('Directory should read bounded metadata only')
    monkeypatch.setattr(batch,'prepare',forbidden);before=list(s.db.iterdump())
    first,second=saved.listing(s,p),saved.listing(s,p,page=2)
    assert first['total']==21 and first['pages']==2 and len(first['rows'])==20 and len(second['rows'])==1
    assert {r['id'] for r in first['rows']}.isdisjoint(r['id'] for r in second['rows'])
    assert saved.listing(s,p,page=100)['rows']==second['rows']
    assert saved.listing(s,other)['rows']==[]
    a=client();login(a,base)
    first_page=page(fetch(a,base,'/confirmed-export/saved')[1]);second_page=page(fetch(a,base,'/confirmed-export/saved?page=2')[1])
    assert len(first_page.select('[data-export-saved-resume]'))==20 and len(second_page.select('[data-export-saved-resume]'))==1
    assert '<img src=x>' in first_page.get_text() and not first_page.select('img')
    assert first_page.select_one('nav[aria-label] a')['href']=='/confirmed-export/saved?page=2'
    assert list(s.db.iterdump())==before


def test_save_rechecks_pins_inside_its_transaction_if_another_process_changes_the_loaded_selection(school_web,monkeypatch):
    from moa import school_confirmed_export_web
    _,s,base=school_web;p=svc.Principal('A','a');item=confirmed(s,p);a=client();csrf=login(a,base)
    status,prepared,_,_=http_prepare(a,base,csrf,[item]);assert status==200
    fields=inputs(prepared.select_one('[data-export-save]'));original=school_confirmed_export_web._load
    def interleave(*args,**kwargs):
        result=original(*args,**kwargs)
        with Store(s.root) as other:save(review,other,p,item['id'],{'kind':'reopen'})
        return result
    monkeypatch.setattr(school_confirmed_export_web,'_load',interleave)
    before,files=other_rows(s),originals(s)
    assert fetch(a,base,'/confirmed-export/save',fields=fields)[0]==409
    assert not s.db.execute('SELECT 1 FROM school_confirmed_export_lists').fetchone()
    assert not s.db.execute('SELECT 1 FROM school_confirmed_export_items').fetchone()
    assert originals(s)==files
    assert [r for r in other_rows(s) if not r.startswith('INSERT INTO "school_structure_reviews"')]==[
        r for r in before if not r.startswith('INSERT INTO "school_structure_reviews"')]


def test_ten_saved_documents_keep_the_existing_download_limits_and_never_return_a_partial_archive(school_web,monkeypatch):
    _,s,base=school_web;p=svc.Principal('A','a');items=[confirmed(s,p,key=str(i)) for i in range(10)]
    a=client();csrf=login(a,base);_,fields,_,_=http_saved(a,base,csrf,items)
    assert saved.get(s,p,fields['id'])['item_count']==10
    status,raw,_=fetch(a,base,'/confirmed-export/saved/download',fields=fields)
    assert status==200 and len(verify_zip(raw,s,p,items)['documents'])==10
    monkeypatch.setattr(batch,'MAX_CONTENT_BYTES',1);before=list(s.db.iterdump())
    status,raw,headers=fetch(a,base,'/confirmed-export/saved/download',fields=fields)
    assert status==400 and headers['Content-Type'].startswith('text/html') and not raw.startswith(b'PK')
    assert list(s.db.iterdump())==before


def test_damaged_incomplete_saved_list_is_not_refreshed_or_downloaded(school_web):
    _,s,base=school_web;p=svc.Principal('A','a');a=client();csrf=login(a,base)
    _,fields,_,_=http_saved(a,base,csrf,[confirmed(s,p),confirmed(s,p,key='two')])
    # Damage only the isolated fixture, simulating a missing persisted pin.
    s.db.executescript('DROP TRIGGER school_confirmed_export_items_no_delete; DELETE FROM school_confirmed_export_items WHERE position=2;')
    before=list(s.db.iterdump())
    assert fetch(a,base,url(fields['id']))[0]==409
    status,raw,_=fetch(a,base,'/confirmed-export/saved/download',fields=fields)
    assert status==409 and not raw.startswith(b'PK') and list(s.db.iterdump())==before


def test_v7_migration_is_explicit_backed_up_additive_repeatable_and_preserves_core_version(schools):
    _,s,p,_=schools;pins=batch.prepare(s,p,[confirmed(s,p)])['pins'];drop_v7(s)
    before=list(s.db.iterdump());files=originals(s);version=s.db.execute('PRAGMA user_version').fetchone()[0]
    assert saved.listing(s,p)['migration_required']
    with pytest.raises(review.Conflict):saved.create(s,p,IDENT,pins,name='명시적 설치 전')
    assert list(s.db.iterdump())==before
    backup=svc.migrate(s)
    with sqlite3.connect(backup) as old:
        assert list(old.iterdump())==before
        assert old.execute('SELECT max(version) FROM school_service_migrations').fetchone()[0]==6
    assert set(before)<=set(s.db.iterdump()) and originals(s)==files
    assert s.db.execute('PRAGMA user_version').fetchone()[0]==version and saved.available(s)
    assert not s.db.execute('PRAGMA foreign_key_check').fetchall()
    after=list(s.db.iterdump());assert svc.migrate(s) is None and list(s.db.iterdump())==after
    saved.create(s,p,IDENT,pins,name='설치 후');assert saved.get(s,p,IDENT)['pins']==pins


def test_failed_migration_rolls_back_and_unknown_versions_are_not_repaired(schools,monkeypatch):
    _,s,p,_=schools;confirmed(s,p);drop_v7(s);before=list(s.db.iterdump());files=originals(s)
    monkeypatch.setattr(saved,'SCHEMA_V7',saved.SCHEMA_V7+'\n INVALID SYNTHETIC DDL;')
    with pytest.raises(sqlite3.Error):svc.migrate(s)
    assert list(s.db.iterdump())==before and not saved.available(s) and originals(s)==files
    s.db.execute("INSERT INTO school_service_migrations VALUES(999,'synthetic')");s.db.commit()
    before=list(s.db.iterdump());backups=list((s.root/'db/backups').iterdir())
    with pytest.raises(RuntimeError):svc.migrate(s)
    assert list(s.db.iterdump())==before and list((s.root/'db/backups').iterdir())==backups


def test_http_without_v7_never_migrates_and_existing_temporary_download_keeps_working(school_web):
    _,s,base=school_web;p=svc.Principal('A','a');items=[confirmed(s,p)];drop_v7(s)
    a=client();csrf=login(a,base);before=list(s.db.iterdump());files=originals(s)
    status,prepared,_,fields=http_prepare(a,base,csrf,items);assert status==200
    assert not prepared.select_one('[data-export-save]') and '명시적 마이그레이션' in prepared.get_text()
    listing=page(fetch(a,base,'/confirmed-export/saved')[1]);assert '명시적 마이그레이션' in listing.get_text()
    assert fetch(a,base,'/confirmed-export/save',fields={'csrf':csrf,'bundle':fields['bundle'],'name':'설치 전'})[0]==409
    assert fetch(a,base,'/confirmed-export/download',fields=fields)[0]==200
    assert list(s.db.iterdump())==before and originals(s)==files
