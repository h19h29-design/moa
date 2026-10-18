"""Synthetic source/DOM fixtures only. These are not live browser claims."""
import copy
import json

import pytest

from moa import mobile, pilot, worker_api as api
from moa.core import Store
from moa.worker_handoff import handoff, questions, rubric_sha256
from test_autoqa_contract import _result_for


def document(store):
    return mobile.create_upload(store, [('안내.html', '<html><p>신청 마감 2026년 10월 30일 오후 5시.</p><p>반드시 2개 선택. 미신청 시 임의 배정.</p></html>'.encode())])


def browser_fixture(store, mid, mutate=False):
    m = json.loads(store.db.execute('SELECT manifest FROM pilot_documents WHERE mobile_id=?', (mid,)).fetchone()[0])
    blocks = [{'source_block': s['origin_block'], 'visible_text': s['text'], 'visible': True,
               'clipped': False} for s in m['sources']]
    if mutate: blocks[0]['visible_text'] = blocks[0]['visible_text'].replace('30일', '31일')
    return {'mobile_id': mid, 'revision_id': m['revision_id'], 'console_errors': [],
            'html_download_verified': True, 'bundle_download_verified': True,
            'downloads': [{'file_id': f['id'], 'status': 200, 'sha256': f['sha256']} for f in m['files']],
            'viewports': [{'width': w, 'http_status': 200, 'html_sha256': m['html_sha256'],
                           'page_overflow': False, 'blocks': copy.deepcopy(blocks)} for w in (360, 393, 430)]}


def ready(store):
    ack = {**handoff(), 'worker_id': 'macbook-laya-01', 'questions': questions(),
           'worker_code_sha256': 'a' * 64, 'daemon_ready': True}
    assert api.record_readiness(store, ack)['ok']


def test_pilot_bounded_idempotent_no_approval_no_empty_db(tmp_path):
    with Store(tmp_path) as s:
        ids = [document(s) for _ in range(4)]
        with pytest.raises(ValueError): pilot.prepare(s, ids)
        assert pilot.prepare(s, ids[:3])['documents'] == 3
        pilot.prepare(s, ids[:3])
        assert s.db.execute('SELECT count(*) FROM mobile_revisions').fetchone()[0] == 3
        with pytest.raises(ValueError, match='full chain'): pilot.prepare(s, ids[3:])
        assert s.db.execute('SELECT count(*) FROM mobile_reviews').fetchone()[0] == 0
        assert s.db.execute('SELECT count(*) FROM mobile_notices WHERE approved_revision IS NOT NULL').fetchone()[0] == 0


def test_actual_html_revision_and_three_viewports_downloads_required(tmp_path):
    with Store(tmp_path) as s:
        mid = document(s); pilot.prepare(s, [mid]); obs = browser_fixture(s, mid)
        assert pilot.ingest_browser(s, obs)['rules'] == 'passed'
        bad = copy.deepcopy(obs); bad['viewports'].pop()
        with pytest.raises(ValueError): pilot.ingest_browser(s, bad)
        for key, value in [('html_sha256', 'a' * 64), ('page_overflow', True), ('unbound_content', True)]:
            bad = copy.deepcopy(obs); bad['viewports'][0][key] = value
            assert pilot.ingest_browser(s, bad)['hard_findings'] > 0
        bad = copy.deepcopy(obs); bad['downloads'] = []
        assert pilot.ingest_browser(s, bad)['rules'] == 'needs_review'
        mobile.generate(s, mid)
        with pytest.raises(ValueError, match='stale'): pilot.ingest_browser(s, obs)


def test_wrong_model_cannot_override_critical_rule_or_become_approval(tmp_path):
    with Store(tmp_path) as s:
        mid = document(s); pilot.prepare(s, [mid])
        assert pilot.ingest_browser(s, browser_fixture(s, mid, mutate=True))['rules'] == 'needs_review'
        assert pilot.publish(s)['published'] == 0
        ready(s); published = pilot.publish(s)
        assert published['published'] == 2
        assert pilot.publish(s)['job_ids'] == published['job_ids']
        assert api.claim(s, 'macbook-laya-01', ['verify']) is None  # legacy daemon cannot steal a new pilot lease
        job = api.claim(s, 'macbook-laya-01', ['verify'], worker_code_sha256='a' * 64)
        result = _result_for(job)
        result['payload']['rubric_sha256'] = rubric_sha256()
        assert api.complete(s, result, api.validator_hash(), worker_id='macbook-laya-01')['verdict'] == 'needs_review'
        evidence = api.evidence_state(s, mid)
        section = next(e for e in evidence if e['job_id'] == job['job_id'])
        assert section['rule_report']['status'] == 'needs_review'
        assert section['model_report']['answers']['conditions']['label'] == 'preserved'
        assert section['final_verdict'] == 'needs_review'
        assert mobile.notice_row(s, mid)['approved_revision'] is None
        assert not pilot.status(s)['end_to_end_documents']


def test_eval_and_approved_inputs_not_regenerated(tmp_path):
    with Store(tmp_path) as s:
        mid = mobile.create_upload(s, [('안내.html', b'<html><p>synthetic</p></html>')], evaluation=True)
        with pytest.raises(ValueError, match='evaluation'): pilot.prepare(s, [mid])


def test_daily_suggestions_max_two_exceptions_and_one_sample_no_refill(tmp_path):
    with Store(tmp_path) as s:
        ids = [document(s) for _ in range(3)]; pilot.prepare(s, ids)
        for mid in ids: pilot.ingest_browser(s, browser_fixture(s, mid))
        for mid in ids[:2]: pilot.ingest_browser(s, browser_fixture(s, mid, mutate=True))
        picked = pilot.review_suggestions(s, '2026-10-18')
        assert len(picked) == 3
        assert sum(p['reason'] == 'important_exception' for p in picked) == 2
        assert sum(p['reason'] == 'whole_document_sample' for p in picked) == 1
        assert picked == pilot.review_suggestions(s, '2026-10-18')
        assert s.db.execute('SELECT count(*) FROM mobile_reviews').fetchone()[0] == 0
