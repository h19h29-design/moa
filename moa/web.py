"""Local review web UI (stdlib only).

Read pages are LAN-local; every mutation requires MOA_REVIEW_TOKEN.
Original HTML is never executed: the notice body is shown as plain text and
attachments download with Content-Disposition: attachment + nosniff.
"""
from __future__ import annotations

import html
import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from .core import Store, encode, kst_now
from .extract import LAYOUTS
from .learn import build_review_queue, effective_table, review
from .render import render_mobile, render_page

DB_LOCK = threading.RLock()


def esc(v) -> str:
    return html.escape(str(v if v is not None else ''))


PAGE = ('<!doctype html><html lang="ko"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>MOA 검수</title><style>'
        'body{font-family:system-ui;max-width:1100px;margin:0 auto;padding:14px;background:#f6f7f9}'
        'a{color:#1463c8}.card{background:#fff;border-radius:10px;padding:12px;margin:10px 0;'
        'box-shadow:0 1px 3px rgba(0,0,0,.08)}'
        'table{border-collapse:collapse;width:100%;font-size:13px;background:#fff}'
        'th,td{border:1px solid #ccd;padding:5px 7px;text-align:left;vertical-align:top;'
        'white-space:pre-wrap}th{background:#eef2f7}'
        '.cols{display:grid;grid-template-columns:1fr 1fr;gap:10px}'
        '@media(max-width:800px){.cols{grid-template-columns:1fr}}'
        'textarea{width:100%;min-height:220px;font-family:monospace;font-size:12px}'
        'input,select{font-size:14px;padding:4px}button{font-size:14px;padding:6px 14px;margin:2px}'
        '.ok{background:#0a7;color:#fff;border:0;border-radius:6px}'
        '.bad{background:#c44;color:#fff;border:0;border-radius:6px}'
        '.hold{background:#888;color:#fff;border:0;border-radius:6px}'
        '.muted{color:#789;font-size:12px}.reasons span{background:#eef2f7;border-radius:8px;'
        'padding:2px 8px;margin:2px;display:inline-block;font-size:12px}'
        'iframe{width:100%;height:420px;border:1px solid #ccd;border-radius:10px;background:#fff}'
        'pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}'
        '.hist{font-size:12px;color:#567}</style></head><body>')


class Handler(BaseHTTPRequestHandler):
    store: Store = None
    token: str = ''

    def log_message(self, *a):
        pass

    def _send(self, body: str, status: int = 200, ctype: str = 'text/html; charset=utf-8'):
        data = body.encode()
        self.send_response(status)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'unsafe-inline' 'self'")
        self.end_headers()
        self.wfile.write(data)

    def _send_file(self, path, filename: str):
        data = path.read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', 'application/octet-stream')
        self.send_header('Content-Disposition',
                         "attachment; filename*=UTF-8''" + re.sub(r'[^\w.가-힣-]', '_', filename))
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # ---------------- GET ----------------
    def do_GET(self):
        with DB_LOCK:
            return self._get()

    def _get(self):
        q = parse_qs(urlsplit(self.path).query)
        path = urlsplit(self.path).path
        try:
            if path == '/':
                return self._queue()
            if path == '/case':
                return self._case(q.get('id', [''])[0], q.get('layout', [''])[0])
            if path == '/preview':
                return self._preview(q.get('id', [''])[0], q.get('layout', [''])[0])
            if path.startswith('/obj/'):
                return self._object(path.rsplit('/', 1)[-1])
            self._send('<p>404</p>', 404)
        except Exception as exc:
            self._send(PAGE + '<div class="card">오류: %s</div>' % esc(exc), 500)

    def _queue(self):
        day = kst_now().date().isoformat()
        rows = self.store.db.execute(
            'SELECT case_id,score,reasons FROM review_queue WHERE day=? ORDER BY score DESC',
            (day,)).fetchall()
        if not rows:
            build_review_queue(self.store, day, int(os.environ.get('REVIEW_QUEUE_SIZE', '20')))
            rows = self.store.db.execute(
                'SELECT case_id,score,reasons FROM review_queue WHERE day=? ORDER BY score DESC',
                (day,)).fetchall()
        items = []
        for r in rows:
            c = self.store.db.execute('SELECT payload,review_status FROM cases WHERE id=?',
                                      (r['case_id'],)).fetchone()
            if not c:
                continue
            p = json.loads(c['payload'])
            reasons = ''.join('<span>%s</span>' % esc(x) for x in json.loads(r['reasons']))
            items.append('<div class="card"><a href="/case?id=%s"><b>%s</b></a> '
                         '<span class="muted">%s · %s · %s</span><br>'
                         '<span class="reasons">%s</span> <span class="muted">%s</span></div>'
                         % (esc(r['case_id']), esc(p.get('school', {}).get('school_name', '?')),
                            esc(p.get('suggestion', {}).get('layout', '')),
                            esc(r['case_id'][:12]), esc(c['review_status']),
                            reasons, ''))
        head = ('<h2>MOA 우선검수 큐 — %s</h2><p class="muted">후보는 정답이 아닙니다. '
                '원문과 대조·수정 후 승인하세요. <a href="/?all=1">전체 미검수</a></p>' % day)
        if parse_qs(urlsplit(self.path).query).get('all'):
            extra = self.store.db.execute(
                "SELECT id,payload FROM cases WHERE review_status='candidate'"
                ' ORDER BY id LIMIT 200').fetchall()
            for c in extra:
                p = json.loads(c['payload'])
                items.append('<div class="card"><a href="/case?id=%s">%s</a> '
                             '<span class="muted">%s</span></div>'
                             % (esc(c['id']), esc(p.get('school', {}).get('school_name', '?')),
                                esc(c['id'][:12])))
        self._send(PAGE + head + ''.join(items))

    def _case(self, case_id: str, layout_ovr: str = ''):
        row = self.store.db.execute('SELECT * FROM cases WHERE id=?', (case_id,)).fetchone()
        if not row:
            return self._send(PAGE + '<div class="card">사례를 찾지 못했습니다.</div>', 404)
        case = json.loads(row['payload'])
        doc = self.store.notice(row['notice_id'])
        table = effective_table(row)
        corrected = bool(row['correction'])
        # Original: plain text only (never executed), plus attachment downloads.
        from bs4 import BeautifulSoup
        body_text = BeautifulSoup(doc.get('body_html', ''), 'html.parser').get_text('\n', strip=True)
        att = ''.join('<li><a href="/obj/%s">%s</a> <span class="muted">%s</span></li>'
                      % (esc(a['sha256']), esc(a.get('filename', a['sha256'][:12])),
                         esc(a.get('kind', '')))
                      for a in doc.get('assets', []))
        raw_rows = []
        grid_rows, grid_cols = table.get('rows', 0), table.get('cols', 0)
        grid = [[None] * grid_cols for _ in range(grid_rows)]
        for c in table.get('cells', []):
            if 0 <= c['row'] < grid_rows and 0 <= c['col'] < grid_cols:
                grid[c['row']][c['col']] = c
        for r in grid:
            raw_rows.append('<tr>' + ''.join(
                ('<th' if c.get('header') else '<td')
                + (' rowspan="%d"' % c['rowspan'] if c.get('rowspan', 1) > 1 else '')
                + (' colspan="%d"' % c['colspan'] if c.get('colspan', 1) > 1 else '')
                + '>%s</%s>' % (esc(c['text']), 'th' if c.get('header') else 'td')
                for c in r if c) + '</tr>')
        hist = ''.join('<div class="hist">%s %s: %s→%s %s</div>'
                       % (esc(h['at'][:19]), esc(h.get('reviewer', '')),
                          esc(h['from']), esc(h['to']), esc(h.get('note', '')))
                       for h in json.loads(row['review_history'] or '[]'))
        cur_layout = layout_ovr if layout_ovr in LAYOUTS else row['layout']
        layouts = ''.join('<option value="%s"%s>%s</option>'
                          % (l, ' selected' if l == cur_layout else '', l) for l in LAYOUTS)
        form = ('<form method="post" action="/review">'
                '<input type="hidden" name="case_id" value="%s">'
                '<div class="card"><b>검수</b><br>'
                '검수자 <input name="reviewer" required> '
                '레이아웃 <select name="layout">%s</select> '
                '토큰 <input name="token" type="password" required '
                'placeholder="MOA_REVIEW_TOKEN"><br>'
                '<label><input type="checkbox" name="rights"> 이용권한 확인</label> '
                '<label><input type="checkbox" name="privacy"> 개인정보 확인</label> '
                '메모 <input name="note" size="30"><br>'
                '수정된 표 JSON(비우면 원본 유지):<br>'
                '<textarea name="correction">%s</textarea><br>'
                '<button class="ok" name="status" value="approved">승인</button>'
                '<button class="hold" name="status" value="held">보류</button>'
                '<button class="bad" name="status" value="rejected">반려</button>'
                '<button name="status" value="candidate">승인취소/재검수</button>'
                '</div></form>')
        html_doc = (PAGE + '<p><a href="/">← 큐</a></p>'
            '<div class="card"><b>%s</b> <span class="muted">%s · %s · 게시일 %s · %s</span><br>'
            '<a href="%s">원문 페이지</a> · 상태 %s · family %s%s</div>'
            '<div class="cols"><div class="card"><b>원문(텍스트)</b><pre>%s</pre>'
            '<b>첨부</b><ul>%s</ul></div>'
            '<div class="card"><b>추출 표%s</b><table>%s</table></div></div>'
            '<div class="card"><b>모바일 미리보기</b> '
            '<form method="get" action="/case" style="display:inline" class="muted">'
            '<input type="hidden" name="id" value="%s">레이아웃 '
            '<select name="layout">%s</select><button>적용</button></form><br>'
            '<iframe src="/preview?id=%s&layout=%s"></iframe></div>'
            % (esc(doc.get('title', '')), esc(case.get('school', {}).get('school_name', '')),
               esc(row['id'][:12]), esc(doc.get('published_date') or '미상'),
               esc(row['review_status']), esc(doc.get('url', '#')),
               esc(row['review_status']), esc(row['family_id'] or '-'),
               ' · <b>사람이 수정한 표 적용 중</b>' if corrected else '',
               esc(body_text[:20000]), att,
               ' (수정본)' if corrected else '', ''.join(raw_rows),
               esc(case_id), layouts, esc(case_id), esc(cur_layout)))
        html_doc += form % (esc(case_id), layouts,
                            esc(json.dumps(table, ensure_ascii=False, indent=1)))
        html_doc += '<div class="card"><b>변경 이력</b>%s</div>' % (hist or '<div class="hist">없음</div>')
        self._send(html_doc)

    def _preview(self, case_id: str, layout: str):
        row = self.store.db.execute('SELECT * FROM cases WHERE id=?', (case_id,)).fetchone()
        if not row:
            return self._send('<p>없음</p>', 404)
        layout = layout if layout in LAYOUTS else row['layout']
        self._send(render_page('모바일 미리보기', render_mobile(effective_table(row), layout)))

    def _object(self, sha: str):
        if not re.fullmatch(r'[a-f0-9]{64}', sha):
            return self._send('<p>잘못된 객체</p>', 400)
        path = self.store.object_path(sha)
        if not path.exists():
            return self._send('<p>없음</p>', 404)
        self._send_file(path, sha[:16])

    # ---------------- POST ----------------
    def do_POST(self):
        with DB_LOCK:
            return self._post()

    def _post(self):
        length = min(int(self.headers.get('Content-Length', 0)), 2 * 1024 * 1024)
        form = parse_qs(self.rfile.read(length).decode('utf-8', 'replace'))
        if urlsplit(self.path).path != '/review':
            return self._send('<p>404</p>', 404)
        if not self.token:
            return self._send(PAGE + '<div class="card">MOA_REVIEW_TOKEN이 설정되지 않아 '
                              '검수 변경이 잠겨 있습니다. NAS .env에 토큰을 추가하세요.</div>', 403)
        if form.get('token', [''])[0] != self.token:
            return self._send(PAGE + '<div class="card">토큰이 일치하지 않습니다.</div>', 403)
        correction = None
        raw = form.get('correction', [''])[0].strip()
        if raw:
            try:
                correction = json.loads(raw)
            except json.JSONDecodeError as exc:
                return self._send(PAGE + '<div class="card">수정 JSON 오류: %s</div>' % esc(exc), 400)
        try:
            result = review(self.store, form['case_id'][0], form['status'][0],
                            layout=form.get('layout', [None])[0] or None,
                            reviewer=form.get('reviewer', [''])[0],
                            note=form.get('note', [''])[0],
                            correction=correction,
                            rights_reviewed='rights' in form,
                            privacy_reviewed='privacy' in form)
        except Exception as exc:
            return self._send(PAGE + '<div class="card">거부: %s</div>'
                              '<p><a href="/case?id=%s">돌아가기</a></p>'
                              % (esc(exc), esc(form.get('case_id', [''])[0])), 400)
        self._send(PAGE + '<div class="card">저장됨: %s → %s</div>'
                   '<p><a href="/case?id=%s">사례로</a> · <a href="/">큐로</a></p>'
                   % (esc(result['case_id'][:12]), esc(result['status']),
                      esc(result['case_id'])))


def serve(root, host='127.0.0.1', port=8321):
    store = Store(root, thread_safe=True)
    Handler.store = store
    Handler.token = os.environ.get('MOA_REVIEW_TOKEN', '')
    server = ThreadingHTTPServer((host, port), Handler)
    print(encode({'event': 'review_ui', 'listen': f'{host}:{port}',
                  'token_required': bool(Handler.token)}), flush=True)
    try:
        server.serve_forever()
    finally:
        store.db.close()
