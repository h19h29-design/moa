"""Separate opt-in staff server; never delegates to research/admin HTTP routes.

Only existing HTTP helpers, session/CSRF protection and multipart parsing are
reused. Passwords are per-identity PBKDF2 records, not the shared review token.
No public endpoint, live network intake, AI model, approval or deployment.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import secrets
import stat
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

from . import mobile, school_service as svc, school_review, school_review_web, school_export_web, school_compare_web, school_restore_web
from . import school_overview, school_overview_web, school_queue_web, school_saved_queue_web, school_draft_web
from .web import Handler, PAGE, esc
from .web_mobile import parse_upload

ITERATIONS = 600_000


def password_record(password):
    """Call only with a protected input/getpass, never a shell literal or log."""
    if not isinstance(password, str) or not 8 <= len(password) <= 512: raise ValueError('Password length')
    salt = secrets.token_bytes(16)
    return {'salt': salt.hex(), 'password_hash': hashlib.pbkdf2_hmac('sha256', password.encode(), salt, ITERATIONS).hex()}


def _validate_credentials(users):
    if not isinstance(users, dict) or len(users) > 500: raise ValueError('Invalid staff configuration')
    for username, item in users.items():
        svc._identifier(username, 'username')
        svc._identifier(item['school'], 'school'); svc._identifier(item['subject'], 'subject')
        if len(bytes.fromhex(item['salt'])) != 16 or len(bytes.fromhex(item['password_hash'])) != 32:
            raise ValueError('Invalid password record')
    return users


def load_credentials(path):
    path = Path(path)
    st = path.lstat()
    if not stat.S_ISREG(st.st_mode) or st.st_mode & 0o077 or st.st_uid != os.getuid():
        raise PermissionError('Staff credential file must be owner-only (0600), not a symlink')
    if st.st_size > 500_000: raise ValueError('Staff configuration too large')
    return _validate_credentials(json.loads(path.read_text())['users'])


class SchoolHandler(Handler):
    token = ''                 # A shared review bearer must never confer school authority.
    worker_token = ''
    credentials = {}
    session_cookie = 'moa_school_session'

    def _login_enabled(self): return bool(self.credentials)

    def _login_identity(self, form):
        user = self.credentials.get(form.get('username', [''])[0])
        password = form.get('password', [''])[0]
        salt = bytes.fromhex(user['salt']) if user else b'\0' * 16
        candidate = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, ITERATIONS).hex()
        expected = user['password_hash'] if user else '0' * 64
        if not secrets.compare_digest(candidate, expected) or not user: return None
        try: svc.require_member(self.store, svc.Principal(user['school'], user['subject']))
        except PermissionError: return None
        return {'school': user['school'], 'subject': user['subject']}

    def _session(self):
        session = super()._session()
        if not session or session.get('api') or not session.get('school') or not session.get('subject'): return None
        try: svc.require_member(self.store, svc.Principal(session['school'], session['subject']))
        except PermissionError: return None
        return session

    def _principal(self):
        session = self._session()
        if not session: raise PermissionError('Login required')
        return svc.Principal(session['school'], session['subject'])

    def _login_page(self, message='', status=200):
        return self._send(PAGE + '<main class="card"><h1>학교 통신문 수신 · 비공개</h1><p>'+esc(message)+'</p>'
            '<form method="post" action="/login"><label>담당자 ID <input name="username" autocomplete="username" required></label>'
            '<label>암호 <input type="password" name="password" autocomplete="current-password" required></label>'
            '<button>로그인</button></form><p>관리자가 지정한 학교의 자료만 열 수 있습니다.</p></main>', status)

    def _page(self, content):
        principal = self._principal()
        return PAGE + '<nav><a href="/">학교 통신문</a> · '+esc(principal.school)+' · 비공개 (private)'
        # Individual callers append the CSRF logout form to avoid mixing upload forms.

    def _shell(self, content):
        return self._page('') + ' · <a href="/review-saved">저장한 검수 목록</a> · <a href="/structure-drafts">내 임시 저장 입력</a><form action="/logout" method="post">'+self._csrf_field()+'<button>로그아웃</button></form></nav><main>'+content+'</main>'

    def _home(self, query):
        principal = self._principal()
        overview = school_overview.overview(self.store, principal, **query)
        content = '<h1>학교별 통신문 수신·관리</h1><p>자동 공개·실제 홈페이지 수집은 꺼져 있습니다.</p>'
        content += school_overview_web.render(overview)
        if svc.require_member(self.store, principal)['role'] in svc.WRITE_ROLES:
            content += ('<section class="card"><h2>담당자 직접 업로드</h2><form action="/upload" method="post" enctype="multipart/form-data">'+self._csrf_field()+
                '<p>같은 게시글 키로 내용이 달라지면 정정본으로 보존됩니다.</p>'
                '<label>게시글 키 <input name="post_key" maxlength="500" required></label>'
                '<label>제목 <input name="title" maxlength="300" required></label>'
                '<label>본문 HTML (선택)<textarea name="body" maxlength="100000"></textarea></label>'
                '<label>HTML 변환할 안내문 <input name="guide" type="file" multiple></label>'
                '<label>원본 첨부 <input name="attachments" type="file" multiple></label>'
                '<p>본문 포함 최대 10개, 파일당 25MiB · 총 50MiB.</p><button>비공개 저장·역할 확인</button></form></section>')
        content += '<h2>수신 출처·상태</h2>'
        for source in svc.source_status(self.store, principal):
            content += '<p>'+esc(source['id'])+' · '+esc(source['kind'])+' · '+esc(source['status'])+' · 마지막 성공 '+esc(source['last_success'] or '없음')+' · 마지막 시도 '+esc(source['last_attempt'] or '없음')+'</p>'
        jobs = self.store.db.execute('SELECT id,status,payload FROM jobs WHERE kind IN (?,?) ORDER BY id DESC LIMIT 200', svc.KINDS)
        for job in jobs:
            if json.loads(job['payload'])['school'] != principal.school: continue
            content += '<p>학교 작업 '+str(job['id'])+' · '+esc(job['status'])
            if job['status'] == 'failed':
                content += '<form method="post" action="/retry">'+self._csrf_field()+'<input type="hidden" name="job" value="'+str(job['id'])+'"><button>오류 재시도</button></form>'
            content += '</p>'
        return self._send(self._shell(content))

    def _notice(self, ident):
        row = svc.get_version(self.store, self._principal(), ident)
        content = '<section class="card" data-version="'+ident+'"><h1>'+esc(row['title'])+'</h1><p>private · 수신 버전 '+str(row['sequence'])+' · '+esc(row['state'])+'</p>'
        if row['previous_version']:
            content += '<a href="/notice?id='+quote(row['previous_version'])+'">이전 수신본</a>'
        content += '<form action="/convert" method="post">'+self._csrf_field()+'<input type="hidden" name="id" value="'+ident+'"><input type="hidden" name="revision" value="'+esc(row['revision'] or '')+'">'
        for f in row['files']:
            content += '<p><a href="/file?id='+ident+'&amp;file='+quote(f['id'])+'">'+esc(f['filename'])+'</a> <select name="role:'+esc(f['id'])+'">'
            for role, label in [('convert','HTML 변환'),('attachment','원본 첨부')]:
                content += '<option value="'+role+'"'+(' selected' if f['role'] == role else '')+'>'+label+'</option>'
            content += '</select></p>'
        content += '<button'+(' disabled' if row['state'] == 'processing' else '')+'>선택한 역할로 HTML 변환</button></form>'
        if row['revision']:
            content += '<p><a href="/html?id='+ident+'">미검수 HTML</a> · <a href="/bundle?id='+ident+'">HTML·모든 원본 ZIP</a></p><iframe title="미검수 HTML" sandbox src="/html?id='+ident+'"></iframe>'
            content += '<p><a href="/structure?id='+ident+'">문서 구조 분석 · 미검수</a></p>'
            content += '<p><a href="/structure-review?id='+ident+'">담당자 구조 검수·수정·이력</a></p>'
        return self._send(self._shell(content+'</section>'))

    def _structure(self, ident, query):
        analysis = svc.structure_for(self.store, self._principal(), ident, revision_id=query.get('revision',[''])[0])
        if query.get('format',[''])[0] == 'json':
            return self._send(json.dumps(analysis,ensure_ascii=False), ctype='application/json; charset=utf-8')
        labels = {'header':'헤더','heading':'제목·소제목','body':'본문','footer':'푸터·발신 정보','footnote':'각주','table':'표'}
        content = '<h1>문서 구조 분석 · 미검수</h1><p>로컬 규칙과 원본 구조의 관찰 결과입니다. 후보·미확정 관계는 사람 확인이 필요합니다. 원본·파일 역할·승인 상태는 변경하지 않습니다.</p>'
        content += '<p>분석 버전 '+esc(analysis['version'])+' · 변환 버전 '+esc(analysis['conversion_revision'])+'</p>'
        content += '<p><a href="/structure?id='+quote(ident)+'&amp;revision='+quote(analysis['conversion_revision'])+'&amp;format=json">근거 JSON</a> · <a href="/notice?id='+quote(ident)+'">수신본</a></p>'
        content += '<p><a href="/structure-review?id='+quote(ident)+'&amp;revision='+quote(analysis['conversion_revision'])+'">담당자 수정본·검수 이력 열기</a> (아래 자동 분석 원본은 보존됩니다.)</p>'
        names = {f['id']:f['filename'] for f in analysis['files']}
        content += '<h2>파일 묶음·처리 상태</h2><ul>'
        for f in analysis['files']:
            content += '<li>'+esc(f['filename'])+' · '+esc(f['role'])+' · '+esc(f['status'])+' · 구역 '+str(f['block_count'])+'</li>'
        content += '</ul><h2>미확정·누락 확인</h2>'
        for warning in analysis['warnings']:
            content += '<p>'+esc(warning['code'])+' · '+esc(names.get(warning.get('file_id'),'묶음'))+' · '+esc(warning.get('reference',''))+'</p>'
        content += '<h2>구역과 원본 위치</h2>'
        for block in analysis['blocks']:
            content += '<section class="card"><h3>'+esc(names[block['file_id']])+' · '+esc(block['id'])+' · '+esc(labels[block['kind']])+'</h3>'
            content += '<p>'+esc(block['basis'])+' · '+esc(block['certainty'])+'</p><pre style="white-space:pre-wrap">'+esc(block['text'])+'</pre><p>'+esc(json.dumps(block['locator'],ensure_ascii=False))+'</p></section>'
        content += '<h2>구역·첨부 관계</h2><table><tr><th>관계</th><th>출발 → 대상</th><th>근거·확실성</th></tr>'
        for relation in analysis['relations']:
            content += '<tr><td>'+esc(relation['kind'])+'</td><td>'+esc(relation['source'])+' → '+esc(relation['target'])+'</td><td>'+esc(relation['basis'])+' · '+esc(relation['certainty'])+'</td></tr>'
        return self._send(self._shell(content+'</table>'))

    def _get(self):
        path = urlsplit(self.path).path
        if path == '/login': return self._login_page()
        if not self._session(): return self._login_page('로그인이 필요합니다.', 401)
        try:
            if path == '/static/school-queue.js':
                return self._send(Path(__file__).with_name('school_queue_ui.js').read_text(),ctype='text/javascript; charset=utf-8')
            if path == '/static/school-draft.js':
                return self._send(Path(__file__).with_name('school_draft_ui.js').read_text(),ctype='text/javascript; charset=utf-8')
            if path == '/review-queue/start': return school_queue_web.start(self,urlsplit(self.path).query)
            if path == '/review-queue': return school_queue_web.render(self,urlsplit(self.path).query)
            if path == '/review-saved': return school_saved_queue_web.render(self,urlsplit(self.path).query)
            if path == '/review-saved/lists': return school_saved_queue_web.directory(self,urlsplit(self.path).query)
            if path == '/structure-drafts': return school_draft_web.directory(self,urlsplit(self.path).query)
            if path == '/structure-draft': return school_draft_web.render(self,urlsplit(self.path).query)
            if path == '/structure-draft/compare':
                from . import school_draft_compare_web
                return school_draft_compare_web.render(self,urlsplit(self.path).query)
            if path == '/structure-draft/restore':
                from . import school_draft_restore_web
                return school_draft_restore_web.render(self,urlsplit(self.path).query)
            if path == '/structure-draft/submit':
                from . import school_draft_handoff_web
                return school_draft_handoff_web.render(self,urlsplit(self.path).query)
            if path in ('/structure-draft/history', '/structure-draft/diff'):
                from . import school_draft_history_web
                render = school_draft_history_web.directory if path.endswith('/history') else school_draft_history_web.render_pair
                return render(self,urlsplit(self.path).query)
            if path == '/structure-restore':
                return school_restore_web.render(self,parse_qs(urlsplit(self.path).query,keep_blank_values=True,max_num_fields=3))
            if path == '/structure-confirm':
                from . import school_review_check_web
                return school_review_check_web.render(self,urlsplit(self.path).query)
            if path in ('/confirmed-export','/confirmed-export/preview','/confirmed-export/saved','/confirmed-export/saved/preview','/confirmed-export/saved/status','/confirmed-export/saved/manage','/confirmed-export/saved/receipts'):
                from . import school_confirmed_export_web
                return school_confirmed_export_web.dispatch(self,path,raw=urlsplit(self.path).query)
            if path == '/structure-compare':
                return school_compare_web.render(self,parse_qs(urlsplit(self.path).query,keep_blank_values=True,max_num_fields=10))
            if path == '/': return self._home(school_overview_web.query(urlsplit(self.path).query))
            q = parse_qs(urlsplit(self.path).query)
            ident = q.get('id', [''])[0]
            if path == '/notice': return self._notice(ident)
            if path == '/structure': return self._structure(ident,q)
            if path == '/structure-review': return school_review_web.render(self,ident,q)
            if path == '/structure-history': return school_review_web.render_history(self,ident,q)
            if path in ('/review-preview','/review-html','/review-bundle'):
                return school_export_web.serve(self,path,parse_qs(urlsplit(self.path).query,keep_blank_values=True,max_num_fields=10))
            if path not in ('/file','/html','/bundle'): return self._send('Not found', 404)
            row = svc.get_version(self.store, self._principal(), ident)
            if path == '/file':
                f = next((f for f in row['files'] if f['id'] == q.get('file', [''])[0]), None)
                if not f: raise LookupError('File not found')
                return self._send_bytes(self.store.object_path(f['sha256']).read_bytes(), f['filename'])
            revision = mobile.revision(self.store, row['mobile_id'], school=True)
            if not revision: raise LookupError('Conversion not ready')
            if path == '/bundle': return self._send_bytes(mobile.bundle(self.store, row['mobile_id'], school=True), 'notice.zip', 'application/zip')
            body = mobile.render_notice(revision['data'], file_url=lambda f: '/file?id='+ident+'&file='+quote(f['id']))
            return self._send(body, ctype='text/html; charset=utf-8')
        except LookupError: return self._send('Not found', 404)
        except PermissionError: return self._send('Forbidden', 403)
        except school_review.Conflict: return self._send('최신 분석·검수 버전을 다시 열어 확인하세요.', 409)
        except ValueError: return self._send('조회 버전·페이지를 확인하세요.', 400)

    def _post(self):
        path = urlsplit(self.path).path
        try:
            # No chunked/ambiguous request bodies; bound BEFORE reading/parsing.
            if self.headers.get('Transfer-Encoding') or len(self.headers.get_all('Content-Length', [])) != 1:
                return self._send('Invalid request framing', 400)
            length = int(self.headers.get('Content-Length', '0'))
            maximum = mobile.MAX_UPLOAD_BYTES + 1_000_000 if path == '/upload' else (300_000 if path in ('/structure-review','/structure-draft/save','/structure-draft/autosave') else 200_000)
            if path in ('/structure-draft/restore','/structure-draft/submit'): maximum = 4096
            if path == '/structure-confirm': maximum = 32768
            if path in ('/confirmed-export/prepare','/confirmed-export/download','/confirmed-export/save','/confirmed-export/saved/download','/confirmed-export/saved/prepare-current','/confirmed-export/saved/archive','/confirmed-export/saved/restore'): maximum = 16384
            if not 0 < length <= maximum: return self._send('Request too large', 413)
            if path != '/login' and not self._session(): return self._login_page('로그인이 필요합니다.', 401)
            data = self.rfile.read(length)
            if len(data) != length: return self._send('Incomplete request', 400)
            if path == '/upload':
                form, uploads, attachment_indices = parse_upload(data, self.headers.get('Content-Type', ''), max_field_bytes=200_000)
            else:
                form = parse_qs(data.decode('utf-8'), keep_blank_values=path in ('/structure-review','/review-queue/save','/review-saved/move','/structure-draft/save','/structure-draft/autosave','/structure-draft/restore','/structure-draft/submit','/confirmed-export/prepare','/confirmed-export/download','/confirmed-export/save','/confirmed-export/saved/download','/confirmed-export/saved/prepare-current','/confirmed-export/saved/archive','/confirmed-export/saved/restore'), max_num_fields=80, errors='strict')
            if path == '/login': return self._login(form)
            if not self._csrf(form): return self._send('요청 인증을 확인하세요.', 403)
            principal = self._principal()
            if path == '/logout': return self._logout()
            if path == '/structure-review': return school_review_web.submit(self,form)
            if path == '/structure-confirm':
                from . import school_review_check_web
                return school_review_check_web.submit(self,form)
            if path in ('/confirmed-export/prepare','/confirmed-export/download','/confirmed-export/save','/confirmed-export/saved/download','/confirmed-export/saved/prepare-current','/confirmed-export/saved/archive','/confirmed-export/saved/restore'):
                from . import school_confirmed_export_web
                return school_confirmed_export_web.dispatch(self,path,form=form)
            if path == '/structure-draft/save': return school_draft_web.submit(self,form)
            if path == '/structure-draft/autosave': return school_draft_web.submit(self,form,autosave=True)
            if path == '/structure-draft/restore':
                from . import school_draft_restore_web
                return school_draft_restore_web.submit(self,form)
            if path == '/structure-draft/submit':
                from . import school_draft_handoff_web
                return school_draft_handoff_web.submit(self,form)
            if path in ('/review-queue/save','/review-saved/move'): return school_saved_queue_web.submit(self,path,form)
            if path == '/upload':
                incoming = svc.IncomingPost(form.get('post_key', [''])[0], form.get('title', [''])[0], form.get('body', [''])[0], tuple(
                    svc.IncomingFile(name, raw, 'attachment' if i in attachment_indices else 'convert') for i, (name, raw) in enumerate(uploads)))
                result = svc.upload(self.store, principal, incoming)
                return self._redirect('/notice?id='+result.version_id)
            if path == '/convert':
                edits = {k[5:]: {'role': v[0]} for k, v in form.items() if k.startswith('role:')}
                ident = form.get('id', [''])[0]
                svc.request_conversion(self.store, principal, ident, edits or None, form.get('revision', [''])[0])
                return self._redirect('/notice?id='+ident)
            if path == '/retry':
                svc.retry_job(self.store, principal, int(form.get('job', [''])[0]))
                return self._redirect('/')
            return self._send('Not found', 404)
        except LookupError: return self._send('Not found', 404)
        except PermissionError: return self._send('Forbidden', 403)
        except school_review.Conflict: return self._send('다른 수정·재변환이 있습니다. 최신 검수 화면을 다시 열어 내용을 대조한 뒤 저장하세요.', 409)
        except (ValueError, UnicodeError): return self._send('입력값·파일·최신 버전을 확인하세요.', 400)

    def setup(self):
        super().setup()
        self.connection.settimeout(15)


def make_handler(store, credentials):
    # Independent session/credential maps; research cookies and bearer tokens do
    # not work here. No legacy serve()/research auto-queue side effects.
    users = copy.deepcopy(_validate_credentials(credentials))
    if not store.db.execute("SELECT 1 FROM sqlite_master WHERE name='school_service_migrations'").fetchone():
        raise RuntimeError('Apply the explicit school migration first')
    class ConfiguredHandler(SchoolHandler):
        sessions = {}
        login_attempts = {}
    ConfiguredHandler.store = store
    ConfiguredHandler.credentials = users
    return ConfiguredHandler
