"""Synthetic regression data only. No real source files or credentials in Git."""
import copy
import io
import json
import threading
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from http.cookiejar import CookieJar
from http.server import ThreadingHTTPServer

import pytest

from moa.core import Store, digest
from moa import mobile


def guide(date='2026.10.01', amount='12,000원', school='가나학교', url='https://example.org/new'):
    return ('<!doctype html><html><body><h1>행사 안내</h1><p>'+school+'</p>'
            '<p>신청 마감 '+date+' 16:30, 선착순. 미신청 시 임의 배정.</p>'
            '<p><a href="'+url+'">신청 안내</a></p><table><tr><th>대상</th><th>금액</th></tr>'
            '<tr><td>2학년</td><td>'+amount+' (형제 무료)</td></tr></table></body></html>').encode()


def uploaded(store, content=None):
    ident = mobile.create_upload(store,[('안내.html',content or guide())])
    return ident,mobile.generate(store,ident)


def approve(store, ident, rev):
    mobile.decide(store,ident,rev['id'],'approved','합성 검수자',compared=True,rights=True,privacy=True)


def test_originals_roles_and_download_bundle_are_real_files(tmp_path):
    with Store(tmp_path) as store:
        raw = guide()
        attachment = b'untouched application reference'
        ident = mobile.create_upload(store,[('안내.html',raw),('신청서.txt',attachment)])
        files = json.loads(mobile.notice_row(store,ident)['files'])
        assert [f['role'] for f in files] == ['convert','attachment']
        assert store.object_path(files[0]['sha256']).read_bytes() == raw
        rev = mobile.generate(store,ident)
        html = mobile.render_notice(rev['data'])
        for text in ('2026.10.01','16:30','12,000원','형제 무료','미신청 시 임의 배정','https://example.org/new'):
            assert text in html
        assert attachment.decode() not in html
        with zipfile.ZipFile(io.BytesIO(mobile.bundle(store,ident))) as z:
            originals = [z.read(n) for n in z.namelist() if n.startswith('attachments/')]
            assert raw in originals and attachment in originals
            assert 'attachments/' in z.read('index.html').decode()


def test_nested_layout_html_keeps_data_table_and_order(tmp_path):
    body = b'<html><table><tr><td><p>Before</p><table><tr><th>Key</th><th>Value</th></tr><tr><td>A</td><td>9</td></tr></table><p>After</p></td></tr></table></html>'
    with Store(tmp_path) as store:
        _,rev = uploaded(store,body)
        blocks = rev['data']['blocks']
        assert [b['type'] for b in blocks] == ['paragraph','table','paragraph']
        assert blocks[0]['text']=='Before' and blocks[-1]['text']=='After'
        assert blocks[1]['table']['cells'][-1]['text']=='9'


def test_html_form_instructions_survive_without_online_application_controls(tmp_path):
    body='<html><form action="https://example.org/submit"><p>10월 30일까지 3층 과학정보부에 제출</p><input name="applicant"><button>신청</button></form></html>'.encode()
    with Store(tmp_path) as store:
        _,rev=uploaded(store,body)
        result=mobile.render_notice(rev['data'])
        assert '10월 30일까지 3층 과학정보부에 제출' in result
        assert '<form' not in result and '<input' not in result and '<button' not in result


def test_hwp_nested_table_parser_keeps_cell_flow_and_spans():
    import struct
    from moa.extract import _hwp_content
    from test_moa import _hwp_rec,_hwp_cell
    outer = _hwp_rec(71,1,struct.pack('<I',0x74626c20))+_hwp_rec(77,2,b'\0'*30)
    cell = bytearray(26)
    struct.pack_into('<HHHH',cell,8,0,0,1,1)
    stream = (outer+_hwp_rec(72,2,bytes(cell))+_hwp_rec(67,3,'앞\r'.encode('utf-16-le'))
              +_hwp_rec(71,3,struct.pack('<I',0x74626c20))+_hwp_rec(77,4,b'\0'*30)
              +_hwp_rec(72,4,bytes(cell))+_hwp_rec(67,5,'속 표\r'.encode('utf-16-le'))
              +_hwp_rec(66,2,b'\0'*24)+_hwp_rec(67,3,'뒤\r'.encode('utf-16-le')))
    tables,blocks = _hwp_content(stream)
    assert len(tables)==2
    assert [b['type'] for b in tables[0]['cells'][0]['blocks']]==['paragraph','table','paragraph']
    assert tables[1]['cells'][0]['text']=='속 표'
    assert blocks[0]['table_index']==0


def repeated_table(last=17):
    rows = [['연번','학과','수업교실']*2]
    for i in range(1,10):
        rows.append([str(i),'학과'+str(i),'2-'+str(i)]+
                    ([str(i+9),'학과'+str(i+9),'2-'+str(i+9)] if i+9<=last else ['','','']))
    return {'rows':len(rows),'cols':6,'cells':[
        {'row':r,'col':c,'rowspan':1,'colspan':1,'text':text,'header':r==0}
        for r,row in enumerate(rows) for c,text in enumerate(row)]}


def test_repeated_headers_normalise_all_17_pairs_and_refuse_same_shape_only():
    table = repeated_table()
    normal,rule = mobile.repeated_columns(table)
    assert rule=='repeated-columns-v1'
    assert normal['rows']==18 and normal['cols']==3
    for r in range(1,18):
        assert [c['text'] for c in normal['cells'] if c['row']==r]==[str(r),'학과'+str(r),'2-'+str(r)]
    changed = copy.deepcopy(table)
    changed['cells'][4]['text']='비용'
    assert mobile.repeated_columns(changed)[1]==''
    missing = copy.deepcopy(table)
    missing['cells'][6]['text']='8'
    assert mobile.repeated_columns(missing)[1]==''


def test_mixed_notice_preserves_submission_conditions_and_keeps_form_original(tmp_path):
    content = '<html><p>10월 30일까지 3층 과학정보부에 제출. 이후 GED에서 직접 지원.</p><p>희망 신청서</p><p>신청자 ( ) 보호자 ( )</p></html>'.encode()
    with Store(tmp_path) as store:
        ident,rev = uploaded(store,content)
        assert any(w['code']=='boundary' for w in rev['data']['warnings'])
        assert len(rev['data']['blocks'])==1
        assert '3층 과학정보부' in mobile.render_notice(rev['data'])
        assert any(x['reason']=='form' for x in rev['data']['excluded'])
        with pytest.raises(ValueError): approve(store,ident,rev)
        rev = mobile.generate(store,ident,{'0':{'role':'convert','scope':'mixed','form_start':1,'boundary_confirmed':True}})
        assert not rev['data']['warnings']
        approve(store,ident,rev)


def test_incomplete_images_are_not_claimed_complete_or_approved(tmp_path):
    image = b'\xff\xd8\xffpartial jpeg sample'
    with Store(tmp_path) as store:
        ident = mobile.create_upload(store,[('부분.jpg',image)])
        rev = mobile.generate(store,ident,{'0':{'role':'convert','incomplete':True}})
        codes = {w['code'] for w in rev['data']['warnings']}
        assert {'needs_vision','incomplete','empty'}<=codes
        assert rev['data']['blocks']==[]
        with pytest.raises(ValueError): approve(store,ident,rev)


def test_human_edits_create_new_version_and_approval_is_revision_bound(tmp_path):
    with Store(tmp_path) as store:
        ident,rev = uploaded(store)
        approve(store,ident,rev)
        blocks = copy.deepcopy(rev['data']['blocks'])
        table = next(b for b in blocks if b['type']=='table')
        table['table']['cells'][-1]['text']='14,000원 (형제 무료)'
        table['layout']='grade_cards'
        table['note']='단위: 원, 형제 무료 조건 유지'
        new = mobile.correct(store,ident,rev['id'],blocks)
        assert new['id']!=rev['id'] and new['sequence']==2
        row = mobile.notice_row(store,ident)
        assert row['state']=='candidate' and row['approved_revision'] is None
        assert '14,000원' in mobile.render_notice(new['data'])
        assert '14,000원' not in mobile.render_notice(mobile.revision(store,ident,rev['id'])['data'])
        with pytest.raises(ValueError): approve(store,ident,rev)


def test_role_or_added_file_invalidates_approval_and_preserves_originals(tmp_path):
    with Store(tmp_path) as store:
        ident,rev = uploaded(store)
        approve(store,ident,rev)
        mobile.add_attachments(store,ident,rev['id'],[('참고.txt',b'reference')])
        row = mobile.notice_row(store,ident)
        assert row['state']=='candidate' and row['approved_revision'] is None
        latest = mobile.revision(store,ident)
        assert len(latest['data']['files'])==2
        approve(store,ident,latest)
        changed = mobile.generate(store,ident,{'0':{'role':'attachment'},latest['data']['files'][1]['id']:{'role':'attachment'}})
        assert mobile.notice_row(store,ident)['state']=='candidate'
        assert any(w['code']=='no_guide' for w in changed['data']['warnings'])


def test_approved_layout_is_reused_without_copying_facts_and_revocation_removes_rules(tmp_path):
    with Store(tmp_path) as store:
        first,rev = uploaded(store,guide('2026.09.01','12,000원','옛학교','https://example.org/old'))
        blocks = rev['data']['blocks']
        next(b for b in blocks if b['type']=='table')['layout']='grade_cards'
        rev = mobile.correct(store,first,rev['id'],blocks)
        approve(store,first,rev)
        second,new = uploaded(store,guide('2027.03.04','99,000원','새학교','https://example.org/new'))
        audit = [x for x in new['data']['recommendations'] if x['method']=='approved-structure-only']
        assert audit and audit[0]['notice_id']==first and audit[0]['revision_id']==rev['id']
        html = mobile.render_notice(new['data'])
        assert '2027.03.04' in html and '99,000원' in html and '새학교' in html and 'https://example.org/new' in html
        assert '2026.09.01' not in html and '12,000원' not in html and '옛학교' not in html and 'https://example.org/old' not in html
        mobile.decide(store,first,rev['id'],'candidate','합성 검수자')
        assert mobile.approved_rules(store)=={}
        assert (tmp_path/'learning/mobile-approved-rules.jsonl').read_text()==''
        _,third = uploaded(store,guide('2027.04.05'))
        assert not any(x['method']=='approved-structure-only' for x in third['data']['recommendations'])


def test_evaluation_approval_never_enters_rule_search_or_export(tmp_path):
    with Store(tmp_path) as store:
        ident = mobile.create_upload(store,[('안내.html',guide())],evaluation=True)
        rev = mobile.generate(store,ident)
        approve(store,ident,rev)
        assert mobile.approved_rules(store)=={}
        assert (tmp_path/'learning/mobile-approved-rules.jsonl').read_text()==''


def test_headerless_tables_never_match_only_dimensions():
    from moa.extract import html_document
    a=html_document('<table><tr><td>일시</td><td>장소</td></tr><tr><td>3월 1일</td><td>운동장</td></tr></table>')['tables'][0]
    b=html_document('<table><tr><td>품목</td><td>금액</td></tr><tr><td>연필</td><td>500원</td></tr></table>')['tables'][0]
    assert not any(c['header'] for c in a['cells'])
    assert mobile.structure_key(a)!=mobile.structure_key(b)
    numeric=html_document('<table><tr><td>1</td><td>2</td></tr><tr><td>3</td><td>4</td></tr></table>')['tables'][0]
    assert mobile.structure_key(numeric)==''


def test_parser_upgrade_archives_human_version_and_queues_fresh_candidate(tmp_path,monkeypatch):
    with Store(tmp_path) as store:
        ident,rev = uploaded(store)
        approve(store,ident,rev)
        assert mobile.approved_rules(store)
        monkeypatch.setattr(mobile,'PARSER_VERSION',mobile.PARSER_VERSION+'-next')
        assert mobile.approved_rules(store)=={}
        assert mobile.queue_outdated(store)==1
        assert mobile.notice_row(store,ident)['state']=='processing'
        assert mobile.drain_mobile(store)==['done']
        latest=mobile.revision(store,ident)
        assert latest['id']!=rev['id'] and mobile.notice_row(store,ident)['state']=='candidate'
        assert mobile.revision(store,ident,rev['id'])['data']==rev['data']
        assert store.db.execute('SELECT action FROM mobile_reviews').fetchone()[0]=='approved'
        assert mobile.queue_outdated(store)==0


def test_hold_unconfirmed_is_not_negative_training_data(tmp_path):
    with Store(tmp_path) as store:
        ident,rev = uploaded(store)
        mobile.decide(store,ident,rev['id'],'held','합성 검수자','unconfirmed')
        assert mobile.notice_row(store,ident)['state']=='held'
        assert mobile.approved_rules(store)=={}
        history = store.db.execute('SELECT action,reason FROM mobile_reviews').fetchone()
        assert tuple(history)==('held','unconfirmed')


def test_queue_persists_across_restart_and_does_not_affect_collection_counts(tmp_path):
    with Store(tmp_path) as store:
        ident = mobile.create_upload(store,[('안내.html',guide())])
        mobile.queue_conversion(store,ident,'',{'0':{'role':'convert'}},'')
        assert mobile.notice_row(store,ident)['state']=='processing'
    with Store(tmp_path) as store:
        assert mobile.drain_mobile(store)==['done']
        assert mobile.notice_row(store,ident)['state']=='candidate'
        assert store.db.execute('SELECT count(*) FROM notices').fetchone()[0]==0
        assert store.db.execute('SELECT status FROM jobs').fetchone()[0]=='done'


def test_rejects_invalid_upload_signature_xss_and_overlapping_cells(tmp_path):
    with pytest.raises(ValueError): mobile.validate_file(b'not pdf','a.pdf')
    with pytest.raises(ValueError): mobile.validate_file(b'<html>bad</html>','bad.exe')
    with Store(tmp_path) as store:
        _,rev = uploaded(store,b'<html><p>&lt;script&gt;alert(1)&lt;/script&gt;</p></html>')
        html = mobile.render_notice(rev['data'])
        assert '<script>alert' not in html and '&lt;script&gt;' in html
    table = repeated_table()
    blocks = [{'type':'table','table':table,'layout':'scroll_table'}]
    table['cells'][0]['colspan']=2
    with pytest.raises(ValueError,match='겹칩니다'): mobile.validate_blocks(blocks)


@pytest.fixture
def web(tmp_path):
    from moa.web import Handler
    store = Store(tmp_path,thread_safe=True)
    class TestHandler(Handler):
        sessions = {}
        login_attempts = {}
    TestHandler.store=store
    TestHandler.token='synthetic-test-password'
    server=ThreadingHTTPServer(('127.0.0.1',0),TestHandler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    yield store,f'http://127.0.0.1:{server.server_address[1]}'
    server.shutdown();server.server_close();thread.join();store.db.close()


def test_all_original_preview_and_download_routes_require_authentication(web):
    store,base = web
    ident,rev = uploaded(store)
    sha=rev['data']['files'][0]['sha256']
    for path in ('/', '/notice?id='+ident, '/mobile/preview?id='+ident,'/mobile/file?id='+ident+'&file=0',
                 '/mobile/original?id='+ident+'&file=0','/mobile/html?id='+ident,'/mobile/bundle?id='+ident,'/obj/'+sha,'/case?id=no'):
        with pytest.raises(urllib.error.HTTPError) as err: urllib.request.urlopen(base+path)
        assert err.value.code==401


def multipart(fields,files=()):
    boundary='synthetic-moa-boundary'
    parts=[]
    for key,value in fields.items():
        parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n').encode())
    for key,name,data in files:
        parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"; filename="{name}"\r\nContent-Type: application/octet-stream\r\n\r\n').encode()+data+b'\r\n')
    return b''.join(parts)+f'--{boundary}--\r\n'.encode(),'multipart/form-data; boundary='+boundary


def test_browser_session_upload_csrf_and_named_attachment_download(web):
    import re
    store,base=web
    jar=CookieJar();browser=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    login=urllib.parse.urlencode({'password':'synthetic-test-password','reviewer':'합성 검수자'}).encode()
    browser.open(base+'/login',login).read()
    cookie=next(iter(jar))
    assert cookie.has_nonstandard_attr('HttpOnly') and cookie.get_nonstandard_attr('SameSite')=='Strict'
    page=browser.open(base+'/').read().decode()
    csrf=re.search('name="csrf" value="([^"]+)"',page).group(1)
    raw,ctype=multipart({'csrf':csrf},[('guide','안내.html',guide()),('attachments','신청서.txt',b'original form')])
    result=browser.open(urllib.request.Request(base+'/upload',raw,{'Content-Type':ctype}))
    ident=urllib.parse.parse_qs(urllib.parse.urlsplit(result.url).query)['id'][0]
    files=json.loads(mobile.notice_row(store,ident)['files'])
    assert files[1]['role']=='attachment'
    response=browser.open(base+'/mobile/file?id='+ident+'&file=1')
    assert response.read()==b'original form'
    assert 'filename*=UTF-8' in response.headers['Content-Disposition']
    assert response.headers['Cache-Control']=='private, no-store'
    with pytest.raises(urllib.error.HTTPError) as denied:
        browser.open(base+'/mobile/generate',urllib.parse.urlencode({'id':ident,'revision':''}).encode())
    assert denied.value.code==403
    with pytest.raises(urllib.error.HTTPError) as cross:
        browser.open(urllib.request.Request(base+'/mobile/generate',urllib.parse.urlencode({'id':ident,'revision':'','csrf':csrf}).encode(),{'Origin':'https://evil.example'}))
    assert cross.value.code==403
    fields={'id':ident,'revision':'','csrf':csrf,'role_0':'convert','role_1':'attachment'}
    browser.open(base+'/mobile/generate',urllib.parse.urlencode(fields).encode()).read()
    assert mobile.drain_mobile(store)==['done']
    assert '모바일 결과' in browser.open(base+'/notice?id='+ident).read().decode()
    assert browser.open(base+'/mobile/bundle?id='+ident).read().startswith(b'PK')


def test_search_uses_human_corrected_table(tmp_path):
    from moa.learn import learn,review,search_cases
    from test_moa import school
    with Store(tmp_path) as store:
        ident,_=store.save_notice(school(),'https://example.org/x','안내','<table><tr><td>장소</td><td>구 체육관</td></tr></table>',[],'2026-09-30')
        learn(store,ident)
        row=store.db.execute('SELECT * FROM cases').fetchone()
        correction=json.loads(row['payload'])['table']
        correction['cells'][-1]['text']='새 강당'
        store.db.execute("UPDATE cases SET split='train' WHERE id=?",(row['id'],))
        store.db.commit()
        review(store,row['id'],'approved',layout='key_value_cards',reviewer='합성 검수자',rights_reviewed=True,privacy_reviewed=True,correction={'table':correction})
        found=search_cases(store,'새 강당')
        assert found and json.loads(found[0]['payload'])['table']['cells'][-1]['text']=='새 강당'
