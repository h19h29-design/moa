"""Server-rendered private overview; search/filter/page GETs never save data."""
import re
from urllib.parse import parse_qs, urlencode

from . import school_overview as overview
from .school_export_web import link as export_link
from .web import esc


def query(raw):
    if len(raw) > 4096: raise ValueError('Overview query too large')
    values = parse_qs(raw, keep_blank_values=True, max_num_fields=3, errors='strict')
    if set(values)-{'q','status','page'} or any(len(v)!=1 for v in values.values()):
        raise ValueError('Invalid overview fields')
    page = values.get('page',['1'])[0]
    if not re.fullmatch(r'[1-9][0-9]{0,6}', page): raise ValueError('Invalid overview page')
    return {'q':values.get('q',[''])[0], 'status':values.get('status',[''])[0], 'page':int(page)}


def _page_link(result, page):
    return '/?'+urlencode({'q':result['q'], 'status':result['status'], 'page':page})+'#review-overview'


def render(result):
    content = '''<style>
        #review-overview{overflow-wrap:anywhere}
        #review-overview *{box-sizing:border-box}
        #review-overview .summary{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}
        #review-overview .summary p{background:#eef2f7;border-radius:8px;padding:12px;margin:0}
        #review-overview .summary strong{display:block;font-size:24px;color:#172d48}
        #review-overview .filters{display:flex;gap:12px;align-items:end;flex-wrap:wrap;margin:18px 0}
        #review-overview label{display:grid;gap:4px;max-width:100%}
        #review-overview input{width:300px;max-width:100%}
        #review-overview select{max-width:100%}
        #review-overview article h3{font-size:18px;margin:0 0 8px}
        #review-overview article p{margin:7px 0}
        #review-overview .badge{display:inline-block;border-radius:5px;padding:3px 8px;background:#eef2f7}
        #review-overview [data-status=confirmed] .badge{background:#e0f3e9;color:#145b3e}
        #review-overview [data-status=conversion_failed] .badge{background:#fbe6e4;color:#922f29}
        #review-overview .actions{display:flex;gap:16px;flex-wrap:wrap;line-height:1.8}
        #review-overview .pagination{display:flex;gap:18px;align-items:center;flex-wrap:wrap;padding:12px 0}
        @media(max-width:600px){#review-overview .summary{grid-template-columns:repeat(2,1fr)}}
        </style><section id="review-overview" aria-labelledby="overview-heading">'''
    content += '<h2 id="overview-heading">학교별 검수 현황</h2><p>'+esc(result['school_name'])+' · 비공개 · 학교 전체 '+str(result['school_total'])+'건</p>'
    content += '<div class="summary" aria-label="학교 전체 검수 상태">'
    for status, label in overview.STATUS.items():
        content += '<p>'+label+'<strong data-count="'+status+'">'+str(result['counts'][status])+'</strong></p>'
    content += ('</div><p>최신 수신본·변환본에 저장된 검수 상태입니다. 위 집계는 검색 조건과 무관한 학교 전체 기준이며, '
                '확인 완료는 공개 승인이나 원본 무결성 재검사를 뜻하지 않습니다.</p>')
    if result['migration_required']:
        content += '<p>검수 이력 기능의 명시적 마이그레이션이 필요합니다. 이 화면에서는 DB를 변경하지 않습니다.</p>'
    content += '<form method="get" action="/" class="filters"><label>제목 검색<input type="search" name="q" maxlength="200" value="'+esc(result['q'])+'" placeholder="제목 일부 입력"></label>'
    content += '<label>검수 상태<select name="status" aria-label="검수 상태">'
    for value, label in {'':'전체 상태', **overview.STATUS}.items():
        content += '<option value="'+value+'"'+(' selected' if value==result['status'] else '')+'>'+label+'</option>'
    content += '</select></label><button>검색·필터 적용</button><a href="/#review-overview">조건 초기화</a></form>'
    content += '<p role="status">검색 결과 '+str(result['total'])+'건 · '+str(result['page'])+' / '+str(result['pages'])+'페이지 · 페이지당 최대 20건 · 최신 수신순</p>'
    if not result['migration_required'] and any(r['structure_available'] and r['state']=='candidate' for r in result['rows']):
        queue_url = '/review-queue/start?'+urlencode({k:result[k] for k in ('q','status','page')})
        content += '<p><a data-action="queue-start" href="'+esc(queue_url)+'">이 페이지 연속 검수 시작</a> · 현재 페이지에서 변환이 완료된 문서만 엽니다.</p>'
    if not result['rows']:
        content += '<p class="card">검색 조건에 맞는 통신문이 없습니다.</p>'
    for row in result['rows']:
        content += _notice(row)
    content += '<nav class="pagination" aria-label="통신문 목록 페이지">'
    if result['page'] > 1:
        content += '<a rel="prev" href="'+esc(_page_link(result,result['page']-1))+'">이전 페이지</a>'
    content += '<span aria-current="page">'+str(result['page'])+' / '+str(result['pages'])+'</span>'
    if result['page'] < result['pages']:
        content += '<a rel="next" href="'+esc(_page_link(result,result['page']+1))+'">다음 페이지</a>'
    return content+'</nav></section>'


def _notice(row):
    ident = row['notice_version']
    receipt_url = '/notice?'+urlencode({'id':ident})
    content = '<article class="card" data-notice-version="'+esc(ident)+'" data-status="'+esc(row['status'])+'"><h3>'
    content += '<a href="'+esc(receipt_url)+'">'+esc(row['title'])+'</a></h3>'
    state = {'files_ready':'변환 전', 'processing':'변환 중', 'conversion_failed':'변환 오류', 'candidate':'변환 완료'}.get(row['state'],'상태 확인 필요')
    content += '<p><span class="badge">'+overview.STATUS[row['status']]+'</span> · '+state+' · 수신 #'+esc(row['sequence'])
    if row['conversion_sequence']: content += ' · 변환 #'+str(row['conversion_sequence'])
    content += '</p><p>수신 시각 '+esc(row['updated_at'])+' · 출처 '+esc(row['source_id'])+'</p>'
    if row['review_id']:
        content += '<p>검수 #'+str(row['review_sequence'])+' · 담당자 '+esc(row['review_actor'])+' · '+esc(row['review_at'])+'</p>'
        if row['state'] in ('processing','conversion_failed'):
            content += '<p>기존 검수·HTML·이력 링크는 이전 변환본 기준입니다. 현재 확인 완료로 집계하지 않습니다.</p>'
    else:
        content += '<p>현재 변환본의 검수 기록이 없습니다.</p>'
    content += '<div class="actions"><a href="'+esc(receipt_url)+'">수신본·원본</a>'
    if row['structure_available']:
        base = urlencode({'id':ident, 'revision':row['revision']})
        content += '<a data-action="review" href="/structure-review?'+esc(base)+'">검수 열기</a>'
        content += '<a data-action="history" href="/structure-history?'+esc(base)+'">검수 이력</a>'
        if row['review_id']:
            content += '<a data-action="html" href="'+esc(export_link('/review-preview',ident,row['revision'],row['review_id']))+'">검수 HTML</a>'
    elif row['state'] != 'processing':
        content += '<span>수신본에서 변환·구조 분석 상태를 확인하세요.</span>'
    return content+'</div></article>'
