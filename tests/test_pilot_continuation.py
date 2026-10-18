"""Synthetic regression cases for context-preserving pilot continuation."""
import copy
import json

from moa import mobile, pilot
from moa.core import Store
from moa.extract import html_document
from test_pilot import browser_fixture, ready, document


def test_editor_comment_is_not_notice_text_but_visible_literal_is_preserved():
    result = html_document('<html><div><!--[data-hwpjson]{"private_editor_state": "X"}-->'
                           '<p>신청 마감은 10월 30일입니다.</p>'
                           '<p>[data-hwpjson] 표기 안내입니다.</p></div></html>')
    assert result['blocks'] == [
        {'type': 'paragraph', 'text': '신청 마감은 10월 30일입니다.'},
        {'type': 'paragraph', 'text': '[data-hwpjson] 표기 안내입니다.'},
    ]


def test_explicit_blank_form_boundary_is_machine_evidence_not_human_approval(tmp_path):
    raw = ('<html><p>10월 30일까지 신청서를 행정실에 제출하세요.</p>'
           '<table><tr><th colspan="2">지원신청서</th></tr>'
           '<tr><td>성명</td><td></td></tr><tr><td>보호자</td><td></td></tr>'
           '<tr><td>연락처</td><td></td></tr><tr><td>서명</td><td></td></tr></table></html>').encode()
    with Store(tmp_path) as s:
        mid = mobile.create_upload(s, [('안내.html', raw)])
        pilot.prepare(s, [mid])
        m = json.loads(s.db.execute('SELECT manifest FROM pilot_documents').fetchone()[0])
        assert m['parser_coverage_complete'] is True
        assert m['human_original_audit'] is False
        assert m['boundary_evidence'][0]['method'] == 'explicit_blank_form_v1'
        assert m['sources'][-1]['role'] == 'form'
        assert mobile.notice_row(s, mid)['approved_revision'] is None
        assert not json.loads(mobile.notice_row(s, mid)['files'])[0]['boundary_confirmed']


def test_ambiguous_form_fields_do_not_become_confirmed_boundary(tmp_path):
    raw = ('<html><p>신청 안내</p><table><tr><td>지원자 성명 보호자 서명 신청자</td>'
           '<td>모두 학생 본인이 확인해야 합니다.</td></tr>'
           '<tr><td>대상</td><td>1학년</td></tr></table></html>').encode()
    with Store(tmp_path) as s:
        mid = mobile.create_upload(s, [('안내.html', raw)]); pilot.prepare(s, [mid])
        m = json.loads(s.db.execute('SELECT manifest FROM pilot_documents').fetchone()[0])
        assert m['parser_coverage_complete'] is False


def test_long_table_splits_with_headers_notes_and_parent_coverage():
    rows = [['학과', '교실']] + [[f'학과{i}', f'교실{i}'] for i in range(1, 18)]
    source = {'id': 's0001', 'text': '학과 안내\n' + '\n'.join(' / '.join(r) for r in rows),
              'table_rows': rows, 'caption': '학과 안내', 'note': '반드시 2개를 선택하세요.'}
    rendered = {'id': 'r1', 'source_refs': ['s0001'], 'table_rows': copy.deepcopy(rows),
                'caption': '학과 안내', 'note': source['note'], 'visible_text': source['text']}
    parts = pilot.section_parts(source, rendered)
    assert len(parts) > 1
    seen = []
    for p in parts:
        assert p['source_text'].startswith('학과 안내\n학과 / 교실\n')
        assert p['source_text'].endswith('반드시 2개를 선택하세요.')
        assert p['source_text'] == p['render_text']
        seen.extend(p['covered_rows'])
        assert p['parent_source_id'] == 's0001'
        assert p['source_sha256'] == pilot.sha(p['source_text'].encode())
    assert seen == list(range(1, 18))


def test_changed_row_cannot_disappear_during_split():
    rows = [['학과', '교실']] + [[f'학과{i}', f'교실{i}'] for i in range(1, 18)]
    source = {'id': 's1', 'text': '', 'table_rows': rows, 'caption': '', 'note': ''}
    changed = copy.deepcopy(rows); changed[8][1] = '다른 교실'
    parts = pilot.section_parts(source, {'table_rows': changed, 'caption': '', 'note': ''})
    assert any(p['source_text'] != p['render_text'] for p in parts)
    assert len([r for p in parts for r in p['covered_rows']]) == 17


def test_refresh_archives_manifest_and_does_not_mutate_originals(tmp_path):
    with Store(tmp_path) as s:
        mid = document(s); pilot.prepare(s, [mid])
        before = json.loads(s.db.execute('SELECT manifest FROM pilot_documents').fetchone()[0])
        pilot.prepare(s, [mid], refresh=True)
        after = json.loads(s.db.execute('SELECT manifest FROM pilot_documents').fetchone()[0])
        assert before['revision_id'] != after['revision_id']
        assert s.db.execute('SELECT manifest FROM pilot_history').fetchone()[0] == json.dumps(before, ensure_ascii=False)
        assert before['files'] == after['files']
        assert s.db.execute('SELECT count(*) FROM mobile_reviews').fetchone()[0] == 0


def test_missing_parent_coverage_cannot_count_completed_document(tmp_path):
    with Store(tmp_path) as s:
        mid = document(s); pilot.prepare(s, [mid]); pilot.ingest_browser(s, browser_fixture(s, mid)); ready(s)
        ids = pilot.publish(s)['job_ids']
        # Synthetic model reports exercise the parent aggregation, not accuracy.
        for jid in ids:
            s.db.execute('UPDATE verification_evidence SET model_report=? WHERE job_id=?', ('{"status":"completed"}',jid))
        assert pilot.status(s)['end_to_end_documents'] == 1
        m = json.loads(s.db.execute('SELECT manifest FROM pilot_documents').fetchone()[0])
        m['model_part_coverage'].pop()
        s.db.execute('UPDATE pilot_documents SET manifest=?', (json.dumps(m),))
        assert pilot.status(s)['end_to_end_documents'] == 0
