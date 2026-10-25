"""Real HTTP autosave contracts; synthetic SSD-isolated DB, no models."""
import json
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest

from moa import school_service as svc, school_review as review
from test_school_structure import converted
from test_school_structure_review import save as formal_save
from test_web_school import school_web, client, fetch, login
from test_school_review_drafts import page, form, inputs, content_without_drafts

PATH='/structure-draft/autosave'


def setup_input(s, a, base):
    receipt,_=converted(svc,s,svc.Principal('A','a'))
    doc,_=page(a,base,'/structure-review?id='+receipt.version_id)
    values=inputs(form(doc)); values.update(text='입력 중 <script>not-executable</script>\n둘째 줄',comment='')
    return receipt,values


def test_autosave_ack_and_lost_response_retry_do_not_duplicate_or_change_reviews(school_web):
    _,s,base=school_web; a=client(); login(a,base)
    receipt,values=setup_input(s,a,base); before=content_without_drafts(s)
    status,body,headers=fetch(a,base,PATH,fields=values)
    assert status==200, body.decode()
    assert headers['Content-Type'].startswith('application/json')
    result=json.loads(body)
    assert set(result)=={'id','created_at','resume'} and len(result['id'])==32
    assert values['text'].encode() not in body and 'csrf'.encode() not in body
    assert 'no-store' in headers['Cache-Control']
    assert json.loads(fetch(a,base,PATH,fields=values)[1])==result  # ACK lost: same request.
    assert json.loads(fetch(a,base,PATH,fields={**values,'expected_draft':result['id']})[1])==result
    assert s.db.execute('SELECT count(*) FROM school_review_drafts').fetchone()[0]==1
    assert content_without_drafts(s)==before
    restored=inputs(form(page(a,base,result['resume'])[0]))
    assert restored['text']==values['text'] and restored['comment']==''
    assert review.view(s,svc.Principal('A','a'),receipt.version_id)['status']=='unreviewed'


def test_autosave_cannot_overwrite_another_tab_or_ack_an_older_different_request(school_web):
    _,s,base=school_web; a=client(); login(a,base)
    receipt,values=setup_input(s,a,base)
    first=json.loads(fetch(a,base,PATH,fields=values)[1])
    assert fetch(a,base,PATH,fields={**values,'text':'別の入力'})[0]==409
    second={**values,'text':'新しい入力','expected_draft':first['id']}
    assert fetch(a,base,PATH,fields=second)[0]==200
    assert fetch(a,base,PATH,fields=values)[0]==409
    formal_save(review,s,svc.Principal('A','a'),receipt.version_id,{'kind':'confirm','acknowledge_warnings':True})
    before=list(s.db.iterdump())
    assert fetch(a,base,PATH,fields=second)[0]==409  # Even identical data cannot bypass a new base.
    assert list(s.db.iterdump())==before


@pytest.mark.parametrize('change,status',[
    ({'csrf':'bad'},403),({'subject':'other'},400),({'action':'confirm'},400),
    ({'expected_draft':'bad'},400),({'text':'x\0y'},400),({'comment':'x'*2001},400),
])
def test_autosave_reuses_strict_validation_and_writes_nothing_on_rejection(school_web,change,status):
    _,s,base=school_web; a=client(); login(a,base)
    _,values=setup_input(s,a,base); before=list(s.db.iterdump())
    assert fetch(a,base,PATH,fields={**values,**change})[0]==status
    assert list(s.db.iterdump())==before


def test_autosave_requires_owner_session_origin_and_current_writer_role(school_web):
    _,s,base=school_web; a,b=client(),client(); login(a,base)
    csrf_b=login(b,base,'b','synthetic-b-only')
    _,values=setup_input(s,a,base); before=list(s.db.iterdump())
    assert fetch(client(),base,PATH,fields=values)[0]==401
    assert fetch(b,base,PATH,fields={**values,'csrf':csrf_b})[0]==404
    assert fetch(a,base,PATH,fields=values,headers={'Origin':'https://evil.example.test'})[0]==403
    assert list(s.db.iterdump())==before
    svc.grant_member(s,'A','a','reader')
    before=list(s.db.iterdump())
    assert fetch(a,base,PATH,fields=values)[0]==403
    assert list(s.db.iterdump())==before
    assert s.db.execute('SELECT count(*) FROM school_review_drafts').fetchone()[0]==0


def test_manual_explicit_saves_remain_append_only_even_for_identical_content(school_web):
    _,s,base=school_web; a=client(); login(a,base)
    _,values=setup_input(s,a,base)
    assert fetch(a,base,'/structure-draft/save',fields=values)[0]==200
    first=s.db.execute('SELECT id FROM school_review_drafts').fetchone()[0]
    assert fetch(a,base,'/structure-draft/save',fields={**values,'expected_draft':first})[0]==200
    assert s.db.execute('SELECT count(*) FROM school_review_drafts').fetchone()[0]==2


def test_autosave_rejects_duplicate_invalid_encoding_and_oversize_without_writes(school_web):
    _,s,base=school_web; a=client(); login(a,base)
    _,values=setup_input(s,a,base); raw=urlencode(values).encode(); before=list(s.db.iterdump())
    assert fetch(a,base,PATH,data=raw+b'&text=duplicate')[0]==400
    assert fetch(a,base,PATH,data=raw.replace(b'comment=',b'comment=%FF'))[0]==400
    assert fetch(a,base,PATH,data=b'x'*300001)[0]==413
    assert list(s.db.iterdump())==before


def test_autosave_rate_limit_is_bounded_per_session_without_consuming_extra_snapshots(school_web,monkeypatch):
    from moa import school_draft_web
    clock=[10.0]; monkeypatch.setattr(school_draft_web,'time',SimpleNamespace(monotonic=lambda:clock[0]))
    _,s,base=school_web; a=client(); login(a,base)
    _,values=setup_input(s,a,base)
    for _ in range(120): assert fetch(a,base,PATH,fields=values)[0]==200
    before=list(s.db.iterdump())
    assert fetch(a,base,PATH,fields=values)[0]==429
    assert list(s.db.iterdump())==before
    clock[0]+=61
    assert fetch(a,base,PATH,fields=values)[0]==200
    assert s.db.execute('SELECT count(*) FROM school_review_drafts').fetchone()[0]==1


def test_only_editable_draft_forms_get_autosave_and_no_read_writes_schema(school_web):
    _,s,base=school_web; a=client(); login(a,base)
    receipt,_=setup_input(s,a,base)
    before=list(s.db.iterdump()); doc,_=page(a,base,'/structure-review?id='+receipt.version_id)
    assert form(doc).has_attr('data-draft-autosave') and form(doc).select_one('[data-draft-status][role=status]')
    assert form(doc).select_one('[data-draft-retry][type=button]')
    assert not form(doc,'confirm').has_attr('data-draft-autosave')
    assert fetch(a,base,'/static/school-draft.js')[0]==200
    assert fetch(client(),base,'/static/school-draft.js')[0]==401
    assert list(s.db.iterdump())==before
    svc.grant_member(s,'A','a','reader')
    assert not page(a,base,'/structure-review?id='+receipt.version_id)[0].select('[data-draft-autosave]')
    svc.grant_member(s,'A','a','uploader')
    s.db.executescript('DROP TABLE school_review_draft_restores; DROP TABLE school_review_drafts; DELETE FROM school_service_migrations WHERE version>=4;')
    before=list(s.db.iterdump())
    assert not page(a,base,'/structure-review?id='+receipt.version_id)[0].select('[data-draft-autosave]')
    assert fetch(a,base,PATH,fields=inputs(form(doc)))[0]==409
    assert list(s.db.iterdump())==before


@pytest.mark.parametrize('kind,changes',[
    ('block',{'text':'','comment':''}),
    ('relation_add',{'relation_source':'미완성','relation_target':'','comment':''}),
    ('relation_remove',{'comment':'아직 작성 중인 관계 사유\n둘째 줄'}),
])
def test_autosave_recovers_partial_input_for_each_supported_form(school_web,kind,changes):
    _,s,base=school_web; a=client(); login(a,base)
    receipt,_=converted(svc,s,svc.Principal('A','a'))
    doc,_=page(a,base,'/structure-review?id='+receipt.version_id)
    values={**inputs(form(doc,kind)),**changes}; before=content_without_drafts(s)
    status,body,_=fetch(a,base,PATH,fields=values); assert status==200
    restored=inputs(form(page(a,base,json.loads(body)['resume'])[0],kind))
    assert all(restored[k]==v for k,v in changes.items())
    assert content_without_drafts(s)==before


def test_autosave_preserves_saved_list_context_without_moving_its_cursor(school_web):
    from test_school_saved_queue import start, pair, save_list
    _,s,base=school_web; a=client(); login(a,base); pair(s)
    doc,_=save_list(a,base,start(a,base)[0]); values=inputs(form(doc)); values['comment']='保存途中'
    cursor=[tuple(r) for r in s.db.execute('SELECT * FROM school_review_lists')]
    status,body,_=fetch(a,base,PATH,fields=values); assert status==200
    result=json.loads(body)
    assert 'saved_id='+values['saved_id'] in result['resume'] and 'saved_item=1' in result['resume']
    assert [tuple(r) for r in s.db.execute('SELECT * FROM school_review_lists')]==cursor
    snapshot=list(s.db.iterdump())
    assert fetch(a,base,PATH,fields={**values,'saved_item':'2'})[0]==400
    assert list(s.db.iterdump())==snapshot
