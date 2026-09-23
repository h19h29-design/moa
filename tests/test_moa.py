import io
import json
import zipfile
from datetime import datetime
from pathlib import Path

import pytest


def school(code='B10', n='1'):
    return dict(office_code=code, office_name='서울특별시교육청' if code=='B10' else '부산광역시교육청',
                school_code=n, school_name=f'테스트{n}학교', school_kind='초등학교', home_url='https://example.org/school/')


def test_quota_and_korean_date():
    from moa.core import quota, kst_now
    assert quota('B10', '서울특별시교육청') == 50
    assert quota('J10', '경기도교육청') == 50
    assert quota('C10', '부산광역시교육청') == 20
    assert quota('NEW', '새교육청') == 20
    assert kst_now().utcoffset().total_seconds() == 32400


def test_objects_dedup_and_revision(tmp_path):
    from moa.core import Store
    with Store(tmp_path) as s:
        a = s.object(b'first', 'a.pdf')
        b = s.object(b'first', 'different-name.pdf')
        c = s.object(b'second', 'a.pdf')
        assert a['sha256'] == b['sha256'] != c['sha256']
        assert len(list((tmp_path/'objects').glob('*/*'))) == 2
        one, new = s.save_notice(school(), 'https://example.org/view/1', '제목', '<p>본문</p>', [a], '2026-09-21')
        assert new
        two, new = s.save_notice(school(), 'https://example.org/view/2', '제목', '<p>본문</p>', [b], '2026-09-21')
        assert not new and one == two
        three, new = s.save_notice(school(), 'https://example.org/view/1', '제목', '<p>본문</p>', [c], '2026-09-21')
        assert new and three != one
        assert s.count_day('2026-09-21', 'B10') == 2
        assert s.db.execute('select count(*) from sightings').fetchone()[0] == 3


def test_layout_changes_not_confused_with_exact_duplicates(tmp_path):
    from moa.core import Store
    with Store(tmp_path) as s:
        a,n = s.save_notice(school(), 'https://example.org/1', '행사', '<p>10월 1일 10,000원</p>', [], '2026-09-21')
        b,n = s.save_notice(school(), 'https://example.org/2', '행사', '<p>10월 2일 10,000원</p>', [], '2026-09-21')
        assert a != b and n


def test_lock_prevents_two_collectors(tmp_path):
    from moa.core import run_lock
    with run_lock(tmp_path):
        with pytest.raises(RuntimeError, match='실행 중'):
            with run_lock(tmp_path):
                pass


def test_html_table_preserves_spans():
    from moa.extract import html_document, table_pattern
    r = html_document('<p>준비물</p><table><tr><th rowspan="2">학년</th><th>일정</th></tr><tr><td>오전</td></tr><tr><td>1학년</td><td>9:00</td></tr></table>')
    t = r['tables'][0]
    assert t['cells'][0]['rowspan'] == 2
    assert t['rows'] == 3 and t['cols'] == 2
    assert t['cells'][2]['col'] == 1
    assert table_pattern(t)['layout'] == 'scroll_table'


def test_hwpx_zip_and_table():
    from moa.extract import hwpx_document
    b=io.BytesIO()
    with zipfile.ZipFile(b,'w') as z:
        z.writestr('Contents/section0.xml', '''<hs:sec xmlns:hs="hs" xmlns:hp="hp"><hp:p><hp:run><hp:t>행사 안내</hp:t><hp:tbl rowCnt="1" colCnt="2"><hp:tr><hp:tc><hp:cellAddr rowAddr="0" colAddr="0"/><hp:cellSpan rowSpan="1" colSpan="1"/><hp:subList><hp:p><hp:run><hp:t>장소</hp:t></hp:run></hp:p></hp:subList></hp:tc><hp:tc><hp:cellAddr rowAddr="0" colAddr="1"/><hp:cellSpan rowSpan="1" colSpan="1"/><hp:subList><hp:p><hp:run><hp:t>운동장</hp:t></hp:run></hp:p></hp:subList></hp:tc></hp:tr></hp:tbl></hp:run></hp:p></hs:sec>''')
    d=hwpx_document(b.getvalue())
    assert d['tables'][0]['cells'][1]['text']=='운동장'
    assert d['tables'][0]['cols']==2


def test_hwpx_rejects_entity():
    from moa.extract import hwpx_document
    b=io.BytesIO()
    with zipfile.ZipFile(b,'w') as z:
        z.writestr('Contents/section0.xml','<!DOCTYPE foo [<!ENTITY x SYSTEM "file:///etc/passwd">]><foo>&x;</foo>')
    with pytest.raises(Exception): hwpx_document(b.getvalue())


def test_safe_url_and_dns():
    from moa.net import canonical_url, public_addresses
    assert canonical_url('https://EXAMPLE.org/a?x=2&utm_source=x#b')=='https://example.org/a?x=2'
    for u in ['file:///etc/passwd','javascript:alert(1)','http://user:pw@example.org/','http://example.org:8080/a']:
        with pytest.raises(ValueError): canonical_url(u)
    for ip in ['127.0.0.1','10.0.0.1','169.254.169.254','::1','100.64.0.1']:
        with pytest.raises(ValueError): public_addresses(ip,443)


def test_sampling_is_reproducible_not_first_schools():
    from moa.crawl import sampled_schools
    data=[school(n=str(i)) for i in range(50)]
    a=sampled_schools(data,'2026-09-21','B10')
    assert a==sampled_schools(data,'2026-09-21','B10')
    assert a!=sampled_schools(data,'2026-09-22','B10')
    assert {s['school_code'] for s in a}=={str(i) for i in range(50)}


def test_board_discovery_and_post_links():
    from moa.crawl import board_links, post_links
    base='https://example.org/school/main.do'
    h='<a href="/school/na/ntt/selectNttList.do?bbsId=12&mi=3">가정통신문</a><a href="/login">로그인</a>'
    assert len(board_links(h,base))==1
    board=board_links(h,base)[0]
    html='<a href="#" data-id="444" class="nttInfoBtn">학부모 안내</a>'
    p=post_links(html,board)
    assert len(p)==1 and 'nttSn=444' in p[0]['url'] and 'bbsId=12' in p[0]['url']
    p=post_links('<a href="/school/M0105/view/123?s_idx=1">교육 안내</a>',base)
    assert '/view/123' in p[0]['url']


def test_extract_detail_rejects_login_and_navigation():
    from moa.crawl import detail
    with pytest.raises(ValueError): detail('<html><h1>로그인</h1><form><input type="password"></form></html>','https://example.org/p','x')
    with pytest.raises(ValueError): detail('<nav>home school news</nav>','https://example.org/p','x')
    d=detail('<h2 class="nttTitle">행사</h2><div class="nttCn"><p>행사에 참가하는 학생은 준비물을 지참해 주세요.</p><table><tr><td>학년</td><td>1학년</td></tr></table></div><a href="/common/nttFileDownload.do?fileKey=abc">안내.pdf</a>','https://example.org/p','x')
    assert d['title']=='행사' and len(d['attachments'])==1
    assert '<table>' in d['body_html']


def test_fake_file_is_not_pdf():
    from moa.extract import detect_kind
    assert detect_kind(b'<html>login</html>','a.pdf')=='html'
    assert detect_kind(b'%PDF-1.7 hello','download.do')=='pdf'
    assert detect_kind(b'\x89PNG\r\n\x1a\nxx','a.do')=='image'


def test_neis_requires_real_key(tmp_path):
    from moa.crawl import sync_schools
    with pytest.raises(ValueError,match='NEIS_API_KEY'):
        sync_schools(None,tmp_path,'')


def test_candidates_are_not_approved(tmp_path):
    from moa.core import Store
    from moa.learn import learn, export_learning
    with Store(tmp_path) as s:
        n,_=s.save_notice(school(),'https://example.org/view/1','안내','<table><tr><td>장소</td><td>운동장</td></tr></table>',[],'2026-09-21')
        learn(s,n)
        learn(s,n)
        export_learning(s)
        assert (tmp_path/'learning/candidates.jsonl').read_text().strip()
        assert (tmp_path/'learning/approved.jsonl').read_text()==''
        assert s.db.execute('select count(*) from cases').fetchone()[0]==1


class FakeNetwork:
    def __init__(self, pages):
        self.pages=pages
        self.requests=0
    def get(self,url,**kwargs):
        from moa.net import Response,canonical_url
        self.requests+=1
        target=canonical_url(url)
        if target not in self.pages: raise RuntimeError('HTTP 404 fixture')
        data=self.pages[target]
        if isinstance(data,Exception): raise data
        return Response(target,200,{},data if isinstance(data,bytes) else data.encode())


def pipeline_fixture(code='B10'):
    rows=[];pages={}
    for n in range(12):
        s=school(code,str(n));s['home_url']=f'https://example.org/s{n}/'
        rows.append(s)
        board=f'https://example.org/s{n}/M0103/'
        pages[s['home_url']]=f'<a href="{board}">가정통신문</a>'
        pages[board]=f'<a href="{board}view/{n+1}">행사 {n}</a>'
        pages[f'{board}view/{n+1}']=f'<h2 class="nttTitle">행사 {n}</h2><div class="nttCn"><p>{n}학년 행사 안내입니다. 준비물을 확인해 주세요.</p><table><tr><td>장소</td><td>교실 {n}</td></tr></table></div>'
    return rows,pages


def test_pipeline_hits_quota_rerun_does_not_overcollect(tmp_path,monkeypatch):
    from moa.core import Store
    from moa.app import collect
    monkeypatch.setenv('MIN_FREE_MB','100')
    rows,pages=pipeline_fixture()
    net=FakeNetwork(pages)
    with Store(tmp_path) as store:
        r=collect(store,net,rows,'2026-09-21',seoul_target=10)
        assert r['status']=='complete' and r['regions']['B10']['new']==10
        assert r['case_count']==10 and r['approved_count']==0
        requests=net.requests
        r=collect(store,net,rows,'2026-09-21',seoul_target=10)
        assert r['regions']['B10']['new']==0 and net.requests==requests


def test_pipeline_other_office_and_missing_attachment(tmp_path,monkeypatch):
    """A refused attachment must not hide the notice body, but it must be recorded."""
    from moa.core import Store
    from moa.app import collect
    rows,pages=pipeline_fixture('C10')
    for n in range(9):
        url=f'https://example.org/s{n}/M0103/view/{n+1}'
        pages[url]+='<a href="/missing.pdf">안내.pdf</a>'
    monkeypatch.setenv('MIN_FREE_MB','100')
    with Store(tmp_path) as store:
        r=collect(store,FakeNetwork(pages),rows,'2026-09-21',other_target=5)
        stats=r['regions']['C10']
        assert stats['target']==5 and stats['new']==5 and stats['shortfall']==0
        assert r['status']=='complete'
        assert stats['attachments_failed']>=1 and stats['attachments_skipped_robots']==0
        assert store.db.execute('select count(*) from notices').fetchone()[0]==5
        payloads=[json.loads(row[0]) for row in store.db.execute('select payload from notices')]
        recorded=[p for p in payloads if 'attachments_failed' in p]
        assert recorded and recorded[0]['attachments_failed'][0]['url'].endswith('/missing.pdf')
        assert all(p['assets']==[] for p in recorded)


def test_notice_with_empty_body_and_no_attachment_is_not_counted(tmp_path,monkeypatch):
    from moa.core import Store
    from moa.app import collect
    monkeypatch.setenv('MIN_FREE_MB','100')
    rows=[school('C10','1')]
    home=rows[0]['home_url']
    board='https://example.org/s1/M0103/'
    pages={home:f'<a href="{board}">가정통신문</a>',
           board:f'<a href="{board}view/1">빈 글</a>',
           f'{board}view/1':'<div class="nttCn"><p>짧음</p></div><a href="/missing.pdf">안내.pdf</a>'}
    with Store(tmp_path) as store:
        r=collect(store,FakeNetwork(pages),rows,'2026-09-21')
        assert r['regions']['C10']['new']==0
        assert r['regions']['C10']['error_count']==1
        assert store.db.execute('select count(*) from notices').fetchone()[0]==0


def test_neis_repeated_sample_rejected(tmp_path):
    from moa.crawl import sync_schools
    from moa.net import Response
    class Neis:
        def get(self,*a,**kw):
            row={'ATPT_OFCDC_SC_CODE':'B10','SD_SCHUL_CODE':'1','ATPT_OFCDC_SC_NM':'서울특별시교육청',
                 'SCHUL_NM':'테스트','HMPG_ADRES':'https://example.org','SCHUL_KND_SC_NM':'초등학교'}
            body={'schoolInfo':[{'head':[{'list_total_count':100}]},{'row':[row]}]}
            return Response('',200,{},json.dumps(body).encode())
    with pytest.raises(RuntimeError,match='반복'): sync_schools(Neis(),tmp_path,'testing-valid-key')
    assert not (tmp_path/'registry/schools.json').exists()


def test_unreviewed_permission_cannot_approve(tmp_path):
    from moa.core import Store
    from moa.learn import learn,approve,export_learning,search_cases
    with Store(tmp_path) as s:
        ident,_=s.save_notice(school(),'https://example.org/1','안내','<table><tr><td>장소</td><td>운동장</td></tr></table>',[],'2026-09-21')
        learn(s,ident)
        cid=s.db.execute('select id from cases').fetchone()[0]
        assert search_cases(s,'운동장')==[]
        with pytest.raises(ValueError): approve(s,cid,'key_value_cards','검수자',False,True)
        approve(s,cid,'key_value_cards','검수자',True,True)
        export_learning(s)
        assert len(search_cases(s,'운동장'))==1
        assert 'human_reviewed' in (tmp_path/'learning/approved.jsonl').read_text()


def test_robots_disallow_never_downloads_target():
    from moa.net import Fetcher,Response
    f=Fetcher()
    calls=[]
    def one(url,limit):
        calls.append(url)
        return Response(url,200,{},b'User-agent: *\nDisallow: /private')
    f._one=one
    with pytest.raises(PermissionError): f.get('https://example.org/private/a.pdf')
    assert calls==['https://example.org/robots.txt']


def test_redirect_rechecks_target_host():
    from moa.net import Fetcher,Response
    f=Fetcher()
    f._one=lambda url,limit: Response(url,302,{'location':'http://127.0.0.1/secrets'},b'')
    with pytest.raises(ValueError): f.get('https://example.org/public',robots=False)


def test_robots_network_error_is_not_allow():
    from moa.net import Fetcher,Response
    f=Fetcher()
    f._one=lambda url,limit: Response(url,503,{},b'Unavailable')
    with pytest.raises(PermissionError): f.get('https://example.org/a')


def test_private_dns_record_rejected(monkeypatch):
    import socket
    from moa.net import public_addresses
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**kw:[(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',443))])
    with pytest.raises(ValueError): public_addresses('example.org',443)


def test_pdf_with_real_table():
    from reportlab.pdfgen import canvas
    from moa.extract import pdf_document
    b=io.BytesIO();c=canvas.Canvas(b)
    for y in [740,710,680]: c.line(50,y,350,y)
    for x in [50,150,350]: c.line(x,680,x,740)
    c.drawString(55,720,'Time');c.drawString(160,720,'Event')
    c.drawString(55,690,'09:00');c.drawString(160,690,'Morning programme')
    c.save()
    d=pdf_document(b.getvalue())
    assert len(d['tables'])==1 and d['tables'][0]['geometry_uncertain']
    assert '09:00' in d['text']


def test_image_and_hwp_not_falsely_marked_analysed(tmp_path):
    from moa.core import Store
    from moa.learn import learn
    with Store(tmp_path) as s:
        a=s.object(b'\x89PNG\r\n\x1a\nxx','scan.png');a['kind']='image'
        ident,_=s.save_notice(school(),'https://example.org/1','안내','',[a],'2026-09-21')
        r=learn(s,ident)
        assert r['status']=='partial' and r['pending'][0]['status']=='needs_vision'


def test_policy_guard_password_form_in_homepage_not_body():
    from moa.crawl import detail
    html='<nav><form><input type="password"></form></nav><div class="nttCn">내용이 공개된 가정통신문을 여기서 안내합니다.</div>'
    # Conservative fail-closed behavior is explicit, not a false successful read.
    with pytest.raises(ValueError): detail(html,'https://example.org','title')


def test_many_tables_not_silently_truncated():
    from moa.extract import html_document
    with pytest.raises(ValueError,match='표'):
        html_document('<table><tr><td>값</td></tr></table>'*101)


def test_streaming_jsonl_is_atomic(tmp_path):
    from moa.core import write_jsonl
    path=tmp_path/'cases.jsonl'
    write_jsonl(path,({'n':i} for i in range(100)))
    assert len(path.read_text().splitlines())==100
    def broken():
        yield {'n':1}
        raise ValueError('bad')
    with pytest.raises(ValueError): write_jsonl(path,broken())
    assert len(path.read_text().splitlines())==100


def test_failed_parser_cache_is_retried(tmp_path,monkeypatch):
    from moa.core import Store,write_json
    from moa.learn import extract_asset
    import subprocess
    from types import SimpleNamespace
    with Store(tmp_path) as s:
        a=s.object(b'%PDF-1.7 test','a.pdf');a['kind']='pdf'
        write_json(tmp_path/'extracted'/f"{a['sha256']}-v1.json",{'status':'parse_error'})
        monkeypatch.setattr(subprocess,'run',lambda *a,**kw:SimpleNamespace(returncode=0,stdout=b'{"status":"extracted","text":"OK","tables":[]}'))
        assert extract_asset(s,a)['status']=='extracted'


def test_same_school_daily_cap_survives_restart(tmp_path,monkeypatch):
    from moa.core import Store
    from moa.app import collect
    monkeypatch.setenv('MIN_FREE_MB','100')
    rows,pages=pipeline_fixture()
    only=[rows[0]]
    with Store(tmp_path) as store:
        first=collect(store,FakeNetwork(pages),only,'2026-09-21')
        assert first['regions']['B10']['new']==1
        board='https://example.org/s0/M0103/'
        pages[board]+='<a href="'+board+'view/100">다른 통신문</a>'
        pages[board+'view/100']='<div class="nttCn">다른 날짜의 새 행사 안내문입니다.</div>'
        net=FakeNetwork(pages)
        second=collect(store,net,only,'2026-09-21')
        assert second['regions']['B10']['new']==0
        assert net.requests==0


def test_single_office_check_does_not_skip_national_schedule(tmp_path):
    from moa.core import Store
    with Store(tmp_path) as store:
        run=store.start_run('2026-09-21')
        store.finish_run(run,{'day':'2026-09-21','status':'complete','only_office':'B10'})
        assert not store.attempted('2026-09-21')
        run=store.start_run('2026-09-21')
        store.finish_run(run,{'day':'2026-09-21','status':'partial','only_office':None})
        assert store.attempted('2026-09-21')


def test_js_shell_homepage_and_board_are_followed():
    from moa.net import Fetcher
    from moa.crawl import discover
    home='https://school.example.org/'
    pages={
        home: "<script>location.href='/main.do';</script>",
        'https://school.example.org/main.do': '<a href="/M010302/">가정통신문</a>',
        'https://school.example.org/M010302/': "<script>document.location.href='/M010302/list.do'</script>",
        'https://school.example.org/M010302/list.do': '<a href="/M010302/view/77.do">행사 안내문</a>',
    }
    school={'office_code':'C10','office_name':'부산광역시교육청','school_code':'1','school_name':'테스트',
            'school_kind':'중학교','home_url':home}
    posts,hosts=discover(FakeNetwork(pages),school,{})
    assert [p['url'] for p in posts]==['https://school.example.org/M010302/view/77.do']
    assert 'school.example.org' in hosts


def test_site_move_cross_host_redirect_requires_explicit_permission():
    from moa.net import Fetcher,Response
    f=Fetcher()
    def one(url,limit):
        if url.endswith('/robots.txt'):
            return Response(url,200,{},b'User-agent: *\nDisallow: /private')
        if 'old.example.org' in url:
            return Response(url,302,{'location':'https://school.platform.kr/abc'},b'')
        return Response(url,200,{},'<a href="/abc/M010302/">가정통신문</a>'.encode())
    f._one=one
    with pytest.raises(ValueError,match='허용되지 않은 호스트'):
        f.get('http://old.example.org/')
    assert f.get('http://old.example.org/',allow_site_move=True).url=='https://school.platform.kr/abc'


def test_hosting_move_to_platform_domain_is_followed():
    from moa.crawl import discover
    pages={
        'http://old.example.org/': '<script>location.href="https://school.platform.kr/abc"</script>',
        'https://school.platform.kr/abc': '<a href="/abc/M010302/">가정통신문</a>',
        'https://school.platform.kr/abc/M010302/': '<a href="/abc/M010302/view/9">급식 안내</a>',
    }
    school={'office_code':'P10','office_name':'전북특별자치도교육청','school_code':'2','school_name':'테스트',
            'school_kind':'초등학교','home_url':'http://old.example.org/'}
    posts,hosts=discover(FakeNetwork(pages),school,{})
    assert posts[0]['url']=='https://school.platform.kr/abc/M010302/view/9'
    assert 'school.platform.kr' in hosts


def test_boardcnts_and_selectntt_javascript_posts():
    from moa.crawl import post_links
    board='https://s.gwe.ms.kr/boardCnts/list.do?boardID=34795&m=0202&s=gosung'
    html='''<tr><td><a href="javascript:" onclick="javascript:goView('34795','9490654', '0', 'null', 'W', '1', 'N', '')">양식 안내</a></td></tr>'''
    posts=post_links(html,board)
    assert len(posts)==1 and 'boardSeq=9490654' in posts[0]['url'] and 'boardID=34795' in posts[0]['url']
    ntt='https://school.gyo6.net/gaejin/na/ntt/selectNttList.do?mi=119798&bbsId=27741'
    html2="""<tr><td><a href="javascript:fnView('555123')">가정통신문</a></td></tr>"""
    posts2=post_links(html2,ntt)
    assert len(posts2)==1 and 'selectNttInfo.do' in posts2[0]['url'] and 'nttSn=555123' in posts2[0]['url']


def test_login_gated_board_is_reported_not_bypassed():
    from moa.crawl import discover
    pages={
        'https://school.example.org/': '<a href="/bbs/list.do">가정통신문</a>',
        'https://school.example.org/bbs/list.do':
            '<form action="/lib/loginpost.php" name="fm_xb_login"><input type="password" name="passwd"></form>',
    }
    school={'office_code':'Q10','office_name':'전남광주통합특별시교육청(전남)','school_code':'3',
            'school_name':'테스트','school_kind':'중학교','home_url':'https://school.example.org/'}
    with pytest.raises(ValueError,match='로그인'):
        discover(FakeNetwork(pages),school,{})


def test_detail_reads_usm_and_boardcnts_bodies():
    from moa.crawl import detail
    usm='<table class="usm-brd-vew"><tr><th class="tch-tit"><h5>공개수업 운영 안내</h5></th></tr><tr><td class="tch-ctnt usm-editor-view"><p>학부모님께 안내드립니다.</p></td></tr></table>'
    d=detail(usm,'https://school.use.go.kr/banchon-e/M010302/view/1','제목')
    assert d['title']=='공개수업 운영 안내' and '안내드립니다' in d['body_html']
    cnts='''<div class="cntBody"><h1 class="tit"><strong>제목</strong>현장체험학습 안내</h1><div class="viewBox"><p>붙임 파일을 참고하세요</p></div></div>'''
    d2=detail(cnts,'https://s.gwe.ms.kr/boardCnts/view.do?boardID=1&boardSeq=2','제목')
    assert '붙임 파일을 참고하세요' in d2['body_html'] and d2['title']=='현장체험학습 안내'


def test_robots_blocked_attachment_is_recorded_not_fatal(tmp_path,monkeypatch):
    from moa.core import Store
    from moa.app import collect
    monkeypatch.setenv('MIN_FREE_MB','100')
    rows=[school('B10','7')]
    home=rows[0]['home_url']
    board='https://example.org/s7/M0103/'
    pages={home:f'<a href="{board}">가정통신문</a>',
           board:f'<a href="{board}view/1">행사 안내</a>',
           f'{board}view/1':'<div class="nttCn"><p>첨부를 확인해 주세요.</p></div><a href="/files/a.pdf">안내.pdf</a>',
           'https://example.org/files/a.pdf':PermissionError('robots.txt 수집 제한')}
    with Store(tmp_path) as store:
        report=collect(store,FakeNetwork(pages),rows,'2026-09-21')
        assert report['regions']['B10']['new']==1
        assert report['regions']['B10']['attachments_skipped_robots']==1
        payload=json.loads(store.db.execute('select payload from notices').fetchone()[0])
        assert payload['assets']==[] and payload['attachments_skipped_robots'][0]['reason']=='robots.txt'


def test_overseas_office_excluded_from_daily_target(tmp_path,monkeypatch):
    from moa.core import Store
    from moa.app import collect
    monkeypatch.setenv('MIN_FREE_MB','100')
    monkeypatch.delenv('EXCLUDE_OFFICES',raising=False)
    rows=[school('B10','1'),school('V10','2')]
    pages=pipeline_fixture()[1]
    with Store(tmp_path) as store:
        report=collect(store,FakeNetwork(pages),rows,'2026-09-21')
        assert 'V10' not in report['regions'] and report['excluded_offices']==['V10']
        assert report['daily_target_total']==50


def test_every_failed_school_is_reported(tmp_path,monkeypatch):
    from moa.core import Store
    from moa.app import collect
    monkeypatch.setenv('MIN_FREE_MB','100')
    rows=[school('C10',str(n)) for n in range(25)]
    for n,row in enumerate(rows):
        row['home_url']=f'https://example.org/f{n}/'
    with Store(tmp_path) as store:
        report=collect(store,FakeNetwork({}),rows,'2026-09-21')
        stats=report['regions']['C10']
        assert stats['schools_tried']==25
        assert stats['error_count']==25 and len(stats['errors'])==25


def test_fetcher_retries_transient_connection_error(monkeypatch):
    """School servers often reset the first connection; one retry must not fail the school."""
    import urllib3
    from moa import net
    from moa.net import Fetcher
    state={'n':0}
    class FakeResponse:
        status=200
        headers={'Content-Type':'text/plain'}
        def stream(self,*a,**k):
            yield b'ok'
        def close(self): pass
    class FakePool:
        def __init__(self,*a,**k): pass
        def urlopen(self,*a,**k):
            state['n']+=1
            if state['n']==1:
                raise urllib3.exceptions.ProtocolError('Connection aborted.')
            return FakeResponse()
        def close(self): pass
    monkeypatch.setattr(net.urllib3,'HTTPSConnectionPool',FakePool)
    monkeypatch.setattr(net,'public_addresses',lambda host,port:['93.184.216.34'])
    monkeypatch.setattr(net.time,'sleep',lambda *_: None)
    response=Fetcher(delay=1.0)._one('https://example.org/x',1024)
    assert response.status==200 and state['n']==2


def test_jsessionid_path_parameter_is_dropped():
    from moa.net import canonical_url
    assert canonical_url('https://s.dge.es.kr/dgdowone/main.do;jsessionid=ABC?sysId=x')==\
        'https://s.dge.es.kr/dgdowone/main.do?sysId=x'


def test_dext5_uploader_attachment_is_read_from_script():
    """Gyeonggi goe*.kr posts render the body in JS but name the real file in the uploader call."""
    from moa.crawl import detail
    html=('<div class="bbs_ViewA"><h3>2026년 2차 학교폭력 실태조사 안내</h3></div>'
          "<script>DEXT5UPLOAD.AddUploadedFile('k1', '안내 가정통신문.pdf', "
          "'/upload/jisan-m/na/bbs_4908/2026/09/579a858e.pdf', '81386', 'k1', G_UploadID);</script>")
    d=detail(html,'https://jisan-m.goepj.kr/jisan-m/na/ntt/selectNttInfo.do?bbsId=4908','제목')
    assert d['title']=='2026년 2차 학교폭력 실태조사 안내'
    assert d['attachments']==[{'url':'https://jisan-m.goepj.kr/upload/jisan-m/na/bbs_4908/2026/09/579a858e.pdf',
                               'filename':'안내 가정통신문.pdf'}]


def test_status_reports_today_dedup_and_review_state(tmp_path,capsys):
    from moa.app import cli
    from moa.core import Store
    from moa.learn import learn
    with Store(tmp_path) as store:
        asset=store.object(b'x','a.pdf')
        ident,_=store.save_notice(school(),'https://example.org/1','안내',
                                  '<table><tr><td>장소</td><td>운동장</td></tr></table>',[asset],
                                  datetime.now().date().isoformat())
        learn(store,ident)
    assert cli(['--data',str(tmp_path),'status'])==0
    out=json.loads(capsys.readouterr().out)
    assert out['documents']==1 and out['documents_today']==1
    assert out['unique_objects_on_disk']==1 and out['sightings']==1
    assert out['table_cases']==1 and out['review_pending_cases']==1 and out['approved']==0


def test_attachment_name_prefers_real_file_name():
    from moa.crawl import attachment_name
    assert attachment_name('다운로드 : 7회) 안내문.pdf','https://x/y/download.do?file=1')=='안내문.pdf'
    assert attachment_name('다운로드 : 7회)','https://x/y/guide.pdf')=='guide.pdf'
    assert attachment_name('다운로드','https://x/y/download.do?fileSid=9')=='다운로드'


def test_error_run_is_retried_but_complete_is_not(tmp_path):
    """A transient error must not consume the whole day, while a finished day is never repeated."""
    from moa.core import Store
    with Store(tmp_path) as store:
        run=store.start_run('2026-09-21')
        store.finish_run(run,{'day':'2026-09-21','status':'error','only_office':None,'error':'NEIS'})
        assert not store.attempted('2026-09-21')
        assert store.last_national_run('2026-09-21')['status']=='error'
        run=store.start_run('2026-09-21')
        store.finish_run(run,{'day':'2026-09-21','status':'partial','only_office':None})
        assert store.attempted('2026-09-21')
        assert store.last_national_run('2026-09-21')['status']=='partial'
        run=store.start_run('2026-09-21')
        store.finish_run(run,{'day':'2026-09-22','status':'error','only_office':'B10','error':'x'})
        assert store.last_national_run('2026-09-22') is None


def test_attachment_on_office_portal_host_is_followed(tmp_path,monkeypatch):
    """Office-wide notices attach files on the office portal domain (www.<office>.go.kr)."""
    from moa.core import Store
    from moa.app import collect
    monkeypatch.setenv('MIN_FREE_MB','100')
    rows=[school('C10','9')]
    home=rows[0]['home_url']
    board='https://example.org/s9/M0103/'
    pages={home:f'<a href="{board}">가정통신문</a>',
           board:f'<a href="{board}view/1">교육청 공통 안내</a>',
           f'{board}view/1':'<div class="nttCn"><p>첨부를 확인해 주세요.</p>'
                            '<a href="https://portal.example.net/download.jje?fileSid=1">소식지.pdf</a></div>',
           'https://portal.example.net/download.jje?fileSid=1':b'%PDF-1.7 office portal file'}
    with Store(tmp_path) as store:
        report=collect(store,FakeNetwork(pages),rows,'2026-09-21')
        stats=report['regions']['C10']
        assert stats['new']==1
        assert stats['attachment_hosts_used']==['portal.example.net']
        payload=json.loads(store.db.execute('select payload from notices').fetchone()[0])
        assert payload['assets'][0]['kind']=='pdf'


def test_neis_paging_finishes_on_record_count_with_duplicate_codes(tmp_path):
    """Real NEIS data repeats office/school codes (blank codes), so paging must finish on the
    record count the head reports, and the office spread must be validated."""
    from moa.crawl import sync_schools
    from moa.net import Response
    offices=['B10','C10','D10','E10','F10','G10','H10','I10','J10','K10','M10','N10',
             'P10','Q10','R10','S10','T10']
    rows=[]
    for index in range(1000):
        blank=index % 100 == 0
        office='B10' if blank else offices[index % len(offices)]
        code='       ' if blank else str(index)
        rows.append({'ATPT_OFCDC_SC_CODE':office,'SD_SCHUL_CODE':code,
                     'ATPT_OFCDC_SC_NM':f'{office}교육청','SCHUL_NM':f'학교{index}',
                     'SCHUL_KND_SC_NM':'초등학교','HMPG_ADRES':f'https://example.org/s{index}/'})
    body={'schoolInfo':[{'head':[{'list_total_count':1000}]},{'row':rows}]}
    class NeisPage:
        def __init__(self): self.calls=0
        def get(self,*a,**kw):
            self.calls+=1
            if self.calls>1:
                return Response('',200,{},json.dumps({'RESULT':{'CODE':'INFO-200'}}).encode())
            return Response('',200,{},json.dumps(body).encode())
    fake=NeisPage()
    result=sync_schools(fake,tmp_path,'testing-valid-key')
    assert fake.calls==1  # finished on the head's record count instead of asking for another page
    assert len(result)==991  # 9 repeated office/school codes collapse to one school each
    saved=json.loads((tmp_path/'registry/schools.json').read_text())
    assert saved['total_records']==1000 and saved['unique_schools']==991
    assert len(saved['offices'])==17


# ---------- Phase 2: targets, backfill, queue, review, splits ----------------

def test_phase2_daily_targets_sum_to_400(tmp_path,monkeypatch):
    from moa.core import Store
    from moa.app import collect
    monkeypatch.setenv('MIN_FREE_MB','100')
    monkeypatch.delenv('EXCLUDE_OFFICES',raising=False)
    offices=['B10','C10','D10','E10','F10','G10','H10','I10','J10','K10','M10','N10',
             'P10','Q10','R10','S10','T10','V10']
    rows=[school(code,'1') for code in offices]
    with Store(tmp_path) as store:
        report=collect(store,FakeNetwork({}),rows,'2026-09-21')
    assert report['daily_target_total']==400
    assert report['regions']['B10']['target']==50
    assert report['regions']['J10']['target']==50
    assert report['regions']['C10']['target']==20
    assert 'V10' not in report['regions']


def test_gyeonggi_target_is_not_other_target():
    from moa.core import quota
    assert quota('J10','경기도교육청',seoul=50,gyeonggi=50,other=20)==50
    assert quota('J10','경기도교육청',seoul=50,gyeonggi=7,other=20)==7
    assert quota('B10','서울특별시교육청',seoul=50,gyeonggi=7,other=20)==50


def _backfill_fixture(code='C10', posts=6, pages_of=3):
    rows=[];pages={}
    s=school(code,'1');s['home_url']='https://example.org/s1/'
    rows.append(s)
    board='https://example.org/s1/M0103/'
    pages[s['home_url']]=f'<a href="{board}">가정통신문</a>'
    for pg in range(pages_of):
        url=board if pg==0 else f'{board}?page={pg+1}'
        links=''.join(
            f'<tr><td><a href="{board}view/{pg*10+i}">안내 {pg*10+i}</a></td>'
            f'<td>2026-0{(i%9)+1}-1{i%9}</td></tr>' for i in range(posts))
        nxt=(f'<a href="{board}?page={pg+2}">{pg+2}</a>' if pg+1<pages_of else '')
        pages[url]=links+nxt
        for i in range(posts):
            n=pg*10+i
            pages[f'{board}view/{n}']=(f'<div class="nttCn"><p>{n}번째 안내문입니다. '
                                       f'준비물을 확인해 주세요.</p></div>')
    return rows,pages


def test_backfill_caps_and_incremental_separation(tmp_path,monkeypatch):
    from moa.core import Store
    from moa import backfill
    monkeypatch.setenv('MIN_FREE_MB','100')
    monkeypatch.setenv('BACKFILL_SCHOOL_DAILY','2')
    rows,pages=_backfill_fixture()
    (tmp_path/'registry').mkdir(exist_ok=True)
    (tmp_path/'registry/schools.json').write_text(json.dumps({'schools':rows}))
    with Store(tmp_path) as store:
        campaign=backfill.create_campaign(store)
        net=FakeNetwork(pages)
        r=backfill.run_batch(store,net,campaign,batch_notices=30,max_minutes=5)
        assert r['new']==2          # per-school daily cap 2
        assert store.count_campaign(campaign['id'])==2
        assert store.count_day('2026-09-21','C10')==0  # backfill never counts as daily
        assert store.db.execute('select count(*) from notices').fetchone()[0]==2
        # A second batch the same day respects the daily cap
        r2=backfill.run_batch(store,net,campaign,batch_notices=30,max_minutes=5)
        assert r2['new']==0


def test_backfill_checkpoint_resumes_after_stop(tmp_path,monkeypatch):
    from moa.core import Store
    from moa import backfill
    monkeypatch.setenv('MIN_FREE_MB','100')
    monkeypatch.setenv('BACKFILL_SCHOOL_DAILY','50')
    monkeypatch.setenv('BACKFILL_SCHOOL_CAP','50')
    rows,pages=_backfill_fixture()
    (tmp_path/'registry').mkdir(exist_ok=True)
    (tmp_path/'registry/schools.json').write_text(json.dumps({'schools':rows}))
    with Store(tmp_path) as store:
        campaign=backfill.create_campaign(store)
        net=FakeNetwork(pages)
        r=backfill.run_batch(store,net,campaign,batch_notices=1,max_minutes=5)
        assert r['new']==1
        cs=store.campaign_school(campaign['id'],'C10:1')
        assert cs and cs['cursor']  # checkpoint persisted
    with Store(tmp_path) as store:
        r=backfill.run_batch(store,net,campaign,batch_notices=30,max_minutes=5)
        assert store.count_campaign(campaign['id'])>=2


def test_backfill_stops_on_repeated_page_and_old_dates(tmp_path,monkeypatch):
    from moa.core import Store
    from moa import backfill
    monkeypatch.setenv('MIN_FREE_MB','100')
    monkeypatch.setenv('BACKFILL_SCHOOL_DAILY','50')
    monkeypatch.setenv('BACKFILL_SCHOOL_CAP','50')
    rows,pages=_backfill_fixture()
    board='https://example.org/s1/M0103/'
    pages[board+'?page=3']=pages[board+'?page=2']  # CMS returns the same page again
    (tmp_path/'registry').mkdir(exist_ok=True)
    (tmp_path/'registry/schools.json').write_text(json.dumps({'schools':rows}))
    with Store(tmp_path) as store:
        campaign=backfill.create_campaign(store)
        r=backfill.run_batch(store,FakeNetwork(pages),campaign,batch_notices=100,max_minutes=5)
        cs=store.campaign_school(campaign['id'],'C10:1')
        assert cs['status']=='done' and r['new']>0


def test_pagination_finds_next_page():
    from moa.crawl import next_page_url, current_page
    base='https://example.org/list.do?bbsId=1&pageIndex=1'
    html='<a href="javascript:fn_egov_link_page(\'2\')">2</a>'
    assert 'pageIndex=2' in next_page_url(html,base)
    html2='<a href="/list.do?bbsId=1&pageIndex=2">다음</a>'
    assert 'pageIndex=2' in next_page_url(html2,base)
    assert next_page_url('<a href="/x">다음</a>',base) is None
    assert current_page('https://x/1?page=3')==3


def test_same_size_different_meaning_tables_get_different_families():
    from moa.extract import html_document
    from moa.learn import template_family
    a=html_document('<table><tr><th>일시</th><th>장소</th></tr><tr><td>3월 1일</td><td>운동장</td></tr></table>')
    b=html_document('<table><tr><th>품목</th><th>단가</th></tr><tr><td>연필</td><td>500원</td></tr></table>')
    assert template_family(a['tables'][0])!=template_family(b['tables'][0])
    c=html_document('<table><tr><th>일시</th><th>장소</th></tr><tr><td>4월 2일</td><td>강당</td></tr></table>')
    assert template_family(a['tables'][0])==template_family(c['tables'][0])


def test_eval_split_excluded_from_search_and_suggestions(tmp_path):
    from moa.core import Store
    from moa.learn import learn,search_cases,split_for,export_learning
    with Store(tmp_path) as s:
        ident,_=s.save_notice(school(),'https://example.org/1','안내',
                              '<table><tr><td>장소</td><td>운동장</td></tr></table>',[],'2026-09-21')
        learn(s,ident)
        cid,fam=s.db.execute('select id,family_id from cases').fetchone()
        s.db.execute("update cases set split='eval',approved=1 where id=?",(cid,))
        s.db.commit()
        assert search_cases(s,'운동장')==[]
        assert search_cases(s,'운동장',include_candidates=True)==[]
        export_learning(s)
        assert 'eval' in (tmp_path/'learning/eval.jsonl').read_text()
        assert (tmp_path/'learning/approved.jsonl').read_text()==''


def test_review_actions_and_unapprove(tmp_path):
    from moa.core import Store
    from moa.learn import learn,review,search_cases
    with Store(tmp_path) as s:
        ident,_=s.save_notice(school(),'https://example.org/1','안내',
                              '<table><tr><td>장소</td><td>운동장</td></tr></table>',[],'2026-09-21')
        learn(s,ident)
        cid=s.db.execute('select id from cases').fetchone()[0]
        review(s,cid,'approved',layout='key_value_cards',reviewer='홍길동',
               rights_reviewed=True,privacy_reviewed=True,
               correction={'note':'헤더 병합 수정'})
        assert search_cases(s,'운동장')
        review(s,cid,'candidate',reviewer='홍길동',note='승인 취소')
        row=s.db.execute('select approved,review_status,review_history,correction from cases').fetchone()
        assert row['approved']==0 and row['review_status']=='candidate'
        assert '승인 취소' in row['review_history'] and row['correction']
        review(s,cid,'rejected',reviewer='홍길동')
        assert s.db.execute('select review_status from cases').fetchone()[0]=='rejected'


def test_review_queue_prioritises_new_families(tmp_path):
    from moa.core import Store
    from moa.learn import learn,build_review_queue
    with Store(tmp_path) as s:
        for n in range(3):
            ident,_=s.save_notice(school(n=str(n)),f'https://example.org/{n}','안내',
                f'<table><tr><td>장소{n}</td><td>운동장</td></tr></table>',[],'2026-09-21')
            learn(s,ident)
        q=build_review_queue(s,'2026-09-21',20)
        assert len(q)==3 and all('신규 양식' in c['reasons'] for c in q)
        assert s.db.execute('select count(*) from review_queue').fetchone()[0]==3


def test_analysis_queue_survives_and_retries(tmp_path):
    from moa.core import Store
    from moa.learn import drain_analyse
    with Store(tmp_path) as s:
        ident,_=s.save_notice(school(),'https://example.org/1','안내',
                              '<table><tr><td>장소</td><td>운동장</td></tr></table>',[],'2026-09-21')
        job=s.db.execute("select * from jobs where kind='analyse'").fetchone()
        assert job and job['status']=='pending'
        s.db.execute("update jobs set status='running',owner='dead',"
                     "updated='2000-01-01T00:00:00+09:00'").fetchone
        s.db.execute("update jobs set status='running',owner='dead',"
                     "updated='2000-01-01T00:00:00+09:00'")
        s.db.commit()
        assert s.requeue_stale_jobs()==1
        r=drain_analyse(s,10,owner='t')
        assert r['done']==1 and r['pending']==0


def test_shared_request_budget_across_instances(tmp_path):
    from moa.core import Budget,Store
    with Store(tmp_path) as s:
        a=Budget(s,'2026-09-21',3);b=Budget(s,'2026-09-21',3)
        a.spend();b.spend()
        assert a.remaining==1 and b.remaining==1
        a.spend()
        import pytest as _p
        with _p.raises(RuntimeError): b.spend()


def test_migration_from_v1_creates_backup_and_columns(tmp_path):
    import sqlite3
    db=tmp_path/'db';db.mkdir()
    conn=sqlite3.connect(db/'moa.sqlite3')
    conn.executescript('''CREATE TABLE notices(id TEXT PRIMARY KEY,office TEXT,school TEXT,
        day TEXT,url TEXT,payload TEXT,analysis_status TEXT DEFAULT 'pending');
        CREATE TABLE cases(id TEXT PRIMARY KEY,notice_id TEXT,pattern TEXT,layout TEXT,
        payload TEXT,approved INTEGER DEFAULT 0,reviewer TEXT,reviewed_at TEXT);
        INSERT INTO notices VALUES('n1','B10','1','2026-09-20','u','{}','extracted');
        INSERT INTO cases VALUES('c1','n1','p','l','{}',1,'r','t');''')
    conn.commit();conn.close()
    from moa.core import Store,SCHEMA_VERSION
    with Store(tmp_path) as s:
        cols={r[1] for r in s.db.execute('PRAGMA table_info(notices)')}
        assert {'published_date','campaign_id','capture','table_state'}<=cols
        ccols={r[1] for r in s.db.execute('PRAGMA table_info(cases)')}
        assert {'family_id','split','review_status'}<=ccols
        assert s.db.execute('PRAGMA user_version').fetchone()[0]==SCHEMA_VERSION
        assert s.db.execute("select review_status from cases where id='c1'").fetchone()[0]=='approved'
        assert list((tmp_path/'db/backups').glob('*.sqlite3'))
        # Re-opening is idempotent: no second migration crash
    with Store(tmp_path) as s:
        assert s.db.execute('select count(*) from notices').fetchone()[0]==1


def test_parser_version_change_reanalyses(tmp_path,monkeypatch):
    from moa.core import Store
    from moa.learn import extract_asset
    import moa.learn as L
    with Store(tmp_path) as s:
        a=s.object(b'%PDF-1.7 x','a.pdf');a['kind']='pdf'
        import subprocess
        from types import SimpleNamespace
        monkeypatch.setattr(subprocess,'run',
            lambda *a,**k:SimpleNamespace(returncode=0,stdout=b'{"status":"extracted","text":"x","tables":[]}'))
        r1=extract_asset(s,a)
        assert r1['parser_version']==L.PARSER_VERSION
        monkeypatch.setattr(L,'PARSER_VERSION','moa-local-v3')
        calls=[]
        monkeypatch.setattr(subprocess,'run',
            lambda *a,**k:(calls.append(1),SimpleNamespace(returncode=0,
                stdout=b'{"status":"extracted","text":"x","tables":[]}'))[1])
        extract_asset(s,a)
        assert calls  # new version re-ran the parser instead of serving the v2 cache
