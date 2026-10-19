"""Compare independent source blocks with actual DOM observations, never model labels.

Conservative text comparison preserves conditions instead of guessing paraphrases.
An extracted source is not a proof that the original parser covered the whole file.
"""
from __future__ import annotations

import re
import hashlib
import json
import unicodedata
from collections import Counter

GATE_VERSION = 'moa-content-gate-v3-evidence'

FORMATS = re.compile(
    r'(?P<date>(?:20\d{2}\s*년\s*)?\d{1,2}\s*월\s*\d{1,2}\s*일|'
    r'20\d{2}\s*[./-]\s*\d{1,2}\s*[./-]\s*\d{1,2}\.?)'
    r'|(?P<short_date>(?<![\d./-])\d{1,2}\s*[./]\s*\d{1,2}\.?(?!\d))'
    r'|(?P<time>(?:오전|오후)?\s*\d{1,2}\s*시(?!간)\s*(?:\d{1,2}\s*분)?|(?<!\d)\d{1,2}:\d{2}(?!\d))'
    r'|(?P<amount>(?<![\d.])[\d,]+(?:\.\d+)?\s*(?:만|천)?\s*원)'
    r'|(?P<selection>(?<!\d)\d+\s*(?:개|과목))')


def content_tokens(text, year=''):
    """Canonical numeric presentation, preserving spaces between prose words.

    Only explicitly recognized facts may change notation. Decimal values are
    not month/day dates unless the surrounding text supplies date context.
    """
    text = unicodedata.normalize('NFC', str(text)).replace('\u00a0', ' ')
    result, cursor = [], 0
    def literal(value):
        value = re.sub(r'\s+', ' ', value).strip()
        if value:
            result.append(('text', value))
    for match in FORMATS.finditer(text):
        kind, value = match.lastgroup, match.group().strip()
        if kind == 'short_date':
            context = re.search(r'(?:신청|마감|일정|일시|기간|접수|등록|개최|기한|날짜)\s*'
                                r'(?:기간|일시|마감|일자)?\s*[:：]?\s*$', text[:match.start()])
            if not context:
                continue
            kind = 'date'
        literal(text[cursor:match.start()])
        result.append((kind, normalise(value, year)))
        cursor = match.end()
    literal(text[cursor:])
    return tuple(result)


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


def _proof_binding(report):
    return _hash({key: report.get(key) for key in (
        'gate_version', 'status', 'findings', 'expected_blocks', 'observed_blocks',
        'source_complete', 'direct_comparison', 'source_inventory_sha256', 'render_observation_sha256')})


def normalise(text: str, year: str = '', *, compact=True) -> str:
    text = str(text).replace('\u00a0', ' ')
    # A missing year can inherit only the explicit source block's year.
    def date(m):
        return '%s-%02d-%02d' % (m.group(1) or year, int(m.group(2)), int(m.group(3)))
    text = re.sub(r'(?:(\d{4})\s*(?:년|[./-])\s*)?(\d{1,2})\s*월\s*(\d{1,2})\s*일', date, text)
    text = re.sub(r'(\d{4})\s*[./-]\s*(\d{1,2})\s*[./-]\s*(\d{1,2})\.?', date, text)
    def short_date(m):
        month, day = int(m.group(1)), int(m.group(2))
        return '%s-%02d-%02d' % (year,month,day) if 1 <= month <= 12 and 1 <= day <= 31 else m.group(0)
    text = re.sub(r'(?<![\d./-])(\d{1,2})\s*[./]\s*(\d{1,2})(?:\.(?!\d)|(?=\s|$|\(|~|-|까지|부터))',
                  short_date, text)
    def clock(m):
        hour = int(m.group(2))
        if m.group(1):
            hour = hour % 12 + (12 if m.group(1) == '오후' else 0)
        return '%02d:%02d' % (hour, int(m.group(3) or 0))
    text = re.sub(r'(오전|오후)?\s*(\d{1,2})\s*시\s*(?:(\d{1,2})\s*분)?', clock, text)
    text = re.sub(r'(?<!\d)(\d{1,2}):(\d{2})(?!\d)',
                  lambda m: '%02d:%s' % (int(m.group(1)),m.group(2)),text)
    text = re.sub(r'(?<=\d),(?=\d{3}(?:\D|$))', '', text)
    return re.sub(r'\s+', '' if compact else ' ', text)


def critical_facts(text, year=''):
    """Keep order/multiplicity within the source block, not a document number bag."""
    # Keep whitespace here: compacting "2026-10-30 17:00" joins two
    # independent facts into an invalid day and hides both regex matches.
    value = normalise(text, year, compact=False)
    return {name: [re.sub(r'\s+','',v) for v in re.findall(expression, value)] for name, expression in (
        ('date', r'(?<!\d)(?:20\d{2})?-\d{2}-\d{2}(?!\d)'),
        ('time', r'(?<!\d)\d{1,2}:\d{2}(?!\d)'),
        ('amount', r'(?<![\d.])\d+(?:\.\d+)?\s*(?:만|천)?\s*원'),
        ('selection', r'(?<!\d)\d+\s*(?:개|과목)'))}


def department_links(rows):
    """Read semantic source headers directly, independently of mobile.repeated_columns.

    Two repeated triples and one flat triple share the same (ordinal, department,
    room) relations. Unknown layouts are NOT inferred from column counts.
    """
    originals = rows
    rows = [[re.sub(r'\s+', '', str(c)) for c in row] for row in rows]
    departments, rooms, numbers = {'학과', '학과명'}, {'교실', '수업교실', '장소'}, {'연번', '번호'}
    for index, row in enumerate(rows):
        if len(row) == 2 and row[0] in departments and row[1] in rooms:
            width, numbered = 2, False
        elif len(row) in (3, 6) and all(row[o] in numbers and row[o+1] in departments and row[o+2] in rooms
                                             for o in range(0, len(row), 3)):
            width, numbered = 3, True
        else:
            continue
        pairs, identity_pairs, valid = [], [], True
        for row_index, data in enumerate(rows[index+1:], start=index+1):
            if not any(data):
                continue
            if len(data) != len(row):
                valid = False
                continue
            for offset in range(0, len(row), width):
                group = data[offset:offset+width]
                if not any(group):
                    continue
                if not all(group) or numbered and not group[0].isdigit():
                    valid = False
                    continue
                pairs.append((str(int(group[0])) if numbered else '', group[-2], group[-1]))
                original = originals[row_index][offset:offset+width]
                identity_pairs.append((pairs[-1][0], content_tokens(original[-2]), content_tokens(original[-1])))
        keys = [p[0] if numbered else p[1] for p in pairs]
        valid &= bool(pairs) and len(keys) == len(set(keys))
        return {'recognized': True, 'valid': bool(valid), 'pairs': pairs, 'identity_pairs': identity_pairs,
                'preamble': '\n'.join(' '.join(str(c) for c in r if str(c).strip()) for r in originals[:index])}
    return {'recognized': False, 'valid': False, 'pairs': [], 'identity_pairs': [], 'preamble': ''}


def compare_fact_bundle(state):
    """Compare scoped native facts with actual DOM facts, not training answers.

    This is a research input adapter, not a proof that a parser covered a whole
    original. Rows may move only when a unique explicit first-column key keeps
    every cell association. Units, headers, notes, conditions and multiplicity
    remain evidence. No oracle metadata or model output is read here.
    """
    result = {'quality_label':'insufficient', 'findings':[], 'mode':'shadow_only',
              'human_approved':False, 'whole_original_verified':False}
    if state.get('source_complete') is not True:
        return {**result,'findings':['incomplete_context']}
    try:
        sources, rendered = state['source_blocks'], state['rendered_blocks']
        if len(sources)!=1 or len(rendered)!=1 or rendered[0]['source_refs']!=[sources[0]['id']]:
            raise ValueError('ambiguous binding')
        left, right = json.loads(sources[0]['text']), json.loads(rendered[0]['visible_text'])
        allowed = {'headers','rows','geometry','caption','unit','notes','conditions','facts'}
        for facts in (left,right):
            if not isinstance(facts,dict) or set(facts)-allowed: raise ValueError('unknown facts')
            for key in ('caption','unit'):
                if key in facts and not isinstance(facts[key],str): raise ValueError('text fact')
            for key in ('headers','notes','conditions','facts'):
                if key in facts and (not isinstance(facts[key],list) or not all(isinstance(v,str) for v in facts[key])):
                    raise ValueError('list fact')
            if 'rows' in facts:
                headers=facts.get('headers')
                if not headers or not isinstance(facts['rows'],list): raise ValueError('table context')
                if any(not isinstance(row,list) or len(row)!=len(headers) or
                       not all(isinstance(v,str) for v in row) for row in facts['rows']):
                    raise ValueError('table shape')
            substantive=[v for key in ('facts','conditions','notes') for v in facts.get(key,[]) if v.strip()]
            substantive += [cell for row in facts.get('rows',[]) for cell in row if cell.strip()]
            if not substantive: raise ValueError('empty fact extraction')
            geometry=facts.get('geometry',[])
            if not isinstance(geometry,list) or any(not isinstance(g,list) or len(g)!=4 or
                    any(type(n)!=int for n in g) or min(g[:2])<0 or min(g[2:])<1 for g in geometry):
                raise ValueError('merge geometry')
        findings=[]
        for key in ('caption','unit','headers','notes','conditions','facts'):
            a,b=left.get(key,'' if key in ('caption','unit') else []),right.get(key,'' if key in ('caption','unit') else [])
            normal=lambda x: content_tokens(x) if isinstance(x,str) else [content_tokens(v) for v in x]
            if normal(a)!=normal(b): findings.append(key+'_mismatch')
        a,b=left.get('rows',[]),right.get('rows',[])
        keys=lambda rows:[content_tokens(row[0]) for row in rows]
        unique_keyed=bool(a) and len(set(keys(a)))==len(a) and len(set(keys(b)))==len(b)
        normalized=lambda rows:[tuple(content_tokens(v) for v in row) for row in rows]
        equal=(Counter(normalized(a))==Counter(normalized(b))) if unique_keyed else normalized(a)==normalized(b)
        if not equal: findings.append('row_relation_mismatch')
        # A flat card has no physical table coordinates. Single unmerged cells
        # carry no extra association beyond the already checked header/keyed row.
        # Nontrivial merges still need explicit matching evidence.
        merges=lambda facts:[g for g in facts.get('geometry',[]) if g[2:]!=[1,1]]
        if merges(left)!=merges(right): findings.append('merge_relation_mismatch')
        return {**result,'quality_label':'changed' if findings else 'preserved','findings':findings,
                'source_facts_sha256':_hash(left),'dom_facts_sha256':_hash(right)}
    except (ValueError,KeyError,TypeError,IndexError):
        return {**result,'findings':['unsupported_fact_context']}


def combine_verdict(rule_report, model_reports, *, browser_ok):
    """Trial quality verdict, never an approval or promotion of an R1 checkpoint."""
    labels, completed = [], bool(model_reports)
    for report in model_reports:
        answers = report.get('criteria', report.get('answers', {}))
        completed &= report.get('status') == 'completed' and bool(answers) and not report.get('truncation')
        completed &= not report.get('context_missing') and not report.get('truncated')
        for answer in answers.values():
            raw = answer.get('model_label', answer.get('label'))
            labels.append(raw)
            completed &= raw in ('preserved', 'changed', 'insufficient')
    findings = rule_report.get('findings', [])
    hard = [f for f in findings if f.get('code') not in ('insufficient_evidence', 'source_inventory_invalid')]
    proof = rule_report.get('identity_proof', {})
    valid_proof = (proof.get('complete') is True and rule_report.get('source_complete') is True and
                   proof.get('evidence_sha256') == _proof_binding(rule_report))
    if hard:
        quality, basis = 'changed', 'direct_mismatch'
    elif rule_report.get('status') != 'passed' or findings or not browser_ok:
        quality, basis = 'insufficient', 'incomplete_source_or_browser'
    elif valid_proof:
        quality, basis = 'preserved', 'verified_content_identity'
    elif proof or not completed or not labels:
        quality, basis = 'insufficient', 'identity_not_proven'
    elif 'changed' in labels:
        quality, basis = 'changed', 'model_alert_without_identity_proof'
    elif 'insufficient' in labels:
        quality, basis = 'insufficient', 'model_context_insufficient'
    else:
        quality, basis = 'insufficient', 'legacy_evidence_requires_fresh_comparison'
    return {'status': 'passed' if quality == 'preserved' else 'needs_review',
            'quality_label': quality, 'decision_basis': basis, 'model_complete': bool(completed),
            'rule_status': rule_report.get('status'),
            'model_labels': labels, 'browser_ok': bool(browser_ok), 'mode': 'shadow_only',
            'human_approved': False, 'auto_pass_enabled': False, 'auto_publish_enabled': False}


def compare(sources: list, rendered: list, *, source_complete=True, attachments=()) -> dict:
    findings, direct = [], []
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
        norm = lambda text: content_tokens(text, year)
        left_text = source.get('text', '')
        right_text = block.get('visible_text', '')
        if 'table_rows' in source:
            left_text = '\n'.join([source.get('caption',''), *(' / '.join(row) for row in source['table_rows']), source.get('note','')])
            right_text = '\n'.join([block.get('caption',''), *(' / '.join(row) for row in block.get('table_rows', [])), block.get('note','')])
        expected_facts, actual_facts = critical_facts(left_text, year), critical_facts(right_text, year)
        evidence = {'source_id': sid, 'expected_facts': expected_facts, 'actual_facts': actual_facts}
        direct.append(evidence)
        for name in expected_facts:
            if expected_facts[name] != actual_facts[name]:
                fail(name + '_mismatch', sid)
        if 'table_rows' in source:
            expected = [[norm(c) for c in row] for row in source['table_rows']]
            actual = [[norm(c) for c in row] for row in block.get('table_rows', [])]
            original = source.get('original_table_rows', source['table_rows'])
            original_expected = [[norm(c) for c in row] for row in original]
            src_links, dom_links = department_links(original), department_links(block.get('table_rows', []))
            link_match = (src_links['recognized'] and src_links['valid'] and dom_links['valid'] and
                          Counter(src_links['pairs']) == Counter(dom_links['pairs']) and
                          Counter(src_links['identity_pairs']) == Counter(dom_links['identity_pairs']))
            evidence['department_links'] = {'recognized': src_links['recognized'],
                'expected_count': len(src_links['pairs']), 'observed_count': len(dom_links['pairs']),
                'one_to_one_preserved': bool(link_match)}
            if src_links['recognized'] and not link_match:
                fail('department_classroom_mismatch', sid)
            # Only explicit repeated semantic triples may change shape. Keep the
            # generic row/merge/context gates for all other tables.
            raw_context = '\n'.join(filter(None, [source.get('original_caption', ''), src_links['preamble']]))
            preamble_kept = not src_links['preamble'] or norm(raw_context) == norm(source.get('caption',''))
            if 'original_caption' in source or src_links['preamble']:
                if norm(raw_context) != norm(block.get('caption','')):
                    fail('table_context_mismatch', sid, 'original_caption_or_preamble')
            reflow = (len(original) > 0 and any(len(row) == 6 for row in original) and link_match
                      and preamble_kept and not dom_links['preamble'])
            if original_expected != actual and not reflow:
                fail('table_relation_mismatch', sid)
            if source.get('table_geometry') != block.get('table_geometry') and source.get('table_geometry'):
                fail('table_merge_mismatch', sid)
            for field in ('caption', 'note'):
                if norm(source.get(field, '')) != norm(block.get(field, '')):
                    fail('table_context_mismatch', sid, field)
            evidence['content_identity'] = original_expected == actual or reflow
            continue
        left, right = norm(source.get('text', '')), norm(block.get('visible_text', ''))
        if left != right:
            fail('content_or_condition_mismatch', sid)
        evidence['content_identity'] = left == right
    report = {'gate_version': GATE_VERSION, 'status': 'needs_review' if findings else 'passed',
            'findings': findings, 'expected_blocks': len(sources),
            'observed_blocks': len(rendered), 'source_complete': bool(source_complete),
            'direct_comparison': direct, 'source_inventory_sha256': _hash(sources),
            'render_observation_sha256': _hash(rendered)}
    report['identity_proof'] = {'complete': not findings and bool(sources) and
        all(e.get('content_identity') for e in direct), 'evidence_sha256': _proof_binding(report)}
    return report
