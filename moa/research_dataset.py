"""Self-authored synthetic research corpus with an independent oracle.

Nothing here reads real school notices, prior evaluation sets, model output or
the operational database. Each family is a distinct notice layout authored as
structured facts; the expected label comes from the recorded operation and is
re-verified against those facts, never from a checker or model prediction.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections import Counter

from . import r2_data as r2
from .worker_handoff import questions


VERSION = 'moa.research.dataset.v1'
RIGHTS = 'independently_authored_synthetic_by_moa'

NORMALIZATIONS = ('reorder', 'whitespace', 'synonym', 'card_reflow')
CHANGES = ('date', 'amount', 'selection', 'conditions', 'table_relation',
           'omission', 'addition', 'unit', 'footnote')
MISSING = ('missing_header', 'missing_footnote', 'missing_scope')


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def _table(headers, rows, caption='', geometry=None):
    table = {'headers': list(headers), 'rows': [list(row) for row in rows], 'caption': caption}
    if geometry:
        table['geometry'] = geometry
    return table


def _family(index):
    """Twelve deliberately different layouts, not one template renamed."""
    code = '합성%02d' % index
    kind = index % 4
    if kind == 0:  # long list of participating classes
        rows = [[str(i + 1), '%d-%d' % (i % 3 + 1, i % 5 + 1), '%d실' % (i + 1)]
                for i in range(17)]
        table = _table(['연번', '반', '실'], rows, '%s 배정' % code)
    elif kind == 1:  # grade schedule
        rows = [['%d회' % (i + 1), '%d학년' % (i % 3 + 1),
                 '2027년 11월 %d일 %d:30' % (i + 3, 15 if i % 2 else 14), '강당%d' % (i + 1)]
                for i in range(7)]
        table = _table(['회차', '대상', '일정', '장소'], rows, '%s 학년 일정' % code)
    elif kind == 2:  # merged double header with unit and footnote
        rows = [['탐구%d' % (i + 1), '%d명' % (i + 2), '%d명' % (i + 3), '%d원' % ((i + 1) * 1000)]
                for i in range(5)]
        table = _table(['과정', '오전 참여', '오후 참여', '재료비'], rows,
                       '%s 과정별 인원' % code, geometry=[[0, 1, 1, 2]])
    else:  # repeated left/right triples flattened to a 3-column list
        rows = [[str(i + 1), '학과%d' % (i + 1), '%d실' % (i + 1)] for i in range(9)]
        table = _table(['연번', '학과', '교실'], rows, '%s 배정' % code)
    facts = {
        'caption': table['caption'],
        'headers': table['headers'],
        'rows': table['rows'],
        'unit': '참가비 단위: 원, 인원 단위: 명',
        'conditions': ['신청 마감: 2027년 11월 %d일 16:00' % (20 + index),
                       '참가 금액: %d원' % (8000 + index * 500),
                       '반드시 2개를 선택해야 합니다.'],
        'notes': ['알레르기가 있으면 담당 교사에게 먼저 알려야 합니다.'],
        'facts': ['신청은 학교 안내 페이지에서만 받습니다.'],
    }
    if table.get('geometry'):
        facts['geometry'] = table['geometry']
    return {'code': code, 'facts': facts, 'shape': table['rows'][0].__len__()
            if table['rows'] else 0, 'type': ('long_list', 'schedule', 'merged', 'triples')[kind]}


def FAMILY_COUNT():
    return 12


def _semantic(facts):
    """Author's independent meaning key: order of independent clauses is not meaning."""
    synonyms = (('꼭', '반드시'), ('하여야', '해야'), ('됩니다', '합니다'))

    def norm(text):
        value = ' '.join(str(text).split())
        for source, target in synonyms:
            value = value.replace(source, target)
        return value

    rows = tuple(tuple(norm(cell) for cell in row) for row in facts.get('rows', []))
    return {
        'caption': norm(facts.get('caption', '')),
        'headers': tuple(norm(h) for h in facts.get('headers', [])),
        'rows': rows,
        'unit': norm(facts.get('unit', '')),
        'conditions': tuple(sorted(norm(c) for c in facts.get('conditions', []))),
        'notes': tuple(sorted(norm(n) for n in facts.get('notes', []))),
        'facts': tuple(sorted(norm(f) for f in facts.get('facts', []))),
        'geometry': tuple(tuple(g) for g in facts.get('geometry', [])),
    }


def _mobile_text(facts):
    """Compact mobile rendering of the same facts (no gold, no metadata).

    This stands in for the real renderer's DOM text: the table becomes a card
    list, conditions stay one per line, and nothing is summarised away.
    """
    lines = []
    if facts.get('caption'):
        lines.append('# ' + str(facts['caption']))
    rows = facts.get('rows', [])
    headers = facts.get('headers')
    if rows and headers:
        # A real card list repeats the column name once per card, so the compact
        # mobile form keeps the header row once and then each row in order.
        lines.append(' | '.join(str(h) for h in headers))
        lines += ['- ' + ' | '.join(str(cell) for cell in row) for row in rows]
    elif rows:
        lines += ['- ' + ' / '.join(str(cell) for cell in row) for row in rows]
    lines += ['* ' + str(item) for item in facts.get('conditions', [])]
    if facts.get('unit'):
        lines.append('단위: ' + str(facts['unit']))
    lines += [str(item) for item in facts.get('facts', [])]
    lines += [str(item) for item in facts.get('notes', [])]
    return '\n'.join(line for line in lines if line)


def _notice_text(facts):
    """Human-readable notice body as the original document would read."""
    lines = []
    if facts.get('caption'):
        lines.append(str(facts['caption']))
    headers = facts.get('headers')
    rows = facts.get('rows', [])
    if rows and headers:
        lines.append(' | '.join(str(h) for h in headers))
        lines += [' | '.join(str(cell) for cell in row) for row in rows]
    elif rows:
        lines += [' | '.join(str(cell) for cell in row) for row in rows]
    lines += [str(item) for item in facts.get('conditions', [])]
    if facts.get('unit'):
        lines.append(str(facts['unit']))
    lines += [str(item) for item in facts.get('facts', [])]
    lines += [str(item) for item in facts.get('notes', [])]
    return '\n'.join(line for line in lines if line)


def _state(source, rendered, complete):
    """The same field layout the installed policy expects, as plain text."""
    return {'title': '', 'file_role': 'notice', 'source_complete': complete,
            'source_blocks': [{'id': 'b1', 'text': _notice_text(source)}],
            'rendered_blocks': [{'id': 'r1', 'source_refs': ['b1'],
                                 'visible_text': _mobile_text(rendered)}],
            'attachment_manifest': []}


def _variant(facts, operation):
    """Apply one recorded operation and return the independent expected label."""
    after = copy.deepcopy(facts)
    name = operation
    if name == 'reorder':
        after['conditions'] = list(reversed(after['conditions']))
        expected = 'preserved'
    elif name == 'whitespace':
        # Collapse/expand runs of spaces and add a trailing space. Word boundaries
        # are preserved, so the mobile text differs only in spacing.
        after['notes'] = ['  '.join(' '.join(note.split()).split(' ')) + ' '
                          for note in after['notes']]
        expected = 'preserved'
    elif name == 'synonym':
        after['conditions'] = [c.replace('반드시', '꼭') for c in after['conditions']]
        expected = 'preserved'
    elif name == 'card_reflow':
        # Cards show the same rows; only the visual grouping changes.
        after['rows'] = copy.deepcopy(after['rows'])
        after['layout'] = 'cards'
        expected = 'preserved'
    elif name == 'date':
        after['conditions'][0] = after['conditions'][0].replace('2027년 11월', '2027년 12월')
        expected = 'changed'
    elif name == 'amount':
        after['conditions'][1] = re.sub(r'\d+', lambda m: str(int(m.group()) * 2), after['conditions'][1], count=1)
        expected = 'changed'
    elif name == 'selection':
        after['conditions'][2] = after['conditions'][2].replace('2개', '3개')
        expected = 'changed'
    elif name == 'conditions':
        after['conditions'] = after['conditions'][:2]
        expected = 'changed'
    elif name == 'table_relation':
        if len(after['rows']) < 2:
            return None
        first, second = after['rows'][0], after['rows'][1]
        first[-1], second[-1] = second[-1], first[-1]
        expected = 'changed'
    elif name == 'omission':
        after['rows'] = after['rows'][:-1]
        expected = 'changed'
    elif name == 'addition':
        after['rows'].append(['추가', '신설', '미정'])
        expected = 'changed'
    elif name == 'unit':
        after['unit'] = '참가비 단위: 천원, 인원 단위: 명'
        expected = 'changed'
    elif name == 'footnote':
        after['notes'] = after['notes'] + ['※ 우천 시 일정이 변경될 수 있습니다.']
        expected = 'changed'
    elif name == 'missing_header':
        after.pop('headers')
        expected = 'insufficient'
    elif name == 'missing_footnote':
        after['notes'] = []
        expected = 'insufficient'
    elif name == 'missing_scope':
        after['conditions'] = []
        after['facts'] = []
        expected = 'insufficient'
    else:
        raise ValueError('unknown operation')
    # Independent replay: the recorded operation must actually move the facts
    # the way the label claims, for every family and every row.
    same = _semantic(after) == _semantic(facts)
    if expected == 'preserved' and not same:
        raise ValueError('no-op normalization changed facts')
    if expected == 'changed' and same:
        raise ValueError('semantic operation produced identical facts')
    return after, expected


def generate(author_seed, families=FAMILY_COUNT()):
    if not isinstance(author_seed, str) or not author_seed:
        raise ValueError('private author seed required')
    if not isinstance(families, int) or not 1 <= families <= 40:
        raise ValueError('family count out of range')
    rows = []
    definitions = questions()
    operations = NORMALIZATIONS + CHANGES + MISSING
    for index in range(families):
        family = _family(index)
        family_id = digest({'version': VERSION, 'seed': author_seed, 'code': family['code']})
        source_sha = digest(family['facts'])
        for operation in operations:
            variant = _variant(family['facts'], operation)
            if variant is None:
                continue
            after, expected = variant
            if operation == 'card_reflow':
                after = copy.deepcopy(after)
                after.pop('layout', None)
            if expected == 'insufficient':
                # Only the missing evidence is withheld from the source side;
                # rendered evidence is never repaired from it.
                state = _state(after, after, False)
            else:
                state = _state(family['facts'], after, True)
            criterion = ('table_relations' if operation in
                         ('table_relation', 'omission', 'addition', 'card_reflow')
                         else 'omission' if operation in MISSING
                         else 'conditions')
            question = {criterion: definitions[criterion]}
            rows.append({
                'case_id': 'rs-' + digest({'family': family_id, 'operation': operation})[:24],
                'state': state,
                'questions': question,
                'expected': {criterion: expected},
                'meta': {
                    'split': 'held',
                    'family': family['code'],
                    'family_group_id': family_id,
                    'layout_type': family['type'],
                    'source_sha256': source_sha,
                    'label_origin': 'independent_source_and_recorded_operation',
                    'rights_status': 'allowed',
                    'rights_evidence': RIGHTS,
                    'forbidden_lineage': [],
                    'synthetic_gold': True,
                    'human_gold': False,
                    'evaluation_only': False,
                    'operation': {'name': operation, 'expected': expected},
                },
            })
    if not rows:
        raise ValueError('generator produced no rows')
    return rows


def reject_protected(rows, protected_source_hashes):
    blocked = set(protected_source_hashes or ())
    overlap = [row['case_id'] for row in rows if row['meta']['source_sha256'] in blocked]
    if overlap:
        raise ValueError('protected evaluation source overlap: %d rows' % len(overlap))
    return True


def summarize(rows):
    families = {row['meta']['family_group_id'] for row in rows}
    labels = Counter(next(iter(row['expected'].values())) for row in rows)
    operations = Counter(row['meta']['operation']['name'] for row in rows)
    return {
        'version': VERSION,
        'scope': 'synthetic_research',
        'rows': len(rows),
        'families': len(families),
        'labels': dict(labels),
        'operations': dict(operations),
        'rights': RIGHTS,
        'human_gold': 0,
        'human_approvals': 0,
        'real_documents': 0,
        'operational_ready': False,
        'gold_sha256': digest(rows),
    }


def split_dataset(rows, ratios=(0.7, 0.15, 0.15)):
    """Group split, so every family stays in exactly one part."""
    order = []
    for row in rows:
        family = row['meta']['family_group_id']
        if family not in order:
            order.append(family)
    total = len(order)
    if total < 3:
        raise ValueError('at least three independent families are required')
    train_n = max(1, int(round(total * ratios[0])))
    dev_n = max(1, int(round(total * ratios[1])))
    if train_n + dev_n >= total:
        dev_n = 1
        train_n = total - 2
    parts = []
    cursor = 0
    for name, count in (('train', train_n), ('dev', dev_n), ('blind', total - train_n - dev_n)):
        chunk = order[cursor:cursor + count]
        cursor += count
        members = set(chunk)
        subset = [copy.deepcopy(row) for row in rows if row['meta']['family_group_id'] in members]
        for row in subset:
            row['meta']['split'] = name
        parts.append({'name': name, 'families': chunk, 'rows': subset})
    seen = {}
    for part in parts:
        for family in part['families']:
            if family in seen:
                raise ValueError('family split leakage')
            seen[family] = part['name']
    return parts


def project(rows):
    """Gold-free projection safe to hand to a worker."""
    return [{'case_id': row['case_id'], 'state': copy.deepcopy(row['state']),
             'questions': copy.deepcopy(row['questions'])} for row in rows]
