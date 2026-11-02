"""School HTTP permission tests with two synthetic identities on loopback only."""
import importlib
import importlib.util
import io
import json
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from http.cookiejar import CookieJar
from http.server import ThreadingHTTPServer

import pytest

from moa import school_service as svc
from moa.core import Store
from test_mobile import multipart


@pytest.fixture
def school_web(tmp_path):
    assert importlib.util.find_spec('moa.web_school') is not None, 'Scoped school HTTP service is missing'
    web = importlib.import_module('moa.web_school')
    with Store(tmp_path, thread_safe=True) as store:
        svc.migrate(store)
        users = {}
        for code, subject, password, role in [('A','a','synthetic-a-only','uploader'),
                                              ('B','b','synthetic-b-only','uploader'),
                                              ('A','reader','synthetic-reader-only','reader')]:
            svc.configure_school(store, code, code + ' 합성학교')
            svc.grant_member(store, code, subject, role)
            users[subject] = dict(school=code, subject=subject, **web.password_record(password))
        server = ThreadingHTTPServer(('127.0.0.1', 0), web.make_handler(store, users))
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        yield web, store, 'http://127.0.0.1:' + str(server.server_port)
        server.shutdown(); thread.join(); server.server_close()


def client(): return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))


def fetch(opener, base, path='/', fields=None, data=None, headers=None):
    if fields is not None: data = urllib.parse.urlencode(fields).encode()
    request = urllib.request.Request(base + path, data=data, headers=headers or {})
    try:
        with opener.open(request, timeout=10) as r: return r.status, r.read(), r.headers
    except urllib.error.HTTPError as e: return e.code, e.read(), e.headers


def login(opener, base, user='a', password='synthetic-a-only'):
    status, body, _ = fetch(opener, base, '/login', fields={'username':user,'password':password})
    assert status == 200
    return re.search(r'name="csrf" value="([^"]+)"', body.decode())[1]


def upload(opener, base, csrf, *, body='<p>합성 안내 12,000원</p>', key='post-1'):
    raw, ctype = multipart({'csrf':csrf,'post_key':key,'title':'합성 행사','body':body},
                           [('attachments','신청서.txt', b'synthetic untouched original')])
    status, page, _ = fetch(opener, base, '/upload', data=raw, headers={'Content-Type':ctype})
    assert status == 200, page.decode()
    return re.search(r'data-version="([a-f0-9]+)"', page.decode())[1]


def test_staff_upload_conversion_html_and_zip_are_scoped(school_web):
    web, store, base = school_web
    a, b, anon = client(), client(), client()
    assert fetch(anon, base)[0] == 401
    csrf = login(a, base); login(b, base, 'b', 'synthetic-b-only')
    ident = upload(a, base, csrf)
    assert len(svc.list_notices(store, svc.Principal('A','a'))) == 1
    assert b'12,000' not in fetch(b, base)[1]
    assert fetch(b, base, '/notice?id='+ident)[0] == 404
    assert fetch(a, base, '/convert', fields={'id':ident,'csrf':csrf,'revision':''})[0] == 200
    assert svc.process_conversions(store) == ['done']
    for endpoint in ('/file?id='+ident+'&file=0', '/html?id='+ident, '/bundle?id='+ident):
        assert fetch(anon, base, endpoint)[0] == 401
        assert fetch(b, base, endpoint)[0] == 404
        assert fetch(a, base, endpoint)[0] == 200
    status, html, headers = fetch(a, base, '/html?id='+ident)
    assert b'12,000' in html and '/file?id='+ident in html.decode()
    assert 'no-store' in headers['Cache-Control']
    with zipfile.ZipFile(io.BytesIO(fetch(a, base, '/bundle?id='+ident)[1])) as z:
        assert b'synthetic untouched original' in [z.read(n) for n in z.namelist() if n.startswith('attachments/')]


def test_no_shared_review_bearer_role_or_cross_school_write(school_web):
    web, store, base = school_web
    a, b, reader = client(), client(), client()
    csrf_a = login(a, base); csrf_b = login(b, base, 'b', 'synthetic-b-only')
    csrf_r = login(reader, base, 'reader', 'synthetic-reader-only')
    ident = upload(a, base, csrf_a)
    assert fetch(b, base, '/convert', fields={'id':ident,'csrf':csrf_b,'school':'A','subject':'a'})[0] == 404
    assert fetch(reader, base, '/convert', fields={'id':ident,'csrf':csrf_r})[0] == 403
    assert fetch(a, base, '/convert', fields={'id':ident,'csrf':'wrong'})[0] == 403
    assert fetch(a, base, '/convert', fields={'id':ident,'csrf':csrf_a}, headers={'Origin':'https://evil.example.test'})[0] == 403
    assert fetch(client(), base, headers={'Authorization':'Bearer synthetic-a-only'})[0] == 401
    for path in ('/obj?sha='+'a'*64, '/mobile/state?id='+ident, '/v1/worker/jobs', '/queue', '/corpus'):
        assert fetch(a, base, path)[0] == 404
    svc.grant_member(store, 'A', 'a', 'uploader', active=False)
    assert fetch(a, base, '/notice?id='+ident)[0] == 401
    assert store.db.execute('SELECT count(*) FROM jobs').fetchone()[0] == 0


def test_upload_html_is_not_executed_or_public_and_roles_are_explicit(school_web):
    _, store, base = school_web
    a = client(); csrf = login(a, base)
    ident = upload(a, base, csrf, body='<script>alert(1)</script><p>Original body</p>')
    page = fetch(a, base, '/notice?id='+ident)[1].decode()
    assert '<script>alert(1)</script>' not in page
    assert 'private' in page and '원본 첨부' in page and 'HTML 변환' in page
    assert fetch(a, base, '/convert', fields={'id':ident,'csrf':csrf,'revision':'','role:0':'attachment','role:1':'convert'})[0] == 200
    assert svc.process_conversions(store) == ['done']
    version = svc.get_version(store, svc.Principal('A','a'), ident)
    assert [f['role'] for f in version['files']] == ['attachment','convert']


def test_password_file_permissions_and_validation(tmp_path):
    assert importlib.util.find_spec('moa.web_school') is not None
    web = importlib.import_module('moa.web_school')
    path = tmp_path/'synthetic-auth.json'
    path.write_text(json.dumps({'users': {'teacher': dict(school='A',subject='a',**web.password_record('synthetic-only'))}}))
    path.chmod(0o644)
    with pytest.raises(PermissionError): web.load_credentials(path)
    path.chmod(0o600)
    assert web.load_credentials(path)['teacher']['school'] == 'A'
    assert 'synthetic-only' not in path.read_text()


def test_shared_db_research_server_cannot_read_or_mutate_school_notices(school_web):
    from moa import mobile
    from moa.web import Handler
    _, store, base = school_web
    a = client(); csrf = login(a, base)
    ident = upload(a, base, csrf)
    svc.request_conversion(store, svc.Principal('A', 'a'), ident)
    assert svc.process_conversions(store) == ['done']
    version = svc.get_version(store, svc.Principal('A', 'a'), ident)
    mid = version['mobile_id']
    class ReviewHandler(Handler):
        token = 'synthetic-review-only'
        sessions = {}
        login_attempts = {}
    ReviewHandler.store = store
    server = ThreadingHTTPServer(('127.0.0.1', 0), ReviewHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    review = 'http://127.0.0.1:' + str(server.server_port)
    headers = {'Authorization': 'Bearer synthetic-review-only'}
    try:
        assert mid not in fetch(client(), review, headers=headers)[1].decode()
        for path in ('/notice', '/mobile/preview', '/mobile/state', '/mobile/file',
                     '/mobile/original', '/mobile/html', '/mobile/bundle'):
            assert fetch(client(), review, path+'?id='+mid+'&file=0', headers=headers)[0] == 404
        sha = version['files'][0]['sha256']
        html_sha = store.db.execute('SELECT html_sha FROM mobile_revisions WHERE id=?', (version['revision'],)).fetchone()[0]
        for obj in (sha, html_sha):
            assert fetch(client(), review, '/obj/'+obj, headers=headers)[0] == 404
        for path in ('/mobile/generate', '/mobile/correct', '/mobile/review'):
            assert fetch(client(), review, path, fields={'id':mid,'revision':version['revision']}, headers=headers)[0] == 404
        raw, ctype = multipart({'id':mid, 'revision':version['revision']}, [('attachments','extra.txt',b'extra')])
        assert fetch(client(), review, '/mobile/add', data=raw, headers={**headers, 'Content-Type':ctype})[0] == 404
        # Both applications on one hostname must retain independent cookies.
        browser = client(); login(browser, base)
        assert fetch(browser, review, '/login', fields={'password':'synthetic-review-only'})[0] == 200
        assert fetch(browser, base)[0] == fetch(browser, review)[0] == 200
        assert fetch(a, base, '/bundle?id='+ident)[0] == 200
        assert store.db.execute('SELECT count(*) FROM mobile_reviews').fetchone()[0] == 0
    finally:
        server.shutdown(); thread.join(); server.server_close()


def test_slow_converter_has_own_connection_and_does_not_block_staff_http(school_web, monkeypatch):
    from moa import mobile, school_cli
    _, store, base = school_web
    a = client(); csrf = login(a, base)
    ident = upload(a, base, csrf)
    svc.request_conversion(store, svc.Principal('A', 'a'), ident)
    parsing, release, stop = threading.Event(), threading.Event(), threading.Event()
    original = mobile.build_conversion
    def slow(worker_store, *args, **kwargs):
        assert worker_store is not store and worker_store.db is not store.db
        parsing.set()
        assert release.wait(10)
        return original(worker_store, *args, **kwargs)
    monkeypatch.setattr(mobile, 'build_conversion', slow)
    worker = threading.Thread(target=school_cli.conversion_worker, args=(store.root, stop))
    worker.start()
    try:
        assert parsing.wait(5)
        assert fetch(a, base, '/notice?id='+ident)[0] == 200
        # The HTTP response arrived before the slow parser was released.
        assert not release.is_set()
    finally:
        stop.set(); release.set(); worker.join(timeout=10)
    assert not worker.is_alive()
    assert svc.get_version(store, svc.Principal('A', 'a'), ident)['revision']


def test_shared_sha_preserves_existing_research_download(school_web):
    from moa import mobile
    from moa.web import Handler
    _, store, base = school_web
    raw = b'<html><p>Existing authorized research original</p></html>'
    mid = mobile.create_upload(store, [('research.html', raw)])
    svc.upload(store, svc.Principal('A', 'a'), svc.IncomingPost('shared', 'School copy', '', (svc.IncomingFile('school.html', raw, 'convert'),)))
    class ReviewHandler(Handler):
        token = 'synthetic-review-only'
    ReviewHandler.store = store
    server = ThreadingHTTPServer(('127.0.0.1', 0), ReviewHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    review = 'http://127.0.0.1:' + str(server.server_port)
    try:
        sha = json.loads(mobile.notice_row(store, mid)['files'])[0]['sha256']
        status, body, _ = fetch(client(), review, '/obj/'+sha, headers={'Authorization':'Bearer synthetic-review-only'})
        assert status == 200 and body == raw
        assert fetch(client(), base, '/obj/'+sha)[0] == 401
    finally:
        server.shutdown(); thread.join(); server.server_close()
