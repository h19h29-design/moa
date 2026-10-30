"""Authored evidence inventories and recorded mutations, not checker labels.

Development and held-out source/template families are disjoint. The evaluator
keeps author seeds, full originals, operations and gold. Workers receive only
the pinned six-field model input and shared questions after candidate freeze.
"""
from __future__ import annotations

import copy
import hashlib

from .evidence import assess_evidence, evidence_input
from .r2_data import fact_state
from .research_challenge import project as frozen_project
from .worker_contract import canonical
from .worker_handoff import questions

VERSION = 'moa.evidence-challenge.v2'
NORMAL = ('identity', 'independent_reorder', 'row_reorder', 'whitespace')
CHANGED = ('date', 'time', 'amount', 'selection', 'condition', 'table_relations',
           'omission', 'duplicate', 'unit', 'footnote', 'procedure_swap', 'reference_swap')
MISSING = ('missing_pages', 'missing_context', 'missing_header', 'missing_footnote', 'missing_merge', 'role_unverified')
OPERATIONS = NORMAL + CHANGED + MISSING
FAMILIES = {'unit': ('fixture_only',),
            'dev': ('dev_application', 'dev_weather', 'dev_club'),
            'test': ('trip_transport', 'library_hours', 'science_equipment',
                     'choice_lunch', 'career_rooms', 'ceremony_roles')}
DEV_OPERATIONS = ('identity', 'independent_reorder', 'row_reorder', 'date', 'table_relations',
                  'condition', 'missing_context', 'missing_header', 'role_unverified')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def _source(seed, family):
    code = digest({'seed': seed, 'template': family})[:10]
    layouts = {
        'trip_transport': (['노선', '탑승 대상', '출발 위치'], [['가노선', '동문 거주자', '운동장'], ['나노선', '서문 거주자', '후문'], ['다노선', '기타 지역', '정문']]),
        'library_hours': (['요일', '이용 학년', '운영 시간'], [['월요일', '1학년', '12:10'], ['수요일', '2학년', '12:40'], ['금요일', '3학년', '13:10']]),
        'science_equipment': (['조', '안전 장비', '보관함'], [['빛조', '보안경', '노랑함'], ['물조', '장갑', '파랑함'], ['흙조', '앞치마', '초록함']]),
        'choice_lunch': (['식단', '신청 대상', '추가 금액'], [['채식', '채식 희망자', '1000원'], ['일반식', '일반식 희망자', '2000원'], ['알레르기식', '담당자 상담 학생', '3000원']]),
        'career_rooms': (['연번', '학과', '수업교실'] * 2, [['1', '해양설계학과', '동관201', '3', '도시생태학과', '서관301'], ['2', '음향제작학과', '동관202', '4', '공간디자인학과', '서관302']]),
        'ceremony_roles': (['팀', '담당 업무', '모이는 곳'], [['새벽팀', '의자 배치', '강당'], ['노을팀', '행사 안내', '로비'], ['바람팀', '물품 정리', '별관']]),
    }
    headers, rows = layouts.get(family, (['신청 구분', '대상', '모임실'],
        [['탐사', '신규 참가자', '푸른실'], ['창작', '재참가자', '하늘실'], ['관찰', '희망 학생', '보라실']]))
    return {'caption': family + ' ' + code, 'headers': headers, 'rows': rows,
            'conditions': ['접수 마감: 2028년 5월 18일', '교육 시작: 14:20', '참가 비용: 3500원',
                           '반드시 3개 선택', '전학 예정자는 신청 대상에서 제외합니다.'],
            'notes': ['준비물은 개인별 지참합니다.', '비가 오면 실내 활동으로 변경합니다.'],
            'facts': ['신청은 담당 교사에게 합니다.'], 'unit': '금액: 원, 선택: 개'}


def _variant(source, operation):
    observed, dom = copy.deepcopy(source), copy.deepcopy(source)
    # A known repeated semantic header can be represented as a single list.
    if dom['headers'] == ['연번', '학과', '수업교실'] * 2:
        dom['headers'] = dom['headers'][:3]
        dom['rows'] = [row[offset:offset+3] for row in dom['rows'] for offset in (0, 3)]
    if operation == 'independent_reorder':
        dom['conditions'] = dom['conditions'][2:] + dom['conditions'][:2]
        dom['notes'].reverse()
    elif operation == 'row_reorder': dom['rows'].reverse()
    elif operation == 'whitespace': dom['notes'] = ['  ' + v + '  ' for v in dom['notes']]
    elif operation in ('date', 'time', 'amount', 'selection'):
        i, old, new = {'date': (0, '18일', '19일'), 'time': (1, '14:20', '14:50'),
                       'amount': (2, '3500원', '5300원'), 'selection': (3, '3개', '2개')}[operation]
        dom['conditions'][i] = dom['conditions'][i].replace(old, new)
    elif operation == 'condition': dom['conditions'][-1] = '전학 예정자도 신청할 수 있습니다.'
    elif operation == 'table_relations': dom['rows'][0][-1], dom['rows'][1][-1] = dom['rows'][1][-1], dom['rows'][0][-1]
    elif operation == 'omission': dom['rows'].pop()
    elif operation == 'duplicate': dom['rows'].append(copy.deepcopy(dom['rows'][0]))
    elif operation == 'unit': dom['unit'] = '금액: 천 원, 선택: 개'
    elif operation == 'footnote': dom['notes'].pop()
    elif operation == 'procedure_swap':
        observed['facts'] = ['1. 준비물을 확인합니다.', '2. 교실로 이동합니다.']
        dom['facts'] = list(reversed(observed['facts']))
    elif operation == 'reference_swap':
        observed['conditions'] = ['신청 마감: 2028년 5월 18일', '위 기한 이후에는 접수하지 않습니다.']
        dom['conditions'] = list(reversed(observed['conditions']))
    original_sha = digest(source)
    contexts = ['b1', 'header1', 'note1', 'annex1', 'merge1']
    p = {'source_revision': original_sha, 'render_revision': digest(dom),
         'file': {'sha256': original_sha, 'integrity_verified': True,
                  'expected_pages': ['p1', 'p2'], 'available_pages': ['p1', 'p2']},
         'scope': {'required_context_ids': contexts, 'provided_context_ids': list(contexts), 'table_inventory_complete': True},
         'tables': [{'id': 't1', 'source_block_id': 'b1', 'required': {'headers': ['header1'], 'footnotes': ['note1'], 'merges': []},
                     'provided': {'headers': ['header1'], 'footnotes': ['note1'], 'merges': []}}],
         'role': {'value': 'notice', 'confirmed_by': 'synthetic_author',
                  'source_sha256': original_sha, 'source_revision': original_sha}}
    if operation == 'missing_pages': p['file']['available_pages'].remove('p2')
    elif operation == 'missing_context':
        p['scope']['provided_context_ids'].remove('annex1'); observed['conditions'].pop()
    elif operation == 'missing_header':
        p['tables'][0]['provided']['headers'] = []; observed['headers'] = []
    elif operation == 'missing_footnote':
        p['tables'][0]['provided']['footnotes'] = []; observed['notes'] = []
    elif operation == 'missing_merge':
        p['tables'][0]['required']['merges'] = ['merge1']; p['tables'][0]['provided']['merges'] = []
    elif operation == 'role_unverified': p['role']['confirmed_by'] = 'parser_hint'
    state = fact_state(observed, dom)
    evidence = assess_evidence(p, state)
    state['source_complete'] = evidence['source_complete']
    key = ('table_relations' if operation in ('identity', 'row_reorder', 'table_relations', 'missing_header', 'missing_merge')
           else 'omission' if operation in ('omission', 'footnote', 'missing_footnote')
           else 'addition' if operation == 'duplicate' else 'conditions')
    # Author's operations explicitly preserve or alter whole relations; no gate
    # or LAYA output is involved. Availability operations remove actual evidence.
    gold = 'preserved' if operation in NORMAL else 'insufficient' if operation in MISSING else 'changed'
    log = {'name': operation, 'observed_source': observed, 'actual_dom': dom,
           'available_pages': p['file']['available_pages'], 'provided_context_ids': p['scope']['provided_context_ids']}
    return state, p, evidence, key, gold, log


def generate(author_seed, split):
    if not isinstance(author_seed, str) or not author_seed or split not in FAMILIES:
        raise ValueError('private author seed and explicit cohort required')
    rows = []
    for family in FAMILIES[split]:
        source = _source(author_seed, family); original_sha = digest(source)
        for op in DEV_OPERATIONS if split == 'dev' else OPERATIONS:
            state, p, evidence, key, gold, log = _variant(source, op)
            rows.append({'case_id': 'ev-' + digest({'source': original_sha, 'op': op})[:24],
                         'state': state, 'provenance': p, 'evidence': evidence,
                         'questions': {key: questions()[key]}, 'expected': {key: gold},
                         'meta': {'split': split, 'evaluation_only': True, 'family': family,
                                  'original_group_id': digest({'version': VERSION, 'family': family, 'source': original_sha}),
                                  'source_sha256': original_sha, 'authored_source': source, 'error_type': op,
                                  'operation': log, 'rights_evidence': 'self_authored_synthetic_only',
                                  'label_origin': 'independent_source_and_recorded_operation'}})
    return rows


def validate(rows, protected_source_hashes):
    from collections import Counter
    ids, groups, inputs, counts = set(), set(), set(), Counter()
    for row in rows:
        meta = row['meta']; source = meta['authored_source']
        if (meta['source_sha256'] in protected_source_hashes or meta['source_sha256'] != digest(source)
                or not meta['evaluation_only'] or meta['rights_evidence'] != 'self_authored_synthetic_only'
                or meta['family'] not in FAMILIES[meta['split']] or meta['error_type'] not in OPERATIONS):
            raise ValueError('protected lineage or source/template provenance mismatch')
        state, p, evidence, key, gold, log = _variant(source, meta['error_type'])
        if any((row['state'] != state, row['provenance'] != p, row['evidence'] != evidence,
                row['expected'] != {key: gold}, row['questions'] != {key: questions()[key]}, meta['operation'] != log)):
            raise ValueError('independent operation / inventory replay mismatch')
        if (row['case_id'] != 'ev-' + digest({'source': meta['source_sha256'], 'op': meta['error_type']})[:24] or
                meta['original_group_id'] != digest({'version': VERSION, 'family': meta['family'], 'source': meta['source_sha256']})):
            raise ValueError('noncanonical case or source group identity')
        input_key = digest({'state': state, 'provenance': p, 'questions': row['questions']})
        if input_key in inputs: raise ValueError('duplicate evaluation input and evidence')
        inputs.add(input_key)
        if row['case_id'] in ids: raise ValueError('duplicate evaluation identity')
        ids.add(row['case_id']); groups.add(meta['original_group_id']); counts[gold] += 1
    return {'version': VERSION, 'items': len(rows), 'groups': len(groups), 'labels': dict(counts),
            'gold_sha256': digest(rows), 'training_eligible': False, 'real_accuracy': None}


def project(rows, freeze, *, explicit):
    inputs = frozen_project(rows, freeze)
    if explicit:
        for row, raw in zip(inputs, rows): row['state'] = evidence_input(raw['state'], raw['evidence'])
    return inputs
