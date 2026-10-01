"""Quick human decisions on disposable synthetic documents, never production data."""
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar

from bs4 import BeautifulSoup
import pytest

from moa import corpus, mobile
from moa.learn import learn
from test_corpus import notice
from test_mobile import uploaded, web


def login(base):
    browser = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
    browser.open(base+'/login', urllib.parse.urlencode(
        {'password': 'synthetic-test-password'}).encode()).read()
    return browser


def post(browser, base, path, fields, headers=None):
    request = urllib.request.Request(base+path, urllib.parse.urlencode(fields).encode(), headers or {})
    try:
        return browser.open(request)
    except urllib.error.HTTPError as error:
        return error


def decision_form(browser, base, ident):
    page = BeautifulSoup(browser.open(base+'/notice?id='+ident).read(), 'html.parser')
    return page.find('form', action='/mobile/review')


def decision_fields(form, action):
    fields = {field['name']: field.get('value', '')
              for field in form.select('input[type=hidden][name]')}
    fields.update(action=action, review_mode='quick')
    return fields


def test_password_only_login_and_two_primary_decisions_need_no_manual_fields(web):
    store, base = web
    login_page = BeautifulSoup(urllib.request.urlopen(base+'/login').read(), 'html.parser')
    assert login_page.select('input[name=reviewer]') == []
    ident, _ = uploaded(store)
    browser = login(base)
    form = decision_form(browser, base, ident)
    assert form.select('input[name=reviewer], input[type=checkbox], [required]') == []
    primary = [button.get('value') for button in form.select('.quick-actions button')]
    assert primary == ['approved', 'held']
    advanced = form.find('details')
    assert advanced is not None and not advanced.has_attr('open')
    assert advanced.select('select[name=reason], textarea[name=note]')


def test_one_click_approval_uses_session_actor_not_client_name_and_exports_rules(web):
    store, base = web
    ident, rev = uploaded(store)
    browser = login(base)
    fields = decision_fields(decision_form(browser, base, ident), 'approved')
    fields['reviewer'] = 'forged client identity'
    response = post(browser, base, '/mobile/review', fields)
    assert response.status == 200
    saved_page = BeautifulSoup(response.read(), 'html.parser')
    assert '아직 미확인' not in saved_page.find(class_='hist').get_text()
    row = mobile.notice_row(store, ident)
    assert row['state'] == 'approved' and row['approved_revision'] == rev['id']
    history = store.db.execute('SELECT * FROM mobile_reviews WHERE notice_id=?', (ident,)).fetchone()
    assert re.fullmatch(r'웹 검수 [0-9a-f]{12}', history['reviewer'])
    assert all(word in history['note'] for word in ('원문', '권한', '개인정보'))
    assert history['revision_id'] == rev['id']
    assert mobile.approved_rules(store)
    assert (store.root/'learning/mobile-approved-rules.jsonl').read_text()


def test_one_click_hold_is_unconfirmed_not_negative_and_actor_is_stable_per_login(web):
    store, base = web
    ident, _ = uploaded(store)
    browser = login(base)
    for action in ('approved', 'held'):
        response = post(browser, base, '/mobile/review',
                        decision_fields(decision_form(browser, base, ident), action))
        assert response.status == 200
        response.read()
    history = store.db.execute('SELECT * FROM mobile_reviews ORDER BY id').fetchall()
    assert history[0]['reviewer'] == history[1]['reviewer']
    assert history[1]['reason'] == 'unconfirmed' and history[1]['note'] == ''
    assert mobile.notice_row(store, ident)['approved_revision'] is None
    assert mobile.approved_rules(store) == {}
    assert (store.root/'learning/mobile-approved-rules.jsonl').read_text() == ''
    second_browser = login(base)
    response = post(second_browser, base, '/mobile/review',
                    decision_fields(decision_form(second_browser, base, ident), 'held'))
    assert response.status == 200
    response.read()
    second_actor = store.db.execute('SELECT reviewer FROM mobile_reviews ORDER BY id DESC').fetchone()[0]
    assert second_actor != history[0]['reviewer']


def test_optional_hold_reason_and_revocation_preserve_history_without_name(web):
    store, base = web
    ident, rev = uploaded(store)
    browser = login(base)
    for action, extras in (('approved', {}), ('held', {'reason':'table', 'note':'합성 표 연결 확인 필요'}),
                           ('candidate', {}), ('rejected', {'reason':'missing'})):
        fields = decision_fields(decision_form(browser, base, ident), action)
        fields.update(extras)
        response = post(browser, base, '/mobile/review', fields)
        assert response.status == 200
        response.read()
    history = store.db.execute('SELECT action,reason,note,revision_id FROM mobile_reviews ORDER BY id').fetchall()
    assert [row['action'] for row in history] == ['approved', 'held', 'candidate', 'rejected']
    assert history[1]['reason'] == 'table' and history[1]['note'] == '합성 표 연결 확인 필요'
    assert all(row['revision_id'] == rev['id'] for row in history)
    assert mobile.approved_rules(store) == {}


def test_quick_approval_cannot_accept_an_old_or_processing_revision(web):
    store, base = web
    ident, rev = uploaded(store)
    browser = login(base)
    fields = decision_fields(decision_form(browser, base, ident), 'approved')
    newer = mobile.correct(store, ident, rev['id'], rev['data']['blocks'])
    assert post(browser, base, '/mobile/review', fields).status == 400
    fields['revision'] = newer['id']
    mobile.queue_conversion(store, ident, newer['id'], {'0': {'role':'convert'}}, '')
    assert post(browser, base, '/mobile/review', fields).status == 400
    assert store.db.execute('SELECT count(*) FROM mobile_reviews').fetchone()[0] == 0


@pytest.mark.parametrize('kind', ['image', 'mixed'])
def test_quick_approval_still_blocks_unreadable_or_unconfirmed_boundaries(web, kind):
    store, base = web
    if kind == 'image':
        ident = mobile.create_upload(store, [('partial.jpg', b'\xff\xd8\xffsynthetic partial jpeg')])
        mobile.generate(store, ident, {'0': {'role':'convert', 'incomplete':True}})
    else:
        ident, _ = uploaded(store, '<html><p>10월 30일까지 3층에 제출.</p><p>희망 신청서</p><p>신청자 ( ) 보호자 ( )</p></html>'.encode())
    browser = login(base)
    response = post(browser, base, '/mobile/review',
                    decision_fields(decision_form(browser, base, ident), 'approved'))
    assert response.status == 400
    assert mobile.notice_row(store, ident)['approved_revision'] is None
    assert store.db.execute('SELECT count(*) FROM mobile_reviews').fetchone()[0] == 0


def test_quick_actions_still_require_login_csrf_and_same_origin(web):
    store, base = web
    ident, _ = uploaded(store)
    browser = login(base)
    fields = decision_fields(decision_form(browser, base, ident), 'approved')
    anonymous = urllib.request.build_opener()
    assert post(anonymous, base, '/mobile/review', fields).status == 403
    assert post(browser, base, '/mobile/review', {k:v for k,v in fields.items() if k!='csrf'}).status == 403
    assert post(browser, base, '/mobile/review', fields, {'Origin':'https://evil.example'}).status == 403
    assert store.db.execute('SELECT count(*) FROM mobile_reviews').fetchone()[0] == 0


def test_quick_browser_consent_does_not_bypass_explicit_api_checks(web):
    store, base = web
    ident, rev = uploaded(store)
    fields = {'id':ident, 'revision':rev['id'], 'action':'approved', 'review_mode':'quick'}
    api = urllib.request.build_opener()
    headers = {'Authorization':'Bearer synthetic-test-password'}
    assert post(api, base, '/mobile/review', fields, headers).status == 400
    fields.update(compared='on', rights='on', privacy='on')
    response = post(api, base, '/mobile/review', fields, headers)
    assert response.status == 200
    response.read()
    history = store.db.execute('SELECT reviewer FROM mobile_reviews').fetchone()[0]
    assert history == '인증 API' and history != 'synthetic-test-password'


def test_corpus_classification_needs_no_name_but_does_not_approve_content(web):
    store, base = web
    ident, _ = notice(store)
    record = corpus.classify_notice(store, ident)
    browser = login(base)
    page = BeautifulSoup(browser.open(base+'/corpus/detail?id='+ident).read(), 'html.parser')
    assert page.select('input[name=reviewer]') == []
    form = page.find('form', action='/corpus/decision')
    csrf = form.find('input', attrs={'name':'csrf'})['value']
    fields = {'csrf':csrf, 'id':ident, 'file':'body', 'fingerprint':record['fingerprint'],
              'role':'convert', 'use':'held', 'note':'합성 확인 필요'}
    response = post(browser, base, '/corpus/decision', fields)
    assert response.status == 200
    response.read()
    reviewer = store.db.execute('SELECT reviewer FROM corpus_reviews').fetchone()[0]
    assert re.fullmatch(r'웹 검수 [0-9a-f]{12}', reviewer)
    assert mobile.notice_row(store, record['mobile_id'])['approved_revision'] is None


def test_legacy_table_review_also_has_two_primary_buttons_and_automatic_actor(web):
    store, base = web
    ident, _ = notice(store)
    learn(store, ident)
    row = store.db.execute('SELECT * FROM cases WHERE notice_id=?', (ident,)).fetchone()
    browser = login(base)
    page = BeautifulSoup(browser.open(base+'/case?id='+row['id']).read(), 'html.parser')
    form = page.find('form', action='/review')
    assert form.select('input[name=reviewer], input[type=checkbox]') == []
    assert [b['value'] for b in form.select('.quick-actions button')] == ['approved', 'held']
    fields = {field['name']:field.get('value','') for field in form.select('input[type=hidden][name]')}
    fields.update(status='approved', review_mode='quick', layout='key_value_cards')
    response = post(browser, base, '/review', fields)
    assert response.status == 200
    response.read()
    saved = store.db.execute('SELECT * FROM cases WHERE id=?', (row['id'],)).fetchone()
    assert saved['review_status'] == 'approved'
    assert re.fullmatch(r'웹 검수 [0-9a-f]{12}', saved['reviewer'])
    assert '개인정보' in json.loads(saved['review_history'])[-1]['note']
