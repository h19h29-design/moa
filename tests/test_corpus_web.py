"""Authenticated curation tests, using disposable synthetic notices only."""
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar

import pytest

from moa import corpus, mobile
from moa.app import cli
from test_corpus import notice
from test_mobile import web


def login(base):
    browser=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
    browser.open(base+'/login',urllib.parse.urlencode({'password':'synthetic-test-password','reviewer':'합성 검수자'}).encode()).read()
    return browser


def post(browser,base,path,form):
    return browser.open(base+path,urllib.parse.urlencode(form).encode())


def test_corpus_routes_are_authenticated_and_csrf_protected(web):
    s,base=web
    ident,_=notice(s);r=corpus.classify_notice(s,ident)
    for path in ('/corpus','/corpus/detail?id='+ident):
        with pytest.raises(urllib.error.HTTPError) as e:urllib.request.urlopen(base+path)
        assert e.value.code==401
    b=login(base)
    for path in ('/corpus/queue','/corpus/decision','/corpus/refresh'):
        with pytest.raises(urllib.error.HTTPError) as e:post(b,base,path,{'id':ident})
        assert e.value.code==403
    assert r['use']=='candidate'


def test_file_decision_filters_history_and_real_original_download(web):
    s,base=web
    ident,asset=notice(s);r=corpus.classify_notice(s,ident)
    b=login(base)
    page=b.open(base+'/corpus/detail?id='+ident).read().decode()
    assert '활용 후보' in page and '작성용 서식' in page and '/mobile/file?' in page
    assert b.open(base+'/mobile/file?id='+r['mobile_id']+'&file=0').read()==b'original form'
    mobile.generate(s,r['mobile_id'])
    assert '모바일 결과' in b.open(base+'/notice?id='+r['mobile_id']).read().decode()
    csrf=re.search('name="csrf" value="([^"]+)"',page).group(1)
    fields={'id':ident,'file':'body','fingerprint':r['fingerprint'],'role':'convert','use':'excluded','reviewer':'합성 검수자','note':'합성 제외 사유','csrf':csrf}
    saved=post(b,base,'/corpus/decision',fields).read().decode()
    assert '합성 제외 사유' in saved and '사용자 분류' in saved
    assert ident in b.open(base+'/corpus?use=excluded').read().decode()
    assert ident not in b.open(base+'/corpus?use=candidate').read().decode()
    with pytest.raises(urllib.error.HTTPError) as e:post(b,base,'/corpus/decision',fields)
    assert e.value.code==400
    assert corpus.stats(s)['approved_eligible']==0
    assert s.object_path(asset['sha256']).read_bytes()==b'original form'


def test_curation_escapes_filename_and_reviewer_content_and_rejects_unknown_filter(web):
    s,base=web
    ident,_=notice(s,'<svg onload=bad()> 신청서.txt');corpus.classify_notice(s,ident)
    b=login(base)
    page=b.open(base+'/corpus/detail?id='+ident).read().decode()
    assert '<svg onload=bad()>' not in page and '&lt;svg onload=bad()&gt;' in page
    with pytest.raises(urllib.error.HTTPError) as e:b.open(base+'/corpus?use=unknown')
    assert e.value.code==400


def test_queue_registration_and_refresh_are_idempotent_without_approval(web):
    s,base=web
    ident,_=notice(s);b=login(base)
    page=b.open(base+'/corpus').read().decode()
    csrf=re.search('name="csrf" value="([^"]+)"',page).group(1)
    post(b,base,'/corpus/queue',{'csrf':csrf}).read()
    assert corpus.stats(s)['jobs']['pending']==1
    assert corpus.drain(s,1)==['done']
    r=corpus.get_record(s,ident)
    post(b,base,'/corpus/refresh',{'csrf':csrf,'id':ident,'fingerprint':r['fingerprint']}).read()
    assert corpus.stats(s)['classified']==1 and corpus.stats(s)['approved_eligible']==0
    assert mobile.notice_row(s,r['mobile_id'])['latest_revision'] is None


def test_cli_bounded_classification_does_not_use_collection_lock(tmp_path,capsys):
    from moa.core import Store,run_lock
    with Store(tmp_path) as s:ident,_=notice(s)
    with run_lock(tmp_path):
        assert cli(['--data',str(tmp_path),'corpus','classify','--batch','1'])==0
    result=json.loads(capsys.readouterr().out)
    assert result['classified']==1 and result['approved_eligible']==0
    assert cli(['--data',str(tmp_path),'corpus','status'])==0
    assert json.loads(capsys.readouterr().out)['uses']=={'candidate':1}
