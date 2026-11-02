"""Exercise actual authenticated staff GETs, not a template-only mock."""
from urllib.parse import parse_qs, urlencode, urlsplit

from bs4 import BeautifulSoup
import pytest

from moa import school_service as svc, school_review as review
from test_school_overview import upload
from test_school_structure import converted
from test_school_structure_review import save
from test_web_school import school_web, client, fetch, login


def soup(opener, base, path='/'):
    status, body, headers = fetch(opener, base, path)
    assert status == 200
    return BeautifulSoup(body, 'html.parser'), headers


def test_home_shows_scoped_counts_search_filter_and_pinned_links(school_web):
    _, s, base = school_web
    a = client(); login(a, base)
    principal = svc.Principal('A','a')
    receipt, version = converted(svc, s, principal)
    event = save(review, s, principal, receipt.version_id, {'kind':'confirm', 'acknowledge_warnings':True})
    upload(s, principal, 'pending', '다른 제목')
    foreign = upload(s, svc.Principal('B','b'), 'foreign', '다른 학교 비공개 제목')
    page, headers = soup(a, base, '/?'+urlencode({'q':'합성 행사', 'status':'confirmed'}))
    assert page.select_one('#review-overview'), 'Review overview is missing'
    assert page.select_one('input[name=q]')['value'] == '합성 행사'
    assert page.select_one('select[name=status] option[selected]')['value'] == 'confirmed'
    assert '검색 결과 1건' in page.get_text()
    assert '학교 전체 2건' in page.get_text()
    assert foreign.version_id not in str(page) and '다른 학교 비공개 제목' not in page.get_text()
    rows = page.select('article[data-notice-version]')
    assert len(rows) == 1 and rows[0]['data-notice-version'] == receipt.version_id
    assert '확인 완료' in rows[0].get_text()
    html_link = rows[0].select_one('a[data-action=html]')['href']
    assert parse_qs(urlsplit(html_link).query) == {'id':[receipt.version_id], 'revision':[version['revision']], 'event':[event]}
    for action in ('review', 'html', 'history'):
        assert fetch(a, base, rows[0].select_one('a[data-action='+action+']')['href'])[0] == 200
    assert 'no-store' in headers['Cache-Control'] and headers['Content-Security-Policy']


def test_pagination_retains_filters_and_search_form_resets_page(school_web):
    _, s, base = school_web
    a = client(); login(a, base)
    for i in range(23): upload(s, svc.Principal('A','a'), str(i), '합성 일정 '+str(i))
    first, _ = soup(a, base, '/?'+urlencode({'q':'일정', 'status':'unreviewed'}))
    assert len(first.select('article[data-notice-version]')) == 20
    next_link = first.select_one('a[rel=next]')['href']
    assert parse_qs(urlsplit(next_link).query) == {'q':['일정'], 'status':['unreviewed'], 'page':['2']}
    second, _ = soup(a, base, next_link)
    assert len(second.select('article[data-notice-version]')) == 3
    assert not {x['data-notice-version'] for x in first.select('article[data-notice-version]')} & {x['data-notice-version'] for x in second.select('article[data-notice-version]')}
    assert second.select_one('a[rel=prev]') and not second.select_one('a[rel=next]')
    form = second.select_one('form[method=get]')
    assert form is not None and form.select_one('[name=page]') is None
    empty, _ = soup(a, base, '/?q=not-found&status=confirmed')
    assert '검색 조건에 맞는 통신문이 없습니다.' in empty.get_text()
    assert not empty.select('article[data-notice-version]')


@pytest.mark.parametrize('query', ['q=one&q=two', 'q=&q=x', 'status=unknown', 'page=0', 'page=-1',
    'page=1.5', 'page=9999999999999999999999999', 'page=&page=2', 'school=B', 'subject=b',
    'q='+('x'*201), 'q=%00', 'q=%0A', 'q=%FF', 'q='+('x'*4100)])
def test_bad_queries_fail_closed_without_db_change(school_web, query):
    _, s, base = school_web
    a = client(); login(a, base)
    before = list(s.db.iterdump())
    assert fetch(a, base, '/?'+query)[0] == 400
    assert list(s.db.iterdump()) == before


def test_anonymous_reader_revocation_and_cross_school_search(school_web):
    _, s, base = school_web
    a, b, reader = client(), client(), client()
    login(a, base); login(b, base, 'b', 'synthetic-b-only'); login(reader, base, 'reader', 'synthetic-reader-only')
    own = upload(s, svc.Principal('A','a'), 'one', '학교A 전용')
    upload(s, svc.Principal('B','b'), 'two', '학교B 전용')
    assert fetch(client(), base, '/?q=A')[0] == 401
    page, _ = soup(reader, base)
    assert page.select_one('article[data-notice-version]')['data-notice-version'] == own.version_id
    assert page.select_one('form[action="/upload"]') is None
    other, _ = soup(b, base, '/?'+urlencode({'q':'학교A'}))
    assert not other.select('article[data-notice-version]') and own.version_id not in str(other)
    svc.grant_member(s, 'A', 'reader', 'reader', active=False)
    assert fetch(reader, base)[0] == 401


def test_user_text_escaped_and_processing_not_claimed_confirmed(school_web):
    _, s, base = school_web
    a = client(); login(a, base)
    principal = svc.Principal('A','a')
    attack = '"><img src=x onerror=alert(1)>'
    uploaded = upload(s, principal, 'attack', attack)
    receipt, _ = converted(svc, s, principal)
    save(review, s, principal, receipt.version_id, {'kind':'confirm', 'acknowledge_warnings':True})
    svc.request_conversion(s, principal, receipt.version_id)
    page, _ = soup(a, base)
    row = page.select_one('article[data-notice-version="'+receipt.version_id+'"]')
    assert row is not None and row['data-status'] == 'unreviewed'
    assert '변환 중' in row.get_text() and '이전 변환본 기준' in row.get_text()
    found, _ = soup(a, base, '/?'+urlencode({'q':attack}))
    assert found.select_one('article')['data-notice-version'] == uploaded.version_id
    assert found.select_one('input[name=q]')['value'] == attack
    assert not found.select('img,script,[onerror]')
