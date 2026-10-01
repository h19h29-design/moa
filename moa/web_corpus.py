"""Authenticated file-role and reuse curation; no automatic human approval."""
from __future__ import annotations

import json
from urllib.parse import urlencode, urlsplit

from . import corpus


class CorpusMixin:
    def _corpus(self, query):
        from .web import esc
        selected=query.get('use',[''])[0]
        if selected and selected not in corpus.USES and selected!='pending':
            raise ValueError('분류 필터를 확인하세요.')
        page=max(1,min(100000,int(query.get('page',['1'])[0])))
        stats=corpus.stats(self.store)
        content=('<main><h1>기존자료 분류</h1><p>자동 분류는 활용 가능성을 나누는 첫 단계입니다. '
                 '활용 후보도 미승인이며, 원문·변환 결과를 사람이 승인한 뒤에만 추천 사례로 사용합니다.</p>'
                 '<section class="card"><h2>진행 상태</h2><p>수집 통신문 %s건 · 분류 %s건 · 미분류 %s건 '
                 '· 승인된 안내 활용 사례 %s건</p>') % (stats['notices'],stats['classified'],stats['notices']-stats['classified'],stats['approved_eligible'])
        content+='<p>'+ ' · '.join(esc(label)+' '+str(stats['uses'].get(key,0))+'건' for key,label in corpus.USES.items())+'</p>'
        content+='<p class="muted">파일별: '+ ' · '.join(esc(label)+' '+str(stats['files'].get(key,0))+'개' for key,label in corpus.USES.items())+'</p>'
        content+='<p class="muted">분류 작업: '+esc(json.dumps(stats['jobs'],ensure_ascii=False))+' · 로컬 규칙·기존 추출 결과만 사용 · 유료 AI/훈련 없음</p>'
        content+='<form method="post" action="/corpus/queue">'+self._csrf_field()+'<button>미분류·규칙 변경 자료 이어서 분류</button></form></section>'
        options='<option value="">전체</option>'
        for key,label in {**corpus.USES,'pending':'미분류'}.items():
            options+='<option value="%s"%s>%s</option>' % (key,' selected' if selected==key else '',esc(label))
        content+='<form action="/corpus" method="get"><label>문서 활용 분류 <select name="use">'+options+'</select></label><button>필터 적용</button></form>'
        where=' WHERE c.notice_id IS NULL' if selected=='pending' else ' WHERE c.use=?' if selected else ''
        params=[selected] if selected and selected!='pending' else []
        join=' FROM notices n LEFT JOIN corpus_notices c ON c.notice_id=n.id'
        total=self.store.db.execute('SELECT count(*)'+join+where,params).fetchone()[0]
        rows=self.store.db.execute('SELECT n.id,n.office,n.school,c.use,c.mobile_id'+join+where+
                                   ' ORDER BY n.day DESC,n.id LIMIT 50 OFFSET ?',params+[(page-1)*50]).fetchall()
        for row in rows:
            title=self.store.notice(row['id']).get('title','안내')
            content+='<section class="card"><h3><a href="/corpus/detail?id=%s">%s</a></h3><p><span class="pill">%s</span> <span class="muted">%s · %s</span></p></section>' % (
                esc(row['id']),esc(title),esc(corpus.USES.get(row['use'],'미분류')),esc(row['office']),esc(row['school']))
        content+='<p>필터 결과 %s건 · %s페이지</p><div class="actions">' % (total,page)
        if page>1:content+='<a href="/corpus?%s">이전</a>' % esc(urlencode({'use':selected,'page':page-1}))
        if page*50<total:content+='<a href="/corpus?%s">다음</a>' % esc(urlencode({'use':selected,'page':page+1}))
        self._send(self._page(content+'</div></main>'))

    def _corpus_detail(self, ident):
        from .web import esc
        doc=self.store.notice(ident)
        record=corpus.get_record(self.store,ident)
        content='<main><p><a href="/corpus">← 기존자료 분류</a></p><h1>'+esc(doc.get('title','안내'))+'</h1>'
        url=doc.get('url','')
        if urlsplit(url).scheme in ('https','http') and urlsplit(url).hostname:
            content+='<p><a href="%s" target="_blank" rel="noopener">원문 페이지 ↗</a></p>' % esc(url)
        if record:
            content+='<p><span class="pill">%s</span> · <a href="/notice?id=%s">모바일 변환·원문 비교·콘텐츠 검수</a></p>' % (esc(corpus.USES[record['use']]),esc(record['mobile_id']))
        content+='<p class="warning">분류 확인은 콘텐츠 승인이 아닙니다. 보류는 오답 확정이 아니며 원본은 삭제하지 않습니다. 평가용은 추천·학습 입력에서 제외됩니다.</p>'
        content+='<form action="/corpus/refresh" method="post">'+self._csrf_field()+'<input name="id" type="hidden" value="%s"><input name="fingerprint" type="hidden" value="%s"><button>현재 원문·파서 상태로 분류 갱신</button></form>' % (esc(ident),esc(record['fingerprint'] if record else ''))
        for file in record['files'] if record else []:
            result=file['effective']
            role=result['role'] if result['role']!='uncertain' else 'convert'
            mid=record['mobile_id'];fid=file['file_id']
            content+='<section class="card"><h2>%s</h2><p><a href="/mobile/original?id=%s&file=%s" target="_blank" rel="noopener">원본 보기 ↗</a> · <a href="/mobile/file?id=%s&file=%s" target="_blank" rel="noopener">원본 다운로드 ↗</a></p>' % (esc(file['filename']),esc(mid),esc(fid),esc(mid),esc(fid))
            content+='<p><span class="pill">%s</span> · %s · 추출 %s · 파서 %s</p><p class="reasons">%s</p>' % (
                esc(corpus.USES[result['use']]),esc(file['kind']),esc(file['source_status']),esc(file['parser_version'] or '미분석'),
                ''.join('<span>'+esc(corpus.REASONS.get(r,r))+'</span>' for r in result['reasons']))
            if file.get('override'):
                content+='<p class="hist">사용자 분류: %s · %s · %s</p>' % (esc(file['override']['reviewer']),esc(file['override']['at'][:19]),esc(file['override']['note']))
            content+='<form method="post" action="/corpus/decision">'+self._csrf_field()
            content+='<input name="id" type="hidden" value="%s"><input name="file" type="hidden" value="%s"><input name="fingerprint" type="hidden" value="%s">' % (esc(ident),esc(fid),esc(record['fingerprint']))
            content+='<div class="file-options"><label>파일 역할 <select name="role"><option value="convert"%s>안내 모바일 변환</option><option value="attachment"%s>원본 첨부 유지</option></select></label>' % (' selected' if role=='convert' else '', ' selected' if role=='attachment' else '')
            content+='<label>활용 분류 <select name="use">'+''.join('<option value="%s"%s>%s</option>' % (k,' selected' if k==result['use'] else '',esc(v)) for k,v in corpus.USES.items())+'</select></label></div>'
            note_label = '분류 사유 (개인정보 후보 전환 시 필수)' if file['flags']['privacy_flag'] else '분류 사유 (선택)'
            content+='<label>'+esc(note_label)+' <textarea name="note" maxlength="2000" style="min-height:70px"></textarea></label>'
            content+='<p class="muted">확인자 이름 없이 접속별 검수 번호와 시각을 자동 기록합니다.</p>'
            if file['flags']['privacy_flag']:
                content+='<label><input type="checkbox" name="privacy_checked"> 원문에서 개인정보 여부를 대조했음 (후보로 변경할 때 확인 결과 메모 필수)</label>'
            content+='<button>분류 저장 (승인 아님)</button></form></section>'
        content+='<section class="card"><h2>분류 변경 이력</h2>'
        history=self.store.db.execute('SELECT * FROM corpus_reviews WHERE notice_id=? ORDER BY id DESC LIMIT 100',(ident,)).fetchall()
        for row in history:
            payload=json.loads(row['payload'])
            content+='<p class="hist">%s · 파일 %s · %s · %s · %s · %s</p>' % (
                esc(row['created_at'][:19]),esc(row['file_id']),esc(row['reviewer']),esc(row['action']),esc(corpus.USES.get(payload.get('use'),'')),esc(row['note']))
        if not history:content+='<p class="muted">사용자 분류 이력 없음 · 자동 분류는 사람 승인 기록을 만들지 않습니다.</p>'
        self._send(self._page(content+'</section></main>'))

    def _corpus_post(self, path, form):
        if path=='/corpus/queue':
            corpus.enqueue_missing(self.store)
            return self._redirect('/corpus')
        ident=form.get('id',[''])[0]
        current=corpus.get_record(self.store,ident)
        fingerprint=form.get('fingerprint',[''])[0]
        if current and current['fingerprint']!=fingerprint:
            raise ValueError('분류가 변경됐습니다. 최신 화면을 여세요.')
        if path=='/corpus/refresh':corpus.classify_notice(self.store,ident)
        elif path=='/corpus/decision':
            reviewer, note, _ = self._review_context(form)
            corpus.decide_file(self.store,ident,form.get('file',[''])[0],fingerprint,
                form.get('role',[''])[0],form.get('use',[''])[0],reviewer,
                note,privacy_checked='privacy_checked' in form)
        else:return self._send('<p>404</p>',404)
        return self._redirect('/corpus/detail?'+urlencode({'id':ident}))
