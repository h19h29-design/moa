"""Restore as a NEW private review, never rewrite originals or audit events."""
import concurrent.futures
import io
import json
import sqlite3
import zipfile
from urllib.parse import urlencode

import pytest
from bs4 import BeautifulSoup

from moa import mobile, school_review as review, school_review_compare as compare
from moa import school_review_export as export, school_service as svc
from moa.core import Store, digest
from test_school_service import schools
from test_school_structure import converted
from test_school_structure_review import save
from test_web_school import school_web, client, fetch, login


def edit(store, principal, ident, text):
    block = next(b for b in review.view(store,principal,ident)['analysis']['blocks'] if b['kind']=='body')
    event = save(review,store,principal,ident,{'kind':'block','block_id':block['id'],
        'block_kind':'body','text':text})
    return event, block['id']


def restore(store, principal, ident, source, **kwargs):
    return save(review,store,principal,ident,
        {'kind':'restore','source_event':source,'acknowledge_restore':True},**kwargs)


def rows(store):
    return [tuple(r) for r in store.db.execute('SELECT * FROM school_structure_reviews ORDER BY sequence')]


def prepared(store, principal):
    receipt, version = converted(svc,store,principal)
    first, block_id = edit(store,principal,receipt.version_id,'비용 13000원')
    confirmed = save(review,store,principal,receipt.version_id,{'kind':'confirm','acknowledge_warnings':True})
    later, _ = edit(store,principal,receipt.version_id,'비용 15000원')
    return receipt, version, first, confirmed, later, block_id


def test_restore_appends_draft_with_source_parent_actor_and_exact_content(schools):
    _,s,a,_ = schools
    receipt, version, first, confirmed, later, block_id = prepared(s,a)
    old_rows = rows(s)
    original = svc.structure_for(s,a,receipt.version_id)
    payload = mobile.revision(s,version['mobile_id'],school=True)['payload']
    objects = {f['sha256']:s.object_path(f['sha256']).read_bytes() for f in version['files']}
    source = review.view(s,a,receipt.version_id,revision_id=version['revision'],event_id=confirmed)
    restored = restore(s,a,receipt.version_id,confirmed,comment='합성 복원 사유')
    v = review.view(s,a,receipt.version_id)
    assert v['analysis']==source['analysis']
    assert v['status']=='draft' and v['latest']['warnings_acknowledged'] is False
    e = v['latest']
    assert e['id']==restored and e['sequence']==4 and e['previous_id']==later
    assert e['actor']==a.subject and e['created_at'] and e['comment']=='합성 복원 사유'
    assert e['action']=='restore'
    assert {'target':'restore_source','before':None,'after':{'event_id':confirmed,'sequence':2}} in e['changes']
    assert {'target':'block','id':block_id,'before':{'kind':'body','text':'비용 15000원'},
            'after':{'kind':'body','text':'비용 13000원'}} in e['changes']
    assert rows(s)[:-1]==old_rows
    assert svc.structure_for(s,a,receipt.version_id)==original
    assert mobile.revision(s,version['mobile_id'],school=True)['payload']==payload
    assert all(s.object_path(sha).read_bytes()==raw for sha,raw in objects.items())
    assert not s.db.execute('SELECT 1 FROM mobile_reviews').fetchone()
    assert not list((s.root/'learning').glob('*.jsonl'))
    for sql in ('DELETE FROM school_structure_reviews',"UPDATE school_structure_reviews SET status='confirmed'"):
        with pytest.raises(sqlite3.IntegrityError): s.db.execute(sql)
        s.db.rollback()


def test_restore_preserves_old_html_zip_and_is_visible_in_new_exports_and_comparison(schools):
    _,s,a,_ = schools
    receipt, v, first, confirmed, later, block_id = prepared(s,a)
    def snap(event): return export.snapshot(s,a,receipt.version_id,revision_id=v['revision'],event_id=event)
    old_html, old_zip = export.render_html(snap(confirmed)), export.bundle(s,snap(confirmed))
    restored = restore(s,a,receipt.version_id,first)
    assert export.render_html(snap(confirmed))==old_html and export.bundle(s,snap(confirmed))==old_zip
    rendered = export.render_html(snap(restored))
    node = BeautifulSoup(rendered,'html.parser').find(attrs={'data-block-id':block_id})
    assert node.find('p',class_='block-text').get_text()=='비용 13000원'
    result = compare.compare(s,a,receipt.version_id,revision_id=v['revision'],left=first,right=restored)
    assert result['summary']['changed_blocks']==0 and result['relations']['total']==0
    with zipfile.ZipFile(io.BytesIO(export.bundle(s,snap(restored)))) as z:
        for f in svc.get_version(s,a,receipt.version_id)['files']:
            assert f['sha256'] in [digest(z.read(n)) for n in z.namelist()]


def test_restore_reinstates_all_relation_evidence_and_removed_and_added_edges(schools):
    _,s,a,_ = schools
    receipt, version = converted(svc,s,a)
    first, _ = edit(s,a,receipt.version_id,'비용 13000원')
    baseline = review.view(s,a,receipt.version_id)['analysis']
    edge = next(r for r in baseline['relations'] if r['kind']=='attachment_reference')
    key = {k:edge[k] for k in ('kind','source','target')}
    save(review,s,a,receipt.version_id,{'kind':'relation_remove','relation':key})
    save(review,s,a,receipt.version_id,{'kind':'relation_add','relation':key})
    restore(s,a,receipt.version_id,first)
    current = review.view(s,a,receipt.version_id)
    assert current['analysis']==baseline
    changes = [c for c in current['latest']['changes'] if c['target']=='relation']
    assert len(changes)==1 and changes[0]['before'][0]['basis']=='staff_correction'
    assert changes[0]['after']==[edge]


def test_restore_preserves_duplicate_native_evidence_without_inventing_approval(schools):
    _,s,a,_=schools; receipt,v=converted(svc,s,a)
    payload=json.loads(mobile.revision(s,v['mobile_id'],school=True)['payload'])
    edge=next(r for r in payload['structure']['relations'] if r['kind']=='attachment_reference')
    payload['structure']['relations'].append({**edge,'basis':'second_synthetic_observation'})
    s.db.execute('UPDATE mobile_revisions SET payload=? WHERE id=?',(json.dumps(payload),v['revision'])); s.db.commit()
    source,_=edit(s,a,receipt.version_id,'비용 13000원')
    save(review,s,a,receipt.version_id,{'kind':'relation_remove','relation':{k:edge[k] for k in ('kind','source','target')}})
    restore(s,a,receipt.version_id,source)
    current=review.view(s,a,receipt.version_id)
    change=next(c for c in current['latest']['changes'] if c['target']=='relation')
    assert change['before']==[] and len(change['after'])==2
    assert {e['basis'] for e in change['after']}=={edge['basis'],'second_synthetic_observation'}
    assert current['analysis']['human_approved'] is False


def test_restore_recovers_header_footnote_multiple_attachments_and_old_restoration(schools):
    _,s,a,_=schools
    receipt=svc.upload(s,a,svc.IncomingPost('zones','합성 구역 복원',
        '<header>합성학교</header><p>비용 12000원 <a href="#n" role="doc-noteref">[1]</a></p>'
        '<aside id="n" role="doc-footnote">취소 시 면제</aside><p>붙임 1. 동의서 1부.</p><p>붙임 2. 준비물 1부.</p>',
        (svc.IncomingFile('동의서.html',b'<html><h1>Agreement</h1></html>','attachment'),
         svc.IncomingFile('준비물.html',b'<html><h1>Checklist</h1></html>','attachment'))))
    svc.request_conversion(s,a,receipt.version_id); assert svc.process_conversions(s)==['done']
    source=save(review,s,a,receipt.version_id,{'kind':'confirm','acknowledge_warnings':True})
    original=review.view(s,a,receipt.version_id)['analysis']
    for b in original['blocks']:
        if b['kind'] in ('header','footnote') or b['file_id']!='0':
            save(review,s,a,receipt.version_id,{'kind':'block','block_id':b['id'],'block_kind':b['kind'],'text':b['text']+' 수정'})
    for r in original['relations']:
        if r['kind'] in ('header_for','footnote_for','attachment_reference'):
            save(review,s,a,receipt.version_id,{'kind':'relation_remove','relation':{k:r[k] for k in ('kind','source','target')}})
    restored=restore(s,a,receipt.version_id,source)
    current=review.view(s,a,receipt.version_id)
    assert current['analysis']==original and current['status']=='draft'
    assert sum(c['target']=='block' for c in current['latest']['changes'])==4
    assert sum(c['target']=='relation' for c in current['latest']['changes'])==4
    edit(s,a,receipt.version_id,'비용 17000원')
    restore(s,a,receipt.version_id,restored)
    assert review.view(s,a,receipt.version_id)['analysis']==original


def test_historical_hash_tampering_cannot_be_restored(schools):
    _,s,a,_=schools; receipt,_,source,_,_,_=prepared(s,a)
    # Corrupt only a synthetic fixture to simulate storage damage; production
    # append-only triggers remain part of the normal writer contract.
    s.db.execute('DROP TRIGGER school_structure_review_no_update')
    s.db.execute('UPDATE school_structure_reviews SET base_hash=? WHERE id=?',('0'*64,source)); s.db.commit()
    before=rows(s)
    with pytest.raises(review.Conflict): restore(s,a,receipt.version_id,source)
    assert rows(s)==before


@pytest.mark.parametrize('source', ['',None,False,[], 'missing','original','x'*161,'bad\x00pin'])
def test_invalid_or_missing_source_never_defaults_to_latest(schools,source):
    _,s,a,_ = schools; receipt,_,_,_,_,_ = prepared(s,a)
    before = list(s.db.iterdump())
    with pytest.raises((ValueError,LookupError)): restore(s,a,receipt.version_id,source)
    assert list(s.db.iterdump())==before


@pytest.mark.parametrize('bad', [False,'yes',None])
def test_restore_requires_explicit_boolean_acknowledgement(schools,bad):
    _,s,a,_ = schools; receipt,_,first,_,_,_ = prepared(s,a)
    before = rows(s)
    with pytest.raises(ValueError):
        save(review,s,a,receipt.version_id,{'kind':'restore','source_event':first,'acknowledge_restore':bad})
    assert rows(s)==before


def test_noop_current_source_and_forged_authority_do_not_append(schools):
    _,s,a,_ = schools; receipt,_,first,confirmed,later,_ = prepared(s,a)
    with pytest.raises(ValueError): restore(s,a,receipt.version_id,later)
    restore(s,a,receipt.version_id,first)
    before = rows(s)
    with pytest.raises(ValueError): restore(s,a,receipt.version_id,confirmed)  # Equal content, already draft.
    for extra in ({'actor':'other'}, {'status':'confirmed'}, {'overlay':{}}):
        with pytest.raises(ValueError): save(review,s,a,receipt.version_id,
            {'kind':'restore','source_event':later,'acknowledge_restore':True,**extra})
    assert rows(s)==before


def test_confirmed_current_can_restore_same_content_but_must_be_confirmed_again(schools):
    _,s,a,_ = schools; receipt,_ = converted(svc,s,a)
    source,_ = edit(s,a,receipt.version_id,'비용 13000원')
    save(review,s,a,receipt.version_id,{'kind':'confirm','acknowledge_warnings':True})
    restore(s,a,receipt.version_id,source)
    v = review.view(s,a,receipt.version_id)
    assert v['status']=='draft' and not v['latest']['warnings_acknowledged']
    assert {'target':'status','before':'confirmed','after':'draft'} in v['latest']['changes']
    save(review,s,a,receipt.version_id,{'kind':'confirm','acknowledge_warnings':True})
    assert review.view(s,a,receipt.version_id)['status']=='confirmed'


def test_restore_scope_roles_and_permission_revocation(schools):
    _,s,a,b = schools; receipt,_,first,_,_,_ = prepared(s,a)
    v = review.view(s,a,receipt.version_id)
    other,_,foreign,_,_,_ = prepared(s,b)
    before = rows(s)
    with pytest.raises(LookupError): restore(s,a,receipt.version_id,foreign)
    with pytest.raises(LookupError): restore(s,b,receipt.version_id,first,view=v)
    with pytest.raises(PermissionError): restore(s,svc.Principal(a.school,'1-reader'),receipt.version_id,first,view=v)
    svc.grant_member(s,a.school,a.subject,'uploader',active=False)
    with pytest.raises(PermissionError): restore(s,a,receipt.version_id,first,view=v)
    assert rows(s)==before


@pytest.mark.parametrize('kind',['processing','reconverted','corrected','other-receipt'])
def test_restore_rejects_different_or_stale_receiving_and_conversion(schools,kind):
    _,s,a,_ = schools; receipt,version,first,_,_,_ = prepared(s,a)
    v = review.view(s,a,receipt.version_id)
    if kind in ('processing','reconverted'):
        svc.request_conversion(s,a,receipt.version_id)
        if kind=='reconverted': assert svc.process_conversions(s)==['done']
    elif kind=='corrected': converted(svc,s,a,price='17000')
    else:
        other,_ = converted(svc,s,a,key='another')
        with pytest.raises(LookupError): restore(s,a,other.version_id,first)
        return
    before = rows(s)
    with pytest.raises(review.Conflict): restore(s,a,receipt.version_id,first,view=v)
    if kind=='reconverted':
        with pytest.raises(LookupError): restore(s,a,receipt.version_id,first)
    assert rows(s)==before


def test_concurrent_restoration_and_stale_form_keep_one_new_event(schools):
    _,s,a,_ = schools; receipt,_,first,_,_,_ = prepared(s,a)
    v = review.view(s,a,receipt.version_id)
    def attempt(_):
        with Store(s.root) as other:
            try: restore(other,a,receipt.version_id,first,view=v)
            except review.Conflict: return 'conflict'
            return 'saved'
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt,range(2)))==['conflict','saved']
    assert len(rows(s))==4


def test_restore_rejects_hash_mismatch_and_oversize_audit_without_partial_writes(schools,monkeypatch):
    _,s,a,_ = schools; receipt,_,first,_,_,_ = prepared(s,a)
    before = rows(s)
    v = review.view(s,a,receipt.version_id); v['base_hash']='0'*64
    with pytest.raises(review.Conflict): restore(s,a,receipt.version_id,first,view=v)
    monkeypatch.setattr(review,'MAX_VIEW_BYTES',1)
    with pytest.raises(ValueError): restore(s,a,receipt.version_id,first)
    assert rows(s)==before


def form(csrf, receipt, v, source):
    return {'csrf':csrf,'id':receipt.version_id,'revision':v['original']['conversion_revision'],
        'base_hash':v['base_hash'],'expected_review':v['latest']['id'],'action':'restore',
        'source_event':source,'acknowledge_restore':'yes','comment':'합성 복원 이유'}


def test_http_preview_history_form_restore_and_safe_output(school_web):
    _,s,base = school_web; opener = client(); csrf=login(opener,base)
    a=svc.Principal('A','a'); receipt,version,first,_,later,_=prepared(s,a)
    url='/structure-restore?'+urlencode({'id':receipt.version_id,'revision':version['revision'],'source':first})
    before=list(s.db.iterdump())
    status, raw, headers = fetch(opener,base,url)
    assert status==200, 'Restore preview route missing'
    assert 'no-store' in headers['Cache-Control'] and list(s.db.iterdump())==before
    soup=BeautifulSoup(raw,'html.parser'); f=soup.find('form',action='/structure-review')
    assert f and f.find('input',attrs={'name':'source_event'})['value']==first
    assert f.find('input',attrs={'name':'expected_review'})['value']==later
    assert f.find('input',attrs={'name':'acknowledge_restore','required':True})
    assert f.find('input',attrs={'name':'acknowledge_restore'}).find_parent('p')
    history=fetch(opener,base,'/structure-history?id='+receipt.version_id)[1]
    assert b'/structure-restore?' in history
    fields=form(csrf,receipt,review.view(s,a,receipt.version_id),first)
    fields['comment']='</textarea><img src=x onerror=alert(1)>'
    assert fetch(opener,base,'/structure-review',fields=fields)[0]==200
    assert fetch(opener,base,'/structure-review',fields=fields)[0]==409
    now=review.view(s,a,receipt.version_id)
    assert now['status']=='draft' and now['latest']['action']=='restore'
    history=fetch(opener,base,'/structure-history?id='+receipt.version_id)[1]
    assert b'<img src=x' not in history and b'&lt;img' in history
    assert first.encode() in history and '복원'.encode() in history


def test_http_restore_auth_scope_csrf_and_strict_fields(school_web):
    _,s,base=school_web; a=svc.Principal('A','a')
    receipt,v,first,_,_,_=prepared(s,a)
    one,two,reader,anon=client(),client(),client(),client()
    csrf=login(one,base); csrf_b=login(two,base,'b','synthetic-b-only'); csrf_r=login(reader,base,'reader','synthetic-reader-only')
    fields=form(csrf,receipt,review.view(s,a,receipt.version_id),first)
    url='/structure-restore?'+urlencode({'id':receipt.version_id,'revision':v['revision'],'source':first})
    assert fetch(anon,base,url)[0]==401 and fetch(two,base,url)[0]==404
    status,html,_=fetch(reader,base,url)
    assert status==200 and b'action="/structure-review"' not in html
    for op,change,headers,want in [(two,{'csrf':csrf_b},{},404),(reader,{'csrf':csrf_r},{},403),
        (one,{'csrf':'bad'},{},403),(one,{}, {'Origin':'https://evil.example.test'},403),
        (one,{'acknowledge_restore':''},{},400),(one,{'source_event':''},{},400),
        (one,{'actor':'forged'},{},400)]:
        assert fetch(op,base,'/structure-review',fields={**fields,**change},headers=headers)[0]==want
    assert fetch(one,base,'/structure-review',data=(urlencode(fields)+'&source_event=other').encode())[0]==400
    for suffix in ('&source=other','&school=A','&id=','&format=json'):
        assert fetch(one,base,url+suffix)[0]==400
    assert fetch(one,base,'/structure-restore?id='+receipt.version_id)[0]==400
    assert len(rows(s))==3


def test_http_restore_rejects_even_blank_duplicate_pins(school_web):
    _,s,base=school_web; a=svc.Principal('A','a')
    receipt,_,source,_,_,_=prepared(s,a)
    one=client(); csrf=login(one,base)
    fields=form(csrf,receipt,review.view(s,a,receipt.version_id),source)
    for key in ('source_event','expected_review','revision','acknowledge_restore','action'):
        assert fetch(one,base,'/structure-review',data=(urlencode(fields)+'&'+key+'=').encode())[0]==400
    assert len(rows(s))==3


def test_http_restore_after_long_history_and_stale_preview(school_web):
    _,s,base=school_web; a=svc.Principal('A','a')
    receipt,v,source,_,_,_=prepared(s,a)
    for i in range(22): edit(s,a,receipt.version_id,'합성 비용 '+str(i))
    one=client(); csrf=login(one,base)
    url='/structure-restore?'+urlencode({'id':receipt.version_id,'revision':v['revision'],'source':source})
    page=fetch(one,base,'/structure-history?id='+receipt.version_id+'&before=6')[1]
    assert source.encode() in page and b'/structure-restore?' in page
    assert fetch(one,base,url)[0]==200
    fields=form(csrf,receipt,review.view(s,a,receipt.version_id),source)
    edit(s,a,receipt.version_id,'합성 다른 담당자 수정')
    assert fetch(one,base,'/structure-review',fields=fields)[0]==409
    svc.request_conversion(s,a,receipt.version_id)
    raw=fetch(one,base,url)[1]
    assert b'action="/structure-review"' not in raw
    assert fetch(one,base,'/structure-review',fields=fields)[0]==409


def test_restore_history_keeps_large_changes_behind_pinned_comparison(school_web):
    _,s,base=school_web; a=svc.Principal('A','a')
    receipt,v,source,_,_,_=prepared(s,a)
    edit(s,a,receipt.version_id,'합성 긴 수정 '+('x'*18000))
    event=restore(s,a,receipt.version_id,source)
    one=client(); login(one,base)
    html=BeautifulSoup(fetch(one,base,'/structure-history?id='+receipt.version_id)[1],'html.parser')
    card=html.find('section',class_='card')
    assert 'x'*18000 not in card.get_text()
    links=[a.get('href','') for a in card.find_all('a')]
    assert any('/structure-compare?' in url and 'right='+event in url for url in links)
    assert any('event='+source in url for url in links)


def test_history_pages_large_restore_audit_by_bytes_without_losing_events(schools,monkeypatch):
    _,s,a,_=schools; receipt,v,source,_,later,_=prepared(s,a)
    edit(s,a,receipt.version_id,'합성 긴 수정 '+('한'*18000))
    for target in (source,later,source,later): restore(s,a,receipt.version_id,target)
    raw=[dict(r) for r in s.db.execute('SELECT id,sequence,changes FROM school_structure_reviews ORDER BY sequence DESC')]
    # Scaled budget exercises the production byte cap without a huge fixture.
    budget=max(len(r['changes'].encode()) for r in raw)
    monkeypatch.setattr(review,'MAX_HISTORY_BYTES',budget,raising=False)
    before=list(s.db.iterdump()); cursor=0; seen=[]
    while True:
        page=review.history(s,a,receipt.version_id,revision_id=v['revision'],before=cursor)
        events=page['events']
        assert events
        audit_bytes=sum(len(next(r['changes'] for r in raw if r['id']==e['id']).encode()) for e in events)
        assert audit_bytes<=budget
        for e in events:
            assert e['changes']==json.loads(next(r['changes'] for r in raw if r['id']==e['id']))
        seen.extend(e['id'] for e in events)
        cursor=page['next_before']
        if not cursor: break
    assert seen==[r['id'] for r in raw] and list(s.db.iterdump())==before


def test_history_rejects_an_oversize_audit_before_decoding(schools,monkeypatch):
    _,s,a,_=schools; receipt,v,_,_,_,_=prepared(s,a)
    monkeypatch.setattr(review,'MAX_HISTORY_BYTES',1,raising=False)
    with pytest.raises(ValueError,match='History audit size limit'):
        review.history(s,a,receipt.version_id,revision_id=v['revision'])
