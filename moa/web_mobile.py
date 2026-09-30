"""Upload-first UI, original comparison and notice-level human review."""
from __future__ import annotations

import json
import os
from email import policy
from email.parser import BytesParser
from pathlib import Path
from urllib.parse import quote, urlsplit

from . import mobile

STYLE = ('label{display:block;margin:12px 0}label input:not([type=checkbox]),label select{display:block;max-width:100%;width:100%;box-sizing:border-box;margin:5px 0}'
         '.hero{padding:24px}.hero h1{font-size:25px;margin:0 0 10px}.nav{display:flex;gap:15px;align-items:center;flex-wrap:wrap;margin:12px 0}'
         '.nav form{margin-left:auto}.files{padding:8px;border:1px solid #ddd;border-radius:8px;margin:12px 0}'
         '.files h3{font-size:16px;overflow-wrap:anywhere}.file-options{display:grid;grid-template-columns:1fr 1fr;gap:12px}'
         '.warning{background:#fff1df;padding:12px;border-radius:8px;color:#873a16}'
         '.preview{height:780px;max-width:440px;display:block;margin:auto}.original-img{width:100%;height:auto}'
         '.pill{background:#eef2f7;border-radius:15px;padding:3px 10px;font-size:13px}.actions{display:flex;gap:5px;flex-wrap:wrap}'
         '.editor-block{border:1px solid #ddd;padding:10px;margin:12px 0;border-radius:8px}.editor-block textarea{min-height:70px;font:inherit;box-sizing:border-box}'
         '.cell-editor{display:grid;grid-template-columns:1fr auto;gap:8px;margin:10px 0}.cell-editor .coords{max-width:160px;font-size:12px}'
         '.coords input[type=number]{width:45px}.cell-editor label{margin:4px 0}.login{max-width:400px;margin:12vh auto}'
         'h1,h2,h3,p,a{overflow-wrap:anywhere}input[type=file]{width:100%}.cols>*{min-width:0}'
         '@media(max-width:700px){.file-options{grid-template-columns:1fr}.preview{height:650px}.cell-editor{grid-template-columns:1fr}.cell-editor .coords{max-width:100%}}')


class MobileMixin:
    def _page(self, content):
        from .web import PAGE
        nav = ('<nav class="nav"><a href="/">MOA · 업로드</a><a href="/corpus">기존자료 분류</a><a href="/queue">표 사례 검수</a>'
               '<form action="/logout" method="post">'+self._csrf_field()+'<button>로그아웃</button></form></nav>')
        return PAGE.replace('</style>',STYLE+'</style>')+nav+content

    def _home(self):
        from .web import esc
        content = ('<main><section class="card hero"><h1>안내문을 모바일로</h1>'
            '<p>안내 내용은 읽기 편한 HTML로, 신청서·참고자료는 원본 첨부로 함께 보관합니다.</p>'
            '<form method="post" action="/upload" enctype="multipart/form-data">'+self._csrf_field()+
            '<label>안내문 <input name="guide" type="file" required multiple '
            'accept=".hwp,.hwpx,.docx,.pdf,.html,.htm,.jpg,.jpeg,.png"></label>'
            '<label>함께 첨부할 서식·참고자료 <input name="attachments" type="file" multiple></label>'
            '<label>제목 (선택) <input name="title" maxlength="300"></label>'
            '<label><input name="evaluation" type="checkbox"> 평가용 자료 (다른 안내문의 추천 사례에서 제외)</label>'
            '<button class="ok">업로드하고 파일 역할 확인</button>'
            '<p class="muted">파일당 25MB · 총 50MB · 최대 10개. HWP/HWPX/DOCX/텍스트 PDF/HTML 변환, 이미지·스캔 문서는 미지원 표시.</p>'
            '</form></section><h2>업로드·변환 기록</h2>')
        rows = self.store.db.execute('SELECT * FROM mobile_notices WHERE origin_notice_id IS NULL OR latest_revision IS NOT NULL'
                                    ' ORDER BY updated_at DESC LIMIT 60').fetchall()
        states = {'files_ready':'파일 역할 확인','processing':'변환 중','candidate':'미검수','approved':'승인','held':'보류','rejected':'반려'}
        for row in rows:
            content += '<section class="card"><a href="/notice?id=%s">%s</a> <span class="pill">%s</span> <span class="muted">%s</span></section>' % (
                esc(row['id']),esc(row['title']),states.get(row['state'],row['state']),
                '수집자료' if row['origin_notice_id'] else '업로드')
        self._send(self._page(content+'</main>'))

    def _upload(self, data):
        ctype = self.headers.get('Content-Type','')
        if not ctype.startswith('multipart/form-data') or len(ctype) > 500:
            raise ValueError('파일 업로드 형식이 올바르지 않습니다.')
        message = BytesParser(policy=policy.default).parsebytes(
            ('Content-Type: '+ctype+'\r\nMIME-Version: 1.0\r\n\r\n').encode()+data)
        if not message.is_multipart(): raise ValueError('업로드 파일을 확인하세요.')
        fields, uploads, attach = {}, [], []
        for part in message.iter_parts():
            name = part.get_param('name',header='content-disposition')
            filename = part.get_filename()
            raw = part.get_payload(decode=True) or b''
            if filename:
                if name not in ('guide','attachments'): raise ValueError('알 수 없는 파일 항목')
                if name == 'attachments': attach.append(len(uploads))
                uploads.append((filename,raw))
            elif name:
                if len(raw) > 2000: raise ValueError('입력값 길이 제한')
                fields[name] = [raw.decode('utf-8','strict')]
        if not self._csrf(fields): return self._send('<p>요청 인증을 확인하세요.</p>',403)
        if urlsplit(self.path).path == '/mobile/add':
            ident = fields.get('id',[''])[0]
            mobile.add_attachments(self.store,ident,fields.get('revision',[''])[0],uploads)
        else:
            ident = mobile.create_upload(self.store,uploads,fields.get('title',[''])[0],attach,'evaluation' in fields)
        return self._redirect('/notice?id='+ident)

    def _notice(self, ident, rid=''):
        from .web import esc
        row = mobile.notice_row(self.store,ident)
        rev = mobile.revision(self.store,ident,rid)
        data = rev['data'] if rev else None
        old = bool(rev and rev['id'] != row['latest_revision'])
        files = data['files'] if old else json.loads(row['files'])
        processing = row['state']=='processing'
        state = {'files_ready':'파일 역할 확인','processing':'변환 중','candidate':'미검수','approved':'승인','held':'보류','rejected':'반려'}.get(row['state'],row['state'])
        content = '<h1>%s</h1><p><span class="pill">%s</span> %s</p>' % (esc(row['title']),state,
            ('변환 버전 '+str(rev['sequence'])) if rev else '먼저 파일별 역할을 확인하세요.')
        if row['split']=='eval': content += '<p class="muted">평가용 자료 · 추천 사례에서 제외됩니다.</p>'
        if row['origin_notice_id']:
            from .corpus import get_record,USES
            curation=get_record(self.store,row['origin_notice_id'])
            content+='<p class="card">자료 활용 분류: %s · <a href="/corpus/detail?id=%s">파일별 분류 확인·수정</a></p>' % (esc(USES[curation['use']] if curation else '미분류'),esc(row['origin_notice_id']))
        if old: content += '<p class="warning">과거 버전입니다. <a href="/notice?id=%s">최신 버전 열기</a></p>' % esc(ident)
        if processing:
            content += '<p class="card" id="conversion-state" data-id="%s">모바일 HTML을 생성하고 있습니다. 완료되면 화면이 갱신됩니다.</p><script src="/static/mobile.js" defer></script>' % esc(ident)
        failed = self.store.db.execute("SELECT 1 FROM jobs WHERE kind='mobile' AND ref_id=? AND status='error'",(ident,)).fetchone()
        if failed: content += '<p class="warning">변환 작업을 완료하지 못했습니다. 파일 역할을 확인하고 다시 생성하세요.</p>'
        content += '<section class="card"><h2>1. 파일별 역할 확인</h2><form action="/mobile/generate" method="post">'+self._csrf_field()
        content += '<input name="id" type="hidden" value="%s"><input name="revision" type="hidden" value="%s">' % (esc(ident),esc(row['latest_revision'] or ''))
        content += '<label>안내 제목 <input name="title" value="%s" maxlength="300"></label>' % esc(row['title'])
        for file in files:
            fid = file['id']
            content += ('<div class="files"><h3>%s</h3><p><a href="/mobile/file?id=%s&file=%s" target="_blank" rel="noopener">원본 다운로드 ↗</a>'
                ' · <a href="/mobile/original?id=%s&file=%s" target="_blank" rel="noopener">원본 보기 ↗</a></p>'
                '<div class="file-options"><label>파일 역할 <select name="role_%s"><option value="convert"%s>모바일 변환</option>'
                '<option value="attachment"%s>원본 첨부 유지</option></select></label>'
                '<label>안내·서식 범위 <select name="scope_%s"><option value="auto"%s>자동 경계 제안</option>'
                '<option value="notice"%s>전체가 안내문</option><option value="mixed"%s>안내와 작성용 서식이 함께 있음</option></select></label></div>'
                '<label>작성용 서식 시작 구역 번호 (혼합 문서) <input type="number" min="0" max="2000" name="form_%s" value="%s"></label>'
                '<label><input type="checkbox" name="boundary_%s"%s> 원문에서 안내·서식 경계 확인</label>'
                '<label><input type="checkbox" name="incomplete_%s"%s> 원본이 일부 페이지뿐임</label></div>') % (
                    esc(file['filename']),esc(ident),esc(fid),esc(ident),esc(fid),esc(fid),
                    ' selected' if file['role']=='convert' else '', ' selected' if file['role']=='attachment' else '',esc(fid),
                    ' selected' if file.get('scope','auto')=='auto' else '', ' selected' if file.get('scope')=='notice' else '',
                    ' selected' if file.get('scope')=='mixed' else '',esc(fid),esc(file.get('form_start') if file.get('form_start') is not None else ''),
                    esc(fid),' checked' if file.get('boundary_confirmed') else '',esc(fid),' checked' if file.get('incomplete') else '')
        if not old and not processing: content += '<button class="ok">모바일 HTML 생성 / 역할 변경 적용</button>'
        content += '</form></section>'
        if not old and not processing:
            content += ('<section class="card"><h3>서식·참고자료 추가</h3><form action="/mobile/add" method="post" enctype="multipart/form-data">'+self._csrf_field()+
                '<input name="id" type="hidden" value="%s"><input name="revision" type="hidden" value="%s">'
                '<label>원본 첨부 <input name="attachments" type="file" required multiple></label><button>추가 후 재검토</button></form></section>') % (esc(ident),esc(row['latest_revision'] or ''))
        if not rev:
            return self._send(self._page(content))
        for warning in data.get('warnings',[]): content += '<p class="warning">'+esc(warning['message'])+'</p>'
        originals = ''
        for file in data['files']:
            if file.get('kind') == 'image':
                originals += '<details><summary>%s</summary><img class="original-img" src="/mobile/original?id=%s&file=%s" alt="원본 이미지"></details>' % (esc(file['filename']),esc(ident),esc(file['id']))
            elif file.get('kind') == 'pdf':
                originals += '<details><summary>%s · PDF 원본 보기</summary><iframe src="/mobile/original?id=%s&file=%s"></iframe></details>' % (esc(file['filename']),esc(ident),esc(file['id']))
        for source in data.get('sources',[]):
            originals += '<details open><summary>원문 추출 텍스트 · 파일 %s</summary><pre>%s</pre></details>' % (esc(source['file_id']),esc(source.get('text','')))
        content += ('<h2>2. 원문과 모바일 결과 비교</h2><div class="cols"><section class="card"><h3>원본</h3><p class="muted">HWP·DOCX 원본은 위 다운로드에서 열어 대조하세요.</p>%s</section>'
                    '<section class="card"><h3>모바일 결과</h3><iframe title="모바일 안내 미리보기" class="preview" src="/mobile/preview?id=%s&revision=%s"></iframe></section></div>') % (originals,esc(ident),esc(rev['id']))
        content += '<section class="card"><h2>변환에 사용한 규칙·승인 사례</h2>'
        for audit in data.get('recommendations',[]):
            content += '<p>%s · 구역 %s' % (esc(audit['rule_id']),esc(audit['block_id']))
            if audit.get('notice_id'):
                active = self.store.db.execute("SELECT 1 FROM mobile_notices WHERE id=? AND state='approved' AND approved_revision=?",
                                                (audit['notice_id'],audit['revision_id'])).fetchone()
                content += ' · <a href="/notice?id=%s&revision=%s">참고 승인 버전</a>%s' % (esc(audit['notice_id']),esc(audit['revision_id']),'' if active else ' (현재 취소/변경됨)')
            content += '</p>'
        if not data.get('recommendations'): content += '<p>지역 파서와 기본 표 표현을 사용했습니다. 참고한 승인 사례는 없습니다.</p>'
        content += '<p class="muted">승인된 구조·표현만 참고하며 이전 문서의 사실값은 복사하지 않습니다.</p></section>'
        if not old and not processing:
            content += ('<details class="card edit-section"><summary>내용·표 표현 수정</summary><h2>3. 내용·표 표현 수정</h2><form action="/mobile/correct" method="post" id="editor-form">'+self._csrf_field()+
                '<input name="id" type="hidden" value="%s"><input name="revision" type="hidden" value="%s">'
                '<label>제목 <input name="title" value="%s"></label><div id="block-editor"></div>'
                '<details><summary>구조 JSON 직접 수정 (고급)</summary><textarea id="blocks" name="blocks">%s</textarea></details>'
                '<button class="ok">수정 저장 후 재검토</button></form></details><script src="/static/mobile.js" defer></script>') % (
                    esc(ident),esc(rev['id']),esc(data['title']),esc(json.dumps(data['blocks'],ensure_ascii=False,indent=1)))
            session = self._session() or {}
            reasons = ''.join('<option value="%s">%s</option>' % (key,esc(label)) for key,label in mobile.REASONS.items())
            content += ('<section class="card"><h2>4. 이 버전 검수</h2><form action="/mobile/review" method="post">'+self._csrf_field()+
                '<input name="id" type="hidden" value="%s"><input name="revision" type="hidden" value="%s">'
                '<label>검수자 <input name="reviewer" required value="%s"></label><label>보류·반려 사유 <select name="reason">%s</select></label>'
                '<label>메모 <textarea name="note" maxlength="2000" style="min-height:70px"></textarea></label>'
                '<label><input type="checkbox" name="compared"> 원문과 안내 내용·표 관계·파일 역할을 대조함</label>'
                '<label><input type="checkbox" name="rights"> 이용 권한 확인</label><label><input type="checkbox" name="privacy"> 개인정보 확인</label>'
                '<div class="actions"><button name="action" value="approved" class="ok">승인</button>'
                '<button name="action" value="held" class="hold">보류</button><button name="action" value="rejected" class="bad">반려</button>'
                '<button name="action" value="candidate">승인취소 / 재검토</button></div></form>'
                '<p class="muted">보류는 오답 확정이 아닙니다. 파일·내용 변경 시 새 버전을 다시 검수합니다.</p></section>') % (
                    esc(ident),esc(rev['id']),esc(session.get('reviewer','')),reasons)
        content += '<section class="card"><h2>HTML·첨부 이용</h2><p><a href="/mobile/html?id=%s">HTML 다운로드</a> · <a href="/mobile/bundle?id=%s">HTML + 실제 원본 파일 ZIP 다운로드</a></p><p class="muted">미검수 결과에는 미검수 표시가 유지됩니다. ZIP을 풀고 index.html을 열면 첨부도 함께 열립니다.</p></section>' % (esc(ident),esc(ident))
        content += '<section class="card"><h2>변경 이력</h2>'
        for history in self.store.db.execute('SELECT sequence,id,created_at FROM mobile_revisions WHERE notice_id=? ORDER BY sequence DESC',(ident,)):
            content += '<p><a href="/notice?id=%s&revision=%s">버전 %s</a> · %s</p>' % (esc(ident),esc(history['id']),history['sequence'],esc(history['created_at'][:19]))
        for history in self.store.db.execute('SELECT * FROM mobile_reviews WHERE notice_id=? ORDER BY id DESC',(ident,)):
            content += '<p class="hist">%s · %s · %s · %s · %s</p>' % (esc(history['created_at'][:19]),esc(history['reviewer']),esc(history['action']),esc(mobile.REASONS[history['reason']]),esc(history['note']))
        self._send(self._page(content+'</section>'))

    def _mobile_file(self, ident, fid, original=False):
        row = mobile.notice_row(self.store,ident)
        file = next((f for f in json.loads(row['files']) if f['id']==fid),None)
        if not file: return self._send('<p>파일 없음</p>',404)
        path = self.store.object_path(file['sha256'])
        if original and file.get('kind') == 'image':
            header = path.read_bytes()[:16]
            ctype = 'image/png' if header.startswith(b'\x89PNG') else 'image/jpeg' if header.startswith(b'\xff\xd8') else \
                    'image/gif' if header.startswith(b'GIF') else 'image/webp' if header.startswith(b'RIFF') else 'application/octet-stream'
            return self._send_bytes(path.read_bytes(),file['filename'],ctype,inline=ctype!='application/octet-stream')
        if original and file.get('kind') == 'pdf': return self._send_bytes(path.read_bytes(),file['filename'],'application/pdf',inline=True)
        return self._send_file(path,file['filename'])

    def _mobile_preview(self, ident, rid=''):
        row = mobile.notice_row(self.store,ident)
        rev = mobile.revision(self.store,ident,rid)
        if not rev: return self._send('<p>변환 결과 없음</p>',404)
        self._send(mobile.render_notice(rev['data'],candidate=row['approved_revision']!=rev['id']))

    def _mobile_download(self, ident, zip_bundle=False):
        row = mobile.notice_row(self.store,ident)
        rev = mobile.revision(self.store,ident)
        if not rev: raise ValueError('먼저 모바일 HTML을 생성하세요.')
        if zip_bundle: return self._send_bytes(mobile.bundle(self.store,ident),row['title']+'.zip','application/zip')
        public = os.environ.get('MOA_PUBLIC_URL','').rstrip('/')
        if urlsplit(public).scheme not in ('http','https'): public = ''
        data = mobile.render_notice(rev['data'],
            file_url=lambda f:public+'/mobile/file?id='+ident+'&file='+quote(f['id']),
            candidate=row['state']!='approved').encode()
        self._send_bytes(data,row['title']+'.html','text/html; charset=utf-8')

    def _mobile_post(self, path, form):
        ident = form.get('id',[''])[0]
        row = mobile.notice_row(self.store,ident)
        rid = form.get('revision',[''])[0]
        if rid != (row['latest_revision'] or ''): raise ValueError('최신 버전을 열어 다시 작업하세요.')
        if path == '/mobile/generate':
            edits = {}
            for f in json.loads(row['files']):
                key = f['id']
                edits[key] = {'role':form.get('role_'+key,[f['role']])[0],
                    'scope':form.get('scope_'+key,['auto'])[0], 'form_start':form.get('form_'+key,[''])[0],
                    'boundary_confirmed':'boundary_'+key in form,'incomplete':'incomplete_'+key in form}
            mobile.queue_conversion(self.store,ident,rid,edits,form.get('title',[''])[0])
        elif path == '/mobile/correct':
            mobile.correct(self.store,ident,rid,json.loads(form.get('blocks',[''])[0]),form.get('title',[''])[0])
        elif path == '/mobile/review':
            mobile.decide(self.store,ident,rid,form.get('action',[''])[0],form.get('reviewer',[''])[0],
                form.get('reason',['unconfirmed'])[0],form.get('note',[''])[0],
                compared='compared' in form,rights='rights' in form,privacy='privacy' in form)
        else: return self._send('<p>404</p>',404)
        return self._redirect('/notice?id='+ident)
