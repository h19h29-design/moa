"""Authenticated saved-list pages; persistent writes are CSRF-protected POSTs."""
import re
from urllib.parse import parse_qs, urlencode

from . import school_queue_web, school_saved_queue as saved, school_service as svc
from .web import esc


def query(raw, allowed):
    if len(raw)>4096: raise ValueError('Saved list query too large')
    values = parse_qs(raw,keep_blank_values=True,max_num_fields=len(allowed),errors='strict')
    if set(values)-allowed or any(len(v)!=1 for v in values.values()): raise ValueError('Invalid saved list query')
    return values


def number(value, maximum):
    if not re.fullmatch(r'[1-9][0-9]{0,18}',value) or int(value)>maximum:
        raise ValueError('Invalid saved list position/version')
    return int(value)


def load(handler, ident, item=''):
    position = number(item,20) if item else None
    principal = handler._principal()
    row = saved.get(handler.store,principal,ident)
    position = position or row['cursor']
    if position>row['item_count']: raise LookupError('Saved list item not found')
    current = row['snapshot']['items'][position-1]
    svc.get_version(handler.store,principal,current['id'])
    return {'key':ident,'item':position,'snapshot':row['snapshot'],'current':current,'saved':row}


def render(handler, raw):
    values = query(raw,{'id','item','block_page','relation_page'})
    if 'id' not in values:
        if set(values): raise ValueError('Invalid saved list query')
        return directory(handler,'')
    context = load(handler,values['id'][0],values.get('item',[''])[0])
    return school_queue_web.render_context(handler,context,values)


def directory(handler, raw):
    values = query(raw,{'page'})
    result = saved.listing(handler.store,handler._principal(),page=number(values.get('page',['1'])[0],1_000_000))
    content = ('<section id="saved-review-lists"><style>#saved-review-lists{overflow-wrap:anywhere}</style>'
        '<h1>저장한 연속 검수 목록</h1><p>내 학교·내 담당자 계정에만 보이는 비공개 목록입니다. '
        '로그아웃·서버 재시작 후에도 이어서 열 수 있습니다.</p>'
        '<p>위치는 검수 완료 건수가 아닙니다. 수정 내용은 각 문서의 저장 버튼으로 저장해야 합니다. '
        '입력 중인 초안은 자동 복구되지 않습니다.</p>')
    if result['migration_required']:
        content += '<p>목록 저장 기능의 명시적 마이그레이션이 필요합니다. 이 화면은 DB를 변경하지 않습니다.</p>'
    elif not result['rows']:
        content += '<p>저장한 목록이 없습니다. 학교 현황에서 연속 검수를 시작한 뒤 목록을 저장하세요.</p>'
    for row in result['rows']:
        content += ('<article class="card"><h2>'+esc(row['name'])+'</h2><p>재개 위치 '+str(row['cursor'])+
            ' / '+str(row['item_count'])+' · 마지막 위치 저장 '+esc(row['updated_at'])+'</p>'
            '<a data-saved-resume href="/review-saved?'+esc(urlencode({'id':row['id']}))+'">이어서 검수</a></article>')
    content += '<nav aria-label="저장 목록 페이지"><span>'+str(result['page'])+' / '+str(result['pages'])+'페이지 · 총 '+str(result['total'])+'개</span>'
    if result['page']>1: content += ' <a rel="prev" href="/review-saved/lists?page='+str(result['page']-1)+'">이전 목록</a>'
    if result['page']<result['pages']: content += ' <a rel="next" href="/review-saved/lists?page='+str(result['page']+1)+'">다음 목록</a>'
    return handler._send(handler._shell(content+'</nav><p><a href="/#review-overview">학교 현황에서 새 목록 시작</a></p></section>'))


def save_form(handler, context):
    if svc.require_member(handler.store,handler._principal())['role'] not in svc.WRITE_ROLES: return ''
    if not saved.available(handler.store):
        return '<p>목록 저장에는 명시적 마이그레이션이 필요합니다. 현재 목록은 로그아웃 시 사라집니다.</p>'
    return ('<form action="/review-queue/save" method="post" data-queue-navigation>'+handler._csrf_field()+
        school_queue_web.hidden(context)+'<label>목록 이름 <input name="name" maxlength="120" '
        'value="연속 검수 목록" required></label><button>목록 저장·이어서 검수</button></form>'
        '<p>문서 순서·수신/변환 버전·현재 위치를 저장합니다. 입력 중인 수정 내용은 저장하지 않습니다.</p>')


def _move_form(handler, context, target, label):
    fields = {'id':context['key'],'expected_version':context['saved']['version'],'target':target}
    return ('<form action="/review-saved/move" method="post" data-queue-navigation>'+handler._csrf_field()+
        ''.join('<input type="hidden" name="'+k+'" value="'+esc(v)+'">' for k,v in fields.items())+
        '<button data-queue="'+('previous' if target<context['item'] else 'next' if target>context['item'] else 'checkpoint')+'">'+label+'</button></form>')


def banner(handler, context):
    row, item = context['saved'],context['item']
    writable = svc.require_member(handler.store,handler._principal())['role'] in svc.WRITE_ROLES
    content = ('<section id="review-queue" class="card"><style>#review-queue{overflow-wrap:anywhere}'
        '#review-queue nav{display:flex;gap:12px;align-items:center;flex-wrap:wrap}'
        '#review-queue nav form{margin:0}#review-queue input{max-width:100%;box-sizing:border-box}</style>'
        '<h2 id="queue-heading">저장한 목록 · '+str(item)+' / '+str(row['item_count'])+'</h2>')
    content += '<p>'+esc(row['name'])+' · '+esc(context['current']['title'])+'</p>'
    content += '<p>저장된 재개 위치 '+str(row['cursor'])+' / '+str(row['item_count'])+' · 위치는 검수 완료 건수가 아닙니다.</p>'
    content += '<p>수정은 개별 저장하세요. 이전/다음 버튼은 이동 위치만 저장합니다. 원본·검수 이력·고정 순서는 보존합니다.</p>'
    content += '<nav aria-label="연속 검수 이동"><a href="/review-saved">저장한 목록으로</a>'
    content += '<a data-queue="list" href="'+esc(school_queue_web.list_link(context['snapshot']))+'">검색 목록으로 돌아가기</a>'
    content += '<a data-queue="current" href="'+esc(school_queue_web.link(context))+'">현재 문서 다시 열기</a>'
    if writable:
        if item>1: content += _move_form(handler,context,item-1,'이전 문서·위치 저장')
        if item<row['item_count']: content += _move_form(handler,context,item+1,'다음 문서·위치 저장')
        if item!=row['cursor']: content += _move_form(handler,context,item,'여기부터 이어하기·위치 저장')
    else:
        for target,label in ((item-1,'이전 문서'),(item+1,'다음 문서')):
            if 1<=target<=row['item_count']:
                content += '<a href="'+esc(school_queue_web.link(context,target))+'">'+label+' (읽기 전용)</a>'
    if item==row['item_count']: content += '<span>저장한 검수 목록 끝</span>'
    return (content+'</nav><p id="queue-unsaved" role="status" hidden>저장하지 않은 입력이 있습니다.</p>'
        '<noscript><p>입력 후 이동하기 전에 저장하세요. JavaScript가 꺼져 있으면 이동 경고가 표시되지 않습니다.</p></noscript>'
        '</section><script src="/static/school-queue.js" defer></script>')


def submit(handler, path, form):
    allowed = {'csrf','queue_id','queue_item','name'} if path=='/review-queue/save' else {'csrf','id','target','expected_version'}
    if set(form)!=allowed or any(len(v)!=1 for v in form.values()): raise ValueError('Invalid saved list fields')
    if path=='/review-queue/save':
        context = school_queue_web._load(handler,form['queue_id'][0],form['queue_item'][0])
        ident = saved.create(handler.store,handler._principal(),context['key'],context['snapshot'],
            name=form['name'][0],cursor=context['item'])
    else:
        ident = form['id'][0]
        saved.move(handler.store,handler._principal(),ident,target=number(form['target'][0],20),
            expected_version=number(form['expected_version'][0],2**63-2))
    return handler._redirect('/review-saved?'+urlencode({'id':ident}))
