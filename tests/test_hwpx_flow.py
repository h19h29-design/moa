"""Native HWPX structure regressions; synthetic text only."""
import io
import zipfile
import pytest

from moa import mobile
from moa.core import Store
from moa.extract import hwpx_document


def hwpx(section):
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as archive:
        archive.writestr('Contents/section0.xml', '<sec xmlns:hp="urn:test">' + section + '</sec>')
    return out.getvalue()


def paragraph(text):
    return '<hp:p><hp:run><hp:t>' + text + '</hp:t></hp:run></hp:p>'


def cell(row, col, body):
    return ('<hp:tc><hp:subList>' + body + '</hp:subList>'
            f'<hp:cellAddr rowAddr="{row}" colAddr="{col}"/>'
            '<hp:cellSpan rowSpan="1" colSpan="1"/></hp:tc>')


def table(rows):
    return '<hp:tbl>' + ''.join('<hp:tr>' + ''.join(
        cell(r, c, paragraph(text)) for c, text in enumerate(row)) + '</hp:tr>'
        for r, row in enumerate(rows)) + '</hp:tbl>'


def test_hwpx_keeps_run_text_and_table_in_original_order_without_duplicate_cells():
    # Dropping paragraph boundaries or returning a text-only fallback breaks this
    # public conversion path: tables move to the end and every cell is duplicated.
    raw = hwpx(paragraph('신청 마감은 10월 30일입니다.') +
               '<hp:p><hp:run><hp:t>안내 </hp:t></hp:run><hp:run><hp:t>조건</hp:t>'
               '</hp:run></hp:p>' + table([['학과', '교실'], ['법학과', '2-1']]) +
               paragraph('선택 후 변경할 수 없습니다.'))
    blocks, _ = mobile.ordered_blocks(hwpx_document(raw))
    assert [b['type'] for b in blocks] == ['paragraph', 'paragraph', 'table', 'paragraph']
    assert blocks[1]['text'] == '안내 조건'
    assert blocks[-1]['text'] == '선택 후 변경할 수 없습니다.'
    assert [c['text'] for c in blocks[2]['table']['cells']] == ['학과', '교실', '법학과', '2-1']


def test_hwpx_nested_layout_preserves_instructions_and_form_boundary(tmp_path):
    inner = table([['학과', '교실'], ['법학과', '2-1']])
    form = table([['성명', ''], ['보호자', ''], ['연락처', ''], ['서명', '']])
    body = (paragraph('참가 안내') + inner + paragraph('10월 30일까지 행정실에 제출하세요.') +
            paragraph('참가 신청서') + form)
    raw = hwpx('<hp:tbl><hp:tr>' + cell(0, 0, body) + '</hp:tr></hp:tbl>')
    with Store(tmp_path) as store:
        ident = mobile.create_upload(store, [('안내.hwpx', raw)], evaluation=True)
        revision = mobile.generate(store, ident)
        blocks = revision['data']['blocks']
        assert [b['type'] for b in blocks] == ['paragraph', 'table', 'paragraph']
        assert blocks[-1]['text'] == '10월 30일까지 행정실에 제출하세요.'
        assert all('성명' not in b.get('text', '') for b in blocks)
        excluded = [b['block'].get('text', '') for b in revision['data']['excluded'] if b['reason'] == 'form']
        assert excluded == ['참가 신청서', '성명', '보호자', '연락처', '서명']
        assert any(w['code'] == 'boundary' for w in revision['data']['warnings'])
        assert mobile.notice_row(store, ident)['approved_revision'] is None


def test_form_submission_deadline_stays_in_notice_even_after_form_title(tmp_path):
    raw = hwpx(paragraph('체험 활동 안내') + paragraph('참가 신청서') +
               paragraph('(회신 기한: 2026. 10. 2.(금), 담임 선생님께 제출)') +
               table([['성명', ''], ['보호자', ''], ['연락처', ''], ['서명', '']]))
    with Store(tmp_path) as store:
        ident = mobile.create_upload(store, [('안내.hwpx', raw)], evaluation=True)
        rev = mobile.generate(store, ident)
        assert [b.get('text') for b in rev['data']['blocks']] == [
            '체험 활동 안내', '(회신 기한: 2026. 10. 2.(금), 담임 선생님께 제출)']
        assert any(w['code'] == 'boundary' for w in rev['data']['warnings'])
        assert mobile.notice_row(store, ident)['approved_revision'] is None


def test_hwpx_parent_cell_address_does_not_come_from_nested_table():
    nested = table([['학과', '교실'], ['법학과', '2-1']])
    raw = hwpx('<hp:tbl><hp:tr>' + cell(2, 3, paragraph('앞') + nested + paragraph('뒤')) +
               '</hp:tr></hp:tbl>')
    parsed = hwpx_document(raw)
    parent = parsed['tables'][0]['cells'][0]
    assert (parent['row'], parent['col']) == (2, 3)
    assert parent['text'] == '앞\n뒤'
    assert [b['type'] for b in parent['blocks']] == ['paragraph', 'table', 'paragraph']


def test_two_named_blank_fields_under_application_title_are_original_form(tmp_path):
    raw=hwpx(paragraph('10월 16일 참관 안내. 9월 30일까지 제출하세요.') +
        table([['학부모 공개수업 참관 신청서', '', '', ''],
               ['학생성명(학번)', '( )', '신청자명(학생과의 관계)', '( )'],
               ['참관신청 과목', '', '', '']]))
    with Store(tmp_path) as store:
        ident=mobile.create_upload(store,[('안내.hwpx',raw)],evaluation=True)
        rev=mobile.generate(store,ident)
        assert len(rev['data']['blocks'])==1
        assert rev['data']['boundary_evidence']
        assert rev['data']['excluded'][0]['reason']=='form'
        assert mobile.notice_row(store,ident)['approved_revision'] is None


def test_hwpx_rejects_excessive_overlapping_span_work_before_expanding_geometry():
    # Nine individually bounded cells would otherwise expand 360,000 slots,
    # beyond the largest allowed table rectangle (1,000 x 200).
    body=''.join(cell(0,0,paragraph('본문')).replace('rowSpan="1" colSpan="1"',
        'rowSpan="200" colSpan="200"') for _ in range(9))
    with pytest.raises(ValueError,match='표 병합'):
        hwpx_document(hwpx('<hp:tbl><hp:tr>'+body+'</hp:tr></hp:tbl>'))
