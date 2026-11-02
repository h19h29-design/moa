"""Synthetic, isolated SQLite and loopback HTTP; no models or operational data."""
import concurrent.futures
import importlib
import importlib.util
import json
import sqlite3

import pytest

from moa import mobile, school_service as svc
from moa.core import Store, digest
from test_school_service import schools
from test_school_structure import converted
from test_web_school import school_web, client, fetch, login


def review_module():
    assert importlib.util.find_spec('moa.school_review'), 'School structure review service is missing'
    return importlib.import_module('moa.school_review')


def save(r, s, principal, ident, action, *, view=None, comment='합성 담당자 확인 사유'):
    v = view or r.view(s, principal, ident)
    return r.save(s, principal, ident, revision_id=v['original']['conversion_revision'],
                  base_hash=v['base_hash'], expected_review=v['latest']['id'] if v['latest'] else '',
                  action=action, comment=comment)


def test_review_preserves_automatic_revision_originals_and_records_before_after(schools):
    m, s, a, _ = schools
    receipt, version = converted(m, s, a)
    r = review_module()
    original = m.structure_for(s, a, receipt.version_id)
    payload = mobile.revision(s, version['mobile_id'], school=True)['payload']
    block = next(b for b in original['blocks'] if b['kind'] == 'body')
    rid = save(r, s, a, receipt.version_id,
        {'kind':'block', 'block_id':block['id'], 'block_kind':'heading', 'text':'수정한 합성 제목'})
    result = r.view(s, a, receipt.version_id)
    edited = next(b for b in result['analysis']['blocks'] if b['id'] == block['id'])
    assert edited['kind'] == 'heading' and edited['text'] == '수정한 합성 제목'
    assert edited['locator'] == block['locator'] and edited['sha256'] == block['sha256']
    assert result['status'] == 'draft' and result['latest']['id'] == rid
    event = r.history(s, a, receipt.version_id, revision_id=version['revision'])['events'][0]
    assert event['actor'] == a.subject and event['created_at'] and event['sequence'] == 1
    assert event['changes'] == [{'target':'block', 'id':block['id'],
        'before':{'kind':'body','text':block['text']}, 'after':{'kind':'heading','text':'수정한 합성 제목'}},
        {'target':'status','before':'unreviewed','after':'draft'}]
    assert m.structure_for(s, a, receipt.version_id) == original
    assert mobile.revision(s, version['mobile_id'], school=True)['payload'] == payload
    assert result['analysis']['human_approved'] is False
    assert all(digest(s.object_path(f['sha256']).read_bytes()) == f['sha256'] for f in version['files'])
    assert not s.db.execute('SELECT 1 FROM mobile_reviews').fetchone()
    assert not list((s.root/'learning').glob('*.jsonl'))


def test_confirmation_and_later_correction_are_append_only_and_reopen_review(schools):
    m, s, a, _ = schools
    receipt, _ = converted(m, s, a)
    r = review_module()
    save(r, s, a, receipt.version_id, {'kind':'confirm', 'acknowledge_warnings':True})
    assert r.view(s, a, receipt.version_id)['status'] == 'confirmed'
    with pytest.raises(sqlite3.IntegrityError):
        s.db.execute("UPDATE school_structure_reviews SET comment='rewritten'")
    s.db.rollback()
    with pytest.raises(sqlite3.IntegrityError): s.db.execute('DELETE FROM school_structure_reviews')
    s.db.rollback()
    v = r.view(s, a, receipt.version_id)
    b = next(b for b in v['analysis']['blocks'] if b['kind'] == 'body')
    save(r, s, a, receipt.version_id, {'kind':'block','block_id':b['id'], 'block_kind':'body', 'text':'새 수정'})
    latest = r.view(s, a, receipt.version_id)
    assert latest['status'] == 'draft'
    events = r.history(s, a, receipt.version_id, revision_id=v['original']['conversion_revision'])['events']
    assert [e['status'] for e in events] == ['draft','confirmed']
    assert events[0]['previous_id'] == events[1]['id']
    with pytest.raises(ValueError): save(r, s, a, receipt.version_id, {'kind':'confirm','acknowledge_warnings':False})


def test_same_school_reader_cross_school_revocation_and_forged_fields(schools):
    m, s, a, b = schools
    receipt, version = converted(m, s, a)
    r = review_module(); reader = m.Principal(a.school, '1-reader')
    assert not r.view(s, reader, receipt.version_id)['editable']
    with pytest.raises(PermissionError): save(r, s, reader, receipt.version_id, {'kind':'reopen'})
    for fn in (lambda: r.view(s, b, receipt.version_id),
               lambda: r.history(s, b, receipt.version_id, revision_id=version['revision']),
               lambda: save(r, s, b, receipt.version_id, {'kind':'reopen'}, view=r.view(s, a, receipt.version_id))):
        with pytest.raises(LookupError): fn()
    with pytest.raises(ValueError): save(r, s, a, receipt.version_id, {'kind':'confirm','acknowledge_warnings':True,'actor':'admin'})
    v = r.view(s, a, receipt.version_id)
    m.grant_member(s, a.school, a.subject, 'uploader', active=False)
    with pytest.raises(PermissionError): save(r, s, a, receipt.version_id, {'kind':'confirm','acknowledge_warnings':True}, view=v)
    assert s.db.execute('SELECT count(*) FROM school_structure_reviews').fetchone()[0] == 0


def test_concurrent_review_and_stale_digest_fail_without_lost_update(schools):
    m, s, a, _ = schools
    receipt, _ = converted(m, s, a)
    r = review_module(); v = r.view(s, a, receipt.version_id)
    def attempt(_):
        with Store(s.root) as other:
            try: save(r, other, a, receipt.version_id, {'kind':'confirm','acknowledge_warnings':True}, view=v)
            except r.Conflict: return 'conflict'
            return 'saved'
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, range(2))) == ['conflict','saved']
    v = r.view(s, a, receipt.version_id); v['base_hash'] = '0'*64
    with pytest.raises(r.Conflict): save(r, s, a, receipt.version_id, {'kind':'reopen'}, view=v)
    assert s.db.execute('SELECT count(*) FROM school_structure_reviews').fetchone()[0] == 1


def test_new_conversion_and_receiving_correction_never_inherit_confirmation(schools):
    m, s, a, _ = schools
    receipt, old = converted(m, s, a)
    r = review_module()
    save(r, s, a, receipt.version_id, {'kind':'confirm','acknowledge_warnings':True})
    v = r.view(s, a, receipt.version_id)
    m.request_conversion(s, a, receipt.version_id)
    with pytest.raises(r.Conflict): save(r, s, a, receipt.version_id, {'kind':'reopen'}, view=v)
    assert m.process_conversions(s) == ['done']
    assert r.view(s, a, receipt.version_id)['status'] == 'unreviewed'
    historical = r.view(s, a, receipt.version_id, revision_id=old['revision'])
    assert historical['status'] == 'confirmed' and not historical['editable']
    with pytest.raises(r.Conflict): save(r, s, a, receipt.version_id, {'kind':'reopen'}, view=v)
    correction, _ = converted(m, s, a, price='15000')
    assert r.view(s, a, correction.version_id)['status'] == 'unreviewed'
    assert not r.view(s, a, receipt.version_id)['editable']


def test_staff_can_correct_relations_but_not_ownership_or_reference_other_versions(schools):
    m, s, a, _ = schools
    receipt, _ = converted(m, s, a)
    r = review_module(); v = r.view(s, a, receipt.version_id)
    edge = next(x for x in v['analysis']['relations'] if x['kind'] == 'attachment_reference')
    key = {k:edge[k] for k in ('kind','source','target')}
    save(r, s, a, receipt.version_id, {'kind':'relation_remove','relation':key})
    assert not any(x['kind'] == 'attachment_reference' for x in r.view(s, a, receipt.version_id)['analysis']['relations'])
    save(r, s, a, receipt.version_id, {'kind':'relation_add','relation':key})
    assert any(x['source'] == edge['source'] and x['target'] == edge['target'] for x in r.view(s, a, receipt.version_id)['analysis']['relations'])
    for relation in ({'kind':'guide_of','source':'file:1','target':'notice'},
                     {'kind':'attachment_reference','source':'foreign/b1','target':'file:1'},
                     {'kind':'attachment_reference','source':edge['source'],'target':'file:foreign'},
                     {'kind':'part_of','source':edge['source'],'target':edge['source']}):
        with pytest.raises(ValueError): save(r, s, a, receipt.version_id, {'kind':'relation_add','relation':relation})


@pytest.mark.parametrize('action,comment', [
    ({'kind':'block','block_id':'missing','block_kind':'body','text':'text'},'reason'),
    ({'kind':'block','block_id':'0/b1','block_kind':'script','text':'text'},'reason'),
    ({'kind':'confirm','acknowledge_warnings':True},''),
    ({'kind':'confirm','acknowledge_warnings':True},'x'*2001),
    ({'kind':'block','block_id':'0/b1','block_kind':'body','text':'x'*20001},'reason'),
    ({'kind':'confirm','acknowledge_warnings':'true'},'reason'),
], ids=['missing-block','bad-kind','empty-reason','long-reason','long-text','nonboolean-ack'])
def test_bad_review_payload_has_no_partial_event(schools, action, comment):
    m, s, a, _ = schools
    receipt, _ = converted(m, s, a); r = review_module()
    with pytest.raises(ValueError): save(r, s, a, receipt.version_id, action, comment=comment)
    assert s.db.execute('SELECT count(*) FROM school_structure_reviews').fetchone()[0] == 0


def test_v1_upgrade_is_explicit_additive_backed_up_and_preserves_existing_rows(tmp_path):
    with Store(tmp_path) as s:
        s.db.executescript(svc.SCHEMA)
        s.db.execute("INSERT INTO school_service_migrations VALUES(1,'synthetic-old')"); s.db.commit()
        svc.configure_school(s, 'A', '합성학교'); svc.grant_member(s, 'A', 'staff', 'uploader')
        receipt, version = converted(svc, s, svc.Principal('A','staff'))
        before = mobile.revision(s, version['mobile_id'], school=True)['payload']
    with Store(tmp_path) as s:
        assert not s.db.execute("SELECT 1 FROM sqlite_master WHERE name='school_structure_reviews'").fetchone()
        backup = svc.migrate(s)
        assert backup is not None, 'Existing school schema was not upgraded'
        with sqlite3.connect(backup) as old:
            assert old.execute('SELECT max(version) FROM school_service_migrations').fetchone()[0] == 1
            assert not old.execute("SELECT 1 FROM sqlite_master WHERE name='school_structure_reviews'").fetchone()
        assert svc.migrate(s) is None
        assert s.db.execute('SELECT version FROM school_service_migrations ORDER BY version').fetchall()[1][0] == 2
        assert s.db.execute('PRAGMA user_version').fetchone()[0] == 5
        assert s.db.execute('PRAGMA foreign_key_check').fetchall() == []
        assert s.db.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
        assert mobile.revision(s, version['mobile_id'], school=True)['payload'] == before
        assert review_module().view(s, svc.Principal('A','staff'), receipt.version_id)['status'] == 'unreviewed'


def test_http_review_forms_history_csrf_roles_and_xss(school_web):
    _, s, base = school_web
    a, b, reader, anon = client(),client(),client(),client()
    csrf = login(a,base); csrf_b = login(b,base,'b','synthetic-b-only')
    csrf_r = login(reader,base,'reader','synthetic-reader-only')
    receipt, _ = converted(svc,s,svc.Principal('A','a'))
    path = '/structure-review?id='+receipt.version_id
    assert fetch(a,base,path)[0] == 200, 'Staff review page missing'
    r = review_module(); v = r.view(s,svc.Principal('A','a'),receipt.version_id)
    block = next(b for b in v['original']['blocks'] if b['kind'] == 'body')
    fields = {'csrf':csrf,'id':receipt.version_id,'revision':v['original']['conversion_revision'],
        'base_hash':v['base_hash'],'expected_review':'','action':'block','block_id':block['id'],
        'block_kind':'heading','text':'</textarea><script>synthetic</script>','comment':'<img src=x onerror=alert(1)>'}
    assert fetch(anon,base,path)[0] == 401
    assert fetch(b,base,path)[0] == 404
    assert fetch(b,base,'/structure-review',fields={**fields,'csrf':csrf_b})[0] == 404
    assert fetch(reader,base,'/structure-review',fields={**fields,'csrf':csrf_r})[0] == 403
    assert fetch(a,base,'/structure-review',fields={**fields,'csrf':'wrong'})[0] == 403
    assert fetch(a,base,'/structure-review',fields=fields,headers={'Origin':'https://evil.example.test'})[0] == 403
    status, html, headers = fetch(a,base,'/structure-review',fields=fields)
    assert status == 200 and b'&lt;script&gt;' in html and b'<script>synthetic' not in html
    assert 'no-store' in headers['Cache-Control']
    assert fetch(a,base,'/structure-review',fields=fields)[0] == 409
    history_path = '/structure-history?id='+receipt.version_id+'&revision='+v['original']['conversion_revision']
    status, history, _ = fetch(a,base,history_path)
    assert status == 200 and '수정 전'.encode() in history and '수정 후'.encode() in history
    assert b'<img src=x' not in history and b'&lt;img' in history
    assert fetch(b,base,history_path)[0] == 404
    assert fetch(reader,base,history_path)[0] == 200
    assert b'action="/structure-review"' not in fetch(reader,base,path)[1]


def test_history_pagination_and_read_only_snapshot_are_stable(schools):
    m,s,a,b = schools; receipt,version = converted(m,s,a)
    r = review_module(); block = next(x for x in r.view(s,a,receipt.version_id)['analysis']['blocks'] if x['kind']=='body')
    ids = []
    for i in range(23):
        ids.append(save(r,s,a,receipt.version_id,{'kind':'block','block_id':block['id'],'block_kind':'body','text':'수정 '+str(i)}))
    first = r.history(s,a,receipt.version_id,revision_id=version['revision'])
    second = r.history(s,a,receipt.version_id,revision_id=version['revision'],before=first['next_before'])
    assert [e['sequence'] for e in first['events']+second['events']] == list(range(23,0,-1))
    assert second['next_before'] == 0
    old = r.view(s,a,receipt.version_id,revision_id=version['revision'],event_id=ids[0])
    assert next(x for x in old['analysis']['blocks'] if x['id']==block['id'])['text'] == '수정 0'
    assert old['historical_event'] and not old['editable']
    with pytest.raises(LookupError): r.view(s,b,receipt.version_id,event_id=ids[0])
    with pytest.raises(LookupError): r.view(s,a,receipt.version_id,event_id='foreign')


def test_semantic_edge_repair_and_containment_cycles(schools):
    m,s,a,_ = schools; receipt,_ = converted(m,s,a); r = review_module()
    v = r.view(s,a,receipt.version_id)
    header = next(x for x in v['analysis']['blocks'] if x['kind']=='header')
    edge = next(x for x in v['analysis']['relations'] if x['kind']=='header_for')
    save(r,s,a,receipt.version_id,{'kind':'block','block_id':header['id'],'block_kind':'body','text':header['text']})
    with pytest.raises(ValueError): save(r,s,a,receipt.version_id,{'kind':'confirm','acknowledge_warnings':True})
    save(r,s,a,receipt.version_id,{'kind':'relation_remove','relation':{k:edge[k] for k in ('kind','source','target')}})
    save(r,s,a,receipt.version_id,{'kind':'confirm','acknowledge_warnings':True})
    body = next(x for x in v['analysis']['blocks'] if x['kind']=='body' and x['file_id']==header['file_id'])
    save(r,s,a,receipt.version_id,{'kind':'relation_add','relation':{'kind':'part_of','source':body['id'],'target':header['id']}})
    with pytest.raises(ValueError):
        save(r,s,a,receipt.version_id,{'kind':'relation_add','relation':{'kind':'part_of','source':header['id'],'target':body['id']}})
    save(r,s,a,receipt.version_id,{'kind':'block','block_id':header['id'],'block_kind':'footnote','text':'담당자 각주 수정'})
    save(r,s,a,receipt.version_id,{'kind':'relation_add','relation':{'kind':'footnote_for','source':body['id'],'target':header['id']}})
    assert r.view(s,a,receipt.version_id)['status'] == 'draft'


def test_incomplete_analysis_stays_incomplete_after_confirmation_and_bounded_changes(schools, monkeypatch):
    m,s,a,_ = schools; r = review_module()
    receipt = m.upload(s,a,m.IncomingPost('partial','합성 미확정','<p>보이는 본문</p>',(m.IncomingFile('신청서.txt',b'synthetic'),)))
    m.request_conversion(s,a,receipt.version_id); assert m.process_conversions(s)==['done']
    v = r.view(s,a,receipt.version_id)
    assert v['analysis']['complete'] is False and v['analysis']['warnings']
    save(r,s,a,receipt.version_id,{'kind':'confirm','acknowledge_warnings':True})
    out = r.view(s,a,receipt.version_id)
    assert out['status']=='confirmed' and out['analysis']['complete'] is False
    assert out['analysis']['warnings']==v['analysis']['warnings']
    monkeypatch.setattr(r,'MAX_OVERLAY_BYTES',1)
    block = v['analysis']['blocks'][0]
    with pytest.raises(ValueError):
        save(r,s,a,receipt.version_id,{'kind':'block','block_id':block['id'],'block_kind':'body','text':'새 텍스트'})
    assert r.view(s,a,receipt.version_id)['latest']==out['latest']


def test_conversion_history_links_survive_reconversion(school_web):
    _,s,base = school_web; a = client(); login(a,base); principal=svc.Principal('A','a')
    receipt,old = converted(svc,s,principal); r=review_module()
    save(r,s,principal,receipt.version_id,{'kind':'confirm','acknowledge_warnings':True})
    svc.request_conversion(s,principal,receipt.version_id); assert svc.process_conversions(s)==['done']
    status, html, _ = fetch(a,base,'/structure-history?id='+receipt.version_id)
    assert status==200 and ('revision='+old['revision']).encode() in html, 'Previous conversion review is not discoverable'
    status, raw, _ = fetch(a,base,'/structure-history?id='+receipt.version_id+'&revision='+old['revision'])
    assert status==200 and '담당자 확인 완료'.encode() in raw


def test_http_rejects_ambiguous_or_forged_authority_and_allows_full_confirmation_flow(school_web):
    from bs4 import BeautifulSoup
    _,s,base = school_web; a=client(); csrf=login(a,base)
    receipt,version = converted(svc,s,svc.Principal('A','a'))
    path='/structure-review?id='+receipt.version_id
    soup=BeautifulSoup(fetch(a,base,path)[1],'html.parser')
    form=next(f for f in soup.find_all('form') if f.find('input',attrs={'name':'action','value':'confirm'}))
    fields={x['name']:x.get('value','') for x in form.find_all('input')}
    fields['comment']='합성 확인 · 사유'; fields['acknowledge_warnings']='yes'
    for extra in ({'school':'B'},{'actor':'forged'},{'subject':'forged'},{'base_hash':'wrong'}):
        assert fetch(a,base,'/structure-review',fields={**fields,**extra})[0] in (400,409)
    assert fetch(a,base,'/structure-review',fields=list(fields.items())+[('action','reopen')])[0]==400
    status,html,_=fetch(a,base,'/structure-review',fields=fields)
    assert status==200 and '담당자 확인 완료'.encode() in html
    soup=BeautifulSoup(html,'html.parser')
    form=next(f for f in soup.find_all('form') if f.find('input',attrs={'name':'action','value':'reopen'}))
    fields={x['name']:x.get('value','') for x in form.find_all('input')}; fields['comment']='합성 다시 검수'
    assert fetch(a,base,'/structure-review',fields=fields)[0]==200
    events=json.loads(fetch(a,base,'/structure-history?id='+receipt.version_id+'&format=json')[1])['events']
    assert [e['action'] for e in events]==['reopen','confirm']
    assert all(e['actor']=='a' for e in events)
    assert fetch(a,base,path+'&block_page=not-a-number')[0]==400
    svc.grant_member(s,'A','a','uploader',active=False)
    assert fetch(a,base,path)[0]==401
    assert fetch(a,base,'/structure-review',fields=fields)[0]==401


def test_unknown_school_schema_does_not_migrate_or_rewrite_existing_table(tmp_path):
    with Store(tmp_path) as s:
        svc.migrate(s)
        s.db.execute("INSERT INTO school_service_migrations VALUES(99,'future')"); s.db.commit()
        before=list((s.root/'db/backups').glob('*'))
        with pytest.raises(RuntimeError): svc.migrate(s)
        assert list((s.root/'db/backups').glob('*'))==before


def test_text_only_edit_and_kind_revert_keep_native_header_table_relationship(schools):
    m,s,a,_=schools; r=review_module()
    receipt=m.upload(s,a,m.IncomingPost('header-table','합성 헤더 표',
        '<header>School<table><tr><td>Header cell</td></tr></table></header><p>Body</p>'))
    m.request_conversion(s,a,receipt.version_id); assert m.process_conversions(s)==['done']
    before=r.view(s,a,receipt.version_id)
    table=next(b for b in before['analysis']['blocks'] if b['kind']=='table' and b.get('zone')=='header')
    save(r,s,a,receipt.version_id,{'kind':'confirm','acknowledge_warnings':True})
    save(r,s,a,receipt.version_id,{'kind':'block','block_id':table['id'],'block_kind':'table','text':'Edited cell'})
    edited=next(b for b in r.view(s,a,receipt.version_id)['analysis']['blocks'] if b['id']==table['id'])
    assert edited['reviewed_fields']==['text'], 'Unchanged kind must not become a kind override'
    save(r,s,a,receipt.version_id,{'kind':'confirm','acknowledge_warnings':True})
    save(r,s,a,receipt.version_id,{'kind':'block','block_id':table['id'],'block_kind':'body','text':'Edited cell'})
    with pytest.raises(ValueError): save(r,s,a,receipt.version_id,{'kind':'confirm','acknowledge_warnings':True})
    save(r,s,a,receipt.version_id,{'kind':'block','block_id':table['id'],'block_kind':'table','text':table['text']})
    save(r,s,a,receipt.version_id,{'kind':'confirm','acknowledge_warnings':True})
    after=r.view(s,a,receipt.version_id)
    assert after['analysis']['blocks']==before['analysis']['blocks']
    assert after['analysis']['relations']==before['analysis']['relations']


@pytest.mark.parametrize('cursor',['before','versions_before'])
def test_http_history_cursor_outside_sqlite_integer_range_returns_400(school_web,cursor):
    _,s,base=school_web; a=client(); login(a,base)
    receipt,_=converted(svc,s,svc.Principal('A','a'))
    assert fetch(a,base,'/structure-history?id='+receipt.version_id+'&'+cursor+'=9223372036854775808')[0]==400


def test_database_insert_cannot_link_a_review_to_another_schools_conversion(schools):
    m,s,a,b=schools; one,first=converted(m,s,a); _,other=converted(m,s,b); r=review_module()
    save(r,s,a,one.version_id,{'kind':'confirm','acknowledge_warnings':True})
    record=dict(s.db.execute('SELECT * FROM school_structure_reviews').fetchone())
    record.update(id='forged-event',conversion_revision=other['revision'])
    with pytest.raises(sqlite3.IntegrityError):
        s.db.execute('INSERT INTO school_structure_reviews VALUES('+','.join('?' for _ in record)+')',tuple(record.values()))
    s.db.rollback()
    assert len(r.history(s,a,one.version_id,revision_id=first['revision'])['events'])==1
