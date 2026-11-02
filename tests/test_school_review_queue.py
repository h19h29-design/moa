"""Session-bound sequential review over real, isolated school HTTP routes."""
from urllib.parse import parse_qs, urlencode, urlsplit

from bs4 import BeautifulSoup
import pytest

from moa import school_service as svc, school_review as review
from moa.core import digest
from test_school_overview import upload
from test_school_structure import converted
from test_school_structure_review import save
from test_web_school import school_web, client, fetch, login


def page(opener, base, path):
    status, body, headers = fetch(opener, base, path)
    assert status == 200, body.decode()
    return BeautifulSoup(body, 'html.parser'), headers


def start(opener, base, **query):
    doc, headers = page(opener, base, '/review-queue/start?'+urlencode(query))
    assert doc.select_one('#review-queue'), 'Sequential review route is missing'
    return doc, headers


def fields(doc, action):
    form = next(f for f in doc.select('form[action="/structure-review"]')
                if f.select_one('input[name=action]')['value'] == action)
    values = {e['name']:e.get('value','') for e in form.select('input[name]')}
    values['comment'] = '합성 연속 검수 사유'
    if action == 'confirm': values['acknowledge_warnings'] = 'yes'
    return values


def pair(s):
    a = svc.Principal('A','a')
    first, _ = converted(svc, s, a, key='first')
    second, _ = converted(svc, s, a, key='second')
    # A deterministic tie exercises stable ordering without wall-clock reliance.
    s.db.execute("UPDATE school_service_posts SET updated_at='2026-10-24T01:00:00Z'"); s.db.commit()
    return a, {first.version_id, second.version_id}


def test_filtered_start_save_next_previous_and_return_preserve_the_page(school_web):
    _, s, base = school_web
    a = client(); login(a, base)
    principal, expected = pair(s)
    foreign, _ = converted(svc, s, svc.Principal('B','b'), key='foreign')
    before = list(s.db.iterdump())
    doc, headers = start(a, base, q='합성 행사', status='unreviewed', page=1)
    assert list(s.db.iterdump()) == before
    assert '1 / 2' in doc.select_one('#review-queue').get_text()
    assert foreign.version_id not in str(doc)
    first = fields(doc,'confirm'); assert first['id'] in expected
    next_url = doc.select_one('[data-queue=next]')['href']
    back = doc.select_one('[data-queue=list]')['href']
    assert parse_qs(urlsplit(back).query) == {'q':['합성 행사'], 'status':['unreviewed'], 'page':['1']}
    status, body, _ = fetch(a, base, '/structure-review', fields=first)
    assert status == 200
    saved = BeautifulSoup(body,'html.parser')
    assert '1 / 2' in saved.select_one('#review-queue').get_text()
    assert review.view(s, principal, first['id'])['status'] == 'confirmed'
    # The current row left the live filter, but the second saved ID is not skipped.
    second, _ = page(a, base, next_url)
    assert '2 / 2' in second.select_one('#review-queue').get_text()
    assert fields(second,'confirm')['id'] == (expected-{first['id']}).pop()
    assert second.select_one('[data-queue=next]') is None
    prior, _ = page(a, base, second.select_one('[data-queue=previous]')['href'])
    assert fields(prior,'reopen')['id'] == first['id']
    returned, _ = page(a, base, back)
    assert len(returned.select('article')) == 1
    assert returned.select_one('input[name=q]')['value'] == '합성 행사'
    assert 'no-store' in headers['Cache-Control']
    assert "script-src 'self'" in headers['Content-Security-Policy']


def test_start_is_bounded_to_current_page_and_skips_nonready_conversions(school_web):
    _, s, base = school_web
    a = client(); login(a, base)
    principal, _ = pair(s)
    # Insertion order can differ from sort order, so obtain the actual page contract.
    for i in range(21): upload(s, principal, str(i), '합성 행사 대기 '+str(i))
    from moa.school_overview import overview
    first = overview(s, principal, q='합성 행사')
    allowed = {r['notice_version'] for r in first['rows'] if r['structure_available']}
    doc, _ = start(a, base, q='합성 행사')
    if allowed:
        assert fields(doc,'confirm')['id'] in allowed
    else:
        assert '검수 가능한 문서가 없습니다' in doc.get_text()
    assert '현재 페이지' in doc.select_one('#review-queue').get_text()
    pending, _ = converted(svc,s,principal,key='processing')
    svc.request_conversion(s,principal,pending.version_id)
    broken, _ = converted(svc,s,principal,key='broken')
    s.db.execute("UPDATE mobile_notices SET state='conversion_failed' WHERE id=(SELECT mobile_id FROM school_service_versions WHERE id=?)",(broken.version_id,)); s.db.commit()
    doc, _ = start(a, base, status='conversion_failed')
    assert '검수 가능한 문서가 없습니다' in doc.get_text()
    assert not doc.select('form[action="/structure-review"]')


def test_queue_access_is_per_login_and_permissions_are_rechecked(school_web):
    _, s, base = school_web
    a, same, b, reader = client(),client(),client(),client()
    login(a,base); login(same,base); login(b,base,'b','synthetic-b-only')
    login(reader,base,'reader','synthetic-reader-only')
    pair(s)
    doc, _ = start(a,base)
    path = doc.select_one('[data-queue=current]')['href']
    assert fetch(client(),base,path)[0] == 401
    assert fetch(same,base,path)[0] == fetch(b,base,path)[0] == 404
    rdoc, _ = start(reader,base)
    assert not rdoc.select('form[action="/structure-review"]')
    svc.grant_member(s,'A','a','uploader',active=False)
    assert fetch(a,base,path)[0] == 401


def test_queue_never_retargets_after_new_receipt_or_conversion(school_web):
    _, s, base = school_web
    a = client(); login(a,base)
    principal = svc.Principal('A','a')
    receipt, version = converted(svc,s,principal)
    doc, _ = start(a,base)
    old_form = fields(doc,'confirm')
    path = doc.select_one('[data-queue=current]')['href']
    newer, _ = converted(svc,s,principal,price='15000')
    stale, _ = page(a,base,path)
    assert not stale.select('form[action="/structure-review"]')
    assert '읽기 전용' in stale.get_text()
    assert version['revision'] in stale.get_text()
    assert fetch(a,base,'/structure-review',fields=old_form)[0] == 409
    assert review.view(s,principal,newer.version_id)['status'] == 'unreviewed'
    assert not s.db.execute('SELECT 1 FROM school_structure_reviews').fetchone()


@pytest.mark.parametrize('query',['school=B','q=x&q=y','page=0','status=no','q=%FF','q=%00','q='+('x'*201)])
def test_bad_start_query_has_no_db_side_effect(school_web,query):
    _, s, base = school_web
    a = client(); login(a,base)
    before = list(s.db.iterdump())
    assert fetch(a,base,'/review-queue/start?'+query)[0] == 400
    assert list(s.db.iterdump()) == before


def test_forged_save_context_csrf_and_replay_cannot_append(school_web):
    _, s, base = school_web
    a = client(); login(a,base)
    pair(s); doc,_ = start(a,base)
    original = fields(doc,'confirm')
    before = list(s.db.iterdump())
    for edits, expected in [({'queue_item':'20'},404),({'queue_id':'x'*24},404),
                            ({'id':'0'*32},400),({'revision':'0'*32},400),
                            ({'csrf':'wrong'},403),({'queue_item':'0'},400)]:
        assert fetch(a,base,'/structure-review',fields={**original,**edits})[0] == expected
        assert list(s.db.iterdump()) == before
    assert fetch(a,base,'/structure-review',fields=original)[0] == 200
    assert fetch(a,base,'/structure-review',fields=original)[0] == 409
    assert s.db.execute('SELECT count(*) FROM school_structure_reviews').fetchone()[0] == 1


def test_navigation_and_edit_keep_originals_automatic_payloads_and_prior_audits(school_web):
    _, s, base = school_web
    a = client(); login(a,base)
    principal, _ = pair(s)
    doc,_ = start(a,base)
    f = fields(doc,'block')
    view = review.view(s,principal,f['id'])
    block = next(b for b in view['analysis']['blocks'] if b['kind']=='body')
    f.update(block_id=block['id'],block_kind='body',text='합성 수정 텍스트')
    files = {str(p):(digest(p.read_bytes()),p.stat().st_mtime_ns) for folder in ('objects','extracted')
             for p in (s.root/folder).rglob('*') if p.is_file()}
    conversions = [tuple(r) for r in s.db.execute('SELECT id,payload FROM mobile_revisions ORDER BY id')]
    before = list(s.db.iterdump())
    assert fetch(a,base,doc.select_one('[data-queue=next]')['href'])[0] == 200
    assert list(s.db.iterdump()) == before
    assert fetch(a,base,'/structure-review',fields=f)[0] == 200
    old = [tuple(r) for r in s.db.execute('SELECT * FROM school_structure_reviews ORDER BY id')]
    assert fetch(a,base,doc.select_one('[data-queue=next]')['href'])[0] == 200
    assert [tuple(r) for r in s.db.execute('SELECT * FROM school_structure_reviews ORDER BY id')] == old
    assert [tuple(r) for r in s.db.execute('SELECT id,payload FROM mobile_revisions ORDER BY id')] == conversions
    assert {str(p):(digest(p.read_bytes()),p.stat().st_mtime_ns) for folder in ('objects','extracted')
            for p in (s.root/folder).rglob('*') if p.is_file()} == files
    assert not s.db.execute('SELECT 1 FROM mobile_reviews').fetchone()


def test_session_queue_limit_logout_and_input_guard_script(school_web):
    _, s, base = school_web
    a = client(); csrf = login(a,base); pair(s)
    first,_ = start(a,base)
    first_path = first.select_one('[data-queue=current]')['href']
    for i in range(3): latest,_ = start(a,base)
    assert fetch(a,base,first_path)[0] == 404
    latest_path = latest.select_one('[data-queue=current]')['href']
    assert fetch(a,base,latest_path)[0] == 200
    script = latest.select_one('script[src="/static/school-queue.js"]')
    assert script is not None
    assert latest.select_one('form[data-queue-review]')
    status, body, headers = fetch(a,base,script['src'])
    assert status == 200 and headers['Content-Type'].startswith('text/javascript')
    assert fetch(client(),base,script['src'])[0] == 401
    assert fetch(a,base,'/logout',fields={'csrf':csrf})[0] == 200
    assert fetch(a,base,latest_path)[0] == 401


def test_twenty_item_snapshot_and_second_page_do_not_overlap(school_web):
    _, s, base = school_web
    a = client(); login(a,base)
    principal = svc.Principal('A','a')
    for i in range(21): converted(svc,s,principal,key=str(i))
    from moa.school_overview import overview
    rows = overview(s,principal,page=1)['rows']
    doc,_ = start(a,base,page=1)
    assert '1 / 20' in doc.select_one('#review-queue').get_text()
    current = doc.select_one('[data-queue=current]')['href']
    query = parse_qs(urlsplit(current).query)
    for position in (1,20):
        target,_ = page(a,base,'/review-queue?'+urlencode({'queue':query['queue'][0],'item':position}))
        assert fields(target,'confirm')['id'] == rows[position-1]['notice_version']
    assert fetch(a,base,'/review-queue?'+urlencode({'queue':query['queue'][0],'item':21}))[0] == 404
    second,_ = start(a,base,page=2)
    assert '1 / 1' in second.select_one('#review-queue').get_text()
    assert fields(second,'confirm')['id'] not in {r['notice_version'] for r in rows}
    assert parse_qs(urlsplit(second.select_one('[data-queue=list]')['href']).query)['page'] == ['2']


def test_pinned_conversion_becomes_readonly_and_old_form_cannot_save(school_web):
    _, s, base = school_web
    a = client(); login(a,base)
    principal = svc.Principal('A','a')
    receipt, version = converted(svc,s,principal)
    doc,_ = start(a,base); form = fields(doc,'confirm')
    path = doc.select_one('[data-queue=current]')['href']
    svc.request_conversion(s,principal,receipt.version_id)
    assert svc.process_conversions(s) == ['done']
    stale,_ = page(a,base,path)
    assert version['revision'] in stale.get_text() and '읽기 전용' in stale.get_text()
    assert not stale.select('form[action="/structure-review"]')
    assert fetch(a,base,'/structure-review',fields=form)[0] == 409
    assert not s.db.execute('SELECT 1 FROM school_structure_reviews').fetchone()


def test_invalid_queue_queries_cross_origin_and_duplicate_post_are_readonly(school_web):
    _, s, base = school_web
    a = client(); login(a,base); pair(s)
    doc,_ = start(a,base); form = fields(doc,'confirm')
    query = parse_qs(urlsplit(doc.select_one('[data-queue=current]')['href']).query)
    prefix = 'queue='+query['queue'][0]
    before = list(s.db.iterdump())
    for suffix in ('&item=0','&item=1&item=2','&item=1&school=B', '&item=%FF',
                   '&item=1&block_page=0','&item=1&block_page=1&relation_page=1&extra=1',
                   '&item='+('1'*4100)):
        assert fetch(a,base,'/review-queue?'+prefix+suffix)[0] == 400
    assert fetch(a,base,'/structure-review',fields=form,headers={'Origin':'https://foreign.example.test'})[0] == 403
    raw = (urlencode(form)+'&queue_item=2').encode()
    assert fetch(a,base,'/structure-review',data=raw)[0] == 400
    assert list(s.db.iterdump()) == before


def test_broken_document_retains_navigation_without_loading_another_revision(school_web):
    _, s, base = school_web
    a = client(); login(a,base); pair(s)
    doc,_ = start(a,base); form = fields(doc,'confirm')
    path = doc.select_one('[data-queue=current]')['href']
    s.db.execute('UPDATE mobile_revisions SET payload=? WHERE id=?',('invalid synthetic JSON',form['revision'])); s.db.commit()
    before = list(s.db.iterdump())
    status, body, _ = fetch(a,base,path)
    assert status == 400
    failure = BeautifulSoup(body,'html.parser')
    assert '고정 문서의 구조를 열 수 없습니다' in failure.get_text()
    assert fetch(a,base,failure.select_one('[data-queue=next]')['href'])[0] == 200
    assert list(s.db.iterdump()) == before


def test_context_text_is_escaped_and_block_pages_remain_in_the_queue(school_web):
    _, s, base = school_web
    a = client(); login(a,base)
    principal = svc.Principal('A','a')
    title = '<img src=x onerror=alert(1)>'
    r = svc.upload(s,principal,svc.IncomingPost('many-blocks',title,'<p>합성 구역</p>'*25))
    svc.request_conversion(s,principal,r.version_id); assert svc.process_conversions(s) == ['done']
    doc,_ = start(a,base,q=title)
    assert not doc.select('img,[onerror]')
    block_link = next(link['href'] for link in doc.select('a[href]') if 'block_page=2' in link['href'])
    second,_ = page(a,base,block_link)
    assert second.select_one('#review-queue')
    assert fields(second,'block')['queue_id'] == fields(doc,'block')['queue_id']
    assert fields(second,'block')['id'] == r.version_id


def test_failed_reconversion_after_start_never_allows_confirmation(school_web):
    _, s, base = school_web
    a = client(); login(a,base); pair(s)
    doc,_ = start(a,base); old_form = fields(doc,'confirm')
    current = doc.select_one('[data-queue=current]')['href']
    s.db.execute("UPDATE mobile_notices SET state='conversion_failed' WHERE id=(SELECT mobile_id FROM school_service_versions WHERE id=?)",
                 (old_form['id'],)); s.db.commit()
    failed,_ = page(a,base,current)
    assert not failed.select('form[action="/structure-review"]')
    assert '읽기 전용' in failed.get_text()
    before = list(s.db.iterdump())
    assert fetch(a,base,'/structure-review',fields=old_form)[0] == 409
    assert list(s.db.iterdump()) == before
