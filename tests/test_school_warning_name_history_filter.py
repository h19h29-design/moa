"""Private name-history filters read only metadata and never rewrite artifacts."""
import unicodedata
from urllib.parse import parse_qs, urlsplit
import pytest
from moa import school_warning_search_names as names, school_warning_search_names_web as history
from moa import school_warning_name_restore_web as restore_ui, school_warning_search_management as manage
from moa import school_service as svc, school_warning_searches as saved
from test_school_service import schools
from test_school_warning_search_management import seed, IDENT
from test_school_warning_search_names import protected, drop_v13
from test_school_review_check import originals
from test_school_warning_searches import page, inputs
from test_web_school import school_web, client, fetch, login


def history_rows(s,p,*,q='',start='',end='',page_no=1):
    return names.get(s,p,IDENT,page=page_no,q=q,start=start,end=end)


def dated(s,p,monkeypatch):
    """Three renames on three distinct synthetic days."""
    monkeypatch.setattr(svc,'_now',lambda:'2026-10-01T09:00:00+09:00')
    seed(s,p,name='처음 조건')
    for day,name in (('2026-10-02','첫 번째 이름'),('2026-10-03','두 번째 이름')):
        monkeypatch.setattr(svc,'_now',lambda day=day:day+'T09:00:00+09:00')
        version=names.get(s,p,IDENT)['name_version']
        names.rename(s,p,IDENT,name=name,expected=version,expected_state=0)
    monkeypatch.setattr(svc,'_now',lambda:'2026-10-04T09:00:00+09:00')


def test_keyword_and_date_filters_read_only_and_preserve_every_row(schools,monkeypatch):
    _,s,p,_=schools;dated(s,p,monkeypatch)
    before,files,condition=protected(s),originals(s),saved.resolve(s,p,IDENT)
    snapshot=list(s.db.iterdump())
    row=history_rows(s,p)
    assert row['total']==2 and row['all_total']==2
    assert [e['name'] for e in row['events']]==['두 번째 이름','첫 번째 이름']
    row=history_rows(s,p,q='첫')
    assert [e['name'] for e in row['events']]==['첫 번째 이름'] and row['all_total']==2 and row['total']==1
    assert history_rows(s,p,q='두 번째')['total']==1
    assert history_rows(s,p,q='없는 이름')['total']==0 and history_rows(s,p,q='없는 이름')['events']==[]
    assert history_rows(s,p,start='2026-10-02')['total']==2
    assert history_rows(s,p,start='2026-10-03')['total']==1
    assert history_rows(s,p,end='2026-10-02')['total']==1
    assert [e['created_at'][:10] for e in history_rows(s,p,start='2026-10-02',end='2026-10-02')['events']]==['2026-10-02']
    assert history_rows(s,p,q='첫 번째',start='2026-10-01',end='2026-10-02')['total']==1
    assert history_rows(s,p,q='첫 번째',start='2026-10-03')['total']==0
    assert list(s.db.iterdump())==snapshot and protected(s)==before and originals(s)==files
    assert saved.resolve(s,p,IDENT)==condition


def test_filters_are_literal_unicode_casefold_and_never_evaluate_sql(schools,monkeypatch):
    _,s,p,_=schools;dated(s,p,monkeypatch)
    version=names.get(s,p,IDENT)['name_version']
    names.rename(s,p,IDENT,name=unicodedata.normalize('NFD','CAFÉ Straße A_%'),expected=version,expected_state=0)
    before=list(s.db.iterdump())
    for q in ('café','CAFÉ','STRASSE','A_%','%','_'):
        assert history_rows(s,p,q=q)['total']==1, q
    for q in ("%' OR 1=1 --",'Strasse%','A\\%'):
        assert history_rows(s,p,q=q)['total']==0, q
    assert list(s.db.iterdump())==before


def test_filtered_pages_clamp_and_keep_the_same_selection(schools,monkeypatch):
    _,s,p,_=schools;dated(s,p,monkeypatch)
    for n in range(2,26):
        names.rename(s,p,IDENT,name=f'이름 {n}',expected=n,expected_state=0)
    row=history_rows(s,p,start='2026-10-04')
    assert row['total']==24 and row['all_total']==26 and row['pages']==2
    assert len(row['events'])==20 and row['events'][0]['name']=='이름 25'
    last=history_rows(s,p,start='2026-10-04',page_no=2)
    assert last['page']==2 and len(last['events'])==4 and last['events'][-1]['name']=='이름 2'
    assert history_rows(s,p,q='번째',page_no=99)['page']==1
    assert history_rows(s,p,q='일치 없음',page_no=5)['page']==1
    assert history_rows(s,p,q='이름')['total']==26
    assert history_rows(s,p,q='이름',start='2026-10-04')['total']==24


@pytest.mark.parametrize('kwargs',[dict(q='x'*121),dict(q='\n'),dict(q='x\x00'),dict(start='2026-02-30'),
    dict(start='2026-1-1'),dict(start='20261001'),dict(start='2026-10-32'),dict(end='2026-13-01'),
    dict(start='2026-10-05',end='2026-10-01'),dict(q=1),dict(start=0)])
def test_invalid_history_filters_never_write(schools,kwargs):
    _,s,p,_=schools;seed(s,p);before=list(s.db.iterdump())
    with pytest.raises((ValueError,UnicodeError)):history_rows(s,p,**kwargs)
    assert list(s.db.iterdump())==before


def test_http_filter_form_counts_escaping_and_no_store(school_web,monkeypatch):
    _,s,base=school_web;p=svc.Principal('A','a');a=client();login(a,base);dated(s,p,monkeypatch)
    names.rename(s,p,IDENT,name='<b>세 번째</b> & 이름',expected=2,expected_state=0)
    route=history.link(IDENT,q='처음',state='all',list_page=2,hq='번째',hf='2026-10-02',ht='2026-10-03')
    status,raw,headers=fetch(a,base,route);assert status==200 and headers['Cache-Control'].startswith('private, no-store')
    doc=page(raw)
    assert doc.select_one('[data-warning-name-filter] input[name=hq]')['value']=='번째'
    assert doc.select_one('[data-warning-name-filter]')['action']=='/structure-warning-searches/name'
    assert doc.select_one('[data-warning-name-filter] input[name=hf]')['value']=='2026-10-02'
    assert doc.select_one('[data-warning-name-filter] input[name="ht"]')['value']=='2026-10-03'
    assert '이름 기록 2개' in doc.select_one('#warning-search-name').text
    assert '전체 3개' in doc.select_one('#warning-search-name').text
    assert doc.select_one('[data-warning-name-event="2"]')
    assert not doc.select_one('[data-warning-name-event="3"]')
    body=fetch(a,base,history.link(IDENT,hq='세 번째'))[1];filtered=page(body)
    assert b'&lt;b&gt;' in body and filtered.select_one('[data-warning-name-history] b') is None
    assert filtered.select_one('[data-warning-name-event="3"]').text.strip().startswith('#3')
    empty=page(fetch(a,base,history.link(IDENT,hq='없음'))[1])
    assert empty.select_one('[data-warning-name-empty]') and empty.select('[data-warning-name-history] li')==[]
    assert parse_qs(urlsplit(empty.select_one('[data-warning-name-filter-clear]')['href']).query)=={'id':[IDENT]}
    for bad in ('?id='+IDENT+'&hf=2026-02-30','?id='+IDENT+'&hq='+'x'*5000,'?id='+IDENT+'&hf=2026-10-05&ht=2026-10-01',
                '?id='+IDENT+'&hq=a&hq=b'):
        assert fetch(a,base,'/structure-warning-searches/name'+bad)[0]==400


def test_http_rename_carries_filters_and_rejects_extra_fields(school_web,monkeypatch):
    _,s,base=school_web;p=svc.Principal('A','a');a=client();login(a,base);dated(s,p,monkeypatch)
    route=history.link(IDENT,q='처음',state='all',list_page=2,hq='번째',hf='2026-10-01',ht='2026-10-02')
    doc=page(fetch(a,base,route)[1]);form=inputs(doc.select_one('[data-warning-name-form]'))
    assert form['hq']=='번째' and form['hf']=='2026-10-01' and form['ht']=='2026-10-02'
    before=list(s.db.iterdump())
    for change in ({'school':'B'},{'subject':'b'},{'id':'z'*24},{'hq':'x'*121},{'hf':'2026-10-00'},
                       {'ht':'2026-10-32'},{'expected':'-1'},{'acknowledge':'no'},{'hq':'\n'}):
        status=fetch(a,base,'/structure-warning-searches/rename',fields={**form,**change})[0]
        assert status in (400,404), (change,status)
    assert list(s.db.iterdump())==before
    status,raw,_=fetch(a,base,'/structure-warning-searches/rename',
        fields={**form,'name':'<b>필터 이름</b>'})
    assert status==200 and names.get(s,p,IDENT)['name']=='<b>필터 이름</b>'
    follow=page(raw)
    assert follow.select_one('[data-warning-name-filter] input[name=hq]')['value']=='번째'
    assert follow.select_one('[data-warning-name-filter] input[name=hf]')['value']=='2026-10-01'
    assert follow.select_one('[data-warning-name-filter] input[name="ht"]')['value']=='2026-10-02'
    assert parse_qs(urlsplit(follow.select_one('[data-warning-name-back]')['href']).query)=={
        'q':['처음'],'state':['all'],'page':['2']}


def test_http_restore_confirmation_keeps_filter_context(school_web,monkeypatch):
    _,s,base=school_web;p=svc.Principal('A','a');a=client();login(a,base);dated(s,p,monkeypatch)
    route=history.link(IDENT,hq='첫',hf='2026-10-02',ht='2026-10-02')
    doc=page(fetch(a,base,route)[1])
    href=doc.select_one('[data-warning-name-event="1"] a')['href']
    query=parse_qs(urlsplit(href).query)
    assert query['hq']==['첫'] and query['hf']==['2026-10-02'] and query['ht']==['2026-10-02']
    status,raw,_=fetch(a,base,href);assert status==200
    confirm=page(raw)
    assert parse_qs(urlsplit(confirm.select_one('[data-warning-name-restore-cancel]')['href']).query)=={
        'id':[IDENT],'hq':['첫'],'hf':['2026-10-02'],'ht':['2026-10-02']}
    fields=inputs(confirm.select_one('[data-warning-name-restore-form]'))
    assert fields['hq']=='첫' and fields['hf']=='2026-10-02' and fields['ht']=='2026-10-02'
    monkeypatch.setattr(svc,'_now',lambda:'2026-10-02T10:00:00+09:00')
    status,raw,_=fetch(a,base,restore_ui.ROUTE,fields=fields);assert status==200
    assert names.get(s,p,IDENT)['name']=='첫 번째 이름'
    after=page(raw)
    assert after.select_one('[data-warning-name-event="3"]')
    assert after.select_one('[data-warning-name-filter] input[name=hq]')['value']=='첫'
    assert after.select_one('[data-warning-name-filter] input[name=hf]')['value']=='2026-10-02'
    assert after.select_one('[data-warning-name-filter] input[name="ht"]')['value']=='2026-10-02'


def test_http_isolation_roles_and_conflict_keep_filters(school_web,monkeypatch):
    _,s,base=school_web;p=svc.Principal('A','a');a,b=client(),client();login(a,base);cb=login(b,base,'b','synthetic-b-only')
    dated(s,p,monkeypatch);route=history.link(IDENT,hq='번째')
    assert fetch(client(),base,route)[0]==401 and fetch(b,base,route)[0]==404
    doc=page(fetch(a,base,route)[1]);form=inputs(doc.select_one('[data-warning-name-form]'))
    before=list(s.db.iterdump())
    assert fetch(b,base,'/structure-warning-searches/rename',fields={**form,'csrf':cb})[0]==404
    assert fetch(a,base,'/structure-warning-searches/rename',fields={**form,'csrf':'wrong'})[0]==403
    assert fetch(a,base,'/structure-warning-searches/rename',fields=form,
        headers={'Origin':'https://evil.invalid'})[0]==403
    assert list(s.db.iterdump())==before
    manage.change(s,p,IDENT,action='archive',expected=0)
    status,raw,_=fetch(a,base,'/structure-warning-searches/rename',fields={**form,'name':'바뀐 이름'})
    assert status==409
    doc=page(raw);assert doc.select_one('[data-warning-name-conflict]')
    assert doc.select_one('[data-warning-name-filter] input[name=hq]')['value']=='번째'
    svc.grant_member(s,'A','a','reader');before=list(s.db.iterdump())
    doc=page(fetch(a,base,route)[1])
    assert doc.select_one('[data-warning-name-filter]') and not doc.select('[data-warning-name-form]')
    assert fetch(a,base,'/structure-warning-searches/rename',fields=form)[0]==403
    assert list(s.db.iterdump())==before
    svc.grant_member(s,'A','a','uploader',active=False)
    assert fetch(a,base,route)[0] in (401,403)


def test_missing_v13_filter_page_and_post_never_migrate(school_web,monkeypatch):
    _,s,base=school_web;p=svc.Principal('A','a');a=client();login(a,base);dated(s,p,monkeypatch)
    route=history.link(IDENT,hq='번째')
    fields=inputs(page(fetch(a,base,route)[1]).select_one('[data-warning-name-form]'))
    drop_v13(s);before=list(s.db.iterdump())
    monkeypatch.setattr(svc,'migrate',lambda *_:pytest.fail('HTTP must never migrate'))
    status,raw,_=fetch(a,base,route);assert status==200
    doc=page(raw)
    assert doc.select_one('[data-warning-name-required]') and not doc.select('[data-warning-name-filter],[data-warning-name-form]')
    assert fetch(a,base,'/structure-warning-searches/rename',fields=fields)[0]==409
    assert list(s.db.iterdump())==before

