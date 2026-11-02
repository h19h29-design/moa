"""New independently authored relation schemas; original source and HTML stay local.

The oracle replays edits on authored facts. Neither LAYA nor a direct checker
supplies labels. Model inputs are built only after an actual browser DOM read.
The old sealed blind shard is deliberately not an input to this module.
"""
from __future__ import annotations

import copy
import hashlib
import html
import json
import re
from collections import Counter

from .research_dataset import canonical, digest, RIGHTS
from .research_pairs import align, model_state, VERSION as INPUT_VERSION
from .worker_handoff import questions

VERSION = 'moa.paired.author.v1'
# Each schema has a different domain, relation graph or multi-axis header.
# Changing a seed, title, number of rows or family name does not create a schema.
SCHEMAS = [
    ('actor_deadlines', ['주체', '해야 할 일', '마감'],
     [['학생', '희망 강좌 선택', '2028년 3월 11일 17:00'],
      ['보호자', '동의서 제출', '2028년 3월 13일 18:00']],
     ['actor->action->deadline', 'student_before_guardian']),
    ('exclusive_choice', ['선택 묶음', '개수', '대상'],
     [['과학과 미술 중', '1개', '1학년'], ['체육과 음악 중', '2개', '2학년']],
     ['group->cardinality', 'grade->choice_group']),
    ('fee_exception', ['요금 대상', '기본 금액', '면제 조건'],
     [['일반 참가자', '12500원', '해당 없음'], ['보조 참가자', '6500원', '사전 승인 시 전액']],
     ['role->amount', 'exception->fee_override']),
    ('prerequisite_chain', ['단계', '완료해야 하는 선행 단계', '허용 작업'],
     [['안전교육', '없음', '기초 실습'], ['실습 인증', '안전교육', '심화 실습'],
      ['보고서', '실습 인증', '수료 신청']],
     ['stage->prerequisite->permission', 'ordered_dependency']),
    ('merged_grade_sessions', ['학년', '오전 / 시작', '오전 / 교실', '오후 / 시작', '오후 / 교실'],
     [['1학년', '09:20', '나무실', '13:10', '바다실'],
      ['3학년', '10:30', '바다실', '14:20', '나무실']],
     ['merged_header->session->time_room', 'grade->two_sessions']),
    ('shared_course_mentors', ['공통 과정', '강사', '실습실', '담당 반'],
     [['생태 탐사', '강사 가', '별빛실', '1반'], ['생태 탐사', '강사 나', '물결실', '2반'],
      ['지질 탐사', '강사 가', '물결실', '3반']],
     ['rowspan_course->mentor', 'mentor+course->room_class']),
    ('repeated_side_groups', ['연번', '학과', '교실', '선행 이수'],
     [[str(i+1), name, str(i+3)+'실', '기초 '+str(i%3+1)] for i, name in enumerate(
         ['해양로봇', '음향설계', '도시숲', '재난통신', '보존과학', '광학재료', '공공역사'])],
     ['left_right_groups->one_list', 'index->department->room->prerequisite']),
    ('two_unit_budget', ['대상 단위', '항목', '값', '적용 범위'],
     [['학생 1인당', '재료비', '7000원', '실습 전체'], ['조 1개당', '운송비', '19000원', '왕복']],
     ['unit_scope->amount', 'person_vs_group']),
    ('capacity_exception', ['접수 구간', '인원 상한', '우선 순위'],
     [['일반 접수', '24명', '접수순'], ['보조 필요 접수', '3명', '담당자 확인 후 별도 배정']],
     ['window->capacity', 'exception->separate_allocation']),
    ('attachment_workflow', ['파일 역할', '작성 여부', '제출처', '마감'],
     [['안내문', '작성 안 함', '제출 안 함', '없음'],
      ['보호자 동의서 원본', '서명 필수', '담임 교사', '2028년 4월 9일 16:30']],
     ['file_role->fill_requirement', 'form->recipient+deadline']),
    ('weekly_rotation', ['주차', '요일', '대상', '시간', '순환 장소'],
     [['첫째 주', '화요일', '1학년', '14:10', '솔실'],
      ['둘째 주', '목요일', '2학년', '15:20', '달실'],
      ['셋째 주', '화요일', '1학년', '14:10', '달실']],
     ['week+weekday+grade->time_room', 'rotating_venue']),
    ('staged_eligibility', ['선발 단계', '해당 대상', '확인 기간', '다음 단계'],
     [['지원', '희망자', '2028년 5월 2일~4일', '서류 확인'],
      ['서류 확인', '지원서 제출자만', '2028년 5월 6일', '면담'],
      ['면담', '서류 통과자만', '2028년 5월 8일', '최종 안내']],
     ['stage->eligible_cohort+window', 'prior_result->next_stage']),
    ('mutual_exclusion', ['현재 상태', '허용 행동', '동시 신청'],
     [['기존 강좌 유지', '신규 신청 불가', '불가'], ['기존 강좌 취소', '취소 확인 후 신규 신청', '불가']],
     ['state->allowed_transition', 'mutually_exclusive_enrollment']),
    ('route_endpoints', ['노선', '승차 지점', '하차 지점', '차량 표식'],
     [['동쪽 노선', '푸른문', '과학관', '노랑'], ['서쪽 노선', '흰문', '전시관', '초록']],
     ['route->pickup+dropoff', 'route->vehicle_marker']),
    ('overnight_interval', ['구간', '시작 날짜·시간', '종료 날짜·시간'],
     [['천체 관측', '2028년 6월 11일 22:10', '2028년 6월 12일 00:20'],
      ['귀가', '2028년 6월 12일 00:30', '2028년 6월 12일 01:00']],
     ['interval->start+end', 'cross_midnight_date_binding']),
    ('equipment_scope', ['물품', '수량', '배부 단위', '반납 조건'],
     [['보안경', '1개', '학생 1인', '실습 종료 즉시'], ['계측기', '2개', '모둠 1개', '점검 후']],
     ['item->quantity+allocation_unit', 'item->return_condition']),
    ('weather_fallback', ['상황', '활동 장소', '집합 시각', '우선 적용 조건'],
     [['맑음', '잔디마당', '09:10', '기본'], ['비', '체육관', '09:30', '강수 예보 시'],
      ['강풍', '각 반 교실', '09:40', '비 예보보다 우선']],
     ['weather->venue+time', 'condition_priority']),
    ('refund_bands', ['취소 시점', '환급 금액', '필요 서류'],
     [['2028년 7월 1일 이전', '18000원', '취소서'],
      ['2028년 7월 1일~3일', '9000원', '취소서와 영수증'],
      ['2028년 7월 4일 이후', '0원', '없음']],
     ['time_band->refund', 'time_band->required_documents']),
]
OPS = ('identity', 'cards', 'clause_order', 'value', 'relation', 'drop_row',
       'condition', 'unit', 'footnote', 'source_header', 'source_note', 'source_condition')


def schemas():
    return [{'name': n, 'headers': h, 'relations': g,
             'structural_sha256': digest({'headers': h, 'relations': g})}
            for n, h, _, g in SCHEMAS]


def source(index):
    name, headers, rows, graph = SCHEMAS[index]
    # These clauses are substantive prerequisites/notes for the selected table.
    facts = {'headers': headers, 'rows': copy.deepcopy(rows),
             'unit': '표의 금액은 원, 시각은 24시간제입니다.',
             'condition': '사전 등록 후 확인을 받은 경우에만 참여할 수 있습니다.',
             'note': '※ 대리 신청은 불가하며 담당자 확인이 필요합니다.'}
    return name, facts


def sections(facts, with_background=True):
    blocks = []
    if with_background:
        for i in range(8):
            blocks.append({'id': 'background'+str(i), 'context_ids': [],
                           'text': '다른 구역 '+str(i+1)+': 학교 자료실 이용 안내는 별도 공지에서 '
                                   '확인하며 이 표의 신청 및 배정 조건과 혼합하지 않습니다.'})
    for identity, key in [('head', 'headers'), ('unit', 'unit'),
                          ('note', 'note'), ('condition', 'condition')]:
        if key in facts:
            value = facts[key]
            blocks.append({'id': identity, 'text': ' | '.join(value)
                           if isinstance(value, list) else value, 'context_ids': []})
    blocks.append({'id': 'main', 'text': '\n'.join(' | '.join(row) for row in facts['rows']),
                   'context_ids': ['head', 'unit', 'note', 'condition']})
    return blocks


def _edit(facts, operation):
    after = copy.deepcopy(facts)
    if operation in ('identity', 'cards', 'clause_order'):
        return after, 'preserved'
    if operation == 'value':
        value = after['rows'][0][-1]
        alternatives = {'해당 없음': '증빙서 제출 시', '기초 실습': '심화 실습',
                        '나무실': '물결실', '바다실': '별빛실',
                        '실습 전체': '실습 첫날만', '접수순': '추첨순',
                        '없음': '2028년 4월 9일 17:00', '솔실': '별실',
                        '서류 확인': '면담', '불가': '가능', '노랑': '파랑',
                        '실습 종료 즉시': '다음 날', '기본': '강수 예보 시', '취소서': '영수증'}
        after['rows'][0][-1] = (re.sub(r'\d+', lambda m: str(int(m.group())+1), value, count=1)
                                  if re.search(r'\d', value) else alternatives[value])
    elif operation == 'relation':
        # Swap a relation-valued column; all other row identities stay fixed.
        column = next(i for i in range(1, len(after['headers']))
                      if after['rows'][0][i] != after['rows'][1][i])
        after['rows'][0][column], after['rows'][1][column] = (
            after['rows'][1][column], after['rows'][0][column])
    elif operation == 'drop_row':
        after['rows'].pop()
    elif operation == 'condition':
        after['condition'] = '사전 등록 및 담당자 확인 없이 참여할 수 있습니다.'
    elif operation == 'unit':
        after['unit'] = '표의 금액은 천원, 시각은 24시간제입니다.'
    elif operation == 'footnote':
        after['note'] = '※ 대리 신청이 가능하며 담당자 확인은 필요하지 않습니다.'
    elif operation.startswith('source_'):
        after.pop({'source_header': 'headers', 'source_note': 'note',
                   'source_condition': 'condition'}[operation])
        return after, 'insufficient'
    else:
        raise ValueError('unrecognized author operation')
    if after == facts:
        raise ValueError('recorded edit has no effect')
    return after, 'changed'


def _render(facts, operation):
    esc = html.escape
    order = ['head', 'main', 'condition', 'unit', 'note']
    if operation == 'clause_order':
        order = ['unit', 'note', 'main', 'head', 'condition']
    by_id = {b['id']: b for b in sections(facts)}
    pieces = ['<!doctype html><html lang="ko"><meta charset="utf-8">',
              '<style>body{font:16px sans-serif;max-width:390px}section{white-space:pre-wrap;'
              'overflow-wrap:anywhere;margin:12px 0}dt{font-weight:bold}</style><body>']
    for block in sections(facts)[:8]:
        pieces.append('<section data-source="'+block['id']+'">'+esc(block['text'])+'</section>')
    for identity in order:
        if identity == 'main' and operation == 'cards':
            # True HTML card reflow; browser text, not the source facts, is input.
            pieces.append('<section data-source="main">')
            for row in facts['rows']:
                pieces.append('<article><dl>')
                for header, cell in zip(facts['headers'], row):
                    pieces.append('<dt>'+esc(header)+'</dt><dd>'+esc(cell)+'</dd>')
                pieces.append('</dl></article>')
            pieces.append('</section>')
        else:
            pieces.append('<section data-source="'+identity+'">'+esc(by_id[identity]['text'])+'</section>')
    return ''.join(pieces)+'</body></html>'


def author():
    """18 source/structural groups; no randomness or naming-only family creation."""
    docs = []
    for index, schema in enumerate(schemas()):
        name, facts = source(index)
        for operation in OPS:
            altered, expected = _edit(facts, operation)
            src = altered if expected == 'insufficient' else facts
            rendered = facts if expected == 'insufficient' else altered
            # Missing evidence is withheld from the source acquisition only.
            # The HTML remains observable and isn't altered to manufacture gold.
            docs.append({'case_id': 'pair-'+name+'-'+operation,
                         'family': name, 'schema': schema,
                         'split': 'dev' if index % 3 == 2 else 'train',
                         'source_sections': sections(src), 'focus_ids': ['main'],
                         'html': _render(rendered, operation),
                         'oracle': {'source_facts': facts, 'observed_source_facts': src,
                                    'rendered_facts': rendered, 'operation': operation,
                                    'expected': expected}})
    return docs


def validate_oracle(doc):
    oracle = doc['oracle']
    changed, label = _edit(oracle['source_facts'], oracle['operation'])
    src = changed if label == 'insufficient' else oracle['source_facts']
    rendered = oracle['source_facts'] if label == 'insufficient' else changed
    if (src != oracle['observed_source_facts'] or rendered != oracle['rendered_facts']
            or label != oracle['expected']):
        raise ValueError('independent author replay mismatch')
    return label


def build(docs, observations, *, protected_hashes=()):
    """Join gold-free actual DOM observations by ID; build both input treatments."""
    if set(observations) != {d['case_id'] for d in docs}:
        raise ValueError('actual DOM inventory mismatch')
    rows, whole, inventory = [], [], {}
    blocked = set(protected_hashes)
    q = questions()
    for doc in docs:
        label = validate_oracle(doc)
        seen = observations[doc['case_id']]
        if (seen.get('html_sha256') != hashlib.sha256(doc['html'].encode()).hexdigest()
                or seen.get('network_blocked') is not True or seen.get('page_errors') != 0
                or seen.get('observer') != 'chromium-innerText-v1'):
            raise ValueError('actual DOM hash/observer required')
        paired = align(doc['source_sections'], seen['blocks'], focus_ids=doc['focus_ids'])
        sh = digest(doc['oracle']['source_facts'])
        schema_sha = doc['schema']['structural_sha256']
        if sh in blocked or schema_sha in blocked:
            raise ValueError('protected lineage overlap')
        for key in (sh, schema_sha, doc['family']):
            if key in inventory and inventory[key] != doc['split']:
                raise ValueError('source/family split leakage')
            inventory[key] = doc['split']
        # The question is constant for this research, so question type cannot
        # disclose the class. All three labels occur for every source schema.
        criterion = 'table_relations'
        meta = {'split': doc['split'], 'family': doc['family'],
                'family_group_id': schema_sha, 'original_group_id': sh,
                'source_sha256': sh, 'structural_sha256': schema_sha,
                'input_version': INPUT_VERSION, 'rights_status': 'allowed',
                'rights_evidence': RIGHTS, 'synthetic_gold': True, 'human_gold': False,
                'evaluation_only': doc['split'] != 'train', 'forbidden_lineage': [],
                'label_origin': 'independent_source_and_recorded_operation',
                'operation': doc['oracle']['operation'], 'alignment': paired['alignment'],
                'html_sha256': seen['html_sha256'], 'dom_sha256': digest(seen['blocks'])}
        row = {'case_id': doc['case_id'], 'state': model_state(paired),
               'questions': {criterion: q[criterion]}, 'expected': {criterion: label}, 'meta': meta}
        full = copy.deepcopy(row)
        full['state']['source_blocks'] = [{'id': b['id'], 'text': b['text']}
                                           for b in doc['source_sections']]
        full['state']['rendered_blocks'] = copy.deepcopy(seen['blocks'])
        full['meta']['input_version'] = 'moa.whole-document-observed-dom.v1'
        rows.append(row)
        whole.append(full)
    counts = {split: Counter(next(iter(r['expected'].values())) for r in rows
                              if r['meta']['split'] == split) for split in ('train', 'dev')}
    summary = {'version': VERSION, 'input_version': INPUT_VERSION,
               'families': len({r['meta']['family_group_id'] for r in rows}),
               'counts': {s: dict(c) for s, c in counts.items()},
               'rows_sha256': digest(rows), 'whole_rows_sha256': digest(whole),
               'real_documents': 0, 'human_gold': 0, 'operational_ready': False,
               'oracle': 'independent_fact_edit_replay', 'actual_dom_observed': True}
    return rows, whole, summary
