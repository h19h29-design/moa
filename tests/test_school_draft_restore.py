"""Explicit private input reuse, isolated SQLite and real authenticated HTTP."""
import concurrent.futures
import importlib
import sqlite3
from urllib.parse import urlencode

from bs4 import BeautifulSoup
import pytest

from moa import school_review as review, school_review_drafts as drafts, school_service as svc
from moa.core import Store, digest
from test_school_service import schools
from test_school_structure import converted
from test_school_structure_review import save as formal_save
from test_school_review_drafts import draft_save, http_draft, page, inputs
from test_web_school import school_web, client, fetch, login


def module(): return importlib.import_module('moa.school_draft_restore')


def route(ident): return '/structure-draft/restore?'+urlencode({'id':ident})


def setup_pair(s,p,ident,action=None):
    v=review.view(s,p,ident)
    action=action or {'kind':'block','block_id':v['analysis']['blocks'][0]['id'],'block_kind':'body','text':'이전 <script>개인 입력</script>\n '}
    source=draft_save(s,p,ident,action=action,comment='')
    head=draft_save(s,p,ident,action=action,expected=source,comment='더 최신 개인 사유')
    return source,head


def restore(s,p,source,head,**changes):
    row=drafts.get(s,p,source)
    args={'expected_draft':head,'base_hash':row['base_hash'],'expected_review':row['base_review']}
    return module().restore(s,p,source,**{**args,**changes})


def preserved_rows(s):
    return [line for line in s.db.iterdump() if not line.startswith((
        'INSERT INTO "school_review_drafts"','INSERT INTO "school_review_draft_restores"'))]


def files(s):
    return {str(p):(digest(p.read_bytes()),p.stat().st_mtime_ns) for folder in ('objects','extracted')
            for p in (s.root/folder).rglob('*') if p.is_file()}


def test_explicit_reuse_appends_input_and_source_only_preserving_all_existing_rows_and_files(schools):
    m,s,p,_=schools; receipt,_=converted(m,s,p); source,head=setup_pair(s,p,receipt.version_id)
    old=[tuple(r) for r in s.db.execute('SELECT * FROM school_review_drafts ORDER BY sequence')]
    before=preserved_rows(s); originals=files(s); source_row=drafts.get(s,p,source)
    preview=module().preview(s,p,source)
    assert preview['can_restore'] and preview['restore_reasons']==[] and preview['head_draft']['id']==head
    assert old==[tuple(r) for r in s.db.execute('SELECT * FROM school_review_drafts ORDER BY sequence')]
    new=restore(s,p,source,head); row=drafts.get(s,p,new)
    assert new not in (source,head) and row['sequence']==3 and row['previous_id']==head
    assert row['payload']==source_row['payload'] and row['base_hash']==source_row['base_hash']
    assert row['base_review']==source_row['base_review'] and row['restored_from']==source
    assert tuple(s.db.execute('SELECT draft_id,source_id FROM school_review_draft_restores').fetchone())==(new,source)
    assert [tuple(r) for r in s.db.execute('SELECT * FROM school_review_drafts ORDER BY sequence')][:2]==old
    assert drafts.get(s,p,source)==source_row and preserved_rows(s)==before and files(s)==originals
    assert review.view(s,p,receipt.version_id)['status']=='unreviewed'
    assert s.db.execute('PRAGMA quick_check').fetchone()[0]=='ok'
    assert not s.db.execute('PRAGMA foreign_key_check').fetchall()


@pytest.mark.parametrize('kind',['relation_add','relation_remove'])
def test_relation_input_reuse_keeps_partial_values_and_does_not_edit_relationships(schools,kind):
    m,s,p,_=schools; receipt,_=converted(m,s,p); v=review.view(s,p,receipt.version_id)
    edge=({'kind':'part_of','source':'','target':'unfinished'} if kind=='relation_add' else
          {k:next(r for r in v['analysis']['relations'] if r['kind'] in review.RELATION_LABELS)[k] for k in ('kind','source','target')})
    source,head=setup_pair(s,p,receipt.version_id,{'kind':kind,'relation':edge})
    new=restore(s,p,source,head)
    assert drafts.get(s,p,new)['payload']['action']=={'kind':kind,'relation':edge}
    assert review.view(s,p,receipt.version_id)==v


def test_replay_and_two_parallel_reuses_append_exactly_once(schools):
    m,s,p,_=schools; receipt,_=converted(m,s,p); source,head=setup_pair(s,p,receipt.version_id)
    args=drafts.get(s,p,source)
    def attempt(_):
        with Store(s.root) as other:
            try:
                return module().restore(other,p,source,expected_draft=head,
                    base_hash=args['base_hash'],expected_review=args['base_review'])
            except review.Conflict: return 'conflict'
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(attempt,range(2)))
    assert results.count('conflict')==1
    before=list(s.db.iterdump())
    with pytest.raises(review.Conflict): restore(s,p,source,head)
    assert list(s.db.iterdump())==before
    assert s.db.execute('SELECT count(*) FROM school_review_drafts').fetchone()[0]==3
    assert s.db.execute('SELECT count(*) FROM school_review_draft_restores').fetchone()[0]==1


def test_failed_source_receipt_insert_rolls_back_new_input_too(schools):
    m,s,p,_=schools; receipt,_=converted(m,s,p); source,head=setup_pair(s,p,receipt.version_id)
    s.db.executescript("CREATE TRIGGER synthetic_receipt_failure BEFORE INSERT ON school_review_draft_restores BEGIN SELECT RAISE(ABORT,'synthetic failure'); END;")
    before=list(s.db.iterdump())
    with pytest.raises(sqlite3.IntegrityError): restore(s,p,source,head)
    assert list(s.db.iterdump())==before and not s.db.in_transaction


def test_preview_keeps_one_snapshot_when_another_tab_saves_during_comparison(schools,monkeypatch):
    m,s,p,_=schools; receipt,_=converted(m,s,p); source,head=setup_pair(s,p,receipt.version_id)
    original=module().compare.compare
    def concurrent(*args,**kwargs):
        result=original(*args,**kwargs)
        with Store(s.root) as other:
            draft_save(other,p,receipt.version_id,expected=head,comment='미리보기 중 다른 탭 저장')
        return result
    monkeypatch.setattr(module().compare,'compare',concurrent)
    result=module().preview(s,p,source)
    assert result['can_restore'] and result['head_draft']['id']==head
    monkeypatch.setattr(module().compare,'compare',original)
    refreshed=module().preview(s,p,source)
    assert refreshed['head_draft']['id']!=head and refreshed['head_draft']['sequence']==3
    before=list(s.db.iterdump())
    with pytest.raises(review.Conflict): restore(s,p,source,head)
    assert list(s.db.iterdump())==before


@pytest.mark.parametrize('error,reason',[(review.Conflict,'structure_changed'),(LookupError,'structure_unavailable')])
def test_unavailable_analysis_blocks_reuse_without_exposing_internal_error(schools,monkeypatch,error,reason):
    m,s,p,_=schools; receipt,_=converted(m,s,p); source,head=setup_pair(s,p,receipt.version_id)
    def unavailable(*args,**kwargs): raise error('synthetic private path details')
    monkeypatch.setattr(review,'view',unavailable)
    before=list(s.db.iterdump()); result=module().preview(s,p,source)
    assert not result['can_restore'] and reason in result['restore_reasons']
    assert 'synthetic private path details' not in str(result)
    with pytest.raises(review.Conflict): restore(s,p,source,head)
    assert list(s.db.iterdump())==before


@pytest.mark.parametrize('change,reason',[
    ('latest','already_latest'),('formal','review_changed'),('head','head_base_changed'),
    ('conversion','conversion_changed'),('correction','receiving_changed'),
    ('pending','conversion_pending'),('failed','conversion_failed'),('reader','read_only')])
def test_changed_state_blocks_reuse_without_rebasing_or_appending(schools,change,reason):
    m,s,p,_=schools; receipt,version=converted(m,s,p); source,head=setup_pair(s,p,receipt.version_id)
    if change=='latest': source=head
    elif change in ('formal','head'):
        formal_save(review,s,p,receipt.version_id,{'kind':'confirm','acknowledge_warnings':True})
        if change=='head': head=draft_save(s,p,receipt.version_id,expected=head)
    elif change=='reader': svc.grant_member(s,p.school,p.subject,'reader')
    elif change=='correction': converted(m,s,p,price='15000')
    else:
        svc.request_conversion(s,p,receipt.version_id)
        if change=='conversion': assert svc.process_conversions(s)==['done']
        if change=='failed':
            s.db.execute("UPDATE mobile_notices SET state='conversion_failed' WHERE id=?",(version['mobile_id'],));s.db.commit()
    before=list(s.db.iterdump()); data=module().preview(s,p,source)
    assert not data['can_restore'] and reason in data['restore_reasons']
    with pytest.raises(PermissionError if change=='reader' else review.Conflict): restore(s,p,source,head)
    assert list(s.db.iterdump())==before


@pytest.mark.parametrize('change',['review','hash','head'])
def test_client_expected_values_must_match_original_base_and_current_head(schools,change):
    m,s,p,_=schools; receipt,_=converted(m,s,p); source,head=setup_pair(s,p,receipt.version_id)
    values={'review':{'expected_review':'0'*32},'hash':{'base_hash':'0'*64},'head':{'expected_draft':source}}
    before=list(s.db.iterdump())
    with pytest.raises(review.Conflict): restore(s,p,source,head,**values[change])
    assert list(s.db.iterdump())==before


def test_another_owner_or_school_never_gets_source_or_can_append_it(schools):
    m,s,p,other=schools; receipt,_=converted(m,s,p); source,head=setup_pair(s,p,receipt.version_id)
    colleague=svc.Principal(p.school,'colleague');svc.grant_member(s,p.school,colleague.subject,'manager')
    row=drafts.get(s,p,source); before=list(s.db.iterdump())
    for who in (other,colleague):
        with pytest.raises(LookupError): module().preview(s,who,source)
        with pytest.raises(LookupError): module().restore(s,who,source,expected_draft=head,base_hash=row['base_hash'],expected_review=row['base_review'])
    assert list(s.db.iterdump())==before


def test_restore_receipt_is_immutable_and_rejects_mismatched_source_payload(schools):
    m,s,p,_=schools; receipt,_=converted(m,s,p); source,head=setup_pair(s,p,receipt.version_id)
    new=restore(s,p,source,head)
    for sql in ('UPDATE school_review_draft_restores SET source_id=draft_id','DELETE FROM school_review_draft_restores'):
        with pytest.raises(sqlite3.IntegrityError): s.db.execute(sql)
        s.db.rollback()
    later=draft_save(s,p,receipt.version_id,expected=new,comment='출처와 다른 사유')
    with pytest.raises(sqlite3.IntegrityError):
        s.db.execute('INSERT INTO school_review_draft_restores VALUES(?,?)',(later,source))
    s.db.rollback()
    assert s.db.execute('SELECT count(*) FROM school_review_draft_restores').fetchone()[0]==1


@pytest.mark.parametrize('invalid_source',['other_owner','immediate_head'])
def test_source_receipt_rejects_foreign_owner_or_nonhistorical_input_even_with_identical_payload(schools,invalid_source):
    m,s,p,_=schools; receipt,_=converted(m,s,p)
    source=draft_save(s,p,receipt.version_id,comment='')
    source_row=drafts.get(s,p,source)
    head=draft_save(s,p,receipt.version_id,expected=source,comment='')
    new=draft_save(s,p,receipt.version_id,expected=head,comment='')
    donor=head
    if invalid_source=='other_owner':
        colleague=svc.Principal(p.school,'colleague');svc.grant_member(s,p.school,colleague.subject,'manager')
        donor=draft_save(s,colleague,receipt.version_id,action=source_row['payload']['action'],comment='')
    before=list(s.db.iterdump())
    with pytest.raises(sqlite3.IntegrityError):
        s.db.execute('INSERT INTO school_review_draft_restores VALUES(?,?)',(new,donor))
    s.db.rollback()
    assert list(s.db.iterdump())==before


def test_v5_upgrade_is_explicit_backed_up_additive_and_repeatable(schools):
    m,s,p,_=schools; receipt,_=converted(m,s,p); source,head=setup_pair(s,p,receipt.version_id)
    s.db.executescript('DROP TABLE IF EXISTS school_confirmed_export_list_events; DROP TABLE school_confirmed_export_items; DROP TABLE school_confirmed_export_lists; DROP TABLE school_review_draft_handoffs; DROP TABLE school_review_draft_restores; DELETE FROM school_service_migrations WHERE version>=5;')
    before=list(s.db.iterdump()); version=s.db.execute('PRAGMA user_version').fetchone()[0]; originals=files(s)
    assert not module().available(s)
    data=module().preview(s,p,source)
    assert not data['can_restore'] and 'restore_migration_required' in data['restore_reasons']
    with pytest.raises(review.Conflict): restore(s,p,source,head)
    assert list(s.db.iterdump())==before
    backup=svc.migrate(s)
    assert backup.parent==s.root/'db/backups'
    with sqlite3.connect(backup) as old:
        assert old.execute('SELECT max(version) FROM school_service_migrations').fetchone()[0]==4
        assert list(old.iterdump())==before
    assert set(before)<=set(s.db.iterdump()) and module().available(s)
    assert s.db.execute('PRAGMA user_version').fetchone()[0]==version and files(s)==originals
    after=list(s.db.iterdump()); assert svc.migrate(s) is None and list(s.db.iterdump())==after
    s.db.execute("INSERT INTO school_service_migrations VALUES(999,'synthetic future')");s.db.commit()
    before=list(s.db.iterdump())
    with pytest.raises(RuntimeError): svc.migrate(s)
    assert list(s.db.iterdump())==before


def http_setup(school_web):
    _,s,base=school_web; a=client();login(a,base);p=svc.Principal('A','a')
    receipt,_,source=http_draft(a,base,s,changes={'text':'저장했던 <script>개인 초안</script>\n ','comment':''})
    head=draft_save(s,p,receipt.version_id,expected=source)
    doc,headers=page(a,base,route(source)); f=doc.select_one('[data-draft-restore-form]')
    assert f is not None
    values=inputs(f); values['acknowledge']='yes'
    return s,base,a,p,source,head,doc,headers,values


def test_http_preview_confirm_and_relogin_recovery_preserve_inputs_and_review_history(school_web):
    s,base,a,p,source,head,doc,headers,values=http_setup(school_web)
    before=list(s.db.iterdump()); originals=files(s)
    again,_=page(a,base,route(source))
    assert again.select_one('#draft-restore-preview') and list(s.db.iterdump())==before
    assert not again.select('script,textarea') and '<script>개인 초안</script>' not in str(again)
    assert '개인 초안' in again.get_text() and 'no-store' in headers['Cache-Control']
    assert "default-src 'self'" in headers['Content-Security-Policy']
    checkbox=again.select_one('[name=acknowledge]')
    assert checkbox['type']=='checkbox' and checkbox.has_attr('required') and not checkbox.has_attr('checked')
    for path in ('/structure-draft/history?id='+head,'/structure-draft/compare?id='+source,'/structure-draft?id='+source):
        linked,_=page(a,base,path); assert linked.select_one('[data-draft-restore]')
    status,body,_=fetch(a,base,'/structure-draft/restore',fields=values)
    assert status==200
    recovered=BeautifulSoup(body,'html.parser'); assert recovered.select_one('#draft-restored')
    new=drafts.listing(s,p)['rows'][0]['id']
    assert drafts.get(s,p,new)['restored_from']==source and recovered.select_one('[data-draft-origin]')
    assert '저장했던 <script>개인 초안</script>\n ' in [x.get_text() for x in recovered.select('textarea[name=text]')]
    assert not s.db.execute('SELECT 1 FROM school_structure_reviews').fetchone()
    after=list(s.db.iterdump())
    assert fetch(a,base,'/structure-draft/restore',fields=values)[0]==409
    fresh=client();login(fresh,base)
    reloaded,_=page(fresh,base,'/structure-draft?id='+new)
    assert reloaded.select_one('[data-draft-origin]') and list(s.db.iterdump())==after
    assert files(s)==originals


@pytest.mark.parametrize('mutation,status',[
    ('no_ack',400),('bad_ack',400),('extra_payload',400),('bad_csrf',403),('origin',403),
    ('duplicate_source',400),('duplicate_ack',400),('invalid_utf8',400),('too_large',413)])
def test_http_requires_exact_authenticated_explicit_confirmation(school_web,mutation,status):
    s,base,a,p,source,head,doc,headers,values=http_setup(school_web)
    before=list(s.db.iterdump()); data=None; request_headers={}
    if mutation=='no_ack': del values['acknowledge']
    elif mutation=='bad_ack': values['acknowledge']='no'
    elif mutation=='extra_payload': values['text']='client must not replace stored source'
    elif mutation=='bad_csrf': values['csrf']='invalid'
    elif mutation=='origin': request_headers['Origin']='https://evil.example.test'
    elif mutation.startswith('duplicate_'):
        field='source_id' if mutation=='duplicate_source' else 'acknowledge'
        data=(urlencode(values)+'&'+urlencode({field:values[field]})).encode()
    elif mutation=='invalid_utf8': data=urlencode(values).encode()+b'&text=\xff'
    elif mutation=='too_large': data=b'x'*4097
    result=fetch(a,base,'/structure-draft/restore',fields=values if data is None else None,data=data,headers=request_headers)
    assert result[0]==status and list(s.db.iterdump())==before


def test_http_permission_and_current_head_are_rechecked_after_preview(school_web):
    s,base,a,p,source,head,doc,headers,values=http_setup(school_web)
    row=drafts.get(s,p,source)
    newest=draft_save(s,p,row['notice_version'],expected=head,comment='다른 탭 저장')
    before=list(s.db.iterdump())
    assert fetch(a,base,'/structure-draft/restore',fields=values)[0]==409
    assert list(s.db.iterdump())==before
    b=client();values['csrf']=login(b,base,'b','synthetic-b-only')
    assert fetch(b,base,route(source))[0]==404
    assert fetch(b,base,'/structure-draft/restore',fields=values)[0]==404
    assert fetch(client(),base,route(source))[0]==401
    values['csrf']=login(a,base);values['expected_draft']=newest
    svc.grant_member(s,'A','a','reader')
    read,_=page(a,base,route(source))
    assert not read.select_one('[data-draft-restore-form]') and read.select_one('[data-restore-reason]')
    assert fetch(a,base,'/structure-draft/restore',fields=values)[0]==403
    svc.grant_member(s,'A','a','reader',active=False)
    assert fetch(a,base,route(source))[0]==401
    assert fetch(a,base,'/structure-draft/restore',fields=values)[0]==401


def test_http_v4_keeps_read_only_preview_and_does_not_upgrade_on_get_or_post(school_web):
    s,base,a,p,source,head,doc,headers,values=http_setup(school_web)
    s.db.executescript('DROP TABLE IF EXISTS school_confirmed_export_list_events; DROP TABLE school_confirmed_export_items; DROP TABLE school_confirmed_export_lists; DROP TABLE school_review_draft_handoffs; DROP TABLE school_review_draft_restores; DELETE FROM school_service_migrations WHERE version>=5;')
    before=list(s.db.iterdump()); readonly,_=page(a,base,route(source))
    assert not readonly.select_one('[data-draft-restore-form]') and readonly.select_one('[data-restore-reason]')
    assert fetch(a,base,'/structure-draft/restore',fields=values)[0]==409
    assert list(s.db.iterdump())==before


@pytest.mark.parametrize('query',['','?id=bad','?id=%FF','?id='+'0'*32+'&id='+'1'*32,
    '?id='+'0'*32+'&school=A','?id='+'x'*4097])
def test_http_restore_query_is_strict_and_bounded(school_web,query):
    _,s,base=school_web;a=client();login(a,base);before=list(s.db.iterdump())
    assert fetch(a,base,'/structure-draft/restore'+query)[0]==400
    assert list(s.db.iterdump())==before
