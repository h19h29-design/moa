"""Read-only, version-pinned comparisons of synthetic school review snapshots."""
import importlib
import importlib.util
import json
from urllib.parse import urlencode

import pytest
from bs4 import BeautifulSoup

from moa import mobile, school_review as review, school_service as svc
from moa.core import digest
from test_school_service import schools
from test_school_structure import converted
from test_school_structure_review import save
from test_web_school import school_web, client, fetch, login


def module():
    assert importlib.util.find_spec('moa.school_review_compare'), 'Pinned comparison service is missing'
    return importlib.import_module('moa.school_review_compare')


def compare(s, a, receipt, version, left='original', right='original', **pages):
    return module().compare(s, a, receipt.version_id, revision_id=version['revision'], left=left, right=right, **pages)


def edit(s, a, receipt, *, text='비용 15000원', kind='body'):
    v = review.view(s, a, receipt.version_id)
    block = next(b for b in v['analysis']['blocks'] if b['file_id']=='0' and '비용' in b['text'])
    event = save(review, s, a, receipt.version_id,
        {'kind':'block','block_id':block['id'],'block_kind':kind,'text':text})
    return event, block


def url(receipt, version, **query):
    return '/structure-compare?'+urlencode({'id':receipt.version_id,'revision':version['revision'],
                                         'left':'original','right':'original',**query})


def test_original_review_diff_preserves_every_stored_byte(schools):
    m, s, a, _ = schools
    receipt, version = converted(m,s,a)
    event, block = edit(s,a,receipt,kind='heading')
    before = list(s.db.iterdump())
    original = m.structure_for(s,a,receipt.version_id)
    result = compare(s,a,receipt,version,right=event)
    assert result['notice_version'] == receipt.version_id
    assert result['conversion_revision'] == version['revision']
    assert result['left']['id']=='original' and result['right']['id']==event
    assert result['right']['actor']==a.subject and result['right']['sequence']==1
    assert result['summary']['changed_blocks']==1
    assert result['summary']['unchanged_blocks']==len(original['blocks'])-1
    assert result['summary']['modified_relations']==0
    change = result['blocks']['items'][0]
    assert change['id']==block['id'] and change['fields']==['kind','text']
    assert change['before']=={'kind':'body','text':'비용 12000원'}
    assert change['after']=={'kind':'heading','text':'비용 15000원'}
    assert change['locator']==block['locator'] and change['sha256']==block['sha256']
    assert result['files']==original['files'] and result['complete']==original['complete']
    assert list(s.db.iterdump())==before
    assert m.structure_for(s,a,receipt.version_id)==original
    assert all(digest(s.object_path(f['sha256']).read_bytes())==f['sha256'] for f in version['files'])


def test_arbitrary_history_reverse_same_and_reverted_content(schools):
    m,s,a,_ = schools
    receipt,v = converted(m,s,a)
    first,_ = edit(s,a,receipt,text='비용 13000원')
    second,_ = edit(s,a,receipt,text='비용 15000원')
    forward = compare(s,a,receipt,v,first,second)
    reverse = compare(s,a,receipt,v,second,first)
    assert forward['blocks']['items'][0]['before']==reverse['blocks']['items'][0]['after']
    assert forward['blocks']['items'][0]['fields']==['text']
    assert compare(s,a,receipt,v,first,first)['summary']['changed_blocks']==0
    third,_ = edit(s,a,receipt,text='비용 12000원')
    assert compare(s,a,receipt,v,right=third)['summary']['changed_blocks']==0


def test_confirmation_only_is_not_a_content_edit(schools):
    m,s,a,_ = schools
    receipt,v = converted(m,s,a)
    first,_ = edit(s,a,receipt)
    second = save(review,s,a,receipt.version_id,{'kind':'confirm','acknowledge_warnings':True})
    result = compare(s,a,receipt,v,first,second)
    assert result['summary']['changed_blocks']==0
    assert result['relations']['total']==0
    assert result['summary']['status_changed'] is True
    assert result['left']['status']=='draft' and result['right']['status']=='confirmed'
    assert result['right']['warnings_acknowledged'] is True


def test_relation_removal_addition_and_provenance_change(schools):
    m,s,a,_ = schools
    receipt,v = converted(m,s,a)
    original = m.structure_for(s,a,receipt.version_id)
    edge = next(e for e in original['relations'] if e['kind']=='attachment_reference')
    key = {k:edge[k] for k in ('kind','source','target')}
    removed = save(review,s,a,receipt.version_id,{'kind':'relation_remove','relation':key})
    added = save(review,s,a,receipt.version_id,{'kind':'relation_add','relation':key})
    assert compare(s,a,receipt,v,right=removed)['summary']['removed_relations']==1
    assert compare(s,a,receipt,v,removed,added)['summary']['added_relations']==1
    result = compare(s,a,receipt,v,right=added)
    assert result['summary']['modified_relations']==1
    change = result['relations']['items'][0]
    assert change['before']==[edge]
    assert change['after'][0]['basis']=='staff_correction'
    assert change['after'][0]['certainty']=='human'


def test_pinned_output_ignores_later_edits_reconversion_and_new_receipt(schools):
    m,s,a,_ = schools
    receipt,v = converted(m,s,a)
    event,_ = edit(s,a,receipt)
    saved = compare(s,a,receipt,v,right=event)
    edit(s,a,receipt,text='비용 16000원')
    m.request_conversion(s,a,receipt.version_id)
    assert m.process_conversions(s)==['done']
    converted(m,s,a,price='17000')
    assert compare(s,a,receipt,v,right=event)==saved


def test_school_receipt_conversion_event_scope_and_revoked_member(schools):
    m,s,a,b = schools
    one,v1 = converted(m,s,a)
    event,_ = edit(s,a,one)
    two,v2 = converted(m,s,a,key='other')
    other,_ = edit(s,a,two)
    reader = m.Principal(a.school,'1-reader')
    assert compare(s,reader,one,v1,right=event)['right']['id']==event
    for principal,receipt,v,left,right in [(b,one,v1,'original',event),
        (a,one,v2,'original',event),(a,one,v1,'original',other),
        (a,one,v1,other,event),(a,one,v1,'original','missing')]:
        with pytest.raises(LookupError): compare(s,principal,receipt,v,left,right)
    m.grant_member(s,a.school,a.subject,'uploader',active=False)
    with pytest.raises(PermissionError): compare(s,a,one,v1,right=event)


@pytest.mark.parametrize('field,value', [('revision_id',''),('left',''),('right',''),
    ('left','x'*161),('right','bad\x00id'),('block_page',0),('relation_page',-1),
    ('warning_page',10001),('block_page',True)])
def test_explicit_pins_and_bounded_pages(schools,field,value):
    m,s,a,_ = schools
    receipt,v = converted(m,s,a)
    args = {'revision_id':v['revision'],'left':'original','right':'original',field:value}
    with pytest.raises(ValueError): module().compare(s,a,receipt.version_id,**args)


def test_changed_blocks_paginate_without_losing_selected_versions(schools):
    m,s,a,_ = schools
    post = m.IncomingPost('many','합성 여러 구역',''.join('<p>합성 구역 '+str(i)+'</p>' for i in range(23)))
    receipt=m.upload(s,a,post); m.request_conversion(s,a,receipt.version_id)
    assert m.process_conversions(s)==['done']
    v=m.get_version(s,a,receipt.version_id)
    for block in m.structure_for(s,a,receipt.version_id)['blocks']:
        event=save(review,s,a,receipt.version_id,{'kind':'block','block_id':block['id'],
            'block_kind':block['kind'],'text':block['text']+' 수정'})
    first=compare(s,a,receipt,v,right=event)
    last=compare(s,a,receipt,v,right=event,block_page=2)
    assert first['blocks']['total']==23 and first['blocks']['pages']==2
    assert len(first['blocks']['items'])==20 and len(last['blocks']['items'])==3
    assert first['right']==last['right']
    assert not {b['id'] for b in first['blocks']['items']} & {b['id'] for b in last['blocks']['items']}
    with pytest.raises(ValueError): compare(s,a,receipt,v,right=event,block_page=3)


def test_read_only_even_when_review_migration_is_absent(schools):
    m,s,a,_=schools
    receipt,v=converted(m,s,a)
    s.db.execute('DROP TABLE school_structure_reviews'); s.db.commit()
    before=list(s.db.iterdump())
    assert compare(s,a,receipt,v)['summary']['changed_blocks']==0
    assert list(s.db.iterdump())==before


def test_hash_mismatch_does_not_hide_corruption(schools):
    m,s,a,_=schools
    receipt,v=converted(m,s,a); event,_=edit(s,a,receipt)
    revision=mobile.revision(s,v['mobile_id'],school=True)
    payload=json.loads(revision['payload']); payload['structure']['blocks'][0]['text']='tampered'
    s.db.execute('UPDATE mobile_revisions SET payload=? WHERE id=?',(json.dumps(payload),v['revision'])); s.db.commit()
    with pytest.raises(review.Conflict): compare(s,a,receipt,v,right=event)


def test_http_private_readonly_links_and_escaped_highlights(school_web):
    _,s,base=school_web
    a,b,reader,anon=client(),client(),client(),client()
    csrf=login(a,base); login(b,base,'b','synthetic-b-only'); login(reader,base,'reader','synthetic-reader-only')
    principal=svc.Principal('A','a'); receipt,v=converted(svc,s,principal)
    event,_=edit(s,principal,receipt,text='비용 <img src=x onerror=alert(1)>원')
    path=url(receipt,v,right=event)
    before=list(s.db.iterdump())
    assert fetch(anon,base,path)[0]==401 and fetch(b,base,path)[0]==404
    assert fetch(reader,base,path)[0]==200
    status,raw,headers=fetch(a,base,path)
    assert status==200, 'Read-only comparison route is missing'
    assert 'no-store' in headers['Cache-Control']
    page=BeautifulSoup(raw,'html.parser')
    assert '변경점 비교' in page.h1.text
    assert page.select_one('[data-change="block"] del').text=='12000'
    assert '<img src=x' in page.select_one('[data-change="block"] ins').text
    assert not page.select('script,img')
    assert page.select_one('select[name=right] option[selected]')['value']==event
    assert page.select_one('select[name=left] option[selected]')['value']=='original'
    assert not page.select('form[action="/structure-review"]')
    data=json.loads(fetch(a,base,path+'&format=json')[1])
    assert data['right']['id']==event
    assert fetch(a,base,'/structure-compare',fields={'csrf':csrf})[0]==404
    assert list(s.db.iterdump())==before
    for source in ('/structure-review?id='+receipt.version_id,
        '/structure-history?id='+receipt.version_id,
        '/review-preview?'+urlencode({'id':receipt.version_id,'revision':v['revision'],'event':event})):
        links=BeautifulSoup(fetch(a,base,source)[1],'html.parser').select('a[href^="/structure-compare?"]')
        assert links and all(fetch(a,base,l['href'])[0]==200 for l in links)
    svc.grant_member(s,'A','a','uploader',active=False)
    assert fetch(a,base,path)[0]==401


@pytest.mark.parametrize('suffix', ['&right=original','&revision=','&left=', '&format=xml',
    '&school=B','&before=-1','&before=9223372036854775808','&block_page=0','&'+ '&'.join('x'+str(i)+'=1' for i in range(20))])
def test_http_rejects_ambiguous_unknown_unbounded_queries(school_web,suffix):
    _,s,base=school_web; a=client(); login(a,base)
    receipt,v=converted(svc,s,svc.Principal('A','a'))
    assert fetch(a,base,url(receipt,v)+suffix)[0]==400


def test_http_missing_pins_and_scoped_history_choice_pagination(school_web):
    _,s,base=school_web; a=client(); login(a,base); principal=svc.Principal('A','a')
    receipt,v=converted(svc,s,principal)
    events=[edit(s,principal,receipt,text='비용 '+str(i)+'원')[0] for i in range(23)]
    for path in ('/structure-compare','/structure-compare?id='+receipt.version_id):
        assert fetch(a,base,path)[0]==400
    path=url(receipt,v,left=events[0],right=events[-1])
    page=BeautifulSoup(fetch(a,base,path)[1],'html.parser')
    assert page.select_one('select[name=left] option[selected]')['value']==events[0]
    assert len(page.select('select[name=left] option'))<=23
    older=page.select_one('a[data-history="older"]')['href']
    later=BeautifulSoup(fetch(a,base,older)[1],'html.parser')
    assert later.select_one('select[name=right] option[selected]')['value']==events[-1]
    assert len(later.select('select[name=left] option'))==5


def test_header_footnote_and_multiple_attachment_edits(schools):
    m,s,a,_=schools
    receipt=m.upload(s,a,m.IncomingPost('zones','합성 구역 비교',
        '<header>합성학교 가정통신문</header><p>비용 12000원 <a href="#n" role="doc-noteref">[1]</a></p>'
        '<aside id="n" role="doc-footnote">취소 시 면제</aside><p>붙임 1. 동의서 1부.</p><p>붙임 2. 준비물 1부.</p>',
        (m.IncomingFile('동의서.html',b'<html><h1>Agreement</h1></html>','attachment'),
         m.IncomingFile('준비물.html',b'<html><h1>Checklist</h1></html>','attachment'))))
    m.request_conversion(s,a,receipt.version_id); assert m.process_conversions(s)==['done']
    v=m.get_version(s,a,receipt.version_id); original=m.structure_for(s,a,receipt.version_id)
    chosen=[b for b in original['blocks'] if b['kind'] in ('header','footnote') or b['file_id']!='0']
    assert len(chosen)==4
    for block in chosen:
        event=save(review,s,a,receipt.version_id,{'kind':'block','block_id':block['id'],
            'block_kind':block['kind'],'text':block['text']+' 검수 수정'})
    edges=[e for e in original['relations'] if e['kind'] in ('header_for','footnote_for','attachment_reference')]
    assert len(edges)==4
    for edge in edges:
        event=save(review,s,a,receipt.version_id,{'kind':'relation_remove',
            'relation':{k:edge[k] for k in ('kind','source','target')}})
    result=compare(s,a,receipt,v,right=event)
    assert result['summary']['changed_blocks']==4 and result['summary']['removed_relations']==4
    assert {b['file_id'] for b in result['blocks']['items']}=={'0','1','2'}
    assert result['files']==original['files']


def test_multiple_evidence_records_for_same_edge_are_all_preserved(schools):
    m,s,a,_=schools; receipt,v=converted(m,s,a)
    revision=mobile.revision(s,v['mobile_id'],school=True)
    payload=json.loads(revision['payload'])
    edge=next(e for e in payload['structure']['relations'] if e['kind']=='attachment_reference')
    payload['structure']['relations'].append({**edge,'basis':'second_synthetic_observation'})
    s.db.execute('UPDATE mobile_revisions SET payload=? WHERE id=?',(json.dumps(payload),v['revision'])); s.db.commit()
    event=save(review,s,a,receipt.version_id,{'kind':'relation_remove',
        'relation':{k:edge[k] for k in ('kind','source','target')}})
    result=compare(s,a,receipt,v,right=event)
    assert result['summary']['removed_relations']==1
    assert {e['basis'] for e in result['relations']['items'][0]['before']}=={edge['basis'],'second_synthetic_observation'}


def test_relation_and_warning_pages_keep_full_counts(schools):
    m,s,a,_=schools
    receipt=m.upload(s,a,m.IncomingPost('edges','합성 많은 관계',
        ''.join('<header>School '+str(i)+'</header><aside role="doc-footnote">미연결 각주 '+str(i)+'</aside>' for i in range(23))))
    m.request_conversion(s,a,receipt.version_id); assert m.process_conversions(s)==['done']
    v=m.get_version(s,a,receipt.version_id); original=m.structure_for(s,a,receipt.version_id)
    for edge in original['relations']:
        if edge['kind']=='header_for':
            event=save(review,s,a,receipt.version_id,{'kind':'relation_remove',
                'relation':{k:edge[k] for k in ('kind','source','target')}})
    first=compare(s,a,receipt,v,right=event)
    last=compare(s,a,receipt,v,right=event,relation_page=2,warning_page=2)
    for group in ('relations','warnings'):
        assert first[group]['total']==23 and len(first[group]['items'])==20 and len(last[group]['items'])==3
    assert not first['complete'] and first['publication_approved'] is False


@pytest.mark.parametrize('before,after', [('가나다','가나라마'),('x'*20000,'x'*19999+'y'),
    ('가\n나','가\n나 '),('<img>','<script>'),('','추가'),('삭제',''),('같음','같음')])
def test_highlight_is_lossless_linear_and_escaped(before,after):
    from moa.school_compare_web import text_pair
    left,right=text_pair(before,after)
    for html,expected in ((left,before),(right,after)):
        parsed=BeautifulSoup(html,'html.parser')
        assert parsed.get_text()==expected
        assert all(tag.name in ('del','ins') for tag in parsed.find_all())


def test_output_limits_fail_without_writes(schools,monkeypatch):
    from moa import school_compare_web as web
    m,s,a,_=schools; receipt,v=converted(m,s,a); event,_=edit(s,a,receipt)
    before=list(s.db.iterdump())
    monkeypatch.setattr(module(),'MAX_JSON_BYTES',32)
    with pytest.raises(ValueError,match='output limit'): compare(s,a,receipt,v,right=event)
    monkeypatch.setattr(web,'MAX_HTML_BYTES',10)
    page=web._Page(); page.add('한글')
    with pytest.raises(ValueError,match='output limit'): page.add('한글')
    assert list(s.db.iterdump())==before
