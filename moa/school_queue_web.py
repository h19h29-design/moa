"""Bounded per-login review lists; no persistent queue, review or DB write."""
import re
import secrets
from urllib.parse import parse_qs, urlencode

from . import school_overview, school_overview_web, school_service as svc
from .web import esc

MAX_LISTS = 3


def link(context, item=None):
    if context.get('saved'):
        return '/review-saved?'+urlencode({'id':context['key'],'item':item or context['item']})
    return '/review-queue?'+urlencode({'queue':context['key'], 'item':item or context['item']})


def list_link(snapshot):
    return '/?'+urlencode(snapshot['query'])+'#review-overview'


def start(handler, raw):
    principal = handler._principal()
    result = school_overview.overview(handler.store, principal, **school_overview_web.query(raw))
    items = [{'id':r['notice_version'], 'revision':r['revision'], 'title':r['title']}
             for r in result['rows'] if r['structure_available'] and r['state']=='candidate']
    snapshot = {'school':principal.school, 'subject':principal.subject,
                'query':{k:result[k] for k in ('q','status','page')}, 'items':items}
    if not items or result['migration_required']:
        return handler._send(handler._shell('<section id="review-queue" class="card"><h1>연속 검수</h1>'
            '<p>현재 페이지에 검수 가능한 문서가 없습니다. 변환 상태와 검색 조건을 확인하세요.</p>'
            '<a data-queue="list" href="'+esc(list_link(snapshot))+'">검색 목록으로 돌아가기</a></section>'))
    # Session expiry/logout/restart also discard these lists. Bound tab storage
    # before insertion; no authority or document bytes are carried in the URL.
    queues = handler._session().setdefault('school_review_queues', {})
    while len(queues) >= MAX_LISTS: queues.pop(next(iter(queues)))
    key = secrets.token_urlsafe(18)
    queues[key] = snapshot
    return handler._redirect(link({'key':key, 'item':1}))


def _load(handler, key, item):
    if not re.fullmatch(r'[A-Za-z0-9_-]{24}', key) or not re.fullmatch(r'[1-9][0-9]?', item):
        raise ValueError('Invalid review list')
    principal = handler._principal()
    snapshot = handler._session().get('school_review_queues',{}).get(key)
    position = int(item)
    if (not snapshot or snapshot['school'] != principal.school or snapshot['subject'] != principal.subject
            or not 1 <= position <= len(snapshot['items'])):
        raise LookupError('Review list not found')
    current = snapshot['items'][position-1]
    svc.get_version(handler.store, principal, current['id'])  # Recheck ownership on every navigation/save.
    return {'key':key, 'item':position, 'snapshot':snapshot, 'current':current}


def render(handler, raw):
    if len(raw) > 4096: raise ValueError('Review list query too large')
    query = parse_qs(raw, keep_blank_values=True, max_num_fields=4, errors='strict')
    if set(query)-{'queue','item','block_page','relation_page'} or any(len(v)!=1 for v in query.values()):
        raise ValueError('Invalid review list query')
    context = _load(handler, query.get('queue',[''])[0], query.get('item',[''])[0])
    return render_context(handler,context,query)


def render_context(handler, context, query):
    current = context['current']
    query.update(revision=[current['revision']])
    from . import school_review as review, school_review_web
    try:
        return school_review_web.render(handler, current['id'], query, queue=context)
    except (review.Conflict, LookupError, ValueError) as exc:
        # A broken pinned document must not trap the reviewer or silently load
        # another revision. The regular review path remains available from home.
        status = 409 if isinstance(exc,review.Conflict) else (404 if isinstance(exc,LookupError) else 400)
        return handler._send(handler._shell(banner(context,handler)+'<p>이 고정 문서의 구조를 열 수 없습니다. '
            '수신본과 최신 검수 상태를 목록에서 확인하세요.</p>'),status)


def submission(handler, form):
    if {'saved_id','saved_item'} & set(form):
        if {'queue_id','queue_item'} & set(form) or not {'saved_id','saved_item'} <= set(form):
            raise ValueError('Invalid saved list context')
        from . import school_saved_queue_web
        context = school_saved_queue_web.load(handler,form['saved_id'][0],form['saved_item'][0])
        if (form.get('id',[''])[0]!=context['current']['id'] or
                form.get('revision',[''])[0]!=context['current']['revision']):
            raise ValueError('Saved list document mismatch')
        return context
    present = {'queue_id','queue_item'} & set(form)
    if not present: return None
    if len(present) != 2: raise ValueError('Incomplete review list context')
    context = _load(handler, form['queue_id'][0], form['queue_item'][0])
    if (form.get('id',[''])[0] != context['current']['id'] or
            form.get('revision',[''])[0] != context['current']['revision']):
        raise ValueError('Review list document mismatch')
    return context


def hidden(context):
    if not context: return ''
    prefix = 'saved' if context.get('saved') else 'queue'
    return ('<input type="hidden" name="'+prefix+'_id" value="'+esc(context['key'])+'">'
            '<input type="hidden" name="'+prefix+'_item" value="'+str(context['item'])+'">')


def banner(context, handler):
    from . import school_saved_queue_web
    if context.get('saved'): return school_saved_queue_web.banner(handler,context)
    snapshot, item = context['snapshot'], context['item']
    total = len(snapshot['items'])
    query = snapshot['query']
    content = ('<section id="review-queue" class="card" aria-labelledby="queue-heading">'
        '<style>#review-queue{overflow-wrap:anywhere}#review-queue nav{display:flex;gap:16px;flex-wrap:wrap;line-height:2}</style>'
        '<h2 id="queue-heading">연속 검수 · '+str(item)+' / '+str(total)+'</h2><p>'+esc(context['current']['title'])+'</p>'
        '<p>현재 페이지에서 시작한 '+str(total)+'건 · 제목: '+esc(query['q'] or '전체')+' · 상태: '+
        esc(school_overview.STATUS.get(query['status'],'전체'))+'</p>'
        '<p>문서와 순서는 시작 시 고정됩니다. 각 수정은 저장 버튼으로 저장한 뒤 다음 문서로 이동하세요.</p>'
        '<nav aria-label="연속 검수 이동"><a data-queue="list" href="'+esc(list_link(snapshot))+'">검색 목록으로 돌아가기</a>'
        '<a data-queue="current" href="'+esc(link(context))+'">현재 문서 다시 열기</a>')
    if item > 1:
        content += '<a data-queue="previous" href="'+esc(link(context,item-1))+'">이전 문서</a>'
    if item < total:
        content += '<a data-queue="next" href="'+esc(link(context,item+1))+'">다음 문서</a>'
    else:
        content += '<span>이 페이지 검수 목록 끝 · 다음 페이지는 검색 목록에서 시작하세요.</span>'
    return (content+'</nav>'+school_saved_queue_web.save_form(handler,context)+
            '<p id="queue-unsaved" role="status" hidden>저장하지 않은 입력이 있습니다.</p>'
            '<noscript><p>입력 후 이동하기 전에 저장하세요. JavaScript가 꺼져 있으면 이동 경고가 표시되지 않습니다.</p></noscript>'
            '</section><script src="/static/school-queue.js" defer></script>')
