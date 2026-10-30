"""Private immutable input history; synthetic SQLite and loopback HTTP only."""
import importlib
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest

from moa import school_review as review, school_review_drafts as drafts, school_service as svc
from moa.core import Store, digest
from test_school_service import schools
from test_school_structure import converted
from test_school_structure_review import save as formal_save
from test_school_review_drafts import draft_save, http_draft, page
from test_web_school import school_web, client, fetch, login


def module(): return importlib.import_module('moa.school_draft_history')


def history_path(ident, **extra):
    return '/structure-draft/history?'+urlencode({'id':ident, **extra})


def pair_path(before, after):
    return '/structure-draft/diff?'+urlencode({'before':before, 'after':after})


def series(s, p, ident, count, *, expected=''):
    result=[]
    for index in range(count):
        expected=draft_save(s,p,ident,expected=expected,comment='개인 사유 '+str(index))
        result.append(expected)
    return result


def test_history_is_pinned_paginated_metadata_and_does_not_modify_anything(schools):
    m,s,a,_=schools; receipt,_=converted(m,s,a)
    ids=series(s,a,receipt.version_id,23)
    snapshot=list(s.db.iterdump())
    files={str(p):(digest(p.read_bytes()),p.stat().st_mtime_ns) for folder in ('objects','extracted')
           for p in (s.root/folder).rglob('*') if p.is_file()}
    data=module().history(s,a,ids[-1])
    assert data['anchor']['id']==data['head']['id']==ids[-1]
    assert (data['page'],data['pages'],data['total'])==(1,2,23)
    assert [r['id'] for r in data['rows']]==list(reversed(ids[3:]))
    assert all('payload' not in row and 'comment' not in row for row in data['rows'])
    last=module().history(s,a,ids[-1],page=1_000_000)
    assert (last['page'],last['pages'])==(2,2)
    assert [r['id'] for r in last['rows']]==list(reversed(ids[:3]))
    assert list(s.db.iterdump())==snapshot
    assert files=={str(p):(digest(p.read_bytes()),p.stat().st_mtime_ns) for folder in ('objects','extracted')
                  for p in (s.root/folder).rglob('*') if p.is_file()}
    newer=draft_save(s,a,receipt.version_id,expected=ids[-1])
    again=module().history(s,a,ids[-1])
    assert again['rows']==data['rows'] and again['total']==23
    assert again['head']['id']==newer and again['anchor']['id']==ids[-1]
    assert module().history(s,a,newer)['total']==24


@pytest.mark.parametrize('invalid',[0,-1,1_000_001,True,'1',None])
def test_history_page_rejects_invalid_service_values(schools,invalid):
    m,s,a,_=schools; receipt,_=converted(m,s,a); ident=draft_save(s,a,receipt.version_id)
    before=list(s.db.iterdump())
    with pytest.raises(ValueError): module().history(s,a,ident,page=invalid)
    assert list(s.db.iterdump())==before


@pytest.mark.parametrize('other_scope',['owner','school','block','notice','conversion','action'])
def test_history_and_pair_never_mix_scopes(schools,other_scope):
    m,s,a,b=schools; receipt,_=converted(m,s,a)
    ident=draft_save(s,a,receipt.version_id)
    person=a; other_notice=receipt.version_id; action=None
    if other_scope=='owner':
        person=svc.Principal(a.school,'other-editor'); svc.grant_member(s,a.school,person.subject,'manager')
    elif other_scope=='school':
        person=b; other_notice=converted(m,s,b)[0].version_id
    elif other_scope=='block':
        blocks=review.view(s,a,receipt.version_id)['analysis']['blocks']
        action={'kind':'block','block_id':blocks[1]['id'],'block_kind':'body','text':'다른 구역의 개인 입력'}
    elif other_scope=='notice': other_notice=converted(m,s,a,key='another-notice')[0].version_id
    elif other_scope=='conversion':
        svc.request_conversion(s,a,receipt.version_id); assert svc.process_conversions(s)==['done']
    else:
        action={'kind':'relation_add','relation':{'kind':'part_of','source':'','target':''}}
    other=draft_save(s,person,other_notice,action=action)
    before=list(s.db.iterdump())
    data=module().history(s,a,ident)
    assert data['head']['id']==ident and [r['id'] for r in data['rows']]==[ident]
    with pytest.raises(LookupError): module().pair(s,a,ident,other)
    with pytest.raises(LookupError): module().pair(s,a,other,ident)
    assert list(s.db.iterdump())==before


def test_pair_returns_original_inputs_in_order_and_preserves_whitespace(schools):
    m,s,a,_=schools; receipt,_=converted(m,s,a)
    block=review.view(s,a,receipt.version_id)['analysis']['blocks'][0]
    action={'kind':'block','block_id':block['id'],'block_kind':'body','text':'본문\n '}
    one=draft_save(s,a,receipt.version_id,action=action,comment='')
    two=draft_save(s,a,receipt.version_id,expected=one,action={**action,'block_kind':'heading','text':'본문\n  '},comment='작성 중')
    before=list(s.db.iterdump()); data=module().pair(s,a,one,two)
    assert data['before']['payload']=={'action':action,'comment':''}
    assert data['after']['payload']['action']['text']=='본문\n  '
    assert data['same_base']
    equal=module().pair(s,a,one,one)
    assert equal['before']==equal['after'] and equal['same_base']
    with pytest.raises(ValueError): module().pair(s,a,two,one)
    assert list(s.db.iterdump())==before


def test_formal_review_change_is_reported_without_rebasing_saved_versions(schools):
    m,s,a,_=schools; receipt,_=converted(m,s,a)
    one=draft_save(s,a,receipt.version_id)
    event=formal_save(review,s,a,receipt.version_id,{'kind':'confirm','acknowledge_warnings':True})
    two=draft_save(s,a,receipt.version_id,expected=one)
    before=list(s.db.iterdump()); data=module().pair(s,a,one,two)
    assert not data['same_base']
    assert data['before']['base_review']=='' and data['after']['base_review']==event
    assert module().history(s,a,two)['total']==2
    assert list(s.db.iterdump())==before


def test_hash_change_also_makes_saved_bases_different(schools,monkeypatch):
    m,s,a,_=schools; receipt,_=converted(m,s,a); one,two=series(s,a,receipt.version_id,2)
    original=drafts.get
    def changed(store,principal,ident):
        row=original(store,principal,ident)
        return {**row,'base_hash':'0'*64} if ident==two else row
    monkeypatch.setattr(drafts,'get',changed)
    data=module().pair(s,a,one,two)
    assert data['before']['base_review']==data['after']['base_review']
    assert not data['same_base']


def test_unavailable_structure_does_not_prevent_reading_saved_input_history(schools,monkeypatch):
    m,s,a,_=schools; receipt,_=converted(m,s,a); one,two=series(s,a,receipt.version_id,2)
    def unavailable(*args,**kwargs): raise AssertionError('History must not reload or parse the document')
    monkeypatch.setattr(review,'view',unavailable); monkeypatch.setattr(svc,'structure_for',unavailable)
    before=list(s.db.iterdump())
    assert module().history(s,a,two)['total']==2
    assert module().pair(s,a,one,two)['after']['id']==two
    assert list(s.db.iterdump())==before


def test_history_head_and_rows_use_one_read_snapshot(schools,monkeypatch):
    m,s,a,_=schools; receipt,_=converted(m,s,a); one=draft_save(s,a,receipt.version_id)
    original=drafts.get; created=[]
    def concurrent(store,principal,ident):
        row=original(store,principal,ident)
        if not created:
            with Store(s.root) as other:
                created.append(draft_save(other,a,receipt.version_id,expected=one))
        return row
    monkeypatch.setattr(drafts,'get',concurrent)
    data=module().history(s,a,one)
    assert data['head']['id']==one and data['total']==1
    assert module().history(s,a,one)['head']['id']==created[0]


def test_pair_permission_checks_share_snapshot_and_next_request_rechecks(schools,monkeypatch):
    m,s,a,_=schools; receipt,_=converted(m,s,a); one,two=series(s,a,receipt.version_id,2)
    original=drafts.get; revoked=[]
    def concurrent(store,principal,ident):
        row=original(store,principal,ident)
        if not revoked:
            with Store(s.root) as other: svc.grant_member(other,a.school,a.subject,'uploader',active=False)
            revoked.append(True)
        return row
    monkeypatch.setattr(drafts,'get',concurrent)
    assert module().pair(s,a,one,two)['after']['id']==two
    with pytest.raises(PermissionError): module().pair(s,a,one,two)


def test_http_history_links_compare_only_private_snapshots_without_writes(school_web):
    _,s,base=school_web; a=client(); login(a,base); p=svc.Principal('A','a')
    receipt,_,one=http_draft(a,base,s,changes={'text':'첫 입력 고유본문'})
    two=draft_save(s,p,receipt.version_id,expected=one,comment='목록에 노출하지 않는 사유')
    before=list(s.db.iterdump()); doc,headers=page(a,base,history_path(two))
    assert doc.select_one('#draft-history') and len(doc.select('[data-draft-history-row]'))==2
    assert '첫 입력 고유본문' not in doc.get_text() and '목록에 노출하지 않는 사유' not in doc.get_text()
    assert doc.find('a',href=pair_path(one,two))
    assert 'no-store' in headers['Cache-Control'] and "default-src 'self'" in headers['Content-Security-Policy']
    for route in ('/structure-drafts','/structure-draft/compare?id='+two,'/structure-draft?id='+one):
        linked,_=page(a,base,route)
        assert linked.select_one('[data-draft-history]')
    comparison,_=page(a,base,pair_path(one,two))
    assert comparison.select_one('#draft-versions-comparison')
    for ident in (one,two): assert comparison.find('a',href='/structure-draft/compare?id='+ident)
    assert not comparison.select_one('[data-draft-recover]')
    assert not comparison.select_one('textarea, form[action="/structure-review"], form[action="/structure-draft/save"]')
    assert list(s.db.iterdump())==before


def test_http_pagination_keeps_anchor_after_new_save(school_web):
    _,s,base=school_web; a=client(); login(a,base); p=svc.Principal('A','a')
    receipt,_,one=http_draft(a,base,s)
    ids=[one]+series(s,p,receipt.version_id,20,expected=one)
    doc,_=page(a,base,history_path(ids[-1]))
    next_link=next(link['href'] for link in doc.select('a[href]')
                   if urlsplit(link['href']).path=='/structure-draft/history'
                   and parse_qs(urlsplit(link['href']).query).get('page')==['2'])
    assert parse_qs(urlsplit(next_link).query)['id']==[ids[-1]]
    latest=draft_save(s,p,receipt.version_id,expected=ids[-1])
    older,_=page(a,base,next_link)
    assert len(older.select('[data-draft-history-row]'))==1
    latest_link=older.select_one('[data-history-latest]')['href']
    assert urlsplit(latest_link).path=='/structure-draft/history'
    assert parse_qs(urlsplit(latest_link).query)['id']==[latest]


@pytest.mark.parametrize('kind',['block','relation_add','relation_remove'])
def test_http_pair_escapes_block_relation_and_comments_with_no_script(school_web,kind):
    _,s,base=school_web; a=client(); login(a,base); p=svc.Principal('A','a')
    receipt,_=converted(svc,s,p); v=review.view(s,p,receipt.version_id)
    unsafe='<script>private-input</script>\n '
    if kind=='block':
        action={'kind':kind,'block_id':v['analysis']['blocks'][0]['id'],'block_kind':'body','text':unsafe}
        changed={**action,'block_kind':'heading','text':unsafe+' '}
    elif kind=='relation_add':
        action={'kind':kind,'relation':{'kind':'part_of','source':'','target':unsafe}}
        changed={'kind':kind,'relation':{'kind':'part_of','source':unsafe,'target':''}}
    else:
        edge=next(r for r in v['analysis']['relations'] if r['kind'] in review.RELATION_LABELS)
        action={'kind':kind,'relation':{k:edge[k] for k in ('kind','source','target')}}; changed=action
    one=draft_save(s,p,receipt.version_id,action=action,comment='')
    two=draft_save(s,p,receipt.version_id,expected=one,action=changed,comment='<img src=x onerror=alert(1)>')
    before=list(s.db.iterdump()); doc,headers=page(a,base,pair_path(one,two))
    assert doc.select_one('[data-draft-before]') and doc.select_one('[data-draft-after]')
    assert not doc.select('script, img, textarea') and '<script>private-input</script>' not in str(doc)
    assert '<img src=x onerror=alert(1)>' in doc.get_text()
    if kind!='relation_remove': assert 'private-input' in doc.get_text()
    assert not doc.select_one('[data-draft-base-changed]')
    assert 'no-store' in headers['Cache-Control']
    assert list(s.db.iterdump())==before


def test_http_changed_base_is_explicit_and_old_draft_is_not_restored(school_web):
    _,s,base=school_web; a=client(); login(a,base); p=svc.Principal('A','a')
    receipt,_,one=http_draft(a,base,s)
    formal_save(review,s,p,receipt.version_id,{'kind':'confirm','acknowledge_warnings':True})
    two=draft_save(s,p,receipt.version_id,expected=one)
    before=list(s.db.iterdump()); doc,_=page(a,base,pair_path(one,two))
    assert doc.select_one('[data-draft-base-changed]')
    assert not doc.select_one('[data-draft-recover]')
    assert list(s.db.iterdump())==before


def test_http_owner_school_reader_revocation_and_disabled_school(school_web):
    _,s,base=school_web; a,b,reader=client(),client(),client()
    login(a,base); login(b,base,'b','synthetic-b-only'); login(reader,base,'reader','synthetic-reader-only')
    _,_,ident=http_draft(a,base,s)
    paths=(history_path(ident),pair_path(ident,ident))
    svc.grant_member(s,'A','reader','manager')
    for route in paths:
        assert fetch(client(),base,route)[0]==401
        assert fetch(b,base,route)[0]==404 and fetch(reader,base,route)[0]==404
    svc.grant_member(s,'A','a','reader')
    for route in paths: assert fetch(a,base,route)[0]==200
    svc.grant_member(s,'A','a','reader',active=False)
    for route in paths: assert fetch(a,base,route)[0]==401
    svc.grant_member(s,'A','a','uploader'); login(a,base)
    svc.configure_school(s,'A','A 합성학교',enabled=False)
    for route in paths: assert fetch(a,base,route)[0]==401


@pytest.mark.parametrize('route',[
    '/structure-draft/history', '/structure-draft/history?id=bad',
    '/structure-draft/history?id='+'0'*32+'&id='+'1'*32,
    '/structure-draft/history?id='+'0'*32+'&page=0',
    '/structure-draft/history?id='+'0'*32+'&page=1000001',
    '/structure-draft/history?id='+'0'*32+'&school=A',
    '/structure-draft/history?id=%FF', '/structure-draft/history?id='+'x'*4097,
    '/structure-draft/diff', '/structure-draft/diff?before='+'0'*32,
    '/structure-draft/diff?before='+'0'*32+'&after=bad',
    '/structure-draft/diff?before='+'0'*32+'&after='+'1'*32+'&after='+'2'*32,
    '/structure-draft/diff?before='+'0'*32+'&after='+'1'*32+'&format=json',
    '/structure-draft/diff?before=%FF&after='+'0'*32,
])
def test_http_history_queries_are_strict_bounded_and_nonmutating(school_web,route):
    _,s,base=school_web; a=client(); login(a,base); before=list(s.db.iterdump())
    assert fetch(a,base,route)[0]==400
    assert list(s.db.iterdump())==before


def test_missing_schema_does_not_trigger_migration_on_history_get(school_web):
    _,s,base=school_web; a=client(); login(a,base)
    s.db.executescript('DROP TABLE IF EXISTS school_warning_search_names; DROP TABLE IF EXISTS school_warning_search_events; DROP TABLE IF EXISTS school_warning_searches; DROP TABLE IF EXISTS school_confirmed_export_downloads; DROP TABLE IF EXISTS school_confirmed_export_list_events; DROP TABLE school_confirmed_export_items; DROP TABLE school_confirmed_export_lists; DROP TABLE school_review_draft_handoffs; DROP TABLE school_review_draft_restores; DROP TABLE school_review_drafts; DELETE FROM school_service_migrations WHERE version>=4;')
    before=list(s.db.iterdump())
    for route in (history_path('0'*32),pair_path('0'*32,'1'*32)):
        assert fetch(a,base,route)[0]==409
    assert list(s.db.iterdump())==before
