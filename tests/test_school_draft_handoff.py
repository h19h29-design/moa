"""Explicit saved-input handoff, immutable provenance and owner-only receipts."""
import concurrent.futures
import importlib
import json
import sqlite3
from urllib.parse import urlencode

import pytest

from moa import school_review as review, school_review_drafts as drafts, school_service as svc
from moa.core import Store, digest
from test_school_service import schools
from test_school_structure import converted
from test_school_structure_review import save as formal_save
from test_school_review_drafts import draft_save, http_draft, page, inputs
from test_web_school import school_web, client, fetch, login


def module(): return importlib.import_module('moa.school_draft_handoff')


def route(ident): return '/structure-draft/submit?'+urlencode({'id':ident})


def submit(s,p,ident,**changes):
    row=drafts.get(s,p,ident)
    return module().submit(s,p,ident,**{'base_hash':row['base_hash'],'expected_review':row['base_review'],**changes})


def files(s):
    return {str(p):(digest(p.read_bytes()),p.stat().st_mtime_ns) for folder in ('objects','extracted')
            for p in (s.root/folder).rglob('*') if p.is_file()}


def unchanged_rows(s):
    return [line for line in s.db.iterdump() if not line.startswith((
        'INSERT INTO "school_structure_reviews"','INSERT INTO "school_review_draft_handoffs"'))]


def test_handoff_uses_exact_stored_input_appends_review_and_receipt_preserving_every_prior_row(schools):
    m,s,p,_=schools; receipt,version=converted(m,s,p)
    previous=formal_save(review,s,p,receipt.version_id,{'kind':'confirm','acknowledge_warnings':True})
    source=draft_save(s,p,receipt.version_id,comment='  검수 저장 사유\n ')
    row=drafts.get(s,p,source); before=unchanged_rows(s); original=files(s)
    prior_reviews=[tuple(r) for r in s.db.execute('SELECT * FROM school_structure_reviews ORDER BY sequence')]
    snapshot=list(s.db.iterdump()); preview=module().preview(s,p,source)
    assert preview['can_submit'] and preview['submit_reasons']==[] and preview['receipt'] is None
    assert list(s.db.iterdump())==snapshot
    event=submit(s,p,source); result=review.view(s,p,receipt.version_id)
    assert result['status']=='draft' and result['latest']['id']==event
    assert result['latest']['previous_id']==previous and result['latest']['actor']==p.subject
    assert result['latest']['comment']==row['payload']['comment'].strip()
    assert not result['latest']['warnings_acknowledged']
    b=next(b for b in result['analysis']['blocks'] if b['id']==row['payload']['action']['block_id'])
    assert b['text']==row['payload']['action']['text'] and b['kind']==row['payload']['action']['block_kind']
    assert drafts.get(s,p,source)==row and unchanged_rows(s)==before and files(s)==original
    assert [tuple(r) for r in s.db.execute('SELECT * FROM school_structure_reviews ORDER BY sequence')][:-1]==prior_reviews
    record=module().for_draft(s,p,source)
    assert record==module().for_review(s,p,event)
    assert record['draft_id']==source and record['review_id']==event and record['sequence']==2
    assert record['notice_version']==receipt.version_id and record['conversion_revision']==version['revision']
    assert tuple(s.db.execute('SELECT review_id,draft_id FROM school_review_draft_handoffs').fetchone())==(event,source)
    after=list(s.db.iterdump()); submitted=module().preview(s,p,source)
    assert not submitted['can_submit'] and 'already_submitted' in submitted['submit_reasons']
    assert submitted['receipt']==record and list(s.db.iterdump())==after
    assert s.db.execute('PRAGMA quick_check').fetchone()[0]=='ok'
    assert not s.db.execute('PRAGMA foreign_key_check').fetchall()


@pytest.mark.parametrize('kind',['relation_add','relation_remove'])
def test_complete_relation_is_formally_saved_with_receipt_and_requires_reconfirmation(schools,kind):
    m,s,p,_=schools; receipt,_=converted(m,s,p); v=review.view(s,p,receipt.version_id)
    if kind=='relation_add':
        blocks=[b for b in v['analysis']['blocks'] if b['file_id']=='0']
        edge={'kind':'part_of','source':blocks[1]['id'],'target':blocks[0]['id']}
    else: edge={k:next(r for r in v['analysis']['relations'] if r['kind'] in review.RELATION_LABELS)[k] for k in ('kind','source','target')}
    source=draft_save(s,p,receipt.version_id,action={'kind':kind,'relation':edge})
    assert module().preview(s,p,source)['can_submit']
    event=submit(s,p,source); v=review.view(s,p,receipt.version_id)
    present=any(all(r[k]==value for k,value in edge.items()) for r in v['analysis']['relations'])
    assert present==(kind=='relation_add') and v['status']=='draft'
    assert module().for_review(s,p,event)['draft_id']==source


def test_restored_input_retains_both_its_original_source_and_its_formal_handoff(schools):
    from moa import school_draft_restore
    m,s,p,_=schools; receipt,_=converted(m,s,p)
    first=draft_save(s,p,receipt.version_id); head=draft_save(s,p,receipt.version_id,expected=first,comment='다음 저장')
    row=drafts.get(s,p,first)
    restored=school_draft_restore.restore(s,p,first,expected_draft=head,base_hash=row['base_hash'],expected_review=row['base_review'])
    before=[tuple(r) for r in s.db.execute('SELECT * FROM school_review_drafts ORDER BY sequence')]
    event=submit(s,p,restored)
    assert drafts.get(s,p,restored)['restored_from']==first
    assert module().for_review(s,p,event)['draft_id']==restored
    assert module().for_draft(s,p,first) is None
    assert [tuple(r) for r in s.db.execute('SELECT * FROM school_review_drafts ORDER BY sequence')]==before


def test_parallel_submission_and_replay_cannot_duplicate_formal_history(schools):
    m,s,p,_=schools; receipt,_=converted(m,s,p); source=draft_save(s,p,receipt.version_id)
    args=drafts.get(s,p,source)
    def attempt(_):
        with Store(s.root) as other:
            try: return module().submit(other,p,source,base_hash=args['base_hash'],expected_review=args['base_review'])
            except review.Conflict: return 'conflict'
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(attempt,range(2)))
    assert results.count('conflict')==1
    before=list(s.db.iterdump())
    with pytest.raises(review.Conflict): submit(s,p,source)
    assert list(s.db.iterdump())==before
    assert s.db.execute('SELECT count(*) FROM school_structure_reviews').fetchone()[0]==1
    assert s.db.execute('SELECT count(*) FROM school_review_draft_handoffs').fetchone()[0]==1


def test_receipt_failure_rolls_back_formal_event_without_touching_saved_inputs(schools):
    m,s,p,_=schools; receipt,_=converted(m,s,p); source=draft_save(s,p,receipt.version_id)
    s.db.executescript("CREATE TRIGGER synthetic_handoff_failure BEFORE INSERT ON school_review_draft_handoffs BEGIN SELECT RAISE(ABORT,'synthetic failure'); END;")
    before=list(s.db.iterdump())
    with pytest.raises(sqlite3.IntegrityError): submit(s,p,source)
    assert list(s.db.iterdump())==before and not s.db.in_transaction


def test_preview_has_one_snapshot_and_submit_rechecks_a_newer_draft_after_initial_read(schools,monkeypatch):
    m,s,p,_=schools; receipt,_=converted(m,s,p); source=draft_save(s,p,receipt.version_id)
    original=module().compare.compare
    def concurrent_preview(*args,**kwargs):
        result=original(*args,**kwargs)
        with Store(s.root) as other: draft_save(other,p,receipt.version_id,expected=source,comment='미리보기 중 저장')
        return result
    monkeypatch.setattr(module().compare,'compare',concurrent_preview)
    data=module().preview(s,p,source)
    assert data['can_submit'] and data['head']['id']==source
    monkeypatch.setattr(module().compare,'compare',original)
    assert not module().preview(s,p,source)['can_submit']
    newest=drafts.listing(s,p)['rows'][0]['id'];original_save=review.save
    def concurrent_submit(*args,**kwargs):
        with Store(s.root) as other: draft_save(other,p,receipt.version_id,expected=newest,comment='정식 저장 직전 다른 탭 저장')
        return original_save(*args,**kwargs)
    monkeypatch.setattr(review,'save',concurrent_submit)
    with pytest.raises(review.Conflict): submit(s,p,newest)
    assert not s.db.execute('SELECT 1 FROM school_structure_reviews').fetchone()
    assert not s.db.execute('SELECT 1 FROM school_review_draft_handoffs').fetchone()
    assert s.db.execute('SELECT count(*) FROM school_review_drafts').fetchone()[0]==3


@pytest.mark.parametrize('invalid,reason',[
    ('reason','reason_required'),('text','invalid_review_input'),('unchanged','invalid_review_input'),
    ('partial_relation','invalid_review_input'),('foreign_endpoint','invalid_review_input')])
def test_unfinished_or_no_change_inputs_stay_private_and_cannot_create_formal_event(schools,invalid,reason):
    m,s,p,_=schools; receipt,_=converted(m,s,p); v=review.view(s,p,receipt.version_id); b=v['analysis']['blocks'][0]
    action={'kind':'block','block_id':b['id'],'block_kind':b['kind'],'text':'수정 입력'}
    comment='저장 사유'
    if invalid=='reason': comment=' \n '
    elif invalid=='text': action['text']=' \n '
    elif invalid=='unchanged': action['text']=b['text']
    else: action={'kind':'relation_add','relation':{'kind':'part_of','source':'' if invalid=='partial_relation' else 'foreign/b0','target':b['id']}}
    source=draft_save(s,p,receipt.version_id,action=action,comment=comment)
    before=list(s.db.iterdump()); data=module().preview(s,p,source)
    assert not data['can_submit'] and reason in data['submit_reasons']
    with pytest.raises((ValueError,review.Conflict)): submit(s,p,source)
    assert list(s.db.iterdump())==before


@pytest.mark.parametrize('change,reason',[
    ('head','newer_input'),('formal','review_changed'),('conversion','conversion_changed'),
    ('correction','receiving_changed'),('pending','conversion_pending'),('failed','conversion_failed'),
    ('reader','read_only')])
def test_current_scope_and_latest_input_are_rechecked_before_writing(schools,change,reason):
    m,s,p,_=schools; receipt,version=converted(m,s,p); source=draft_save(s,p,receipt.version_id)
    assert module().preview(s,p,source)['can_submit']
    if change=='head': draft_save(s,p,receipt.version_id,expected=source,comment='다른 탭 최신 저장')
    elif change=='formal': formal_save(review,s,p,receipt.version_id,{'kind':'confirm','acknowledge_warnings':True})
    elif change=='reader': svc.grant_member(s,p.school,p.subject,'reader')
    elif change=='correction': converted(m,s,p,price='15000')
    else:
        svc.request_conversion(s,p,receipt.version_id)
        if change=='conversion': assert svc.process_conversions(s)==['done']
        if change=='failed':
            s.db.execute("UPDATE mobile_notices SET state='conversion_failed' WHERE id=?",(version['mobile_id'],));s.db.commit()
    before=list(s.db.iterdump()); data=module().preview(s,p,source)
    assert not data['can_submit'] and reason in data['submit_reasons']
    with pytest.raises(PermissionError if change=='reader' else review.Conflict): submit(s,p,source)
    assert list(s.db.iterdump())==before


@pytest.mark.parametrize('change',['hash','parent'])
def test_submitted_base_must_equal_the_saved_input_basis(schools,change):
    m,s,p,_=schools; receipt,_=converted(m,s,p); source=draft_save(s,p,receipt.version_id)
    before=list(s.db.iterdump())
    with pytest.raises(review.Conflict): submit(s,p,source,**({'base_hash':'0'*64} if change=='hash' else {'expected_review':'0'*32}))
    assert list(s.db.iterdump())==before


@pytest.mark.parametrize('change',['action','comment','confirm'])
def test_review_save_cannot_attach_forged_draft_provenance(schools,change):
    m,s,p,_=schools; receipt,_=converted(m,s,p); source=draft_save(s,p,receipt.version_id); row=drafts.get(s,p,source)
    action=row['payload']['action']; comment=row['payload']['comment']
    if change=='action': action={**action,'text':'not the stored input'}
    elif change=='comment': comment='not the stored reason'
    else: action={'kind':'confirm','acknowledge_warnings':True}
    before=list(s.db.iterdump())
    with pytest.raises(ValueError):
        review.save(s,p,receipt.version_id,revision_id=row['conversion_revision'],base_hash=row['base_hash'],
            expected_review=row['base_review'],action=action,comment=comment,source_draft=source)
    assert list(s.db.iterdump())==before


def test_owner_only_receipts_survive_later_reviews_without_entering_shared_data(schools):
    m,s,p,foreign=schools; receipt,_=converted(m,s,p)
    b=next(b for b in review.view(s,p,receipt.version_id)['analysis']['blocks'] if b['kind']=='body')
    source=draft_save(s,p,receipt.version_id,action={'kind':'block','block_id':b['id'],'block_kind':'body','text':'검수한 본문'})
    event=submit(s,p,source); record=module().for_draft(s,p,source)
    colleague=svc.Principal(p.school,'colleague');svc.grant_member(s,p.school,colleague.subject,'manager')
    assert module().for_review(s,colleague,event) is None
    with pytest.raises(LookupError): module().for_draft(s,colleague,source)
    with pytest.raises(LookupError): module().preview(s,foreign,source)
    with pytest.raises(LookupError): submit(s,foreign,source)
    formal_save(review,s,p,receipt.version_id,{'kind':'confirm','acknowledge_warnings':True})
    assert module().for_draft(s,p,source)==record
    assert source not in json.dumps(review.view(s,p,receipt.version_id))
    assert source not in json.dumps(review.history(s,p,receipt.version_id,revision_id=record['conversion_revision']))
    for sql in ('UPDATE school_review_draft_handoffs SET draft_id=review_id','DELETE FROM school_review_draft_handoffs'):
        with pytest.raises(sqlite3.IntegrityError): s.db.execute(sql)
        s.db.rollback()


def test_receipt_insert_rejects_draft_from_other_owner_with_identical_payload(schools):
    m,s,p,_=schools; receipt,_=converted(m,s,p); source=draft_save(s,p,receipt.version_id); row=drafts.get(s,p,source)
    colleague=svc.Principal(p.school,'colleague');svc.grant_member(s,p.school,colleague.subject,'manager')
    foreign=draft_save(s,colleague,receipt.version_id,action=row['payload']['action'],comment=row['payload']['comment'])
    event=formal_save(review,s,p,receipt.version_id,row['payload']['action'],comment=row['payload']['comment'])
    before=list(s.db.iterdump())
    with pytest.raises(sqlite3.IntegrityError): s.db.execute('INSERT INTO school_review_draft_handoffs VALUES(?,?)',(event,foreign))
    s.db.rollback();assert list(s.db.iterdump())==before


def test_v6_upgrade_is_explicit_additive_backed_up_and_v5_regular_review_still_works(schools):
    m,s,p,_=schools; receipt,_=converted(m,s,p); source=draft_save(s,p,receipt.version_id)
    s.db.executescript('DROP TABLE IF EXISTS school_warning_search_events; DROP TABLE IF EXISTS school_warning_searches; DROP TABLE IF EXISTS school_receipt_searches; DROP TABLE IF EXISTS school_confirmed_export_downloads; DROP TABLE IF EXISTS school_confirmed_export_list_events; DROP TABLE school_confirmed_export_items; DROP TABLE school_confirmed_export_lists; DROP TABLE school_review_draft_handoffs; DELETE FROM school_service_migrations WHERE version>=6;')
    before=list(s.db.iterdump()); version=s.db.execute('PRAGMA user_version').fetchone()[0]; original=files(s)
    data=module().preview(s,p,source)
    assert not data['can_submit'] and 'handoff_migration_required' in data['submit_reasons']
    assert module().for_draft(s,p,source) is None
    with pytest.raises(review.Conflict): submit(s,p,source)
    assert list(s.db.iterdump())==before
    second,_=converted(m,s,p,key='another')
    formal_save(review,s,p,second.version_id,{'kind':'confirm','acknowledge_warnings':True})
    before=list(s.db.iterdump());original=files(s)
    backup=svc.migrate(s)
    assert backup.parent==s.root/'db/backups'
    with sqlite3.connect(backup) as old:
        assert old.execute('SELECT max(version) FROM school_service_migrations').fetchone()[0]==5
        assert list(old.iterdump())==before
    assert set(before)<=set(s.db.iterdump()) and module().preview(s,p,source)['can_submit']
    assert s.db.execute('PRAGMA user_version').fetchone()[0]==version and files(s)==original
    after=list(s.db.iterdump());assert svc.migrate(s) is None and list(s.db.iterdump())==after
    s.db.execute("INSERT INTO school_service_migrations VALUES(999,'synthetic future')");s.db.commit()
    before=list(s.db.iterdump())
    with pytest.raises(RuntimeError): svc.migrate(s)
    assert list(s.db.iterdump())==before


def http_setup(school_web):
    _,s,base=school_web; a=client();login(a,base);p=svc.Principal('A','a')
    receipt,_,source=http_draft(a,base,s,changes={'text':'저장한 <script>합성 입력</script>\n ','comment':'  정식 검수 사유  '})
    doc,headers=page(a,base,route(source)); f=doc.select_one('[data-draft-submit-form]')
    assert f is not None
    values=inputs(f);values['acknowledge']='yes'
    return s,base,a,p,receipt,source,doc,headers,values


def test_http_preview_explicit_save_and_relogin_show_only_owners_private_source(school_web):
    s,base,a,p,receipt,source,doc,headers,values=http_setup(school_web)
    before=list(s.db.iterdump());original=files(s)
    again,_=page(a,base,route(source));assert list(s.db.iterdump())==before
    assert again.select_one('#draft-submit-preview') and not again.select('textarea')
    assert not again.select_one('#draft-submit-preview script') and '저장한 <script>합성 입력</script>' not in str(again)
    assert 'no-store' in headers['Cache-Control'] and "default-src 'self'" in headers['Content-Security-Policy']
    checkbox=again.select_one('[name=acknowledge]')
    assert checkbox['type']=='checkbox' and checkbox.has_attr('required') and not checkbox.has_attr('checked')
    assert fetch(a,base,'/structure-draft/submit',fields=values)[0]==200
    event=review.view(s,p,receipt.version_id)['latest']['id'];record=module().for_review(s,p,event)
    assert record['draft_id']==source
    after=list(s.db.iterdump())
    assert fetch(a,base,'/structure-draft/submit',fields=values)[0]==409
    fresh=client();login(fresh,base)
    for route_ in ('/structure-draft?id='+source,route(source),'/structure-review?id='+receipt.version_id,
                   '/structure-history?id='+receipt.version_id):
        body=fetch(fresh,base,route_)[1].decode()
        assert source in body
    other=client();login(other,base,'reader','synthetic-reader-only')
    for route_ in ('/structure-review?id='+receipt.version_id,'/structure-history?id='+receipt.version_id):
        assert source not in fetch(other,base,route_)[1].decode()
    for route_ in ('/structure-review?id='+receipt.version_id+'&format=json',
                   '/review-html?id='+receipt.version_id+'&revision='+record['conversion_revision']+'&event='+event):
        status,body,_=fetch(fresh,base,route_);assert status==200 and source not in body.decode()
    assert list(s.db.iterdump())==after and files(s)==original


@pytest.mark.parametrize('mutation,status',[
    ('no_ack',400),('bad_ack',400),('extra_payload',400),('bad_csrf',403),('origin',403),
    ('duplicate_id',400),('duplicate_ack',400),('invalid_utf8',400),('too_large',413)])
def test_http_confirmation_is_exact_authenticated_and_bounded(school_web,mutation,status):
    s,base,a,p,receipt,source,doc,headers,values=http_setup(school_web)
    before=list(s.db.iterdump());data=None;request_headers={}
    if mutation=='no_ack': del values['acknowledge']
    elif mutation=='bad_ack': values['acknowledge']='no'
    elif mutation=='extra_payload': values['text']='must use saved source'
    elif mutation=='bad_csrf': values['csrf']='invalid'
    elif mutation=='origin': request_headers['Origin']='https://evil.example.test'
    elif mutation.startswith('duplicate_'):
        field='draft_id' if mutation=='duplicate_id' else 'acknowledge'
        data=(urlencode(values)+'&'+urlencode({field:values[field]})).encode()
    elif mutation=='invalid_utf8': data=urlencode(values).encode()+b'&text=%FF'
    elif mutation=='too_large': data=b'x'*4097
    result=fetch(a,base,'/structure-draft/submit',fields=values if data is None else None,data=data,headers=request_headers)
    assert result[0]==status and list(s.db.iterdump())==before


def test_http_rechecks_owner_school_current_head_and_write_permission_after_preview(school_web):
    s,base,a,p,receipt,source,doc,headers,values=http_setup(school_web)
    other=client();values_other={**values,'csrf':login(other,base,'reader','synthetic-reader-only')}
    svc.grant_member(s,'A','reader','manager')
    assert fetch(other,base,route(source))[0]==404
    assert fetch(other,base,'/structure-draft/submit',fields=values_other)[0]==404
    foreign=client();values_foreign={**values,'csrf':login(foreign,base,'b','synthetic-b-only')}
    assert fetch(foreign,base,route(source))[0]==404
    assert fetch(foreign,base,'/structure-draft/submit',fields=values_foreign)[0]==404
    assert fetch(client(),base,route(source))[0]==401
    draft_save(s,p,receipt.version_id,expected=source,comment='동시 저장')
    before=list(s.db.iterdump())
    assert fetch(a,base,'/structure-draft/submit',fields=values)[0]==409
    assert list(s.db.iterdump())==before
    svc.grant_member(s,'A','a','reader')
    assert fetch(a,base,'/structure-draft/submit',fields=values)[0]==403
    svc.grant_member(s,'A','a','reader',active=False)
    assert fetch(a,base,route(source))[0]==401


def test_http_v5_missing_handoff_schema_never_upgrades_implicitly(school_web):
    s,base,a,p,receipt,source,doc,headers,values=http_setup(school_web)
    s.db.executescript('DROP TABLE IF EXISTS school_warning_search_events; DROP TABLE IF EXISTS school_warning_searches; DROP TABLE IF EXISTS school_receipt_searches; DROP TABLE IF EXISTS school_confirmed_export_downloads; DROP TABLE IF EXISTS school_confirmed_export_list_events; DROP TABLE school_confirmed_export_items; DROP TABLE school_confirmed_export_lists; DROP TABLE school_review_draft_handoffs; DELETE FROM school_service_migrations WHERE version>=6;')
    before=list(s.db.iterdump());readonly,_=page(a,base,route(source))
    assert not readonly.select_one('[data-draft-submit-form]')
    assert fetch(a,base,'/structure-draft/submit',fields=values)[0]==409
    assert list(s.db.iterdump())==before


@pytest.mark.parametrize('query',['','?id=bad','?id=%FF','?id='+'0'*32+'&id='+'1'*32,
    '?id='+'0'*32+'&school=A','?id='+'x'*4097])
def test_http_query_is_strict_and_read_only(school_web,query):
    _,s,base=school_web;a=client();login(a,base);before=list(s.db.iterdump())
    assert fetch(a,base,'/structure-draft/submit'+query)[0]==400
    assert list(s.db.iterdump())==before
