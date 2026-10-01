"""Session authentication for both reads and writes; credentials never enter URLs."""
from __future__ import annotations

import os
import secrets
import time
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, urlsplit


class AuthMixin:
    sessions = {}
    login_attempts = {}

    def _session(self):
        if getattr(self,'auth_session',None): return self.auth_session
        bearer = self.headers.get('Authorization','')
        if bearer.startswith('Bearer ') and self.token and secrets.compare_digest(bearer[7:],self.token):
            self.auth_session = {'csrf':'','api':True,'reviewer':'','expires':time.time()+1}
            return self.auth_session
        cookie = SimpleCookie()
        try: cookie.load(self.headers.get('Cookie',''))
        except Exception: return None
        key = cookie.get('moa_session')
        session = self.sessions.get(key.value) if key else None
        if session and session['expires'] > time.time():
            self.auth_session = session
            return session
        return None

    def _origin_valid(self):
        origin = self.headers.get('Origin','')
        return not origin or urlsplit(origin).netloc.lower() == self.headers.get('Host','').lower()

    def _csrf(self, form):
        session = self._session()
        return bool(session and (session.get('api') or
                    self._origin_valid() and secrets.compare_digest(
                    form.get('csrf',[''])[0],session['csrf'])))

    def _csrf_field(self):
        session = self._session()
        return '<input type="hidden" name="csrf" value="%s">' % (session['csrf'] if session else '')

    def _review_context(self, form, action=''):
        session = self._session()
        if not session:
            raise ValueError('로그인이 필요합니다.')
        if session.get('api'):
            reviewer = '인증 API'
        else:
            if not session.get('reviewer'):
                session['reviewer'] = '웹 검수 '+secrets.token_hex(6)
            reviewer = session['reviewer']
        quick_approval = (not session.get('api') and action == 'approved'
                          and form.get('review_mode', [''])[0] == 'quick')
        note = form.get('note', [''])[0]
        if quick_approval:
            note = '빠른 승인: 원문 비교·이용 권한·개인정보 확인 완료.' + (' '+note if note else '')
        return reviewer, note, quick_approval

    def _login_page(self, message='', status=200):
        from .web import PAGE,esc
        return self._send(PAGE+'<main class="card login"><h1>MOA</h1>'
            '<p>안내문 업로드·모바일 변환·검수</p><p>'+esc(message)+'</p>'
            '<form method="post" action="/login"><label>검수 암호'
            '<input type="password" name="password" autocomplete="current-password" required></label>'
            '<button class="ok">로그인</button></form>'
            '<p class="muted">기존 MOA 검수 암호로 로그인합니다.</p></main>',status)

    def _login(self, form):
        if not self.token: return self._login_page('관리자가 검수 암호를 설정해야 합니다.',503)
        key = self.client_address[0]
        now = time.time()
        attempts = [t for t in self.login_attempts.get(key,[]) if t > now-300]
        if len(attempts) >= 10: return self._login_page('잠시 후 다시 시도하세요.',429)
        attempts.append(now)
        self.login_attempts[key] = attempts
        if not self._origin_valid() or not secrets.compare_digest(form.get('password',[''])[0],self.token):
            return self._login_page('로그인 정보를 확인하세요.',403)
        self.login_attempts.pop(key,None)
        for sid,s in list(self.sessions.items()):
            if s['expires'] <= now: self.sessions.pop(sid,None)
        if len(self.sessions) >= 128: self.sessions.pop(next(iter(self.sessions)))
        sid = secrets.token_urlsafe(32)
        self.sessions[sid] = {'csrf':secrets.token_urlsafe(32),'expires':now+8*3600,
                              'reviewer':'웹 검수 '+secrets.token_hex(6), 'api':False}
        secure = '; Secure' if self.headers.get('X-Forwarded-Proto') == 'https' or \
            urlsplit(os.environ.get('MOA_PUBLIC_URL','')).scheme == 'https' and \
            self.headers.get('Host','').split(':')[0] == urlsplit(os.environ.get('MOA_PUBLIC_URL','')).hostname else ''
        return self._redirect('/',{'Set-Cookie':f'moa_session={sid}; HttpOnly; SameSite=Strict; Path=/; Max-Age=28800{secure}'})

    def _logout(self):
        cookie = SimpleCookie()
        try: cookie.load(self.headers.get('Cookie',''))
        except Exception: pass
        if cookie.get('moa_session'): self.sessions.pop(cookie['moa_session'].value,None)
        return self._redirect('/login',{'Set-Cookie':'moa_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0'})
