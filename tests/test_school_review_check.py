"""Final review summary stays pinned; confirmation appends one existing event."""
import concurrent.futures
from urllib.parse import urlencode

import pytest
from bs4 import BeautifulSoup

from moa import school_review as review, school_review_check as check
from moa import school_service as svc, school_review_export as export
from moa.core import Store, digest
from test_school_service import schools
from test_school_structure import converted
from test_school_structure_review import save
from test_web_school import school_web, client, fetch, login


def setup(s,p):
    receipt,version=converted(svc,s,p)
    v=review.view(s,p,receipt.version_id); b=next(b for b in v['analysis']['blocks'] if b['kind']=='body')
    event=save(review,s,p,receipt.version_id,{'kind':'block','block_id':b['id'],'block_kind':'body','text':'고친 <script>합성 본문</script>'})
    return receipt.version_id,version['revision'],event


def preview(s,p,scope):
    ident,revision,event=scope
    return check.preview(s,p,ident,revision_id=revision,event_id=event)


def confirm(s,p,scope,**changes):
    ident,revision,event=scope
    args=dict(revision_id=revision,event_id=event,base_hash=review.view(s,p,ident,revision_id=revision)['base_hash'],comment='  최종 대조 사유  ',acknowledge=True)
    return check.confirm(s,p,ident,**{**args,**changes})


def originals(s):
    return {str(f):(digest(f.read_bytes()),f.stat().st_mtime_ns) for folder in ('objects','extracted')
            for f in (s.root/folder).rglob('*') if f.is_file()}


def stable(s):
    return [line for line in s.db.iterdump() if not line.startswith('INSERT INTO "school_structure_reviews"')]


def test_summary_and_html_are_read_only_and_confirmation_preserves_old_events_and_files(schools):
    _,s,p,_=schools;scope=setup(s,p)
    before=list(s.db.iterdump()); files=originals(s); data=preview(s,p,scope)
    assert data['can_confirm'] and data['reasons']==[]
    assert data['summary']['changed_blocks']==1 and data['saved']['review']['id']==scope[2]
    assert data['saved']['analysis']==data['view']['analysis']
    old_html=export.render_html(data['saved']);old_zip=export.bundle(s,data['saved'])
    old_rows=[tuple(r) for r in s.db.execute('SELECT * FROM school_structure_reviews ORDER BY sequence')]
    assert list(s.db.iterdump())==before and originals(s)==files
    unchanged=stable(s);event=confirm(s,p,scope);now=review.view(s,p,scope[0])
    assert now['status']=='confirmed' and now['latest']['previous_id']==scope[2]
    assert now['latest']['comment']=='최종 대조 사유' and now['latest']['warnings_acknowledged']
    assert now['latest']['changes']==[{'target':'status','before':'draft','after':'confirmed'}]
    assert now['analysis']==data['saved']['analysis']
    assert [tuple(r) for r in s.db.execute('SELECT * FROM school_structure_reviews ORDER BY sequence')][:-1]==old_rows
    assert stable(s)==unchanged and originals(s)==files
    assert export.render_html(export.snapshot(s,p,scope[0],revision_id=scope[1],event_id=scope[2]))==old_html
    assert export.bundle(s,data['saved'])==old_zip
    final=preview(s,p,(scope[0],scope[1],event))
    assert not final['can_confirm'] and final['reasons']==['already_confirmed']
    assert s.db.execute('PRAGMA quick_check').fetchone()[0]=='ok'
    assert not s.db.execute('PRAGMA foreign_key_check').fetchall()


@pytest.mark.parametrize('change',['review','conversion','correction','pending','reader','revoked','hash'])
def test_confirm_rechecks_changes_after_preview_without_appending_history(schools,change):
    _,s,p,_=schools;scope=setup(s,p);v=preview(s,p,scope)['view'];options={}
    if change=='review': save(review,s,p,scope[0],{'kind':'confirm','acknowledge_warnings':True})
    elif change in ('conversion','pending'):
        svc.request_conversion(s,p,scope[0])
        if change=='conversion': assert svc.process_conversions(s)==['done']
    elif change=='correction': converted(svc,s,p,price='15000')
    elif change in ('reader','revoked'): svc.grant_member(s,p.school,p.subject,'reader',active=change!='revoked')
    else: options['base_hash']='0'*64
    before=list(s.db.iterdump())
    with pytest.raises(PermissionError if change in ('reader','revoked') else review.Conflict):
        confirm(s,p,scope,**{'base_hash':v['base_hash'],**options})
    assert list(s.db.iterdump())==before


def test_parallel_confirm_and_replay_append_only_once(schools):
    _,s,p,_=schools;scope=setup(s,p);v=preview(s,p,scope)['view']
    def attempt(_):
        with Store(s.root) as other:
            try: return confirm(other,p,scope,base_hash=v['base_hash'])
            except review.Conflict:return 'conflict'
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(attempt,range(2)))
    assert results.count('conflict')==1
    before=list(s.db.iterdump())
    with pytest.raises(review.Conflict):confirm(s,p,scope)
    assert list(s.db.iterdump())==before


def test_preview_uses_one_sqlite_snapshot_and_preserves_its_html_when_new_review_arrives(schools,monkeypatch):
    _,s,p,_=schools;scope=setup(s,p);original=review.view
    def interleave(*args,**kwargs):
        result=original(*args,**kwargs)
        with Store(s.root) as other:
            other_v=original(other,p,scope[0]);b=next(b for b in other_v['analysis']['blocks'] if b['kind']=='body')
            save(review,other,p,scope[0],{'kind':'block','block_id':b['id'],'block_kind':'body','text':'다른 탭의 최신 본문'})
        return result
    monkeypatch.setattr(review,'view',interleave)
    # Only the first current read triggers the concurrent writer.
    first=True
    def once(*args,**kwargs):
        nonlocal first
        if not first:return original(*args,**kwargs)
        first=False;return interleave(*args,**kwargs)
    monkeypatch.setattr(review,'view',once)
    data=preview(s,p,scope)
    assert data['can_confirm'] and data['current']['latest']['id']==scope[2]
    assert b'\xeb\x8b\xa4\xeb\xa5\xb8 \xed\x83\xad' not in export.render_html(data['saved'])
    monkeypatch.setattr(review,'view',original)
    assert preview(s,p,scope)['reasons']==['review_changed']
    with pytest.raises(review.Conflict):confirm(s,p,scope)


def test_invalid_semantic_relation_is_explained_before_confirmation(schools):
    _,s,p,_=schools;scope=setup(s,p);v=review.view(s,p,scope[0]);b=next(b for b in v['analysis']['blocks'] if b['kind']=='header')
    event=save(review,s,p,scope[0],{'kind':'block','block_id':b['id'],'block_kind':'body','text':'본문으로 변경한 헤더'})
    bad=(scope[0],scope[1],event);before=list(s.db.iterdump())
    assert preview(s,p,bad)['reasons']==['invalid_relations']
    with pytest.raises(ValueError):confirm(s,p,bad)
    assert list(s.db.iterdump())==before


@pytest.mark.parametrize('ack,comment',[(False,'사유'),('yes','사유'),(True,''),(True,'x'*2001),(True,'\0')])
def test_invalid_acknowledgement_and_reason_never_write(schools,ack,comment):
    _,s,p,_=schools;scope=setup(s,p);before=list(s.db.iterdump())
    with pytest.raises(ValueError):confirm(s,p,scope,acknowledge=ack,comment=comment)
    assert list(s.db.iterdump())==before


def http_setup(school_web):
    _,s,base=school_web;p=svc.Principal('A','a');scope=setup(s,p)
    a=client();login(a,base)
    route='/structure-confirm?'+urlencode(dict(zip(('id','revision','event'),scope)))
    status,raw,headers=fetch(a,base,route);assert status==200
    page=BeautifulSoup(raw,'html.parser');form=page.select_one('[data-final-confirm]');assert form
    values={e['name']:e.get('value','') for e in form.select('input[name]')}
    values.update(comment='최종 확인 사유',acknowledge='yes')
    return s,base,p,scope,a,route,page,headers,values


def test_http_summary_confirmation_receipt_links_and_privacy(school_web):
    s,base,p,scope,a,route,page,headers,fields=http_setup(school_web)
    before=list(s.db.iterdump());files=originals(s)
    assert page.select_one('[data-check-summary]') and page.select_one('[data-check-scope]')
    assert page.select_one('[data-check-warnings]')
    frame=page.select_one('iframe');assert frame.has_attr('sandbox') and scope[2] in frame['src']
    assert not page.select_one('[name=acknowledge]').has_attr('checked')
    assert page.select_one('[name=acknowledge]').has_attr('required')
    assert 'no-store' in headers['Cache-Control'] and "default-src 'self'" in headers['Content-Security-Policy']
    assert not page.select('#review-final-check script:not([src])')
    html=fetch(a,base,frame['src'])[1];assert b'&lt;script&gt;' in html
    assert fetch(a,base,route)[0]==200 and list(s.db.iterdump())==before
    for endpoint in ('/structure-review?id='+scope[0],'/review-preview?'+urlencode(dict(zip(('id','revision','event'),scope)))):
        assert BeautifulSoup(fetch(a,base,endpoint)[1],'html.parser').select_one('[data-final-check]')
    status,raw,_=fetch(a,base,'/structure-confirm',fields=fields);assert status==200
    receipt=BeautifulSoup(raw,'html.parser');assert '담당자 확인 기록' in receipt.get_text()
    assert receipt.select_one('[data-final-reason=already_confirmed]') and not receipt.select_one('[data-final-confirm]')
    event=review.view(s,p,scope[0])['latest'];assert event['status']=='confirmed'
    assert event['previous_id']==scope[2] and event['comment']==fields['comment']
    assert scope[2] in fetch(a,base,route)[1].decode()
    assert fetch(a,base,frame['src'])[1]==html and originals(s)==files
    assert fetch(a,base,'/structure-confirm',fields=fields)[0]==409
    fresh=client();login(fresh,base)
    assert '담당자 확인 기록' in fetch(fresh,base,'/structure-confirm?'+urlencode({'id':scope[0],'revision':scope[1],'event':event['id']}))[1].decode()


@pytest.mark.parametrize('mutation,status',[
    ('no_ack',400),('bad_ack',400),('duplicate',400),('injected',400),('blank',400),
    ('long_comment',400),('bad_utf8',400),('large',413),('csrf',403),('origin',403),('changed',409)])
def test_http_rejects_ambiguous_unauthorised_or_stale_confirmations(school_web,mutation,status):
    s,base,p,scope,a,route,page,headers,fields=http_setup(school_web);data=None;extra={}
    if mutation=='no_ack':del fields['acknowledge']
    elif mutation=='bad_ack':fields['acknowledge']='no'
    elif mutation=='duplicate':data=(urlencode(fields)+'&event='+scope[2]).encode()
    elif mutation=='injected':fields['actor']='other'
    elif mutation=='blank':fields['comment']=''
    elif mutation=='long_comment':fields['comment']='x'*2001
    elif mutation=='bad_utf8':data=urlencode(fields).encode()+b'&bad=%FF'
    elif mutation=='large':data=b'x'*32769
    elif mutation=='csrf':fields['csrf']='invalid'
    elif mutation=='origin':extra={'Origin':'https://foreign.example.test'}
    else:save(review,s,p,scope[0],{'kind':'confirm','acknowledge_warnings':True})
    before=list(s.db.iterdump())
    assert fetch(a,base,'/structure-confirm',fields=fields if data is None else None,data=data,headers=extra)[0]==status
    assert list(s.db.iterdump())==before


def test_http_reader_cross_school_revocation_and_old_version_are_read_only(school_web):
    s,base,p,scope,a,route,page,headers,fields=http_setup(school_web)
    reader=client();reader_fields={**fields,'csrf':login(reader,base,'reader','synthetic-reader-only')}
    assert not BeautifulSoup(fetch(reader,base,route)[1],'html.parser').select_one('[data-final-confirm]')
    assert fetch(reader,base,'/structure-confirm',fields=reader_fields)[0]==403
    b=client();b_fields={**fields,'csrf':login(b,base,'b','synthetic-b-only')}
    assert fetch(b,base,route)[0]==404 and fetch(b,base,'/structure-confirm',fields=b_fields)[0]==404
    assert fetch(client(),base,route)[0]==401
    converted(svc,s,p,price='15000')
    old=BeautifulSoup(fetch(a,base,route)[1],'html.parser')
    assert old.select_one('[data-final-reason=version_changed]') and not old.select_one('[data-final-confirm]')
    assert fetch(a,base,'/structure-confirm',fields=fields)[0]==409
    svc.grant_member(s,'A','a','uploader',active=False)
    assert fetch(a,base,route)[0]==401 and fetch(a,base,'/structure-confirm',fields=fields)[0]==401


@pytest.mark.parametrize('query',['','?id=x','?id=%FF','?id='+'x'*4097,'?id=x&revision=y&event=z&event=z','?id=x&revision=y&event=z&school=A'])
def test_query_is_strict_and_never_writes(school_web,query):
    _,s,base=school_web;a=client();login(a,base);before=list(s.db.iterdump())
    assert fetch(a,base,'/structure-confirm'+query)[0]==400
    assert list(s.db.iterdump())==before


def test_maximum_unicode_reason_fits_bounded_form_without_changing_any_content(school_web):
    s,base,p,scope,a,route,page,headers,fields=http_setup(school_web)
    fields['comment']='😀'*2000
    assert len(urlencode(fields).encode())<32768
    assert fetch(a,base,'/structure-confirm',fields=fields)[0]==200
    assert review.view(s,p,scope[0])['latest']['comment']==fields['comment']


def test_foreign_event_id_cannot_confirm_own_notice_or_be_read(school_web):
    s,base,p,scope,a,route,page,headers,fields=http_setup(school_web)
    other=setup(s,svc.Principal('B','b'));fields['event']=other[2]
    before=list(s.db.iterdump())
    assert fetch(a,base,'/structure-confirm',fields=fields)[0]==409
    assert fetch(a,base,'/structure-confirm?'+urlencode({'id':scope[0],'revision':scope[1],'event':other[2]}))[0]==404
    assert list(s.db.iterdump())==before
