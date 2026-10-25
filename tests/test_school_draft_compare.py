"""Read-only private draft comparison on isolated SQLite and loopback HTTP."""
import importlib
from urllib.parse import urlencode

import pytest

from moa import school_review as review, school_review_drafts as drafts, school_service as svc
from moa.core import Store, digest
from test_school_service import schools
from test_school_structure import converted
from test_school_structure_review import save as formal_save
from test_school_review_drafts import draft_save, http_draft, page
from test_web_school import school_web, client, fetch, login


def comparison(s, p, ident):
    return importlib.import_module('moa.school_draft_compare').compare(s,p,ident)


def path(ident): return '/structure-draft/compare?'+urlencode({'id':ident})


def test_block_saved_input_is_compared_to_pinned_formal_fields_without_writes(schools):
    m,s,a,_=schools; receipt,_=converted(m,s,a)
    v=review.view(s,a,receipt.version_id); block=v['analysis']['blocks'][0]
    ident=draft_save(s,a,receipt.version_id,action={
        'kind':'block','block_id':block['id'],'block_kind':'heading','text':'임시 <script>본문</script>\n'},comment='개인 사유')
    before=list(s.db.iterdump())
    files={str(p):(digest(p.read_bytes()),p.stat().st_mtime_ns) for folder in ('objects','extracted')
           for p in (s.root/folder).rglob('*') if p.is_file()}
    result=comparison(s,a,ident)
    assert result['recoverable'] and result['reasons']==[] and result['comparable']
    assert result['current']['review_id']=='' and result['current']['revision']==v['original']['conversion_revision']
    assert result['fields']['text']=={'current':block['text'],'saved':'임시 <script>본문</script>\n'}
    assert result['fields']['kind']=={'current':block['kind'],'saved':'heading'}
    assert result['draft']['payload']['comment']=='개인 사유'
    assert list(s.db.iterdump())==before
    assert files=={str(p):(digest(p.read_bytes()),p.stat().st_mtime_ns) for folder in ('objects','extracted')
                  for p in (s.root/folder).rglob('*') if p.is_file()}


def test_new_formal_review_is_compared_but_never_rebases_old_input(schools):
    m,s,a,_=schools; receipt,_=converted(m,s,a)
    ident=draft_save(s,a,receipt.version_id)
    row=drafts.get(s,a,ident); action={**row['payload']['action'],'text':'현재 정식 수정본'}
    event=formal_save(review,s,a,receipt.version_id,action)
    before=list(s.db.iterdump()); result=comparison(s,a,ident)
    assert result['reasons']==['review_changed'] and not result['recoverable']
    assert result['current']['review_id']==event and result['fields']['text']['current']=='현재 정식 수정본'
    assert result['draft']['base_review']=='' and drafts.get(s,a,ident)==row
    assert list(s.db.iterdump())==before


@pytest.mark.parametrize('kind',['relation_add','relation_remove'])
def test_relation_comparison_reports_presence_not_a_text_rewrite(schools,kind):
    m,s,a,_=schools; receipt,_=converted(m,s,a); v=review.view(s,a,receipt.version_id)
    edge=next(r for r in v['analysis']['relations'] if r['kind'] in review.RELATION_LABELS)
    action={'kind':kind,'relation':{k:edge[k] for k in ('kind','source','target')}}
    ident=draft_save(s,a,receipt.version_id,action=action)
    result=comparison(s,a,ident)
    assert result['relation']['current_count']>=1
    assert result['relation']['saved_present']==(kind=='relation_add')
    assert result['fields']=={} and result['comparable']
    assert s.db.execute('SELECT count(*) FROM school_structure_reviews').fetchone()[0]==0


def test_unfinished_relation_endpoints_are_shown_as_unresolved(schools):
    m,s,a,_=schools; receipt,_=converted(m,s,a)
    ident=draft_save(s,a,receipt.version_id,action={'kind':'relation_add',
        'relation':{'kind':'part_of','source':'','target':'foreign/b9'}})
    result=comparison(s,a,ident)
    assert result['relation']=={'current_count':0,'saved_present':True,'source_exists':False,'target_exists':False}
    assert result['recoverable']  # Partial personal input, never a valid formal action.


@pytest.mark.parametrize('change,reason',[
    ('new_input','newer_input'),('reader','read_only'),('confirm','review_changed'),
    ('conversion','conversion_changed'),('correction','receiving_changed'),
    ('pending','conversion_pending'),('failed','conversion_failed'),
])
def test_stale_reasons_preserve_saved_scope_and_disable_recovery(schools,change,reason):
    m,s,a,_=schools; receipt,version=converted(m,s,a)
    ident=draft_save(s,a,receipt.version_id); row=drafts.get(s,a,ident)
    latest=receipt.version_id
    if change=='new_input': draft_save(s,a,receipt.version_id,expected=ident,comment='더 최신 입력')
    elif change=='reader': svc.grant_member(s,a.school,a.subject,'reader')
    elif change=='confirm': formal_save(review,s,a,receipt.version_id,{'kind':'confirm','acknowledge_warnings':True})
    elif change=='correction': latest=converted(m,s,a,price='15000')[0].version_id
    else:
        svc.request_conversion(s,a,receipt.version_id)
        if change=='conversion': assert svc.process_conversions(s)==['done']
        elif change=='failed':
            s.db.execute("UPDATE mobile_notices SET state='conversion_failed' WHERE id=?",(version['mobile_id'],)); s.db.commit()
    before=list(s.db.iterdump()); result=comparison(s,a,ident)
    assert reason in result['reasons'] and not result['recoverable']
    assert result['current']['revision']==version['revision']  # Never align IDs across reconversions.
    assert result['latest_version']==latest
    assert result['draft']['conversion_revision']==row['conversion_revision']
    assert drafts.get(s,a,ident)==row and list(s.db.iterdump())==before


@pytest.mark.parametrize('error,reason',[(review.Conflict,'structure_changed'),(LookupError,'structure_unavailable')])
def test_unavailable_structure_preserves_saved_input_without_false_alignment(schools,monkeypatch,error,reason):
    m,s,a,_=schools; receipt,_=converted(m,s,a); ident=draft_save(s,a,receipt.version_id)
    def unavailable(*args,**kwargs): raise error('private internal details')
    monkeypatch.setattr(review,'view',unavailable)
    result=comparison(s,a,ident)
    assert result['fields']=={} and not result['comparable'] and not result['recoverable']
    assert reason in result['reasons'] and result['draft']['payload']['action']['text']=='임시 입력'
    assert 'private internal details' not in str(result)


def test_comparison_uses_one_sqlite_snapshot_even_if_review_changes_mid_read(schools,monkeypatch):
    m,s,a,_=schools; receipt,_=converted(m,s,a); ident=draft_save(s,a,receipt.version_id)
    original=review.view
    def concurrent(*args,**kwargs):
        with Store(s.root) as other:
            formal_save(review,other,a,receipt.version_id,{'kind':'confirm','acknowledge_warnings':True},
                        view=original(other,a,receipt.version_id))
        return original(*args,**kwargs)
    monkeypatch.setattr(review,'view',concurrent)
    result=comparison(s,a,ident)
    assert result['current']['review_id']=='' and result['recoverable']
    monkeypatch.setattr(review,'view',original)
    assert comparison(s,a,ident)['reasons']==['review_changed']


def test_http_comparison_is_escaped_private_and_linked_from_directory_and_stale_recovery(school_web):
    _,s,base=school_web; a=client(); login(a,base); p=svc.Principal('A','a')
    receipt,values,ident=http_draft(a,base,s,changes={'text':'<script>draft-private</script>\n미완성','comment':'<img src=x onerror=alert(1)>'})
    before=list(s.db.iterdump()); doc,headers=page(a,base,path(ident))
    assert doc.select_one('#draft-comparison') and doc.select_one('[data-draft-recover]')
    assert doc.select_one('[data-draft-compare-current]') and doc.select_one('[data-draft-compare-saved]')
    assert 'draft-private' in doc.get_text() and '<script>draft-private</script>' not in str(doc)
    assert not doc.select_one('img') and not doc.select_one('form[action="/structure-review"]')
    assert not doc.select_one('script[src]') and not doc.select_one('textarea')
    assert 'no-store' in headers['Cache-Control'] and "default-src 'self'" in headers['Content-Security-Policy']
    directory,_=page(a,base,'/structure-drafts')
    assert directory.select_one('[data-draft-compare]')['href']==path(ident)
    assert list(s.db.iterdump())==before
    formal_save(review,s,p,receipt.version_id,{'kind':'confirm','acknowledge_warnings':True})
    stale,_=page(a,base,'/structure-draft?id='+ident)
    assert stale.select_one('[data-draft-compare]')['href']==path(ident)
    doc,_=page(a,base,path(ident))
    assert not doc.select_one('[data-draft-recover]')
    assert doc.select_one('[data-draft-reason="review_changed"]')
    pinned=doc.select_one('[data-current-snapshot]')['href']
    assert 'event=' in pinned and 'revision='+values['revision'] in pinned


def test_http_owner_school_membership_and_read_only_role(school_web):
    _,s,base=school_web; a,b,reader=client(),client(),client()
    login(a,base); login(b,base,'b','synthetic-b-only'); login(reader,base,'reader','synthetic-reader-only')
    _,_,ident=http_draft(a,base,s)
    for who in (b,reader): assert fetch(who,base,path(ident))[0]==404
    svc.grant_member(s,'A','reader','manager')
    assert fetch(reader,base,path(ident))[0]==404
    assert fetch(client(),base,path(ident))[0]==401
    svc.grant_member(s,'A','a','reader')
    doc,_=page(a,base,path(ident))
    assert doc.select_one('[data-draft-reason="read_only"]') and not doc.select_one('[data-draft-recover]')
    svc.grant_member(s,'A','a','reader',active=False)
    assert fetch(a,base,path(ident))[0]==401


@pytest.mark.parametrize('suffix',['','?id=bad','?id='+'0'*32+'&id='+'1'*32,'?id='+'0'*32+'&school=A',
                                  '?id=%FF','?id='+'0'*32+'&format=json','?id='+'x'*4097],
                         ids=['missing','invalid','duplicate','authority','encoding','format','oversized'])
def test_comparison_query_is_strict_and_bounded(school_web,suffix):
    _,s,base=school_web; a=client(); login(a,base); before=list(s.db.iterdump())
    assert fetch(a,base,'/structure-draft/compare'+suffix)[0]==400
    assert list(s.db.iterdump())==before


def test_hash_mismatch_never_labels_saved_and_new_structure_as_comparable(schools,monkeypatch):
    m,s,a,_=schools; receipt,_=converted(m,s,a); ident=draft_save(s,a,receipt.version_id)
    original=review.view
    def changed(*args,**kwargs): return {**original(*args,**kwargs),'base_hash':'0'*64}
    monkeypatch.setattr(review,'view',changed)
    result=comparison(s,a,ident)
    assert result['reasons']==['structure_changed'] and not result['comparable']
    assert not result['recoverable'] and result['fields']=={}


def test_superseded_input_compares_original_payload_and_links_only_own_new_head(school_web):
    _,s,base=school_web; a=client(); login(a,base); p=svc.Principal('A','a')
    receipt,_,ident=http_draft(a,base,s,changes={'text':'첫 번째 보존 입력'})
    newer=draft_save(s,p,receipt.version_id,expected=ident,comment='두 번째 사유')
    before=list(s.db.iterdump()); doc,_=page(a,base,path(ident))
    assert doc.select_one('[data-draft-reason="newer_input"]')
    assert not doc.select_one('[data-draft-recover]') and '첫 번째 보존 입력' in doc.get_text()
    assert doc.find('a',href=path(newer))
    assert list(s.db.iterdump())==before


def test_long_identical_and_whitespace_only_text_remain_exact_and_bounded(school_web):
    _,s,base=school_web; a=client(); login(a,base); p=svc.Principal('A','a')
    receipt,values,ident=http_draft(a,base,s,changes={'text':'x'*19999+'\n'})
    before=list(s.db.iterdump()); doc,_=page(a,base,path(ident))
    assert doc.select_one('[data-draft-compare-saved] pre').get_text()=='x'*19999+'\n'
    assert len(str(doc).encode())<100000 and list(s.db.iterdump())==before
    v=review.view(s,p,receipt.version_id)
    original=next(b for b in v['analysis']['blocks'] if b['id']==values['block_id'])['text']
    same=draft_save(s,p,receipt.version_id,expected=ident,action={
        'kind':'block','block_id':values['block_id'],'block_kind':values['block_kind'],'text':original})
    doc,_=page(a,base,path(same))
    assert not doc.select('ins,del') and '종류·텍스트가 같습니다' in doc.get_text()
    space=draft_save(s,p,receipt.version_id,expected=same,action={
        'kind':'block','block_id':values['block_id'],'block_kind':values['block_kind'],'text':original+' '})
    doc,_=page(a,base,path(space))
    assert doc.select_one('ins').get_text()==' ' and '종류 또는 텍스트가 다릅니다' in doc.get_text()


def test_missing_draft_schema_is_not_automatically_installed(school_web):
    _,s,base=school_web; a=client(); login(a,base)
    s.db.executescript('DROP TABLE school_review_draft_restores; DROP TABLE school_review_drafts; DELETE FROM school_service_migrations WHERE version>=4;')
    before=list(s.db.iterdump())
    assert fetch(a,base,path('0'*32))[0]==409
    assert list(s.db.iterdump())==before
