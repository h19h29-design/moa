"""Saved/current diagnosis, explicit fresh preparation and evidence preservation."""
from urllib.parse import urlencode

import pytest

from moa import school_confirmed_export as batch, school_confirmed_export_saved as saved
from moa import school_confirmed_export_refresh as refresh, school_review as review
from moa import school_review_export as export, school_service as svc
from moa.core import Store
from test_school_service import schools
from test_school_structure import converted
from test_school_structure_review import save
from test_school_review_check import originals
from test_school_confirmed_export import confirmed, verify_zip
from test_school_confirmed_export_saved import IDENT, page, inputs, http_saved, drop_v7
from test_web_school import school_web, client, fetch, login


def url(ident=IDENT):return '/confirmed-export/saved/status?'+urlencode({'id':ident})


def setup_list(s,p):
    items=[confirmed(s,p,key='two',price='11000'),confirmed(s,p)]
    saved.create(s,p,IDENT,batch.prepare(s,p,items)['pins'],name='<합성> 저장 묶음')
    return items


def reconfirm(s,p,item):
    save(review,s,p,item['id'],{'kind':'reopen'})
    return {**item,'event':save(review,s,p,item['id'],{'kind':'confirm','acknowledge_warnings':True})}


def test_unchanged_status_and_fresh_preparation_are_bounded_read_only_and_preserve_history(schools):
    _,s,p,_=schools;items=setup_list(s,p);frozen=saved.get(s,p,IDENT)
    before=list(s.db.iterdump());files=originals(s);changes=s.db.total_changes
    previous=export.snapshot(s,p,items[0]['id'],revision_id=items[0]['revision'],event_id=items[0]['event'])
    html,archive=export.render_html(previous),export.bundle(s,previous)
    s.db.execute('PRAGMA query_only=ON')
    data=refresh.inspect(s,p,IDENT)
    assert data['can_prepare'] and not data['changed'] and not data['limit']
    assert all(not item['reasons'] for item in data['items'])
    assert data['prepared']['pins']==frozen['pins']
    assert refresh.prepare(s,p,IDENT,expected=data['expected'])==data
    with Store(s.root) as reopened:assert refresh.inspect(reopened,p,IDENT)==data
    assert saved.get(s,p,IDENT)==frozen and list(s.db.iterdump())==before and s.db.total_changes==changes
    assert originals(s)==files and export.render_html(previous)==html and export.bundle(s,previous)==archive


@pytest.mark.parametrize('change,reason,eligible',[
    ('reopen','confirmation_pending',False),('reconfirm','confirmation_changed',True),
    ('edit','confirmation_pending',False),('conversion','conversion_changed',False),
    ('conversion_confirm','conversion_changed',True),('receipt','receiving_changed',False),
    ('receipt_confirm','receiving_changed',True),('processing','processing',False),
    ('conversion_failed','conversion_failed',False),('hash','snapshot_changed',False),
    ('missing_original','snapshot_changed',False),('symlink_original','snapshot_changed',False),
    ('missing_revision','unavailable',False),
])
def test_each_document_explains_changes_and_never_uses_an_older_confirmation(schools,monkeypatch,change,reason,eligible):
    _,s,p,_=schools;items=setup_list(s,p);item=items[-1];frozen=saved.get(s,p,IDENT)
    if change in ('reopen','reconfirm','edit'):
        save(review,s,p,item['id'],{'kind':'reopen'})
        if change=='reconfirm':save(review,s,p,item['id'],{'kind':'confirm','acknowledge_warnings':True})
        if change=='edit':
            block=next(b for b in review.view(s,p,item['id'])['analysis']['blocks'] if b['kind']=='body')
            save(review,s,p,item['id'],{'kind':'block','block_id':block['id'],'block_kind':'body','text':'수정된 합성 문장'})
    elif change.startswith('receipt'):
        receipt,_=converted(svc,s,p,price='15000')
        if change.endswith('confirm'):save(review,s,p,receipt.version_id,{'kind':'confirm','acknowledge_warnings':True})
    elif change.startswith('conversion') and change!='conversion_failed':
        svc.request_conversion(s,p,item['id']);assert svc.process_conversions(s)==['done']
        if change.endswith('confirm'):save(review,s,p,item['id'],{'kind':'confirm','acknowledge_warnings':True})
    elif change in ('processing','conversion_failed','missing_revision'):
        sql='latest_revision=NULL' if change=='missing_revision' else 'state=?'
        s.db.execute('UPDATE mobile_notices SET '+sql+' WHERE id=(SELECT mobile_id FROM school_service_versions WHERE id=?)',
            (item['id'],) if change=='missing_revision' else (change,item['id']));s.db.commit()
    elif change=='hash':
        original=export.snapshot
        def different(*a,**kw):
            data=original(*a,**kw)
            if data['notice_version']==item['id']:data['analysis']['complete']=not data['analysis']['complete']
            return data
        monkeypatch.setattr(export,'snapshot',different)
    else:
        obj=s.object_path(svc.get_version(s,p,item['id'])['files'][0]['sha256']);raw=obj.read_bytes();obj.unlink()
        if change=='symlink_original':
            target=s.root/'synthetic-indirect';target.write_bytes(raw);obj.symlink_to(target)
    before=list(s.db.iterdump());changes=s.db.total_changes
    data=refresh.inspect(s,p,IDENT)
    assert not data['items'][0]['reasons'] and reason in data['items'][1]['reasons']
    assert data['can_prepare'] is eligible and data['changed']
    if eligible:
        result=refresh.prepare(s,p,IDENT,expected=data['expected'])
        assert result['prepared']['pins'][0]==frozen['pins'][0]
        assert result['prepared']['pins'][1]!=frozen['pins'][1]
    else:
        assert not data['expected'] and data['prepared'] is None
        with pytest.raises(review.Conflict):refresh.prepare(s,p,IDENT,expected='0'*64)
    assert saved.get(s,p,IDENT)==frozen and list(s.db.iterdump())==before and s.db.total_changes==changes


def test_corrected_receiving_version_without_conversion_cannot_fall_back_to_old_confirmed_copy(schools):
    _,s,p,_=schools;setup_list(s,p)
    latest=svc.upload(s,p,svc.IncomingPost('trip','새 정정본','새 합성 본문',()))
    before=list(s.db.iterdump());data=refresh.inspect(s,p,IDENT)
    assert data['items'][1]['current']['id']==latest.version_id
    assert 'receiving_changed' in data['items'][1]['reasons'] and not data['can_prepare']
    assert list(s.db.iterdump())==before


def test_current_original_problem_is_attributed_to_its_document_after_reconfirmation(schools):
    _,s,p,_=schools;items=setup_list(s,p);reconfirm(s,p,items[1])
    s.object_path(svc.get_version(s,p,items[1]['id'])['files'][0]['sha256']).unlink()
    data=refresh.inspect(s,p,IDENT)
    assert not data['items'][0]['reasons'] and 'unavailable' in data['items'][1]['reasons']
    assert not data['can_prepare']


def test_aggregate_limits_block_preparation_without_excluding_any_document(schools,monkeypatch):
    _,s,p,_=schools;items=setup_list(s,p)
    size=batch.prepare(s,p,[items[0]])['original_bytes']
    monkeypatch.setattr(batch,'MAX_ORIGINAL_BYTES',size+10)
    before=list(s.db.iterdump());data=refresh.inspect(s,p,IDENT)
    assert data['limit'] and not data['can_prepare'] and len(data['items'])==2
    with pytest.raises(review.Conflict):refresh.prepare(s,p,IDENT,expected='0'*64)
    assert list(s.db.iterdump())==before


@pytest.mark.parametrize('token',['','x'*64,'A'*64,'0'*63,'0'*65,None,{},1])
def test_invalid_comparison_fingerprint_never_prepares_or_writes(schools,token):
    _,s,p,_=schools;setup_list(s,p);before=list(s.db.iterdump())
    with pytest.raises(ValueError):refresh.prepare(s,p,IDENT,expected=token)
    assert list(s.db.iterdump())==before


def test_a_new_confirmation_after_display_invalidates_the_submitted_fingerprint(schools):
    _,s,p,_=schools;items=setup_list(s,p);data=refresh.inspect(s,p,IDENT)
    reconfirm(s,p,items[1]);before=list(s.db.iterdump())
    with pytest.raises(review.Conflict):refresh.prepare(s,p,IDENT,expected=data['expected'])
    assert list(s.db.iterdump())==before


def test_comparison_reads_all_documents_at_one_sqlite_snapshot_and_next_request_sees_change(schools,monkeypatch):
    _,s,p,_=schools;items=setup_list(s,p);expected=refresh.inspect(s,p,IDENT)
    original=refresh._item;fired=False
    def interleave(*a,**kw):
        nonlocal fired
        result=original(*a,**kw)
        if not fired:
            fired=True
            with Store(s.root) as other:reconfirm(other,p,items[1])
        return result
    monkeypatch.setattr(refresh,'_item',interleave)
    assert refresh.inspect(s,p,IDENT)==expected
    assert refresh.inspect(s,p,IDENT)['expected']!=expected['expected']


def test_owner_membership_and_write_permission_are_rechecked_and_migration_is_never_implicit(schools):
    _,s,p,other=schools;setup_list(s,p);expected=refresh.inspect(s,p,IDENT)['expected']
    svc.grant_member(s,p.school,'second','uploader');second=svc.Principal(p.school,'second')
    for who in (other,second):
        with pytest.raises(LookupError):refresh.inspect(s,who,IDENT)
        with pytest.raises(LookupError):refresh.prepare(s,who,IDENT,expected=expected)
    svc.grant_member(s,p.school,p.subject,'reader')
    assert refresh.inspect(s,p,IDENT)['can_prepare']
    with pytest.raises(PermissionError):refresh.prepare(s,p,IDENT,expected=expected)
    svc.grant_member(s,p.school,p.subject,'uploader',active=False)
    with pytest.raises(PermissionError):refresh.inspect(s,p,IDENT)
    svc.grant_member(s,p.school,p.subject,'uploader');drop_v7(s)
    before=list(s.db.iterdump())
    with pytest.raises(review.Conflict):refresh.inspect(s,p,IDENT)
    assert list(s.db.iterdump())==before and not saved.available(s)


def refresh_form(a,base,ident):
    status,raw,_=fetch(a,base,url(ident));assert status==200
    doc=page(raw);form=doc.select_one('[data-export-refresh]');assert form
    ack=form.select_one('[name=acknowledge]');assert ack.has_attr('required') and not ack.has_attr('checked')
    fields=inputs(form);fields['acknowledge']='yes';return doc,fields


def test_http_rebuild_is_explicit_separate_and_survives_relogin_with_old_history_untouched(school_web):
    _,s,base=school_web;p=svc.Principal('A','a');items=[confirmed(s,p,key='two'),confirmed(s,p)]
    a=client();csrf=login(a,base)
    _,old,_,_=http_saved(a,base,csrf,items,name='<img src=x> 이전 묶음')
    frozen=saved.get(s,p,old['id']);old_saved=export.snapshot(s,p,items[1]['id'],revision_id=items[1]['revision'],event_id=items[1]['event'])
    old_html,old_zip=export.render_html(old_saved),export.bundle(s,old_saved)
    items[1]=reconfirm(s,p,items[1]);before=list(s.db.iterdump());files=originals(s)
    status,raw,_=fetch(a,base,'/confirmed-export/saved/preview?id='+old['id'])
    assert status==409 and page(raw).select_one('[data-export-status-link]')['href']==url(old['id'])
    doc,fields=refresh_form(a,base,old['id'])
    assert len(doc.select('[data-export-status-item]'))==2 and doc.select('[data-export-change=confirmation_changed]')
    assert not doc.select('img') and '<img src=x>' in doc.get_text()
    assert fetch(a,base,'/confirmed-export/saved/prepare-current',fields={k:v for k,v in fields.items() if k!='acknowledge'})[0]==400
    status,raw,headers=fetch(a,base,'/confirmed-export/saved/prepare-current',fields=fields)
    assert status==200 and 'no-store' in headers['Cache-Control']
    prepared=page(raw);assert prepared.select_one('[data-export-refresh-source]')
    assert not prepared.select_one('[data-export-download] [name=acknowledge]').has_attr('checked')
    assert len(prepared.select('[data-export-document]'))==2 and list(s.db.iterdump())==before
    new=inputs(prepared.select_one('[data-export-save]'));assert new['bundle']!=old['id']
    new['name']='최신 확인본 새 목록'
    status,raw,_=fetch(a,base,'/confirmed-export/save',fields=new);assert status==200
    download=inputs(page(raw).select_one('[data-export-download]'));download['acknowledge']='yes'
    assert download['id']!=old['id'] and s.db.execute('SELECT count(*) FROM school_confirmed_export_lists').fetchone()[0]==2
    expected=batch.prepare(s,p,items)['pins'];assert saved.get(s,p,download['id'])['pins']==expected
    assert saved.get(s,p,old['id'])==frozen
    after=list(s.db.iterdump());raw_zip=fetch(a,base,'/confirmed-export/saved/download',fields=download)[1]
    verify_zip(raw_zip,s,p,items)
    assert fetch(a,base,'/logout',fields={'csrf':csrf})[0]==200
    fresh=client();login(fresh,base)
    directory=page(fetch(fresh,base,'/confirmed-export/saved')[1]);assert len(directory.select('[data-export-status-link]'))==2
    assert page(fetch(fresh,base,url(download['id']))[1]).select_one('[data-export-refresh]')
    new_page=page(fetch(fresh,base,'/confirmed-export/saved/preview?id='+download['id'])[1])
    values=inputs(new_page.select_one('[data-export-download]'));values['acknowledge']='yes'
    assert fetch(fresh,base,'/confirmed-export/saved/download',fields=values)[1]==raw_zip
    assert fetch(fresh,base,'/confirmed-export/saved/preview?id='+old['id'])[0]==409
    assert list(s.db.iterdump())==after and originals(s)==files
    assert export.render_html(old_saved)==old_html and export.bundle(s,old_saved)==old_zip
    assert s.db.execute('PRAGMA quick_check').fetchone()[0]=='ok' and not s.db.execute('PRAGMA foreign_key_check').fetchall()


def test_http_unconfirmed_or_changed_again_never_creates_a_partial_or_unseen_preparation(school_web):
    _,s,base=school_web;p=svc.Principal('A','a');items=[confirmed(s,p),confirmed(s,p,key='two')]
    a=client();csrf=login(a,base);_,old,_,_=http_saved(a,base,csrf,items)
    _,fields=refresh_form(a,base,old['id'])
    items[1]=reconfirm(s,p,items[1]);before=list(s.db.iterdump())
    status,raw,_=fetch(a,base,'/confirmed-export/saved/prepare-current',fields=fields)
    assert status==409 and page(raw).select_one('[data-export-refresh-conflict]') and not page(raw).select('[data-export-download]')
    _,latest=refresh_form(a,base,old['id'])
    save(review,s,p,items[0]['id'],{'kind':'reopen'});before=list(s.db.iterdump())
    status,raw,_=fetch(a,base,'/confirmed-export/saved/prepare-current',fields=latest)
    assert status==409 and page(raw).select_one('[data-export-refresh-blocked]') and not page(raw).select('[data-export-refresh]')
    assert len(page(raw).select('[data-export-status-item]'))==2 and list(s.db.iterdump())==before


@pytest.mark.parametrize('invalid',['extra','duplicate','blank_extra','blank_duplicate','missing','csrf','origin','utf8','oversize','fingerprint','wrong_fingerprint','missing_ack','ack_no'])
def test_http_refresh_rejects_bad_input_without_writes_or_partial_download(school_web,invalid):
    _,s,base=school_web;p=svc.Principal('A','a');a=client();csrf=login(a,base)
    _,old,_,_=http_saved(a,base,csrf,[confirmed(s,p)]);_,form=refresh_form(a,base,old['id'])
    values=list(form.items());headers={};expected=400
    if invalid=='extra':values.append(('school','B'))
    elif invalid=='duplicate':values.append(values[-1])
    elif invalid=='blank_extra':values.append(('school',''))
    elif invalid=='blank_duplicate':values.append((values[-1][0],''))
    elif invalid in ('missing','missing_ack'):values=[(k,v) for k,v in values if k!='acknowledge']
    elif invalid=='csrf':values=[(k,'wrong' if k=='csrf' else v) for k,v in values];expected=403
    elif invalid=='origin':headers['Origin']='https://untrusted.invalid';expected=403
    elif invalid=='fingerprint':values=[(k,'invalid' if k=='expected' else v) for k,v in values]
    elif invalid=='wrong_fingerprint':values=[(k,'0'*64 if k=='expected' else v) for k,v in values];expected=409
    elif invalid=='ack_no':values=[(k,'no' if k=='acknowledge' else v) for k,v in values]
    raw=urlencode(values).encode()
    if invalid=='utf8':raw+=b'&bad=%FF'
    if invalid=='oversize':raw+=b'&bad='+b'x'*16384;expected=413
    before=list(s.db.iterdump());status,body,_=fetch(a,base,'/confirmed-export/saved/prepare-current',data=raw,headers=headers)
    assert status==expected and not body.startswith(b'PK') and list(s.db.iterdump())==before


@pytest.mark.parametrize('query',['','id=','id=bad','id=%FF','id='+IDENT+'&id='+IDENT,'id='+IDENT+'&school=A','id='+'x'*4097])
def test_status_queries_are_strict_bounded_and_read_only(school_web,query):
    _,s,base=school_web;a=client();login(a,base);before=list(s.db.iterdump())
    assert fetch(a,base,'/confirmed-export/saved/status?'+query)[0]==400 and list(s.db.iterdump())==before


def test_http_scope_demoted_owner_and_reauthentication_apply_to_both_routes(school_web):
    _,s,base=school_web;p=svc.Principal('A','a');a=client();csrf=login(a,base)
    _,old,_,_=http_saved(a,base,csrf,[confirmed(s,p)]);_,fields=refresh_form(a,base,old['id'])
    b=client();token=login(b,base,'b','synthetic-b-only')
    assert fetch(b,base,url(old['id']))[0]==404
    assert fetch(b,base,'/confirmed-export/saved/prepare-current',fields={**fields,'csrf':token})[0]==404
    svc.grant_member(s,'A','a','reader')
    doc=page(fetch(a,base,url(old['id']))[1]);assert not doc.select('[data-export-refresh]')
    assert fetch(a,base,'/confirmed-export/saved/prepare-current',fields=fields)[0]==403
    svc.grant_member(s,'A','a','uploader',active=False)
    assert fetch(a,base,url(old['id']))[0]==401
    assert fetch(a,base,'/confirmed-export/saved/prepare-current',fields=fields)[0]==401


def test_same_size_original_tampering_is_rechecked_at_zip_time_and_not_claimed_as_hash_verified(school_web):
    _,s,base=school_web;p=svc.Principal('A','a');item=confirmed(s,p);a=client();csrf=login(a,base)
    _,old,_,_=http_saved(a,base,csrf,[item]);obj=s.object_path(svc.get_version(s,p,item['id'])['files'][0]['sha256'])
    obj.write_bytes(b'x'*obj.stat().st_size);doc,fields=refresh_form(a,base,old['id'])
    assert 'SHA 검사는 ZIP 다운로드 때' in doc.get_text()
    assert fetch(a,base,'/confirmed-export/saved/prepare-current',fields=fields)[0]==200
    status,raw,_=fetch(a,base,'/confirmed-export/saved/download',fields=old)
    assert status==409 and not raw.startswith(b'PK')
