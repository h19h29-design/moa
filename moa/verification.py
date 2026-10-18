"""Compare independent source blocks with actual DOM observations, never model labels.

Conservative text comparison preserves conditions instead of guessing paraphrases.
An extracted source is not a proof that the original parser covered the whole file.
"""
from __future__ import annotations

import re

GATE_VERSION = 'moa-content-gate-v1'


def normalise(text: str, year: str = '') -> str:
    text = str(text).replace('\u00a0', ' ')
    # A missing year can inherit only the explicit source block's year.
    def date(m):
        return '%s-%02d-%02d' % (m.group(1) or year, int(m.group(2)), int(m.group(3)))
    text = re.sub(r'(?:(\d{4})\s*(?:년|[./-])\s*)?(\d{1,2})\s*월\s*(\d{1,2})\s*일', date, text)
    text = re.sub(r'(\d{4})\s*[./-]\s*(\d{1,2})\s*[./-]\s*(\d{1,2})\.?', date, text)
    def clock(m):
        hour = int(m.group(2))
        if m.group(1):
            hour = hour % 12 + (12 if m.group(1) == '오후' else 0)
        return '%02d:%02d' % (hour, int(m.group(3) or 0))
    text = re.sub(r'(오전|오후)?\s*(\d{1,2})\s*시\s*(?:(\d{1,2})\s*분)?', clock, text)
    text = re.sub(r'(?<=\d),(?=\d{3}(?:\D|$))', '', text)
    return re.sub(r'\s+', '', text)


def compare(sources: list, rendered: list, *, source_complete=True, attachments=()) -> dict:
    findings = []
    def fail(code, source_id='', detail=''):
        findings.append({'code': code, 'source_id': source_id, 'severity': 'critical', 'detail': detail})
    if not source_complete:
        fail('insufficient_evidence', detail='Original coverage or form boundary is unverified')
    known = {b['id'] for b in sources}
    if not sources or len(known) != len(sources):
        fail('source_inventory_invalid')
    refs = {}
    for block in rendered:
        if not block.get('source_refs'):
            fail('unbound_render_content')
        for ref in block.get('source_refs', []):
            refs.setdefault(ref, []).append(block)
            if ref not in known:
                fail('unknown_render_source', ref)
    originals = {a['id'] for a in attachments if a.get('available') and
                 re.fullmatch('[0-9a-f]{64}', a.get('sha256', ''))}
    for source in sources:
        sid = source['id']
        if source.get('role') == 'form':
            if refs.get(sid):
                fail('unexpected_form_conversion', sid)
            if source.get('attachment_id') not in originals:
                fail('original_form_unavailable', sid)
            continue
        matches = refs.get(sid, [])
        if len(matches) != 1 or len(matches[0].get('source_refs', [])) != 1:
            fail('missing_or_ambiguous_render', sid)
            continue
        block = matches[0]
        if block.get('visible') is False:
            fail('important_content_hidden', sid)
        if block.get('clipped'):
            fail('important_content_clipped', sid)
        years = re.findall(r'\b(20\d{2})\s*(?:년|[./-])', source.get('text', ''))
        year = years[0] if len(set(years)) == 1 else ''
        norm = lambda text: normalise(text, year)
        if 'table_rows' in source:
            expected = [[norm(c) for c in row] for row in source['table_rows']]
            actual = [[norm(c) for c in row] for row in block.get('table_rows', [])]
            if expected != actual:
                fail('table_relation_mismatch', sid)
            if source.get('table_geometry') != block.get('table_geometry') and source.get('table_geometry'):
                fail('table_merge_mismatch', sid)
            for field in ('caption', 'note'):
                if norm(source.get(field, '')) != norm(block.get(field, '')):
                    fail('table_context_mismatch', sid, field)
            continue
        left, right = norm(source.get('text', '')), norm(block.get('visible_text', ''))
        if left != right:
            code = 'content_or_condition_mismatch'
            for name, expression in (('date_mismatch', r'(?:20\d{2})?-\d{2}-\d{2}'),
                                     ('time_mismatch', r'\d{2}:\d{2}'),
                                     ('amount_mismatch', r'\d+(?:원|만원)'),
                                     ('selection_mismatch', r'\d+개')):
                if re.findall(expression, left) != re.findall(expression, right):
                    code = name
                    break
            fail(code, sid)
    return {'gate_version': GATE_VERSION, 'status': 'needs_review' if findings else 'passed',
            'findings': findings, 'expected_blocks': len(sources),
            'observed_blocks': len(rendered), 'source_complete': bool(source_complete)}
